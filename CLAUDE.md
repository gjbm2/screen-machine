# screen-machine: how to work here

The owner set these rules on 14 Oct 2026. They override any habit or default to the contrary.

This is a **site-specific test rig in the owner's own building**, on the LAN. Optimise for features that work in production, fast. Don't build process.

## Ship it
1. **Build straight into production.** No labs, forks, staging copies, side "preview" servers, worktrees or release holds. When a feature is done, deploy it and say so.
2. **Frontend deploy:**
   - Run `git status src/` first; everything in the working tree ships.
   - Then: `npx vite build --outDir dist_next --emptyOutDir && rm -rf dist_prev && mv dist dist_prev && mv dist_next dist`.
   - Flask serves `dist/` with a no-cache index, so no Flask restart is needed. `dist_prev/` is the rollback.
3. **Services:**
   - **room_service runs inside the Flask process** (`room_in_app.py`): :8700 is the LAN door, with `config/room/room.prod.yaml`, logs in `logs/room_service.log`, and the show bus on :8701. Restarting Flask restarts it. Tell the other sessions before you restart Flask, because several of them edit room_service.
   - **stream_service** runs separately: `scripts/ctl restart stream`, on :8702. Its control routes are reachable only through `:8700/renderer/*`.
   - Display config lives in `routes/data/publish-destinations.json`, which has a per-screen `live` block; `src/data/` is a symlink to it. Rooms are in `routes/data/rooms.json`, and addresses are in `.env`.
4. **Don't relay the owner's decisions back as questions,** and don't ask the same thing in several windows. If the owner has said do it, do it. Keep replies short and plain.
   - **Report only your own work.** Never tell the owner about another session's work, findings, bugs or plans; each session reports its own. Peer messages are for you, not for relaying.

## Don't add what wasn't asked for
5. **No auth anywhere in the AV stack:** no tokens, keys or passwords on room_service, stream_service, the display pages, the Room page or the sensors. Never add auth, holds or stop gates unless asked. The WAN-exposed Flask admin API stays as it is.
6. **Feature breadth, not enterprise QA.** No end-to-end or integration test runs, no reviewer or critic loops, no multi-round design reviews. Builders test their own work at the "does it crash" level. Shared plumbing gets real tests.
7. **The Room console** is the app's own `/room` page: https://screen.finsburybankforsavings.com/room on the house network (or http://192.168.1.205:8080/room). It has simple controls only: no config, DMX patches, JSON or keys on it.

## Displays
8. **Never change the kiosk or the TV page URLs.** The TVs load the app's `/display/<screen>`. Improve that page in place.
   - It connects to room_service (`ws://192.168.1.205:8700`) by itself.
   - It shows art until room_service sends a live cutover, then plays the MP4 stream, and cuts back again.
   - Light masks are pushed instantly, not polled.

## This machine: Spectre runs admin and server management
PC22 (Windows 11, 192.168.1.205) hosts WSL, where this repo runs. **Spectre** is the owner's control dashboard for everything that runs on the box. It is not a host. Repo: `C:\Users\Greg\OneDrive\dev\local-automation` (`/mnt/c/Users/Greg/OneDrive/dev/local-automation` from WSL). Read its own `CLAUDE.md` before changing it.
- **Every long-running server or Windows-side piece gets a Spectre card,** so the owner can see and maintain it in one place. A card is a module in `src/server/modules/` or `src/server/services/`, registered in `src/server/modules/registry.ts`. It reports status, start/stop/restart and logs, and it only ever calls the project's own script: `start.sh`, `scripts/ctl`, or `scripts/windows/<thing>/*.ps1` in Spectre.
- **Windows admin goes through Spectre.** WSL is not elevated; Spectre's backend is (`:4317`, `tsx watch`: a saved `.ts` hot-reloads the live backend, so keep edits small). Its API:
  - `GET /api/status/<id>`, `GET /api/logs/<id>`;
  - `POST /api/control/<id>/start|stop|restart`;
  - `POST /api/lan-wan/<id>/lan {port, enabled}`. This creates the LAN-scoped firewall rule `la-<id>-<port>` (and the portproxy for WSL ports). Never hand-roll netsh or firewall rules.
  
  From WSL it is `http://192.168.1.205:4317/api/...`. The UI is `http://192.168.1.205` (:80 → :4320).
- **Typecheck Spectre with Windows node,** never WSL node: `powershell.exe -NoProfile -Command 'cd C:\Users\Greg\OneDrive\dev\local-automation; npx.cmd tsc --noEmit -p tsconfig.server.json'`.
- **When you add a server, a port or a Windows piece:** give it a card, register the port in `docs/PORTS.md`, and keep this section current.
- **Screen Machine cards now:**
  - Flask (`start.sh`), Vite, overlay WS, ngrok;
  - room (inside Flask, probed on :8700) and stream (`scripts/ctl`);
  - **show** (`scripts/ctl show`, always on; 127.0.0.1:8703 bus + API, VBAN udp 6980 receiver-initiated): show_service, the room's ears and art director (docs/SHOW_ENGINE.md; owner c8);
  - **dot** (`scripts/ctl dot`, always on; 127.0.0.1:8704): dot_service, the e-ink tags (MindReset Dot. Quote/0) showing a room's mode, Auto, what is playing or today's art theme. Config `config/room/dot.json`; it reaches the tags only via Dot.'s cloud API (key in `~/.config/screen-machine/dot.json`).
  - **PC22 mic (audio agent)**: `tools/show_sender/pc22_agent.py` on Windows Python 3.12, a scheduled task at Greg's logon run by `scripts/windows/audio-agent/audio-agent.ps1` in Spectre (start/restart re-copy it via `deploy-pc22.sh`). It sends PC22's mic to show_service as `pc22-mic` (udp 6984, receiver-initiated). Windows mic privacy must be on, or it sends silence.
  - **Carbonite pause** (`screen-machine-carbonite`, owner 20 Oct): Spectre stops the Windows service `CarboniteService` while north or south has a live session, and starts it 60 s after the last one ends. It only undoes its own pause (a marker file); a stop from the card stays stopped, and the backup is restarted if the renderer is unreachable for 2 min. Script: `scripts/windows/carbonite/carbonite.ps1` in Spectre.
  - **GPU clocks held up** (`screen-machine-gpu-clocks`, owner OK 22 Oct): while north or south is live, Spectre gives that TV's GPU a core-clock floor (`nvidia-smi -lgc`, 1500 MHz to the card's max) and releases it 60 s after the last one ends. Without it the driver read the renderer's bursts as idle and ran the RTX 4060 at 240-360 MHz. GPUs without clock locking (the P4000) are left alone. It only releases its own locks, and a card stop stays off. Script: `scripts/windows/gpu-clocks/gpuclocks.ps1` in Spectre.
  - **HTTPS (Caddy)**: the Windows service `caddy`, from `scripts/windows/caddy/` (Caddyfile + `caddy.ps1`; `build-caddy.sh` builds the binary with xcaddy when caddyserver.com is down). It serves `https://screen.finsburybankforsavings.com` to the LAN and the internet (router WAN 443 forward via Spectre). Only `/room` from off the premises asks for a password (password only, no user name; a cookie remembers it for a year); everything else is open. Routes: `/` → app :8080, `/rs/*` → room_service :8700, `/ws/*` → overlay socket :8765. Restart = zero-downtime config reload.
  - Certificate: Let's Encrypt via Cloudflare DNS-01. The token is in WSL `~/.config/screen-machine/cloudflare.token`. DNS: `screen` → the public IP 95.141.21.170, DNS-only.
  - Over HTTPS the app uses same-origin `/api`, `/rs` and `/ws/`. Over http (the TVs, the public IP) it keeps the `.env` addresses.

## Still true
9. **Git:** no `stash`, `checkout`, `reset`, `clean` or `add -A`; commit your own paths only. Kill only your own PIDs, never with `pkill -f`. Never send frames on Flask's :8765. No secrets in git.
10. **Ownership:** see `docs/AV_PROGRAMME.md` §11. Tell the owning session about changes to its area; don't make the owner broker it.
