#!/usr/bin/env python3
"""Kiosk browser cycle: fade the TVs to black, restart their Chromes, fade back in (owner, 23 Nov: a browser restart
"cycled clumsily through plain white").

How it stays black the whole way (measured on the TVs 23 Nov: black throughout, about 5 s):
  1. each TV's page fades to black (its __smFadeOut, or a black layer on an older page);
  2. its Chrome is stopped; the launcher (kiosk-wayland.sh) starts a new one within 5 s, which loads at once
     (--password-store=basic: it used to wait 25 s on a missing keyring, showing its empty white window);
  3. the page loads under black (index.html sm-display + #sm-cover) and fades in when its first picture is on glass.
It won't cycle while a TV is live, frozen or just back from live (the page's own reload rule) unless --force.

  kiosk_cycle.py cycle [north|south ...] [--force] [--relaunch]
      --relaunch also restarts the launcher itself (to load a new kiosk-wayland.sh) with its sway environment.
  kiosk_cycle.py wait-ready PORT [--timeout S]       exit 0 once that Chrome's page has loaded its black styling

Runs nightly from kiosk-cycle.timer (systemd --user, 04:30: Chrome's memory doesn't creep for weeks) and by hand.
Lives next to output_watchdog.py (~/kiosk-watchdog/), whose stdlib DevTools client it uses. Logs "cycle:" lines to
/var/log/kiosk.log.
"""
import argparse
import glob
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from output_watchdog import LOG, SCREENS, cdp_eval, chrome_browser_pid, display_page, sway_sock  # noqa: E402

LAUNCHER = "/usr/local/bin/kiosk-wayland.sh"
LAUNCHER_PID = "/tmp/kiosk-wayland.pid"

# The page's own fade (resolves once black); an older page without it gets a plain black layer.
FADE_JS = ("(window.__smFadeOut ? window.__smFadeOut() : new Promise(r => { const d = document.createElement('div');"
           " d.style.cssText = 'position:fixed;inset:0;background:#000;opacity:0;transition:opacity 1s ease;"
           "z-index:2147483647;pointer-events:none'; document.body.appendChild(d);"
           " requestAnimationFrame(() => { d.style.opacity = '1'; }); setTimeout(r, 1200); })).then(() => true)")
MAY_JS = "window.__smCtl ? window.__smCtl.canReloadNow() : true"
PAINTED_JS = "document.documentElement.getAttribute('data-sm-painted') === '1'"
# The page has parsed its head: from here every frame it draws is black (index.html sm-display + #sm-cover).
READY_JS = "document.documentElement.classList.contains('sm-display') && document.readyState !== 'loading'"


def log(msg):
    line = f"{time.strftime('%F %T')}  cycle: {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def evaluate(port, expr, timeout=5):
    ws = display_page(port)
    if not ws:
        return None
    try:
        return cdp_eval(ws, expr, timeout)
    except (OSError, ValueError, ConnectionError):
        return None


def painted(port, timeout, expr=PAINTED_JS):
    end = time.time() + timeout
    seen = None
    while time.time() < end:
        seen = evaluate(port, expr, timeout=2)
        if seen is True:
            return True
        time.sleep(0.25)
    page = display_page(port)
    print(f"{time.strftime('%F %T')}  cycle: port {port}: not painted ({'no page' if not page else f'page says {seen!r}'})",
          file=sys.stderr)
    return False


def stop_and_wait(pids, timeout=10):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.time() + timeout
    while time.time() < end and any(os.path.exists(f"/proc/{p}") for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if os.path.exists(f"/proc/{pid}"):
            os.kill(pid, signal.SIGKILL)


def _is_launcher(d):
    try:
        argv = open(f"{d}/cmdline", "rb").read().split(b"\0")
    except OSError:
        return False
    return len(argv) > 1 and argv[0].endswith(b"bash") and argv[1] == LAUNCHER.encode()


def launcher_env():
    """The running launcher's pid and environment (it was started by sway: SWAYSOCK, WAYLAND_DISPLAY, ...)."""
    try:
        pid = int(open(LAUNCHER_PID).read().strip())
    except (OSError, ValueError):
        pid = None
    if pid is None or not os.path.exists(f"/proc/{pid}"):
        # a launcher from before the pid file: find it by its command line
        pid = next((int(d[6:]) for d in glob.glob("/proc/[0-9]*") if _is_launcher(d)), None)
    if pid is None:
        return None, None
    try:
        raw = open(f"/proc/{pid}/environ", "rb").read()
        env = dict(kv.split("=", 1) for kv in raw.decode(errors="replace").split("\0") if "=" in kv)
    except OSError:
        env = None
    if not env or not env.get("SWAYSOCK"):
        # its environment isn't readable (a login-session process): rebuild what it needs from sway's sockets
        sock = sway_sock()
        run = f"/run/user/{os.getuid()}"
        wl = sorted(n for n in os.listdir(run) if n.startswith("wayland-") and not n.endswith(".lock"))
        if not sock or not wl:
            return pid, None
        env = {**os.environ, "SWAYSOCK": sock, "WAYLAND_DISPLAY": wl[0], "XDG_RUNTIME_DIR": run}
    return pid, env


def cycle(names, force, relaunch):
    ports = {n: SCREENS[n][1] for n in names}
    if not force:
        busy = [n for n, p in ports.items() if evaluate(p, MAY_JS) is False]
        if busy:
            log(f"not now: {', '.join(busy)} live, frozen or just back from live (--force to cycle anyway)")
            return 2
    old_launcher, env = launcher_env() if relaunch else (None, None)
    if relaunch and not env:
        log("no running launcher to take the environment from; cycling the browsers only")
        relaunch = False
    with ThreadPoolExecutor(len(ports)) as ex:
        faded = dict(zip(ports, ex.map(lambda p: evaluate(p, FADE_JS, timeout=6), ports.values())))
    log("faded to black: " + ", ".join(f"{n} {'ok' if v else 'no page'}" for n, v in faded.items()))
    t0 = time.time()
    if relaunch:
        stop_and_wait([old_launcher])
    stop_and_wait([pid for pid in (chrome_browser_pid(n) for n in names) if pid])
    if relaunch:
        subprocess.Popen([LAUNCHER], env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"launcher restarted ({LAUNCHER})")
    log(f"stopped {', '.join(names)}; the launcher brings them back under black")
    back = {n: painted(p, 60) for n, p in ports.items()}
    log("back: " + ", ".join(f"{n} {'drawing' if ok else 'NOT drawing after 60 s'}" for n, ok in back.items())
        + f" ({time.time() - t0:.0f} s)")
    return 0 if all(back.values()) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cycle")
    c.add_argument("screens", nargs="*", help=f"any of {', '.join(SCREENS)} (default: all)")
    c.add_argument("--force", action="store_true")
    c.add_argument("--relaunch", action="store_true")
    w = sub.add_parser("wait-ready")
    w.add_argument("port", type=int)
    w.add_argument("--timeout", type=float, default=20)
    a = ap.parse_args()
    if a.cmd == "wait-ready":
        sys.exit(0 if painted(a.port, a.timeout, READY_JS) else 1)
    bad = [n for n in a.screens if n not in SCREENS]
    if bad:
        ap.error(f"unknown screen {', '.join(bad)} (have {', '.join(SCREENS)})")
    # a new launcher starts every screen's Chrome, so a relaunch cycles them all
    sys.exit(cycle(list(SCREENS) if a.relaunch or not a.screens else a.screens, a.force, a.relaunch))


if __name__ == "__main__":
    main()
