# Ops control: GPU renderer (stream_service) and room management

All servers on PC22's WSL use one pattern: **a project script starts each in a GNU screen session, and a Spectre card calls that script.**

- **Room management** runs **inside the Flask process** (screen session `flask`, `./start.sh start flask`) since 14 Oct.
  Its LAN-only door is :8700 (API/WS, room UI, `/renderer/*` pass-through); show bus on 127.0.0.1:8701; Art-Net in on udp/6454.
  Spectre card *Screen Machine — Room (in Flask)* probes `:8700/healthz` and `/api/v1/dmx` (Art-Net in, show bus, Hue);
  Start = start Flask; Stop is refused (stop Flask instead). Logs: `logs/room_service.log`. Rollback: `ROOM_IN_APP=0` in `.env` + Flask restart.
- **GPU renderer** (stream_service, :8702) is an on-demand worker in screen session `sm-stream`, run by `scripts/ctl`.

| Layer | What | Where |
|---|---|---|
| Spectre | *Screen Machine — Live renderer* card: status (`/healthz` + session), Start/Stop/Restart, logs, LAN toggle | `local-automation` `services/screen-machine-stream.ts` (standard `wslService`) |
| Project script | `scripts/ctl start\|stop\|restart\|status\|health\|logs stream` | this repo |
| Settings | `deploy/sm-stream.env` (owner screen-machine-04) | this repo |
| Network | portproxy `0.0.0.0:8700/8702 -> WSL` + firewall rules `la-screen-machine-{room,stream}-*` scoped to 192.168.1-4.0/24 (never WAN) | Spectre LAN toggles |
| Boot | Flask (+ room) via the Screen Machine group / `scripts/wsl-boot.sh`; renderer optional via the *Live renderer* boot group (`scripts/ctl start all`) | Spectre boot-start |

Ports and owners: `docs/PORTS.md`.

## Commands

```
scripts/ctl status              # stream: running/stopped (+ HOLD if any); exit 0 ok, 3 stopped, 1 unhealthy
scripts/ctl start stream        # refuses (exit 2) if deploy/sm-stream.env has STREAM_HOLD, or :8702 is taken
scripts/ctl stop stream         # SIGTERM to the pid, then its process group after 15 s; never pkill
scripts/ctl logs stream 200     # ~/.local/state/screen-machine/logs/stream_service.log
screen -r sm-stream             # attach (Ctrl-A D to detach)
```

## Notes

- Port comes from `STREAM_PORT`; never put `--port` in `STREAM_ARGS`. Other `KEY=VALUE` lines become env vars.
- A `STREAM_HOLD="owner: reason"` line blocks start/restart until its owner deletes it (`--force` overrides).
- Pre-logon boot: Windows task `\Start WSL Services` runs `~/wsl-autostart.sh`, a shim to `scripts/wsl-boot.sh`
  (prod Flask + ngrok via start.sh, then `scripts/ctl start all`; no pkill, no Vite). Previous script: `~/wsl-autostart.sh.bak-20261014`.
- Art-Net: Maestro sends to 192.168.1.205:6454/udp; Windows needs a UDP relay into WSL (portproxy is TCP-only) — pending owner OK.
- Smoke test: `tests/ops/test_ctl.sh` (fake service on 18702; never touches the real session).
