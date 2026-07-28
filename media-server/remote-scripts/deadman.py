#!/usr/bin/env python3
"""Deadman: external watchdog for screen-machine, run by a systemd timer on
the media-server box (the always-on machine driving the kiosk displays).

Every run:
  1. probes screen-machine's /api/health via the SAME public URL the kiosk
     Chrome instances load — so one probe catches Flask death, WSL-IP /
     portproxy drift and public-IP changes alike
  2. on success, inspects the health JSON: wedged scheduler loops, dead WS
     server, and "Siren cannot send its own alert emails"
  3. checks local disk usage and local systemd services (kiosk, light relay)
  4. emails alerts DIRECTLY via the Gmail REST API (stdlib urllib only —
     nothing to install on this box), with per-key throttles and recovery
     notices

Self-mailbox note: the alert recipient is an alias of the sending account, so
after every send the message must be labeled INBOX+UNREAD or it sits in Sent
only (see docs/ALERTING_PROPOSAL.md §2).

Config:  /etc/deadman/deadman.env   (root:root 600)
State:   /var/lib/deadman/state.json
Log:     /var/log/deadman.log
Install: media-server/local-scripts/install-deadman.sh (run from WSL)
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

ENV_FILE = "/etc/deadman/deadman.env"
STATE_FILE = "/var/lib/deadman/state.json"
LOG_FILE = "/var/log/deadman.log"

THROTTLES_S = {
    "host_down": 6 * 3600,
    "heartbeat_stale": 6 * 3600,
    "ws_dead": 6 * 3600,
    "email_channel": 6 * 3600,
    "email_unconfigured": 24 * 3600,
    "disk": 24 * 3600,
    "service": 6 * 3600,
}
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def load_env(path=ENV_FILE):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError as e:
        log(f"cannot read {path}: {e}")
    return env


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            if isinstance(s, dict):
                s.setdefault("misses", 0)
                s.setdefault("last_email", {})
                s.setdefault("active", {})
                return s
    except Exception:
        pass
    return {"misses": 0, "last_email": {}, "active": {}}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"cannot save state: {e}")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    sys.stdout.write(line)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-2000:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _http(url, data=None, headers=None, timeout=15, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def send_email(env, subject, body):
    """Gmail REST via urllib; returns bool. Never raises."""
    try:
        needed = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                  "GOOGLE_REFRESH_TOKEN", "ALERT_EMAIL_TO")
        if not all(env.get(k) for k in needed):
            log("email not configured (missing env keys)")
            return False
        data = urllib.parse.urlencode({
            "client_id": env["GOOGLE_CLIENT_ID"],
            "client_secret": env["GOOGLE_CLIENT_SECRET"],
            "refresh_token": env["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }).encode()
        status, text = _http(TOKEN_URL, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        token = json.loads(text)["access_token"]

        msg = EmailMessage()
        msg["To"] = env["ALERT_EMAIL_TO"]
        msg["Subject"] = subject
        msg.set_content(body + f"\n\n--\nDeadman watchdog on "
                               f"{os.uname().nodename} "
                               f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
        status, text = _http(
            f"{API_BASE}/messages/send",
            data=json.dumps({"raw": raw}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        if status != 200:
            log(f"gmail send failed: HTTP {status}")
            return False
        # Self-mailbox: without this the message sits in Sent only
        if env.get("SELF_MAILBOX", "1") not in ("0", "false"):
            msg_id = json.loads(text).get("id")
            if msg_id:
                try:
                    _http(f"{API_BASE}/messages/{msg_id}/modify",
                          data=json.dumps(
                              {"addLabelIds": ["INBOX", "UNREAD"]}).encode(),
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          timeout=10)
                except Exception as e:
                    log(f"label add failed (message in Sent only): {e}")
        log(f"emailed: {subject}")
        return True
    except Exception as e:
        log(f"send_email failed: {type(e).__name__}: {e}")
        return False


def throttled(state, key, now):
    interval = THROTTLES_S.get(key.split(":")[0], 6 * 3600)
    if now - state["last_email"].get(key, 0) >= interval:
        state["last_email"][key] = now
        return True
    return False


def raise_issue(env, state, now, key, subject, body):
    state["active"][key] = True
    if throttled(state, key, now):
        send_email(env, f"[screen-machine-watchdog] {subject}", body)


def resolve_issue(env, state, key, subject, body):
    if state["active"].get(key):
        state["active"][key] = False
        state["last_email"].pop(key, None)
        send_email(env, f"[screen-machine-watchdog] RECOVERED: {subject}", body)


def probe(env):
    url = env.get("PROBE_URL",
                  "http://95.141.21.170:8000/api/health?probe=deadman")
    try:
        status, text = _http(url, timeout=10)
        if status == 200:
            try:
                return True, json.loads(text)
            except Exception:
                return True, None
        return True, None       # any HTTP response = host reachable
    except urllib.error.HTTPError:
        return True, None       # 404 etc: reachable, no health endpoint yet
    except Exception:
        return False, None


def check_host(env, state, now):
    ok, health = probe(env)
    if not ok:
        state["misses"] += 1
        log(f"probe FAILED ({state['misses']} consecutive)")
        if state["misses"] >= int(env.get("MISS_THRESHOLD", "2")):
            raise_issue(env, state, now, "host_down",
                        "CRITICAL: screen-machine unreachable",
                        f"Probe of {env.get('PROBE_URL', 'public health URL')} "
                        f"has failed {state['misses']} consecutive times "
                        "(>=10 min). Flask may be dead, WSL may be down or "
                        "rebooted into the stale boot topology, or the "
                        "portproxy/public-IP path the kiosks depend on is "
                        "broken.")
        return None
    if state["misses"] >= int(env.get("MISS_THRESHOLD", "2")):
        resolve_issue(env, state, "host_down",
                      "screen-machine reachable",
                      "The health probe succeeds again.")
    state["misses"] = 0
    return health


def check_health_payload(env, state, now, health):
    if not isinstance(health, dict):
        return
    stale = health.get("stale_destinations") or []
    if stale:
        raise_issue(env, state, now, "heartbeat_stale",
                    f"scheduler wedged: {', '.join(map(str, stale))}",
                    "Flask answers but these scheduler loops have not ticked "
                    f"for over 10 minutes: {stale}. Screens will show stale "
                    "art until restarted.")
    else:
        resolve_issue(env, state, "heartbeat_stale", "scheduler ticking",
                      "All scheduler loops are ticking again.")

    ws = health.get("ws_server") or {}
    if ws.get("supervised") and ws.get("alive") is False:
        raise_issue(env, state, now, "ws_dead",
                    "overlay WebSocket server down",
                    "The overlay WS server thread is not alive — overlays, "
                    "RunPod progress and lux ingestion are broken.")

    alerts = health.get("alerts") or {}
    if alerts.get("channel_consecutive_failures", 0) >= 3 or \
            alerts.get("email_channel") == "failing":
        raise_issue(env, state, now, "email_channel",
                    "screen-machine cannot send its own alert emails",
                    f"Siren reports {alerts.get('channel_consecutive_failures')}"
                    " consecutive Gmail delivery failures. Its in-process "
                    "alerts are NOT arriving — check the Google token.")
    elif alerts.get("email_channel") == "ok":
        resolve_issue(env, state, "email_channel", "alert email channel",
                      "Siren's email channel is delivering again.")
    if alerts.get("email_channel") == "unconfigured":
        raise_issue(env, state, now, "email_unconfigured",
                    "screen-machine alerting unconfigured",
                    "Siren reports its email channel is unconfigured — "
                    "ALERT_EMAIL_TO / GOOGLE_* missing from .env?")


def check_disk(env, state, now):
    try:
        du = shutil.disk_usage("/")
        pct = round(du.used / du.total * 100, 1)
    except Exception as e:
        log(f"disk check failed: {e}")
        return
    threshold = float(env.get("DISK_THRESHOLD_PCT", "90"))
    if pct >= threshold:
        raise_issue(env, state, now, "disk",
                    f"media-server disk at {pct}%",
                    f"Root filesystem on the kiosk box is {pct}% full "
                    f"(threshold {threshold}%). This box has previously hit "
                    "100% and broken the displays. Free space soon.")
    elif pct < threshold - 5:
        resolve_issue(env, state, "disk", "media-server disk",
                      f"Root filesystem back to {pct}%.")
    return pct


def check_services(env, state, now):
    for svc in env.get("SERVICES", "kiosk.service light-relay.service").split():
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=10)
            active = r.stdout.strip() == "active"
        except Exception as e:
            log(f"systemctl check failed for {svc}: {e}")
            continue
        key = f"service:{svc}"
        if not active:
            raise_issue(env, state, now, key, f"{svc} not active",
                        f"systemctl reports {svc} is "
                        f"'{r.stdout.strip() or 'unknown'}' on the media "
                        "server.")
        else:
            resolve_issue(env, state, key, svc, f"{svc} is active again.")


def main():
    env = load_env()
    state = load_state()
    now = time.time()
    health = check_host(env, state, now)
    if health is not None:
        check_health_payload(env, state, now, health)
    pct = check_disk(env, state, now)
    check_services(env, state, now)
    save_state(state)
    log(f"run complete: misses={state['misses']} disk={pct}% "
        f"active_issues={[k for k, v in state['active'].items() if v]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"deadman crashed: {type(e).__name__}: {e}")
        sys.exit(0)  # never let a watchdog bug flap the systemd unit
