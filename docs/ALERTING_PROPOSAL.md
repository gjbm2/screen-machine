# Production Alerting for screen-machine — Proposal

**Status:** IMPLEMENTED and live as of 2026-07-28 (Siren in `utils/alerts/`, Deadman deployed to the media server as `deadman.timer`). One manual step outstanding: create a free healthchecks.io check and set `HEALTHCHECK_PING_URL` in `.env`.
**Date:** 2026-07-28
**Codename:** Siren (in-process) + Deadman (external watchdog)

---

## 1. Why now — the evidence

This isn't hypothetical. The investigation found three real, recent incidents that produced **zero human-facing signal**:

1. **OpenAI credit exhaustion is happening right now.** The current log holds **107** `insufficient_quota` 429 errors in `handle_reason`/`handle_generate` (`routes/scheduler_handlers.py:1410` → `routes/openai.py:159/238`) — 5 on 2026-07-19 and **102 on 2026-07-28 (today)**. The scheduler silently applies fallback values and keeps ticking; screens show stale art. The human-readable message is logged at **INFO** level — only the traceback at ERROR.
2. **A 9-day unnoticed Flask outage.** `logs/screen_machine.log` jumps from 2026-07-19 11:05 to 2026-07-28 09:45. The log normally churns ~10 MB per 8 hours; nine days of silence went undetected because nothing watches the process.
3. **The kiosk box disk is at 93%** (was 25.9% in Nov 2025), with 161 recorded samples at 100% in `media-server/maintenance.log`. Nothing alerts on it; `check-status-compact.sh` gathers exactly the right facts but nothing schedules it.

Today the entire error surface is: `logs/screen_machine.log` (rotating), the in-memory `console_logs` list behind `GET /api/logs`, in-memory per-destination scheduler logs (lost on restart), and 5-second on-TV overlays. **There is no email, push, or webhook channel anywhere in the codebase.**

---

## 2. What auto-klevio gives us

The sibling repo `~/dev/auto-klevio` (Node/TypeScript) already emails alerts via the **Gmail API v1** — `POST /gmail/v1/users/me/messages/send` with a base64url-encoded raw RFC-2822 message (`src/gmail.ts:269-358`). Key facts for reuse:

- **Credentials are four env vars** in `~/dev/auto-klevio/.env` (gitignored): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_REDIRECT_URI`. No token files on disk; access tokens are minted in-memory from the refresh token.
- **Granted scopes**: `gmail.modify` (superset of `gmail.send` — sending is already authorized) + `spreadsheets`, obtained once via `auth-setup.ts` with `access_type=offline`.
- **Refresh tokens are language-agnostic and safely shareable.** They don't rotate on use; each process mints independent access tokens. The token has been live since ~Jan 2026, so the OAuth app is not in "Testing" status (which would expire tokens after 7 days). **Do not re-run any consent flow** — the existing grant suffices.
- **Reuse = two plain HTTPS calls.** No Google SDK needed: `POST https://oauth2.googleapis.com/token` (refresh → access token), then `POST .../users/me/messages/send` with the raw message. `requests` is already used throughout screen-machine (pin it in `requirements.txt`). Omitting the `From:` header lets Gmail stamp the authenticated sender.
- Patterns worth copying: `sendNotificationEmail` **never throws** (logs and returns false); recipient env var unset ⇒ alerting silently disabled (dev/CI safe); email bodies embed a captured log transcript. (One simplification vs the source: auto-klevio resolves the sender via `users.getProfile` and sets an explicit `From:` header; in Python we simply omit `From:` — Gmail stamps the authenticated sender — saving the extra API call.)
- Pattern deliberately **not** copied: auto-klevio has no throttle/dedup — a persistent failure re-emails every 5-minute tick. We must do better (§6).
- **Self-mailbox delivery quirk (discovered during implementation):** the sending account is greg@gregmarsh.co.uk and alerts@gregmarsh.co.uk is an alias of that same mailbox. A plain `messages.send` to it produces one copy labeled only `SENT` — it never reaches the inbox. Every sender must therefore follow up with `messages/{id}/modify` adding `INBOX`+`UNREAD` (covered by the existing `gmail.modify` scope). Implemented in `GmailChannel` (auto-detected via sender/recipient domain match; `ALERT_SELF_MAILBOX` overrides) and in the Deadman mailer.

Setup is a one-time manual copy of the three secret values from `auto-klevio/.env` into `screen-machine/.env` (never committed, never printed).

---

## 3. Architecture — two legs plus a third-party dead-man's switch

No purely in-process design can report its own death (incident #2), and nothing on WSL can see the kiosk box's disk (incident #3). Hence:

```
┌────────────────────────────── WSL2 ──────────────────────────────┐
│  Flask process (app.py, waitress ×64 threads)                    │
│                                                                  │
│   call sites ──alert()──► bounded queue ──► dispatch worker      │
│   (OpenAI wrapper,          (never blocks,   (dedup, throttle,   │
│    scheduler callbacks,      never raises)    storm brake,       │
│    excepthook, Flask         ▲                reminders)         │
│    errorhandler, RunPod,     │                    │              │
│    WS supervisor)            │                    ▼              │
│                              │              GmailChannel ──────────► alerts@gregmarsh.co.uk
│   GET /api/health ◄──────────┘                                   │
│      │        health-gated heartbeat ping ─────────────────────────► healthchecks.io
└──────┼───────────────────────────────────────────────────────────┘   (emails on
       │ probed via the PUBLIC kiosk URL                                silence)
       │ http://95.141.21.170:8000/api/health
┌──────▼──────────── media server (192.168.1.92, always-on) ───────┐
│  Deadman: systemd timer, every 5 min                             │
│   • curl /api/health via the public kiosk path                   │
│   • local df check (disk at 93% today!)                          │
│   • reads health JSON: tick ages, ws_alive, email-channel status │
│   • emails DIRECTLY via Gmail REST (stdlib urllib, no WSL dep)   │
└──────────────────────────────────────────────────────────────────┘
```

- **Siren** (in-process): a small alert bus — one never-raising `alert()` function, a bounded queue, one self-healing worker thread, dedup/throttle policy, Gmail channel. Catches everything the process can see.
- **Deadman** (external): a slim watchdog on the always-on, systemd-supervised kiosk box, probing Flask via **the exact public URL the kiosk displays use** — so one curl catches process death, WSL-IP/portproxy drift, and public-IP change (all observed failure classes). It emails directly, with zero WSL dependency.
- **healthchecks.io** (third-party, free tier): Siren pings a check URL every 5 min, **gated on internal health** (scheduler tick freshness + WS thread liveness). Pings stop ⇒ healthchecks' own servers email. This has fail-safe polarity (silence → alarm), survives a revoked Gmail token, and is the only leg covering whole-home power/ISP outage — the one class Deadman shares fate with.

Mutual monitoring: Deadman reads Siren's email-channel failure counter from `/api/health` (so "screen-machine can't send its own alerts" gets emailed externally); `/api/health` exposes `last_watchdog_probe` so a dead Deadman is visible in-process.

### Fail-safe invariants (non-negotiable)

1. `alert()` **never raises and never blocks** — whole body in `try/except`, caller-side work is a dataclass + `put_nowait` on a bounded queue (`maxsize=1000`; overflow counted, surfaced later). Safe from waitress threads, per-destination asyncio scheduler loops, generation worker threads, and the WS server thread.
2. **Bounded everything**: queue capped, dedup-state dict capped (evict oldest beyond ~500 keys), global email storm brake (§6).
3. **No alert-about-alert loops**: the alerts package never imports `utils.logger`; it logs via its own stdlib logger sharing the existing `RotatingFileHandler`. Channel failures are never emailed through the failing channel — they increment a counter read externally.
4. **Graceful when unconfigured**: missing creds ⇒ one startup log line, `alert()` becomes a no-op past accounting. Dev checkouts and CI need zero setup (`ALERTING_ENABLED=0` in test conftest).
5. **Worker self-healing**: every worker duty individually wrapped in `try/except`; every `alert()` call runs `_ensure_worker_alive()` and restarts a dead worker. (This codebase's documented pathology is unobserved daemon death — the never-read scheduler futures, the unsupervised WS thread. The alerting system must not add another instance of it.)
6. **Secrets never logged, never emailed.** Bodies carry exception text and context dicts only.

---

## 4. Module layout and public API

```
utils/alerts/
    __init__.py        # public API: alert(), init_alerting()
    taxonomy.py        # category registry, severity constants, default policies
    dispatcher.py      # bounded queue, self-healing worker, throttle, reminders
    state.py           # persisted per-key state -> logs/alerts_state.json (atomic tmp+rename)
    health.py          # heartbeat ticks, build_health() for /api/health, hc.io ping
    channels/
        base.py        # AlertChannel protocol: send(subject, body) -> bool, never raises
        email_gmail.py # Gmail REST via requests (token refresh + messages.send)
    selftest.py        # python -m utils.alerts.selftest -> sends a real test email

media-server/remote-scripts/
    deadman.sh         # probe + df + health-JSON checks, throttled mailing, recovery notices
    deadman-mail.py    # standalone Gmail sender, stdlib urllib only (nothing to install)
    deadman.timer/.service   # OnCalendar=*:0/5, Persistent=true
media-server/local-scripts/
    install-deadman.sh # scp + enable, reusing the sshpass pattern from routes/admin_api.py:23-56
```

```python
# utils/alerts/__init__.py
def alert(
    category: str,                    # taxonomy key, e.g. "openai.quota"
    summary: str,                     # one line -> subject fragment
    *,
    severity: str | None = None,      # "info"|"warning"|"error"|"critical"; None -> category default
    detail: str = "",
    exc: BaseException | None = None, # traceback auto-captured
    context: dict | None = None,      # {"destination": "north-screen", "job_id": ...}
    dedup_key: str | None = None,     # default: category  (quota is ONE incident, not 1800/hr)
) -> None:                            # ALWAYS returns None, NEVER raises
```

That one function is the entire integration surface for call sites.

---

## 5. Alert taxonomy — categories mapped to real failure surfaces

Every category below maps to a failure surface found in the investigation, not invented. Severity drives delivery policy (§6).

| Category | Default severity | Raised where | Today's behaviour |
|---|---|---|---|
| `openai.quota` (`insufficient_quota` 429) | critical | wrapper in `routes/openai.py` (§7.1) | swallowed into INFO logs + silent fallback |
| `openai.auth` (key rejected / unset) | critical | same wrapper + `routes/openai.py:60-62` | RuntimeError swallowed by caller catches |
| `openai.error` (other API errors) | error | same wrapper | same |
| `runpod.job_failed` / `runpod.timeout` | error | `routes/generate.py:638-657`, `:702` (timeout message notes: job stuck IN_QUEUE usually = **RunPod credit out**) | log line + 5-second TV overlay |
| `generation.job_failed` | error | `routes/generate_handler.py:667-669` (the swallowed catch — invisible to `threading.excepthook` because it *is* caught) | `results[i]=None`, silently filtered |
| `scheduler.loop_died` | critical | done-callbacks on **both** futures: `routes/scheduler.py:781` **and** `:962` (never read today) | loop dies unobserved |
| `scheduler.wedged` | critical | health sentinel: per-destination tick timestamps stale >10 min while thread alive | undetectable today |
| `scheduler.instruction_error` | error | existing catches at `routes/scheduler.py:565-572`, `:1116-1120`, `:1256-1260` (dedup key includes destination + instruction type) | in-memory log only |
| `ws.server_died` | critical | supervised wrapper replacing the unsupervised daemon thread at `app.py:179`; alert + restart | Flask silently continues without overlays, RunPod progress relay, or lux ingestion |
| `app.thread_crash` | error | global `threading.excepthook` — covers every fire-and-forget spawn site (`generate_handler.py:775/782/847/914`, `alexa.py:824-829`, `alexa.py:853`, and any future ones) | stderr traceback, reaches no log |
| `app.unhandled` | error | new Flask `errorhandler(Exception)` (none exists today); pass through HTTP <500 untouched | raw 500 to browser only |
| `device.samsung_token` | warning | guard the **unguarded** re-pair retry at `routes/samsung_utils.py:53-71` (needs physical intervention when it fails) | exception propagates or vanishes |
| `assemblyai.stream` | warning | `routes/audio_utils.py:257-260` (gives up after 3 reconnects) | silent give-up |
| `media.download_failed` | warning | `routes/generate_utils.py:76-79` (artefact lost) | swallowed None |
| `alerting.storm` / `alerting.dropped` | error | dispatcher self-report (cap breached / queue overflow) | n/a |
| `watchdog.host_down` / `.heartbeat_stale` / `.disk` / `.service` / `.email_channel` | — | emailed by **Deadman** directly, not by Siren | n/a |

Deliberately **not** alerted (noise, or physically visible on the wall): websocket handshake noise (1,000+ `opening handshake failed` INFO lines in the current log), lightsensor staleness, the media server's Chrome "no recent activity" heuristic (~90% of samples — too noisy until tuned), ngrok tunnel death (ngrok isn't even running in the current topology). Each is a one-line `alert()` call later if wanted.

---

## 6. Delivery policy — why a failing loop sends 2 emails, not 500

The scheduler ticks every 2 s (`SCHEDULER_TICK_INTERVAL`, `config.py:29`); an out-of-credit `reason` instruction would fire `openai.quota` up to 1,800×/hour. Policy, applied in the worker (never on the caller's thread):

| Severity | Delivery | Per-key cooldown | Ongoing reminder |
|---|---|---|---|
| `critical` | email immediately | 10 min | re-email every 6 h while still recurring ("STILL FAILING: … 4,102 occurrences since 09:14") |
| `error` | email immediately | 6 h | counts roll into next email |
| `warning` | batched: one rollup email per hour max | — | — |
| `info` | never emailed; state/log only | — | — |

- **Per-key state** (`{first_seen, last_seen, count, count_since_sent, last_sent}`) persisted to `logs/alerts_state.json` via **atomic tmp+rename**, written only by the worker. A restart mid-incident does not replay history (no restart-spam), but deliberately allows one fresh notification — a restart is itself signal.
- **Suppressed occurrences are counted, never lost**: the next allowed email reports "occurred 214× since last email".
- **Global storm brake**: hard cap 12 emails/hour across all keys. On breach, send exactly one `alerting.storm` email ("suppressing for 60 min; active keys: …") then go quiet. This bounds worst-case Gmail volume *by construction*, regardless of bugs elsewhere.
- **Per-send retry**: a failed channel send keeps the key's counters intact and retries on the next worker cycle (up to 3 attempts) — a transient Gmail blip doesn't lose a one-shot critical like `scheduler.loop_died`. Persistent channel failure increments `channel_consecutive_failures`, visible in `/api/health` and checked by Deadman.
- **Mute table** (small, optional nicety): `routes/data/alerts.json` with per-category mutes/cooldown overrides, hot-reloaded via the existing `_load_json_once` mtime pattern (`routes/utils.py:126-155`) — silence a noisy category without restarting Flask.

Note: Siren's dedup is independent of the 2-second duplicate throttle in `utils/logger.py:78-90` — primary alerts are raised at call sites, not sniffed from logs.

### Email format

```
Subject: [screen-machine][CRITICAL] openai.quota — OpenAI account out of credit

Host: WSL  Time: 2026-07-28 14:03:12
Category: openai.quota   Severity: critical
First seen: 13:58:41   Occurrences since last email: 47
Context: {"destination": "north-screen", "instruction": "reason", "model": "gpt-4o"}

Error code: 429 - {'error': {'type': 'insufficient_quota', ...}}
<traceback>

Health snapshot: scheduler ticks {north: 1.9s, south: 2.1s}, ws_alive: true, queue: 0
Recent log tail (last 30 lines of logs/screen_machine.log): ...
```

One email is a mini incident report (the log tail is the poor man's version of auto-klevio's capture-buffer transcript; read best-effort in the worker, never on the caller's thread).

---

## 7. Integration points (concrete)

### 7.1 OpenAI — the canonical case, 100% coverage in one file

Every production OpenAI call goes through `openai_prompt()` (`routes/openai.py:51`) with three `chat.completions.create` sites (`:159`, `:182`, `:238`). Route all three through one wrapper:

```python
# routes/openai.py
import openai as _openai_mod
from utils.alerts import alert

def _create(client, **kwargs):
    try:
        return client.chat.completions.create(**kwargs)
    except _openai_mod.RateLimitError as e:
        if "insufficient_quota" in str(e):
            alert("openai.quota", "OpenAI account out of credit", severity="critical", exc=e)
        else:
            alert("openai.error", "OpenAI rate-limited (429, post-retry)", severity="warning", exc=e)
        raise                                  # caller behaviour UNCHANGED
    except _openai_mod.AuthenticationError as e:
        alert("openai.auth", "OpenAI API key rejected", severity="critical", exc=e)
        raise
    except _openai_mod.OpenAIError as e:
        alert("openai.error", f"OpenAI API failure: {type(e).__name__}", exc=e)
        raise
```

Why here and not in the four callers (`generate_handler.py:518/590`, `alexa.py:538`, `scheduler_handlers.py:1410`): the callers all *deliberately* swallow/fallback — correct for keeping screens alive, wrong place to detect. Wrapping inside `openai_prompt` alerts **before** the swallowing, and the existing tests that mock `routes.openai.openai_prompt` (`test.py:76`, `tests/unit/test_instruction_handlers.py`) are untouched. The SDK already retries 429s twice internally (openai 1.77.0, `DEFAULT_MAX_RETRIES=2`), so what reaches the wrapper is real. Also add an `openai.auth` alert beside the missing-key RuntimeError at `routes/openai.py:60-62`.

### 7.2 Structural safety nets

**Scheduler loop death** — the futures from `asyncio.run_coroutine_threadsafe` at `routes/scheduler.py:781` **and `:962`** are polled for `.done()`/`.cancelled()` and cancelled on stop, but their **exceptions are never inspected** — a loop that dies with an error is unobserved. Same 6 lines at both sites (note the `cancelled()` guard — calling `.exception()` on a cancelled future raises):

```python
def _observe(fut, dest=publish_destination):
    if not fut.cancelled() and fut.exception() is not None:
        alert("scheduler.loop_died", f"Scheduler loop for '{dest}' died",
              severity="critical", exc=fut.exception(),
              dedup_key=f"sched-died:{dest}", context={"destination": dest})
future.add_done_callback(_observe)
```

Plus an explicit alert beside the fatal re-raise at `routes/scheduler.py:1307-1312`.

**Naked thread crashes** — one hook in `app.py` covers every fire-and-forget spawn site without touching any of them:

```python
_orig_hook = threading.excepthook
def _alerting_excepthook(args):
    alert("app.thread_crash",
          f"Uncaught {args.exc_type.__name__} in thread '{getattr(args.thread, 'name', '?')}'",
          exc=args.exc_value, dedup_key=f"thread-crash:{args.exc_type.__name__}")
    _orig_hook(args)
threading.excepthook = _alerting_excepthook
```

**Swallowed generation failures** — `routes/generate_handler.py:667-669` catches the exception itself, so the excepthook never fires there; add an explicit `alert("generation.job_failed", ...)` inside that catch.

**Flask** — register `app.register_error_handler(Exception, ...)` in `app.py` (none exists anywhere today): alert `app.unhandled` for ≥500s, pass through `HTTPException` <500 untouched, return the same JSON-500 shape as `generate_api.py`.

**WS server supervision** — replace the unsupervised `Thread(target=start_ws_server, daemon=True)` at `app.py:179` with a wrapper: on `ws_main()` exiting or raising → `alert("ws.server_died", severity="critical")`, sleep 5 s, restart. Fixes the current mode where the loop dies and Flask silently loses overlays, RunPod progress relay, and lux ingestion.

**RunPod** — beside the existing overlay pushes at `routes/generate.py:638-657` (FAILED/CANCELLED) and the 1200-s timeout at `:702`: `alert("runpod.job_failed"/"runpod.timeout", ..., context={"endpoint": runpod_id, "job_id": ...})`. The timeout message says "job never left IN_QUEUE — check RunPod credit", because that is how credit-out manifests on RunPod.

**Point instrumentation (warnings, later phase)** — Samsung re-pair guard (`samsung_utils.py:53-71`), AssemblyAI give-up (`audio_utils.py:257-260`), artefact-download failures (`generate_utils.py:76-79`).

### 7.3 Heartbeats, `/api/health`, and the health-gated dead-man ping

- `run_scheduler_loop` (`routes/scheduler.py:1002` `while True`) calls `health.tick(dest)` once per iteration — one dict write.
- New `GET /api/health` in `app.py` returns: uptime, per-destination `{alive, last_tick_age_s}`, `ws_server_alive`, `alerts: {queued, dropped, emails_24h, channel_consecutive_failures, email_channel}`, WSL disk %, `output/` size, `last_watchdog_probe` (stamped when the request carries `?probe=deadman`).
- A 12-line loop in the dispatcher worker GETs `HEALTHCHECK_PING_URL` every 5 min — **only if** all scheduler tick ages are fresh (or legitimately stopped) and the WS thread is alive. A wedged scheduler or dead WS daemon stops the ping ⇒ healthchecks.io emails. Failures to ping are logged and ignored (never alerted — wrong direction).

### 7.4 Deadman — the media-server watchdog

Deployed by `install-deadman.sh` (same sshpass/scp pattern as `routes/admin_api.py:23-56`); systemd timer `OnCalendar=*:0/5`, `Persistent=true` (the box already runs systemd with `Restart=always` for `kiosk.service`). Each run (state in `/var/lib/deadman/state.json`, creds in `/etc/deadman/deadman.env` chmod 600):

1. `curl -fsS --max-time 10 "http://95.141.21.170:8000/api/health?probe=deadman"` — deliberately the **public URL the kiosk Chrome instances load** (`media-server/remote-scripts/kiosk-loop.sh:30-31`), so one probe also catches netsh-portproxy drift after a WSL IP change and public-IP changes — both have happened before.
2. On 2 consecutive misses (≥10 min, avoids blips): email `watchdog.host_down` directly via Gmail REST (stdlib `urllib`, nothing to install on the box). Repeat every 6 h; send a **recovery email** on first success after failure.
3. On success, parse the JSON (python3 one-liner): tick age >600 s → `watchdog.heartbeat_stale`; `ws_server_alive:false` → escalate; `channel_consecutive_failures ≥ 3` → `watchdog.email_channel` ("screen-machine cannot send its own alerts").
4. Local checks: `df --output=pcent /` ≥90% → `watchdog.disk` (**fires immediately — the box is at 93% today**); `systemctl is-active kiosk.service light-relay.service` → `watchdog.service`.

### Failure → detector matrix

| Failure | Siren | healthchecks.io | Deadman |
|---|---|---|---|
| OpenAI quota/auth, RunPod, API errors | ✅ §7.1–7.2 | — | — |
| Scheduler loop dies (exception) | ✅ done-callbacks | ✅ (gated ping stops if all loops dead) | health JSON |
| Scheduler wedged (alive, not ticking) | sentinel | ✅ gated ping stops | ✅ health JSON |
| Flask/WSL/Windows dead, reboot into stale topology | ✗ impossible | ✅ | ✅ probe fails |
| WSL-IP/portproxy drift, public-IP change (kiosks blank) | ✗ | ✗ (outbound ping still works!) | ✅ public-path probe |
| Media-server disk full / services dead | ✗ invisible | ✗ | ✅ local checks |
| Gmail refresh token revoked (silences Siren + Deadman emails) | counter in /api/health | ✅ independent of Gmail | flags it, can't email it |
| Whole-home power/ISP outage | ✗ | ✅ (external service) | ✗ same house |

The one residual single point: a revoked Google token silences both email legs. healthchecks.io still alarms on liveness, and an optional **ntfy.sh channel** (credential-free push, ~15 lines) is the designated Phase-5 diversity fix.

---

## 8. Configuration

`config.py` — new section, house style (flat UPPERCASE constants):

```python
# --- Alerting (Siren) ---
ALERTING_ENABLED = True                # tests/dev override via env ALERTING_ENABLED=0
ALERT_QUEUE_MAX = 1000
ALERT_STATE_PATH = os.path.join(str(ROOT_DIR), "logs", "alerts_state.json")
ALERT_THROTTLE = {
    "critical": {"min_interval_s": 600,  "reminder_s": 6 * 3600},
    "error":    {"min_interval_s": 6 * 3600, "reminder_s": None},
    "warning":  {"batch_flush_s": 3600},
}
ALERT_GLOBAL_MAX_PER_HOUR = 12         # storm brake; one alerting.storm email on breach
ALERT_LOG_TAIL_LINES = 30
HEARTBEAT_STALE_S = 600                # scheduler considered wedged after 10 min
```

`.env` — secrets only, per existing convention (`load_dotenv` at `app.py:45`, `os.getenv` at point of use). Values hand-copied once from `~/dev/auto-klevio/.env`:

```
ALERT_EMAIL_TO=alerts@gregmarsh.co.uk
GOOGLE_CLIENT_ID=<copy from auto-klevio/.env>
GOOGLE_CLIENT_SECRET=<copy>
GOOGLE_REFRESH_TOKEN=<copy>
HEALTHCHECK_PING_URL=https://hc-ping.com/<uuid>     # from free healthchecks.io account
```

Missing creds ⇒ Gmail channel unconfigured; the bus still runs and accounts, app starts normally.

---

## 9. Testing and drills

- `python -m utils.alerts.selftest` — end-to-end test email; prints token-refresh and send status (no secret values). First thing after copying creds.
- Unit tests: throttle/storm/reminder math with a `FakeChannel` capturing sends; a poisoned-state-file test and a raising-channel test proving the worker survives (invariant 5); `alert()` fed garbage must never raise. Existing suites unaffected (`ALERTING_ENABLED=0` in conftest; they already mock `routes.openai.openai_prompt`; `threading.excepthook` restored in a fixture).
- Documented drills: set a bogus `OPENAI_API_KEY` → expect one `openai.auth` critical email and the scheduler still applying fallbacks; `pkill -f app.py` → expect healthchecks.io email within ~10 min and Deadman `host_down` within 15; restart → recovery email.

---

## 10. Phased implementation plan

**Phase 0 — prove the pipe (~1 h).**
Copy creds into `.env`; implement `channels/email_gmail.py` + `selftest`; a test email lands at alerts@gregmarsh.co.uk. De-risks the only external dependency first.

**Phase 1 — canonical case + liveness (~half day).**
Core package (`alert()`, dispatcher, state, taxonomy, config). OpenAI wrapper (§7.1). Scheduler done-callbacks at **both** `scheduler.py:781` and `:962`. healthchecks.io account + health-gated ping.
*Ship point: OpenAI-out-of-credit emails within ~1 minute, exactly once, with 6-h reminders — and a dead Flask emails within ~10 minutes. Both recorded incident classes closed.*

**Phase 2 — broad in-process coverage (~half day).**
`threading.excepthook`; Flask errorhandler; `generation.job_failed` at `generate_handler.py:667-669`; RunPod alerts; WS-server supervision + restart; `/api/health` + scheduler tick heartbeats + wedge sentinel.

**Phase 3 — Deadman (~half day to 1 day).**
`deadman.sh` / `deadman-mail.py` / systemd timer + installer; public-path probe, health-JSON checks, disk + service checks, throttle/recovery state.
*Pays off immediately: fires on the kiosk box's 93% disk on first run.*

**Phase 4 — warnings + polish (optional, ~half day).**
Samsung/AssemblyAI/download warning instrumentation with hourly rollup; mute table in `routes/data/alerts.json`; startup notice comparing last-heartbeat age ("previous instance last alive at …").

**Phase 5 — channel diversity (optional).**
ntfy.sh push channel for criticals in both legs (kills the shared-Gmail-token single point); on-TV `OverlayChannel` via the existing `send_overlay` (`routes/display.py:185`).

Total core (Phases 0–3): **~2 days**, no new Python dependencies (pin `requests`), one free SaaS account, one systemd timer on a box that already runs systemd.

---

## 11. Deliberately out of scope (but surfaced by this investigation)

These are *ops hygiene* problems alerting will now make visible instead of silently absorbing — each deserves its own small task:

1. **Stale boot topology**: `/home/gjbm2/wsl-autostart.sh` restores the old dev-mode setup (`npm run dev`, `npx serve`) and the `\LocalAutomation_BootStart_screen-machine` logon task last exited 1 (failed). A Windows reboot currently restores the wrong world — Deadman will email about it within 15 minutes, but fixing the script is separate work.
2. **netsh portproxy hard-codes the dynamic WSL IP** (172.28.255.46) — breaks silently on WSL restart. (Detected by Deadman's public-path probe; a boot-time re-provisioning script would fix it properly.)
3. **No process supervision** (GNU screen sessions, no systemd/pm2). Migrating Flask to a systemd unit inside WSL would eliminate a whole failure class; alerting makes its absence visible rather than fixing it.
4. **Log-sniffing bridge**: a `logging.Handler` on the `screen_machine` logger was considered as a zero-touch catch-all and rejected for the core: the canonical quota message logs at INFO, the log stream carries chronic noise (1,000+ websocket handshake lines — which *do* propagate into the `screen_machine` handlers), and call-site alerts carry context logs lack. Revisit only if a real failure ever bypasses the instrumented sites.
5. **Media-server `maintenance.log`** is git-tracked and unbounded (9.1 MB); `output/` is 3.6 GB with no pruning. Worth a cleanup task.

---

## Appendix: design process

Three independent designs (minimal-reuse "FLARE", generalised-bus "Klaxon", ops-first "Siren + Deadman") were drafted against the same codebase findings and adversarially judged through reliability, simplicity, and coverage lenses, with claims verified against the code. Siren + Deadman won reliability (9/10) and coverage (9/10); FLARE won simplicity (9/10). This proposal is Siren + Deadman with FLARE's grafts (both scheduler hook sites, healthchecks.io promoted to Phase 1, minimal surface discipline) and Klaxon's grafts (per-send retry, atomic state writes, pure-function policy for testability, hot-reloadable mute table, public-kiosk-URL probe target).
