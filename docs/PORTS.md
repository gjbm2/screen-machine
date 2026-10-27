# Port register

> **Architecture (owner, 14 Oct):** one front door — the Screen Machine app (Flask, WSL:5000; public via WAN 8000 → PC22:8080 → WSL:5000 and ngrok). Room management runs inside Flask with its own LAN-only door :8700. The GPU renderer (stream_service, :8702) is a separate on-demand worker. No passwords on the AV services.

The authoritative list of every port screen-machine uses or must avoid, on the workstation (Windows PC22 and its WSL2 Ubuntu), the kiosk mini-PC, the router and the LAN devices.

- **Owner:** screen-machine-ca. Spectre mirrors this register in its service modules.
- **Last updated:** 2026-10-14. The AV block was agreed with screen-machine-2f, -04 and -10 that day.

## Rules

1. **Allocate here first.** Before any new listener ships (a service, a dev server or a test harness), ask screen-machine-ca to add its port here. Only then does it go into code, an env file or a Spectre module.
2. **Spectre owns Windows exposure.** Windows portproxy entries and firewall rules are created, removed and re-pointed only by Spectre's lanWan layer (owner screen-machine-ca), never by hand with `netsh` or `New-NetFirewallRule`.
   - A new LAN rule is named `la-<serviceId>-<port>`, on `Profile Domain,Private`, never Public.
   - **Default scope:** `RemoteAddress 192.168.1.0/24,192.168.2.0/24,192.168.3.0/24,192.168.4.0/24` (the router's four internal subnets, guest Wi-Fi included).
   - **Per-port overrides** are listed in the "LAN RemoteAddress" column of the table that owns the port (§1). Spectre's module for that port (`lanRemoteAddresses`) must carry exactly that value.
   - Each port gets a portproxy entry `0.0.0.0:P -> <WSL eth0 IP>:P` (NAT mode, one hop). Spectre's reconcile re-points it when the WSL IP changes.
3. **No AV port is ever forwarded from the WAN.** The router forwards only the ports in §4.
4. **Kiosk-critical ports cannot be switched off casually.**
   - These are 8080 (WAN 8000), 5000 and 8765.
   - The TVs load `http://95.141.21.170:8000/display/*` via WAN loop-back to Windows 8080 and then WSL 5000. The light relay is `ws://95.141.21.170:8765`. The deadman probes `:8000/api/health`.
   - Spectre marks these ports `critical`. It refuses to switch their LAN or WAN exposure off without `force`.
5. **Dev instances use WSL-only ports** (§2.3). Neither Spectre nor `scripts/ctl` manages them, and they are never exposed.

**Scope terms used below:**
- **WSL-only:** reachable inside WSL, and from Windows `localhost` through WSL's localhost forwarding. Not reachable from the LAN.
- **LAN:** a Windows portproxy entry plus a LAN-scoped firewall rule.
- **WAN:** a router port forward to PC22. WAN implies LAN.

**Hosts:**
- **PC22:** the Windows 11 workstation. LAN 192.168.1.205, WAN 95.141.21.170.
- **WSL:** Ubuntu on WSL2 in NAT mode. Its `eth0` address changes when WSL restarts; it was 172.28.255.46 on 14 Oct.
- **Kiosk:** the mini-PC `screen-machine-drawingroom`. Ethernet over powerline at 192.168.1.10 (primary), Wi-Fi at 192.168.1.92.

## 1. AV block (WSL, production)

| Port | Service | Supervisor | Bind | Scope | Windows exposure | LAN RemoteAddress | Owner |
|---|---|---|---|---|---|---|---|
| **8700** | Room management's LAN door: aiohttp API/WS, room UI at `/`, and `/renderer/*` pass-through to the renderer. No passwords (owner ruling 14 Oct). Runs **inside the Flask process** since 14 Oct 22:52 (2f; rollback `ROOM_IN_APP=0` + Flask restart). | screen session `flask` (`./start.sh start flask`); logs `logs/room_service.log`; flags in `deploy/sm-room.env` | `0.0.0.0` | **LAN** (guest Wi-Fi included, by owner ruling); **never WAN** | Spectre lanWan (`screen-machine-room`): portproxy `0.0.0.0:8700 -> WSL:8700` plus rule `la-screen-machine-room-8700` | the default: `192.168.1.0/24, 192.168.2.0/24, 192.168.3.0/24, 192.168.4.0/24` (owner ruling: operable from the whole LAN, guest Wi-Fi included) | service: screen-machine-2f; ctl and port: screen-machine-ca |
| **8701** | show bus (**built 14 Oct**): per-frame latest-value show state, published by room_service | inside room_service (screen session `sm-room`) | `127.0.0.1` | **localhost only** | none, ever | n/a | screen-machine-2f |
| **6454/udp** | Art-Net in: Maestro's ArtDmx (unicast to 192.168.1.205) for room_service's virtual DMX devices (Hue lamps, show bus). ~40 pkt/s, ≤530 B, one way. | room_service binds `0.0.0.0:6454/udp` in WSL; on Windows the **Art-Net UDP relay** (Spectre `scripts/windows/artnet-relay.*`, owner-decided AV-D2) forwards `0.0.0.0:6454/udp -> WSL:6454` because portproxy is TCP-only | `0.0.0.0` | **LAN** (Maestro); **never WAN** | Windows firewall rule `la-artnet-relay-6454-udp` (UDP, Domain/Private) — **pending owner OK** | `192.168.1.0/24, 192.168.2.0/24, 192.168.3.0/24, 192.168.4.0/24` | service: screen-machine-68; relay and port: screen-machine-ca |
| **8702** | stream_service, production: live 4K60 media WebSocket for the kiosks. `--public-base ws://192.168.1.205:8702`. No passwords (owner ruling 14 Oct). Control routes are going localhost-only (reached via the app's `/renderer/*`); media sockets for TVs and previews stay on the LAN. On-demand worker: stopped is normal. | screen session `sm-stream` via `scripts/ctl`; flags in `deploy/sm-stream.env` | `0.0.0.0` | **LAN** (kiosks); **never WAN** | Spectre lanWan (`screen-machine-stream`): portproxy `0.0.0.0:8702 -> WSL:8702` plus rule `la-screen-machine-stream-8702` | the default: `192.168.1.0/24, 192.168.2.0/24, 192.168.3.0/24, 192.168.4.0/24` (as in Spectre's `screen-machine-stream` module). Only the kiosk (main subnet, 192.168.1.10 and .92) needs it, so 04 and the owner may narrow it to `192.168.1.0/24`. That would change this cell and the Spectre module together. | service: screen-machine-04; ctl and port: screen-machine-ca |

| **8703** | show_service (**production since 16 Oct**, docs/SHOW_ENGINE.md): the show bus v2 (`/v1/show`: music.*, show.state) and its API (`/healthz`, `/api/v1/*`). The renderer subscribes with `--music-bus ws://127.0.0.1:8703/v1/show`. Always on. No passwords. | screen session `sm-show` via `scripts/ctl`; flags in `deploy/sm-show.env` | `127.0.0.1` | **localhost only**; pages reach it through `:8700/show/*` (2f's pass-through, pending) | none | n/a | service: screen-machine-c8; ctl and port: screen-machine-ca (added by c8 16 Oct while ca was blocked) |
| **6980/udp** | VBAN audio + MIDI into show_service (SHOW_ENGINE §3.1). **Receiver-initiated**: show_service sends SUBSCRIBE from this WSL socket to each sender (the kiosk's show-sender on its own udp 6980, the test piano on WSL udp 6981), and the senders reply to the NAT-observed address. | inside show_service | WSL `0.0.0.0:6980` | **LAN replies through WSL NAT only**; never WAN | **none needed** (no portproxy, no relay, no inbound rule) | n/a | service: screen-machine-c8 |
| **6984/udp** | PC22 audio agent (Windows): a PC22 mic (chosen on the Room page; the Dell U3224KB's by default: the Insta360 Link lags it by 134-148 ms) as VBAN stream `pc22-mic` for show_service, until the kiosk mic exists. **Receiver-initiated**: show_service SUBSCRIBEs from WSL to 192.168.1.205:6984 (config/room/show.yaml `pc22`) and the agent replies to the WSL source address. | `tools/show_sender/pc22_agent.py`, Windows Python 3.12, a scheduled task at Greg's logon | Windows `0.0.0.0:6984` | **WSL only** in practice (no LAN client); never WAN | **none needed** (verified 16 Oct: SUBSCRIBE and replies flow without a rule) | n/a | agent: screen-machine-d4; Spectre card `screen-machine-audio-agent` |
| **8704** | dot_service (27 Oct): the e-ink tags (MindReset Dot. Quote/0, config/room/dot.json). `/healthz`, `/api/v1/status`, `/preview/<device>.png`, `POST /api/v1/push`. Reaches the tags only through Dot.'s cloud API (outbound HTTPS). No passwords. | screen session `sm-dot` via `scripts/ctl`; flags in `deploy/sm-dot.env`; Spectre card `screen-machine-dot` | `127.0.0.1` | **localhost only** | none | n/a | screen-machine-18 |
- **8705 and up are unallocated** for the AV block (8703 is show_service, 8704 dot_service), except the dev tools 8711 and 8712 (§2.3). A new AV port is added here first.
- **Not yet production-ready.** Nothing in the AV block starts at boot until its owner says so: 2f for room_service, 04 for stream_service. Each env file carries a hold line (`ROOM_HOLD=`, `STREAM_HOLD=`) that makes `scripts/ctl` refuse start, restart and enable until the owner deletes it. See `docs/OPS_CONTROL.md` §4.

## 2. WSL: everything else

### 2.1 screen-machine (Flask stack, `./start.sh` and GNU screen)

| Port | Service | Process | Bind | Scope | Windows exposure | Notes |
|---|---|---|---|---|---|---|
| **5000** | Flask API plus the built SPA (`dist/`), serving `/display/*` | `screen` session `flask` (`./start.sh start flask`); Spectre module `screen-machine-flask` | `0.0.0.0` | **WAN** (direct 5000; also via 8080 <- WAN 8000) | portproxy `5000 -> WSL:5000` and `8080 -> WSL:5000` | **kiosk-critical** |
| **8765** | overlay and light WebSocket (inside the Flask process) | same process as 5000 | `0.0.0.0` | **WAN** | portproxy `8765 -> WSL:8765` | **kiosk-critical** (light relay) |
| 4040 | ngrok local inspector API | `screen` session `ngrok` | `127.0.0.1` | WSL-only | none | ngrok tunnels :5000 outbound to `adapted-vervet-eternal.ngrok-free.app`; it needs no inbound port |

### 2.2 Other WSL services

| Port | Service | Owner and manager | Scope | Windows exposure |
|---|---|---|---|---|
| 22 | sshd | system | **WAN** via 2222 | portproxy `2222 -> WSL:22` |
| 9000 | dagnet backend | dagnet (Spectre) | WSL-only | none |
| 5173 | dagnet frontend (Vite) | dagnet (Spectre) | WSL-only | none |
| **3001** | exlib-server | exlib (Spectre `exlib-server`). The exlib phone app hardcodes `192.168.1.205:3001`. | **LAN** | portproxy `3001 -> WSL:3001` |
| **8081** | exlib-mobile (Metro bundler) | exlib (Spectre `exlib-mobile`) | **LAN** | portproxy `8081 -> WSL:8081` plus rule `la-exlib-mobile-8081` |

### 2.3 Dev-only (WSL-only, unmanaged, never exposed)

| Ports | Use | Notes |
|---|---|---|
| 3001 | room_service dev instance (its CLI default) | Only while exlib-server is **not** running, because 3001 belongs to exlib-server. |
| 3010-3019, 3114 | test harnesses and mock servers | Leftovers (e.g. 3016) being stopped 14 Oct. |
| 3090-3099 | room_service dev and preview instances | 3091 was 2f's preview; being stopped 14 Oct (production is 8700). Bind dev instances to `127.0.0.1`. |
| 5174-5199 | Vite dev servers (page and panel work; use `--strictPort`) | 5173 is dagnet's. Seen on 14 Oct: 5174, 5185, 5186. |
| 8082-8099 | stream_service dev instances (8082 is its CLI default) | 8092 retired 14 Oct. Leftover previews (e.g. 8093) are being stopped. |
| 8711 | show-synth: simulated show bus (K3 frames), a DEV SIMULATOR ONLY; production uses the real show bus on 127.0.0.1:8701 (room_service) (owner screen-machine-04, `stream_service.tools.show_synth` in `~/dev/sm-renderer-wip`) | Dev only. WSL only: never a portproxy or firewall rule. Registered by 04 on 14 Oct. |
| 8712 | show-bus synthesiser instance (same dev tool as 8711; not production) | `127.0.0.1`, dev only. 8705-8710 and 8713 and up stay unallocated for the AV block. |
| 8081 | bd's streaming lab (`experiments/stream-latency`) default | 8081 belongs to exlib Metro, so **the lab must never run while exlib-mobile is running**. Prefer an 8082-8099 port. |

Dev ports outside these ranges must be registered first (Rule 1). The ranges were widened on 14 Oct to cover the listeners found by `ss -Hltnp`.

## 3. Windows PC22

| Port | What | Scope | Managed by |
|---|---|---|---|
| 4317 | Spectre API (Express) | LAN | Spectre |
| 4318 | Spectre WebSocket (status and logs) | LAN | Spectre |
| 4320 | Spectre UI (Vite) | LAN | Spectre |
| 80 | portproxy `0.0.0.0:80 -> 127.0.0.1:4320` (Spectre UI on port 80) | LAN | Spectre |

**Portproxy listeners into WSL.** Each is `0.0.0.0:P -> <WSL eth0 IP>:P` unless noted. Read with `netsh interface portproxy show v4tov4` on 2026-10-14.

| Listen | Connect | For | Exposure |
|---|---|---|---|
| 5000 | WSL:5000 | Flask | WAN, critical |
| 8765 | WSL:8765 | overlay and light WS | WAN, critical |
| 8080 | WSL:**5000** | Flask (TVs via WAN 8000) | WAN, critical |
| 2222 | WSL:**22** | WSL sshd | WAN |
| 3001 | WSL:3001 | exlib-server | LAN |
| 8081 | WSL:8081 | exlib-mobile Metro | LAN |
| **8700** | WSL:8700 | room_service | **new**, LAN only. Spectre creates it when room_service goes to production. |
| **8702** | WSL:8702 | stream_service | **new**, LAN only. Spectre creates it when stream_service goes to production. |

The legacy `fix-wsl-networking.ps1` rebuilds only 5000, 8765, 8080 and 2222. Spectre's lanWan reconcile is the supported way to re-point every WSL-targeted entry after a WSL IP change.

## 4. Router: WAN forwards (95.141.21.170)

| WAN port | Forwards to | For |
|---|---|---|
| 8000 | PC22:8080 (-> WSL 5000) | TVs load `/display/*`; the deadman probes `/api/health`. **Kiosk-critical.** |
| 8765 | PC22:8765 | light relay WebSocket. **Kiosk-critical.** |
| 5000 | PC22:5000 | Flask direct |
| 2222 | PC22:2222 (-> WSL 22) | SSH into WSL |
| 2223 | kiosk 192.168.1.92:22 | SSH into the kiosk |

**No AV port (8700-8702) is ever WAN-forwarded.** The owner confirms this on the router (AV programme D4). Phones reach room_service only on the LAN, guest Wi-Fi included.

## 5. Kiosk mini-PC (`screen-machine-drawingroom`)

| Port | What | Scope |
|---|---|---|
| 22 | sshd | LAN (192.168.1.10 and .92); WAN via router 2223 -> .92 |
| 9222 | Chrome DevTools (CDP), north TV Chrome | `127.0.0.1` only; reach it over SSH |
| 9223 | Chrome DevTools (CDP), south TV Chrome | `127.0.0.1` only; reach it over SSH |
| 631 | CUPS (distro default; screen-machine doesn't use it) | local |

**Outbound from the kiosk** (no listener needed):
- `http://95.141.21.170:8000/display/<id>`
- `ws://95.141.21.170:8765`
- once the AV services are live: `ws://192.168.1.205:8700` (control) and `ws://192.168.1.205:8702` (media)

## 6. LAN devices (screen-machine is the client)

| Device | Address | Port | Used by |
|---|---|---|---|
| Bloc iQ blinds hub (Dooya/Motion protocol) | 192.168.1.136 | UDP 32100 | room_service blinds subsystem. Unicast request and reply from WSL works; hub multicast does not reach WSL, so it polls. |
| Samsung The Frame Pro, north | 192.168.1.150 | TCP 1516 (Samsung IP Control, JSON-RPC over HTTPS); TCP 8002 (samsungtvws, WSS) | IP Control reads (tokens in `~/.config/screen-machine/ipcontrol/`); Flask's samsungtvws actions |
| Samsung The Frame Pro, south | 192.168.1.151 | TCP 1516; TCP 8002 | same as north |

**UDP and WSL:** no inbound LAN UDP or multicast reaches WSL in NAT mode. Each UDP adapter proves its own path (AV programme §6).

## Changing this register

Send changes to screen-machine-ca. The register changes first, then the code or env file, then the Spectre module (`ports`, `critical`, `lanRemoteAddresses`). For example, an AV port change also needs `deploy/sm-<svc>.env`, any `--public-base`, and Spectre's portproxy and firewall entries, in that order.

## Outbound from WSL (no inbound rule needed)

| Destination | Ports | Used by | Notes |
|---|---|---|---|
| Hue Bridge Pro 192.168.1.72 | tcp/443 (HTTPS API), udp/2100 (DTLS entertainment stream) | room_service (screen-machine-68) | Proven from WSL on 14 Oct; NAT handles replies. |
| Bloc iQ hub 192.168.1.136 | udp/32100 (unicast request/reply) | room_service blinds adapter (screen-machine-aa) | Multicast push (238.0.0.18:32101) does not reach WSL. |
