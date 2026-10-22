> **SUPERSEDED (13 Oct 2026, late evening).** This is v1 of the room AV programme, written by screen-machine-10 and archived unchanged below this note. The live plan is `docs/AV_PROGRAMME.md` (v2), now maintained by screen-machine-2f. Keep this file for its research on Maestro, Hue, Art-Net, Sonos, ports and room auth (§1, §2.3, §3.1–§3.7, §3.10), which v2 relies on for the later integrations. Its milestones, the cut design in §3.3, the safety gates and the timeline are replaced.

# Room AV programme of work

**For:** the owner, plus the agent sessions working in this checkout:
- **room**: screen-machine-10, which wrote this document;
- **bd**: streaming, renderer and kiosk;
- **refactor**: screen-machine-12, which maintains `REFACTOR_PLAN.md`.

**Date:** Tue 13 Oct 2026, late evening. **Status:** proposal for the owner's decision. Nothing in it has been built.

**How it was made:**
- Eight parallel research agents gathered the evidence, read-only (`docs/av/evidence/`).
- Three independent programme designs were scored by two judges, and the best was synthesised.
- That draft was then revised by hand against the owner's directives given this evening: Game Mode closed, hard cuts only, the art/live compositing split, and the sway kiosk.
- Three adversarial critics (facts, delivery, owner directives and safety) then reviewed it. Their findings were checked against the code and the documents and applied, and the text was re-synced with the DCL (revised 20:58) and RP (revised 21:00). The main consequence: AV-D6 now recommends keeping the RP/DCL order (§7).

**Document map:**

| Document | Role |
|---|---|
| this file | Room level: `room_service`, Art-Net virtual fixtures, Hue, Sonos, OSC to MaestroDMX, the mode supervisor, the phone UI, and the contracts from the room to the display layer and the stream service |
| `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` (DCL) | Display-page internals and the art↔live cut. Revised by bd at 20:58 on 13 Oct for hard cuts and the art/live split. **DCL §2.5 is the authority for the cut handshake:** its sequence, message names and timeouts. Also cited: invariant I6 (art in the DOM, live on the GPU); §2.3 (protocol examples); §2.2 and §3 step 10 (renderer requirements); §2.7 (failure rows, all hard cuts); §5 item 20 (overlays during live, AV-D15). |
| `docs/REFACTOR_PLAN.md` (RP) | The screen-machine codebase and its delivery: phases P0–P6B, T1–T8 and decisions D1–D27. This programme refers to RP decisions by their D-number. New decisions here are numbered **AV-D1…**. |
| `docs/av/AV_SYSTEM_SPEC_v0.1.md` | The owner's spec. §2.3 below lists the corrections to it. |
| `docs/av/evidence/` | Research files r0–r7 and the facts digest. Cited here as r0…r7 and FD. |
| `docs/GAME_MODE_AUTO_SPIKE_HANDOVER.md` | The closed Game Mode spike: TV facts and IP Control. |

**Assumptions to confirm** (AV-D1):
- [A1] MaestroDMX is on hand; its firmware version is unknown. The mixer, the second Sonos Connect, the mic and the piano line-out cable are not (M2c).
- [A2] The AV room holds both Frames, the kiosk and the light sensor, and the sensor can see the Hue lights.
- [A3] The router supports DHCP reservations. Guest-network isolation is unknown.
- [A4] The owner can supervise about three sessions for 1–2 h a day, plus device windows.
- [A5] A laptop is available for an Art-Net capture, and a phone can feed line-level audio into Maestro's RCA input.

---

## 0. Summary

1. **The room becomes the platform, and screen-machine becomes its screens subsystem.** RP is still right for the codebase. It has no concept of lighting, devices or shows, so it continues as the *screens and platform* workstream, re-scoped as in §2.
2. **A new `room_service` provides the "virtual lighting" infrastructure Maestro needs.**
   - It owns: Art-Net in and the patch decoder; a fixed-rate show-state bus; the Hue, OSC (Maestro) and Sonos adapters; the mode supervisor (Off, Ambient, Piano, Disco, Intro); flash limiting; and an authenticated phone UI.
   - It is a separate, always-on process. It never runs inside Flask.
3. **Run it in WSL on the workstation, with a small Windows UDP relay for Art-Net** (AV-D2; no hardware to buy).
   - Maestro's Art-Net is inbound UDP. It can't reach WSL directly, because WSL sits behind NAT and netsh portproxy forwards TCP only (r5 §1–5). That is RP's D22 trigger.
   - A Windows task of about 50 lines listens on UDP 6454 and forwards each packet to WSL's current IP. It answers Maestro's ArtPoll itself. An inbound firewall rule scoped to Maestro's IP goes with it.
   - The cost: room control stops while the workstation reboots. Maestro keeps the heads running on its own, and the screens depend on the workstation anyway.
   - Until the relay is in, `room_service` runs against synthetic Art-Net on localhost.
4. **Hue first, this week.** M1 to M2b need none of the DMX heads, the mixer or a Sonos Connect:
   - **M1 (Thu 22 Oct):** Hue follows a synthetic show over REST, and Off restores the house exactly.
   - **M2a (Fri 30 Oct):** music → Maestro → Art-Net → Hue, after safety gate G1a.
   - **M2b (Wed 4 Nov):** Disco on Hue Entertainment streaming, after gate G1b.
   - **M2c (W3–W4):** the audio chain (mixer, mic, piano and Sonos into Maestro), so the room reacts to the room's music rather than to a phone.
5. **The owner's display directives are adopted as binding:**
   - **Art mode:** Chromium composites everything (still, ambient mask, overlays).
   - **Live mode:** the page shows **one full-screen `<video>`** (4K60) **and nothing else**. The workstation renderer composites everything, including every transition effect, the Maestro-driven level and the flash limiting. Live runs at full intensity, with no mask.
   - **Art↔live is a hard cut between matching frames, using an anchor-still handshake** (DCL §2.5):
     - the stream opens on the exact still as it appears on glass, mask included;
     - the page cuts to the video on its first presented frame and acknowledges;
     - only then does the renderer diverge;
     - on exit the renderer converges back to the still before the page cuts.
   - **Software never writes TV picture settings, and never switches them per mode.** Game Mode is never used (spike §10; RP D24 closed). Power and Art Mode↔HDMI are operational state, changed only through DevicesPort (§2.2).
6. **Video stays on the measured path:**
   - HEVC 3840×2160 over ws-fMP4/MSE, with one independent session per screen (north on the RTX 4060, south on the Quadro P4000).
   - The P4000 is untested: every lab run used the 4060. It is a Pascal card, so its NVENC should do HEVC 4K60 but not AV1, and CUDA rendering will be slower. A pacing test on it comes before K6 is committed.
   - The **kiosk now plays 4K60** under sway: pre-made footage dropped 0.03% of frames.
   - **Live 4K60 depends on the renderer pacing frames evenly**: today's lab generator drops 6–7%. 4K30 is the fallback rung. It was proven under X11; bd re-runs it under sway in the first V1 window.
7. **Safety gates delivery:**
   - At most 3 flashes per second across the whole field of view: screens and Hue now, the heads from M7.
   - Photosafe mode is on by default.
   - Maestro's strobe and blinder trigger enables are off on all four groups.
   - Only paired phones can control the room, and `room_service` is never reachable from the WAN. The screens' existing mask and scheduler endpoints stay WAN-exposed until P3-reach (M5).
8. **TV state comes from Samsung IP Control** (JSON-RPC on :1516; spike §10). It reads power, Art Mode, input and picture settings over the TVs' Wi-Fi, and it is reliable enough to poll. Screens trail the renderer by about 0.31 s, measured on 13 Oct, so the renderer draws about 0.3 s ahead and beat-phases its visuals, with a ±50 ms trim on the phone UI.
9. **Screens are sequenced after the light push** (AV-D6, revised). The revised DCL handshake needs the P4 hub, so this programme no longer asks to pull channels ahead of it.
   - The room's value path (Hue, Maestro, phone control) does not depend on that choice and starts tomorrow.
   - Light push is on glass in about 10–18 Nov.
   - Screens are claimable on devtest in about 24 Nov–10 Dec.
   - Screens run as fixtures in production from mid-December, and the 7 trial evenings probably run into January.
10. **Effort to M6** (screens as fixtures in production), in session-days (RP's "owner-days"):

    | Session | Effort |
    |---|---|
    | room | 19–25.5 |
    | refactor | ≈34.5–47: RP/DCL's own 30–41 to production live, plus P3 (2–3) and the room's K5 and DevicesPort (2–3.5) |
    | bd | ≈12.5–18.5, including DCL's P6A′ (6–9), split across M4 and M6 |
    | owner | ≈8.5–11.5 of their own time, over about 12 weeks |

    The owner's attention is the constraint for the room. The refactor session is the long pole for the screens.

---

## 1. Where we are (13 Oct, late evening)

| Subsystem | State | Evidence |
|---|---|---|
| Backend | One `app.py` process (waitress :5000 + WS :8765) runs from the dev checkout. P0 hotfixes are deployed. Scheduler handlers block their own loops. | RP §1; r0 §1–2 |
| Exposure | ngrok exposes all of :5000. The router forwards 5000, 8000 (→ Windows 8080 → Flask), 8080, 8765 and 2222 from the WAN, and possibly 8081/3001 too (per RP §1/D4; not verified on the router). Only the admin API and `/test-overlay` need a token. Mask overrides, `events/throw`, publish and Alexa are open: `brightness-override 0` is effectively a remote blackout. | r0 §3; r1 §8 |
| Git / prod | Live state is tracked in git and rewritten all the time. Deployed kiosk work is uncommitted. Several sessions share this tree. `start.sh` uses a broad `pkill -f`. | RP §1; KIOSK §7 |
| Kiosk N95 | **sway 1.9 (Wayland) since 19:31 UTC.** `kiosk-x11.service` is the disabled rollback. Chrome 136 with zero-copy VA-API. **4K60 HEVC 30 Mbit/s pre-made footage on both TVs: 0.03% dropped, rAF 60.0.** Survives reboot (back in about 40 s), a Chrome crash and a TV unplug. Chrome is not apt-held (RP D7). | FD late updates; KIOSK §3 |
| Network | Powerline ~170–190 Mbit/s (24 h soak running). Wi-Fi fallback 15–20 Mbit/s, which cannot carry two 4K streams. UDP loss never measured. A dead path behind a live link goes undetected. | KIOSK §5 |
| TVs | 2025 Frame Pro, via Wireless One Connect, on Wi-Fi. Picture: **FilmmakerMode**, Warm2, BT.1886. **Game Mode never used.** Both TVs read Auto, which never engages under sway because wlroots sends content type Graphics. Input Signal Plus is not yet checked over IP Control. IP Control works: about 2,400 calls with 0 timeouts and 2 empty replies. Each two-call poll took p50 ≈660 ms, p95 770–850 ms, max 3.5 s. The TV serves one connection at a time, so calls are serialised and empty replies retried. The `samsungtvws` remote drops often, and the Art-Mode sleep/wake code has known bugs. | spike §10; r1 §11 |
| Display page | Polls content every 1 s and the mask every 3 s. **A failed mask fetch blacks the screen.** Phantom overlays. Hard reloads at 2/6/10/14/18/22 h. `.mp4` plays as a slow-motion loop. 0 lux gives 1% brightness. Mask overrides force `warm_alpha 0` (`routes/display.py:124-136`). The DCL was revised at 20:58 for the art/live split and hard cuts. | r1 §1–5 |
| Lab (bd) | One encoder session, fanned out to every viewer. Live 4K60 drops 6–7%: the generator misses 16.7 ms about 2% of the time (render plus a 12 MB raw pipe write per frame), and the player's catch-up speed-up forces drops. 4K30 live: about 0.25% dropped. Runs from a scratchpad venv on :8081. | FD; r2 |
| GPUs | RTX 4060 + Quadro P4000, driver 566.14. NVENC works in WSL. GeForce NVENC is capped at 8 sessions per system. No GL–CUDA interop in WSL, so the renderer is CUDA. | r6 §5–6; r2 §5 |
| WSL network | NAT. portproxy is TCP-only, so **no inbound LAN UDP** and no multicast. Outbound UDP to the internet works (NTP). Outbound UDP to LAN devices (OSC, Hue DTLS) is untested. | r5 §1–5, §11 |
| Maestro [A1] | One Internal DMX universe plus **one** Art-Net universe (unicast to configured nodes). OSC in on :7672, with no replies. Exactly 4 groups. ACTIVITY is a single global value. `/live/*` drops Show mode. Its AP password defaults to "mymaestro". RCA input is −10 dBV. | r3 |
| Hue | **Two** BSB002 bridges (.162 "18½ 2F", .217 "Kitchen"). Not paired; which one holds the room's lights is unknown. Entertainment allows ≤10 colour lights per area and one stream per bridge. No clean restore after a stream. | r7 |
| Sonos | No code yet. The household's UPnP/Auth toggles are unknown. Sonos advisory SSA-2025-0002 recommends UPnP off, which breaks SoCo. | r5 §6–10 |
| Room software | None. python-osc, aiohue, hue-entertainment and soco are not installed. aiohttp, cryptography and Python 3.12 are available. | r0 §11 |
| Alerting | Siren runs in Flask and is unsafe in a second process unchanged. Deadman probes the public `/api/health`. | r0 §7–8 |

**Latency:**
- Maestro → heads: about instant.
- Hue: +50–150 ms (estimate).
- Screens, **measured 13 Oct** (`tools/room/latency_cal`, south TV; the result is in `config/room/latency.json`):
  - Chrome "presented" → light at the room sensor: **179 ms brightening** and 104 ms darkening (±5 ms). This is the TV, the One Connect box and the panel. The TV brightens over about 230 ms (10–90%), probably because of local dimming.
  - Render → light: **≈309 ms** = bd's lab stream capture → presented (130 ms at 4K60 under sway) + 179 ms.
  - Still to add: the show-bus hop, and about 50 ms if PyNvVideoCodec is used (3 frames at 60 fps).
  - Confirm it end to end through the real stream once the renderer exists.
- Beat phasing: the renderer draws about 0.28–0.30 s ahead, landing slightly early, because visuals that lag the sound are noticed sooner (ITU-R BT.1359: about 45 ms). A ±50 ms trim on the phone UI absorbs the beat-tracking and audio-path delay. Art-Net carries no beat phase (r3 §6), so the renderer infers it from the audio.
- Re-run the calibration (about 35 s, no sudo) after any change in the display chain.

---

## 2. The refactor plan, and corrections to the spec

### 2.1 Verdict

**RP is good engineering for the screens and the platform, and it says nothing about the room** (r4 §1).

**Its seams fit the room well:**
- screen claims and leases (D12);
- per-screen `policy` with an HTTP twin;
- the live player state machine;
- stream sessions with source switching;
- `/glass` (desired vs applied);
- KioskMonitor;
- release-dir deploys.

**What it lacks:**
- a show plane, device adapters, a mode supervisor and a fixture-driven renderer;
- room authentication;
- physical and photosensitive safety;
- inbound UDP;
- a mode-aware sensor mask.

The changes below were sent to the refactor session. On 13 Oct it recorded them in **RP §5.2** ("Room-AV programme: proposed scope inside existing phases"), with pointers from each affected phase. They are proposals pending **RP D28** (= AV-D1/AV-D6). This document does not edit RP. Under AV-D6 (a) **RP's order stays as it is**; the room adds scope inside existing phases.

| RP item (RP estimate, days) | Requested change |
|---|---|
| P0.5 (1–1.5) | **Keep, first.** Hold-last mask and gated reloads protect every later restart. |
| P1 (3–4) | **Extend:** the test guard must block UDP `sendto` as well as TCP, so no test can emit Art-Net, OSC or Hue to real lights. Add device fakes. Optional trim (r4 §3): the `resolve_schedule` goldens and the 13 schedule-script schema checks move to T6. The `send_overlay` and `publish_to_destination` goldens stay in P1, because P2's path edits touch those modules. |
| P2 (5–7) | **Keep as the hard gate.** More always-on services and several sessions now share the prod tree. Keep step 5's Windows boot task and `backup.timer`. Add: the Siren subject prefix and ping URL from env (the paths already move in step 1), and the Maestro exports in `backup.timer`. |
| P3 (2–3 + owner) | **Split.** *Front:* LAN origin, `?kiosk=` and the Chrome hold (D7), now. *Reach:* close the WAN forwards and narrow ngrok **before any claim API ships** (K5), including 8765 after D5 (P4 step 7). Add: DHCP reservations at each device's *current* address; the port register (K1); guest isolation; a UDP-loss test on the powerline. D22 is resolved by AV-D2. |
| P4 hub + shadow (4–6; 1.5–2 + 7 days) | **Keep in RP order** (AV-D6). Show-state never rides the Flask hub. |
| P4b (3–4) | **Art mode only**, as RP already says. Add: **a room hold**, so that while the room sets `show_active`, SensorService holds brightness *and* warm values under a token-gated hold with a TTL the room renews (§3.8). Also an **output slew limit** (§3.9 point 5); a **token on `maskon`, `maskoff` and `brightness-override`** before P4b ships; and a loop-gain check with Hue in view. |
| P5 step 2: `sm/channels` (2) | **Keep in place** (it sits on the P4 store). Add (≈1–2): a `room` claim rank, room-claim policy (§3.4 K5), service-token writes, and ChannelService running the DCL §2.5 handshake. |
| P5 steps 1 and 3 | Keep. Overlays sent to a live screen follow AV-D15. |
| P6A (bd) | **Re-scope** (K6): two sessions pinned to GPUs; `source.kind=show`; anchor, diverge, re-anchor and converge (DCL §2.5); renderer-owned transitions and limiter; even 60 fps pacing (GPU-resident to NVENC); a service token; no stamping in production. LL-HLS and WebRTC dropped for v1. |
| P6B | **Stage 1** = DCL step 9 (4–5; DCL's revised figure). **Stage 2:** the room claim is its first consumer, at a minimum rung of 4K30 (AV-D12). |
| T2 subset (1–1.5) | **Pull forward, before P6B stage 2, using IP Control.** DevicesPort reads observed TV state over IP Control. Wake and Art-Mode actions use the fixed samsungtvws code (check Art Mode first; if unknown, don't toggle; verify, then retry), or IP Control setters if the owner approves testing them. Take over `ipctl.py` and the soak results from screen-machine-85 (§4). |
| D24, §3.5 | **Done (13 Oct):** D24 closed per spike §10; §3.5 now says IP Control reads and sets Game Mode, with Input Signal Plus unchecked. |
| T1, T3–T8 | As RP. New code reuses their patterns: logging, settings-only env, Siren style. |

### 2.2 Principles and decisions that change

| RP item | Change |
|---|---|
| §1/§7 "no microservices beyond the stream service" | **Stream service + room service.** The renderer lives inside the stream service. No broker, no Docker; no ASGI for the legacy app. |
| Principle 5, three planes | Add a **show plane** (latest value, lossy, fixed rate, freshness-gated) and a **device-command plane** (rate-limited; outcomes are ok/failed/unconfirmed; desired kept separate from observed). |
| Principle 6, "fail to still, never to black" | Becomes: **a stream failure is a hard cut to the still. Stale show input is handled inside the stream: the renderer eases to a calm look. Commanded darkness is content, not failure.** |
| Principle 7, no app auth | Unchanged for the screen-machine operator UI until P3. **Room control is authenticated** (§3.6), and screen claims carry a service token. |
| Principle 8, single owner | Extends to **devices**: Hue is owned by the house outside shows and by the room during them, with explicit hand-over; TVs are read and driven only through DevicesPort; stream sessions only through reconcile; Maestro only through the room. |
| New principles | **Physical safety:** safe by default; no unauthenticated effects; a limiter at every light output; shows lapse to Off. **The heads work without any computer** (Maestro is autonomous). **Live mode: the browser only plays video.** **TV settings:** "TV settings" means picture settings (Game Mode, picture mode, expert picture values). Software never writes them; drift is only reported (`tv.settings_drift`). Power and Art Mode↔HDMI are operational state: DevicesPort alone changes them, only outside a cut (the wake completes ≥60 s before the anchor handshake), and never while a claim is live. A one-off Game Mode change made by the owner is configuration, not a runtime switch. |
| D10/D11 | MSE + HEVC confirmed. **Profile: 4K60 once the renderer paces evenly; 4K30 is the fallback rung** (proven under X11; to be re-run under sway). wctrack/WebCodecs kept as an option. No WHEP in v1. |
| D12 | base < scheduler < api/alexa < **room** < operator. |
| D13 | Timed reloads never fire during a claim. Reload replacement lands before evening shows. |
| D14/D25/D27 item 9 | Mask is art-mode only: **live has no mask** (owner). At each cut the server holds `mask.set`, and the anchor carries the mask the page acked as on glass (DCL §2.5–2.6). |
| D16 | Becomes the full port register (§3.5). |
| D21 (NVIDIA driver ≥570) | Becomes relevant: zero-copy CUDA→NVENC for even 4K60 pacing. Try PyNvVideoCodec first (works now, adds about 3 frames, which is acceptable because latency is not critical). Upgrade the driver only if needed (AV-D9). |
| D22 | **Trigger fired** (Art-Net, ArtPoll). Resolved by a Windows UDP relay (AV-D2). WSL stays on NAT. Sonos is polled, so GENA callbacks aren't needed. |
| D24 | **Closed: Game Mode is never used** (owner, after the IP Control setter test showed Samsung's Game Bar overlay and flicker; spike §10). RP updated 13 Oct: IP Control reads and sets Game Mode; Input Signal Plus is unchecked; picture mode is FilmmakerMode. |

### 2.3 Corrections the AV spec needs

| The spec says | Correct to (evidence) |
|---|---|
| H.264 over WebRTC via MediaMTX; test 1080p60 first | **HEVC 3840×2160 over ws-fMP4/MSE**, proven on both TVs; 4K60 on glass under sway. WebRTC needs UDP (or ICE-TCP) into WSL. Chromium and MediaMTX disagree on Linux HEVC over WebRTC (r6 §1–4). |
| ~100–200 ms glass-to-glass target | **Measured: about 0.31 s from renderer to light** (13 Oct; `tools/room/latency_cal`). The renderer draws about 0.3 s ahead for beat-phased visuals, with a ±50 ms trim. |
| Screens show the renderer's output via simple pages | Correct, and now strict: **live = one `<video>`, nothing over it.** The renderer draws all transitions, text and effects. |
| GPU server unspecified; chrome://gpu as the check | The server is the existing WSL workstation (CUDA renderer; no GL–CUDA interop). Verify decode with the CDP Media domain, not chrome://gpu (r4 §23). |
| One Hue bridge; reuse artnet-bridge | **Two bridges.** ≤10 colour lights per stream; one stream per bridge; white bulbs REST only; any app can stop a stream; no clean restore. artnet-bridge is plan B only: 6 Hz cap, no fallback, no restore (r7). |
| `Hue-Ambience` RGB+DIMMER+ACTIVITY; per-screen ACTIVITY | **One RGB fixture per colour light, no dynamic DIMMER** (Maestro dims LEDs through the colour channels). **ACTIVITY is global.** Screens differ through groups and pixel strips (r3 §3, §5). |
| Load a show, then send `/live/N/*` tweaks | `/live/*` **exits Show mode**, so bake parameters into cues. Triggers do nothing while stopped. Blackout = `/global/brightness 0` (r3 §8). |
| Ambient: "gentle movement" | Pan/tilt moves only under Maestro Patterns, **which need audio** (r3 §3). |
| Mixer stereo out → Maestro | Maestro's input is RCA at −10 dBV max. An MG06 at +4 dBu overdrives it, so use an attenuator/converter or a USB interface (r3 §6). |
| `artnetAgeMs: 30`; "ensure firewall rules" | Unchanged Art-Net may legally repeat only every ~0.8–4 s, so staleness is measured in seconds. **Inbound UDP cannot reach WSL directly**, so a Windows relay forwards it (AV-D2). Whether Maestro needs an ArtPoll reply is untested; the relay answers ArtPoll anyway (r5 §1, §13). |
| node-sonos-http-api | **SoCo** (polled), with aiosonos as fallback. UPnP may be switched off (SSA-2025-0002) (r5 §6–10). |
| Wired Gigabit LAN; LAN-only services | The room is on powerline. WAN forwards and ngrok exist today. Maestro's AP password is public (r3 §7). |
| "Avoid uncontrolled strobes" | A quantified joint flash budget enforced at every output (§3.9; r6 §9–10). |

---

## 3. Target architecture

### 3.1 Processes and hosts

```
 WORKSTATION 192.168.1.205
 +----------------------------------------------------------------------------------------+
 | WINDOWS: artnet-relay (scheduled task at boot)   UDP 6454 in (firewall: Maestro only) |
 |          -> forwards to WSL's current IP; answers ArtPoll itself                       |
 |          netsh portproxy (TCP): 8700 for paired phones; 5000/8765/8081 as today        |
 | WSL2 (NAT)                                                                             |
 |  room_service (python -m room_service)          sm-web: Flask :5000 + WS :8765 [refactor]|
 |   :6454/udp Art-Net (via relay)      --K5------> ChannelService: claims, policy,        |
 |   decoder -> limiter -> show-state     token     anchor handshake, reconcile (ONLY      |
 |   Hue   -> bridges 443/tcp + 2100/udp            owner of sessions); DevicesPort (TVs   |
 |   OSC   -> Maestro 7672/udp                      via IP Control :1516)                  |
 |   Sonos -> players :1400 (polled)               sm-stream :8081 (P6A)            [bd]  |
 |   supervisor; UI + API :8700 (paired)  <--K3---  north: ShowSource on RTX 4060          |
 |   show bus :8701 (localhost, token)       ws     south: ShowSource on P4000            |
 +----------------------------------------------------------------------------------------+
   paired phones -> :8700                                   | ws-fMP4 (powerline)  ^ control WS v2
   Maestro --XLR--> wireless DMX TX --> heads               v                      |
   (no computer in this path)              kiosk N95, sway (dumb host): 2x Chrome /display/<id>
```

**`room_service`:**
- Built on aiohttp, in its own venv. It imports only shared schemas, nothing from `routes/`, `sm/` or `utils/`.
- Deployed as `git archive` → `/srv/room/releases/<sha>` with a `current` symlink and a systemd unit in WSL. State and secrets live under `/srv/room/{state,config}`.
- Started as `python -m room_service`, so `start.sh`'s `pkill -f "python.*app\.py"` can never match it.
- Until the relay is installed, it binds to 127.0.0.1 and is opened from the workstation browser.

**`artnet-relay`** (Windows; the owner installs it):
- A small Python or PowerShell UDP forwarder that runs as a scheduled task at boot, with about 50 lines of code kept under `tools/room/`.
- It re-resolves WSL's IP, which changes whenever WSL restarts.
- It answers ArtPoll with a static ArtPollReply naming the workstation's LAN IP, so Maestro sees one valid node.
- One inbound Windows Firewall rule allows UDP 6454 from Maestro's IP only. Undo means deleting the task and the rule.
- `room_service` de-duplicates by Art-Net sequence number and timestamps arrivals, so a duplicated or bunched packet never makes the show flap.

**The renderer** lives in bd's stream service as a show-driven source. It subscribes to the show bus and **never parses Art-Net**.

**Flask** owns screen state and stream sessions, and never carries show-state.

**The kiosk** stays a dumb host. Only the server talks to the TVs.

### 3.2 Planes

| Plane | Semantics | Path |
|---|---|---|
| **Show** (new) | Latest value, lossy, fixed 50 Hz, freshness-gated | Maestro → :6454 → decoder → limiter → bus :8701 → renderer; Hue in-process |
| **Device command** (new) | Rate-limited; outcome `ok`, `failed` or `unconfirmed`; observed state kept separate from desired | room → Hue, OSC, Sonos; sm-web → TVs |
| Control | Small, ordered, persisted, authenticated | phone → room API → sm-web claim → page (WS v2, or the HTTP twin) |
| Media / files | As in RP | sm-stream → page; `/output` |

### 3.3 Art mode, live mode and the cut (owner directives, 13 Oct)

**Art mode:** the page composites everything in the DOM: the still, the ambient brightness mask and warm tint, overlays, and art transitions. This is today's behaviour, hardened by P0.5 and the DCL.

**Live mode:**
- The page shows **one full-viewport `<video>`** (MSE, HEVC 3840×2160, native scale), and **nothing** on screen above or around it:
  - no mask, overlay, HUD or captions;
  - no CSS opacity, filter or transform;
  - no page fades.
- The control WS stays connected for commands, acks and health, but draws nothing.
- The **renderer** draws everything into the stream:
  - the Maestro-driven look and level;
  - every transition (an in-stream crossfade, a "screen melt" or any shader effect);
  - any text;
  - the photosensitivity limiting.
- Live runs at full intensity ("a lighting rig, not a simulated artwork").
- The player **never** plays faster than 1.0×. It resyncs only by a hard jump to the live edge, which is a visible cut, so it must be rare.

**The cut.** DCL §2.5 (rev. 13 Oct 20:58) is the authority for the sequence, the message names and the timeouts. In summary:

**Art → live** (a claim drives it; ChannelService orchestrates):
1. The server waits until no overlay is showing. It publishes `phase:"starting"` and **holds `mask.set` and `overlay.show`** for that screen.
2. The page lets any running mask transition finish and pauses a clip base. It then acks `{kind:"anchor", base_rev, mask_rev, clip_t}`, which is exactly what is on glass.
3. The server creates the session with that anchor: the base URL, the fit (`local_flags.fill`), `clip_t` and the **acked** mask values.
   - The renderer draws the anchor with the page's own compositing arithmetic, using one shared formula golden-tested against page screenshots.
   - It holds the anchor, paced at 60 fps, with a high-quality I-frame. The hold limit is 30 s. The session then reports `ready`.
4. The page warms the `<video>` up under the art stack. In the rVFC callback for the first presented frame it hides the art stack in one property change: a **hard cut** between identical pictures. It then sends `ack visible {layer: live}`.
5. Only after that ack does the server release its holds and call `diverge`. The renderer ramps to full intensity and runs its transition.

**Live → art:**
1. The server fixes the exit target (the current base and the then-current mask), holds `mask.set`, and publishes `phase:"leaving"` with `exit{base_rev, mask_rev}`.
2. The page decodes the target and applies the target mask to the hidden art stack.
3. The server calls `converge`. The renderer runs its own transition into the target and holds it. sm-stream reports the hold's first pts, which the server republishes as `exit.hold_pts`.
4. The page cuts on the first presented frame with `mediaTime ≥ hold_pts`, sends `ack visible {layer: base}`, and detaches. The server deletes the session only after that ack.
   - If `hold_pts` is not presented within 15 s, the page cuts anyway. That cut is unmatched, which makes it a failure path.

**Failures** (DCL §2.5 "Stalls"):
- After 2 s with no new frame, the page hard-cuts to the still with the latest stored mask. This cut is unmatched, which is acceptable only for a fault.
- **Live comes back only through the entry handshake** (re-anchor, then a cut at `anchor_pts`), never by cutting straight back into a running stream.
- After 3 fallbacks in 10 min the screen reports `failed` and stays in art.
- Failure cuts and live-edge jumps count as flashes (§3.9 point 6).

**Added to the DCL by bd at the room's request (13 Oct):**
- The server also holds base changes, from `starting` to the entry ack, through a re-anchor, and from `leaving` to the exit ack. A publish in those windows updates only the server's base. The page starts no still-to-still transition until the ack, and on exit a newer base arrives after the ack as an ordinary art change (§2.5).
- Mask values written to a hidden art stack, or at any cut, are committed with `transition: none` before the cut frame (§2.6).
- Every anchor-match case runs at full brightness and at brightness ≈0.3 / warm_alpha ≈0.05 (§6 item 11).

**TV settings** (picture settings) are never written in any transition (§2.2). The wake to HDMI completes at least 60 s before the anchor handshake.

**Acceptance:**
- An on-glass capture of the still against the anchor frame, at full brightness and at a non-neutral mask. It needs `grim` under sway, which means a package install on the kiosk (an owner-approved task).
- Geometry must be identical and colour matched (primaries, transfer, matrix and range tags against Chrome's sRGB `<img>`).
- The 4:2:0 compression softening is accepted.

K5 and K6 below carry the server and renderer side.

### 3.4 Contracts

Every contract has one owner, a schema, a golden example and a test double:
- **Within a major version, changes are additive only**, logged in `docs/contracts/CHANGELOG.md` and acknowledged by consumers.
- **A breaking change needs a new major version and the owner's OK.**
- **Freezes:** K3, K5 and K6 have formal freezes, because they can stall another session.

| # | Contract | Owner → consumers | v0 / freeze | Test double |
|---|---|---|---|---|
| K1 | Port register `docs/PORTS.md` | room → all | Thu 15 Oct / additive | TCP + UDP guard |
| K2 | Fixture patch ↔ Maestro profiles ↔ decoder | room → receiver, Hue, owner | Tue 20 Oct / after M2a | `artnet-synth`, recorded replays |
| K3 | Show-state schema and bus (ws :8701) | room → bd renderer, room UI | **Fri 16 Oct** / end of W1 | `show-synth`, `show-replay` |
| K4 | Room control API (:8700) | room → UI; later scheduler, Alexa | Wed 21 Oct / M3 | `fake-room` |
| K5 | Room claim of screens + DevicesPort (extends DCL/RP channels) | refactor → room | proposal Fri 23 Oct; cross-acked with K6 by Wed 28 Oct / before P5 step 2 code | `fake-screens` |
| K6 | Stream session with show source and the DCL §2.5 calls (extends RP §4.6) | bd → sm-web reconcile | Thu 22 Oct / before the P6A build | two-process stopgap |
| K7 | Safety policy, limiter spec, flash test vectors | room; **owner signs** → renderer, Hue, Maestro config, refactor (mask path) | Fri 16 Oct / before G1a | IRIS harness |

**K1 v0** settles :8081 and :3001:
- :8081 stays bd's lab/stream server, which already has a portproxy rule, until the production stream service gets its own K1 port. It already serves static files, so it also serves the P0.5/V1 static files.
- :3001 is unused by bd; room allocates it in K1 v0.
- The kiosk reaches WSL only through TCP portproxy rules. Any new rule is an owner Windows task, booked in §6 step 8.
| K8 | Health: `/healthz`; room block in `/api/health` (after P2); Siren `room.*` | room + refactor → Deadman | Wed 21 Oct / M3 | canned `/healthz` |

**K2: patch.**

How the patch is kept in step with Maestro:
- **Built by hand:** Maestro's profiles, stage and shows are made once in its UI. There is no published JSON schema (r3 §4). The exports are versioned under `room_service/maestro/` and re-exported after every firmware update.
- **Our side:** `config/room/patch.yaml` holds the universe/Port-Address and, for each fixture, `{id, group, start, channels[{offset, attr, field}]}`.
- **Commissioning probe:** a "Fixture Default" snapshot sets known bytes, and the receiver asserts them. This catches the universe 0/1 off-by-one.
- **Drift:** later export-hash drift raises `room.patch_drift`. It warns only.

**Patch v0.** A test item, validated in M2a. Maestro allows exactly 4 groups.

| DMX | Group | Contents |
|---|---|---|
| — | G1 | Physical heads on **Internal DMX** (placeholders until purchase) |
| 1–64 | G2 north | `Room-Global` (ACTIVITY; a BRIGHTNESS echo of Global Brightness; a static MODE-ID set per cue) + 8 RGB "pixels" + a control fixture (PAN, TILT, ZOOM, GOBO steps → `effect`, PRISM → `variant`) |
| 65–128 | G3 south | 8 RGB pixels + a control fixture |
| 129–192 | G4 Hue | One RGB fixture per streamable colour light (≤10) |
| 193–208 | G2 / G3 (one each) | `SM Pulse`: a 1-channel dynamic DIMMER inside each screen's group, as a cheap pulse signal. It is a test item, and there is no fifth group for it. |

**Patch rules:**
- No dynamic DIMMER on RGB fixtures.
- No STROBE or SHUTTER channels.
- Maestro smoothing is 0; consumers ease.
- Strobe and blinder trigger enables are off on all four groups, G1 included (§3.9 point 4).
- White-only bulbs get no channels. They follow the G4 average over REST at ≤1 change/s.

**K3: show bus.** Endpoint `ws://<room>:8701/v1/show`.
- **Handshake:** the client sends `hello{v, role, client_id, token}`; the server replies `welcome{schema, rate_hz, stale_after_ms}`.
- **Rate:** a fixed **50 Hz** whatever the Art-Net cadence, so input that only sends keep-alives never makes the renderer flap. It drops to 10 Hz in `safe` or `black`.
- **Delivery:** latest value only. A client with a frame already buffered gets the newer one instead. Nothing is ever replayed.
- **Recording:** every session is recorded as JSONL for bd to replay.

```json
{"v":1,"type":"show.state","seq":118233,"t_ms":81234567.8,"mode":{"id":"disco","rev":42},"look":"live",
 "input":{"kind":"artnet","online":true,"age_ms":23,"pps":40.1},
 "safety":{"photosafe":true,"master":0.8,"blackout":false,"budget_hz":{"north":1,"south":1,"hue":1}},
 "global":{"activity":0.42,"brightness_echo":0.8,"mode_id_echo":3},
 "fixtures":{"screen.north":{"rgb":[0.25,0.45,0.9],"level":0.9,"pulse":0.4,"pan":0.2,"tilt":-0.1,
   "zoom":0.55,"effect":2,"variant":0,"pixels":[[0.2,0.4,0.9]]},"screen.south":{},
   "hue":{"lights":{"<uuid>":[0.8,0.5,0.2]}}}}
```

- **Values:** 0–1, except pan and tilt, which run −1…1.
- **Time:** `t_ms` is on the room host's monotonic clock and is never compared across hosts.
- **`look`:**
  - `live` while Art-Net is fresh;
  - `hold` after T_stale with no packet: 5 s until measured, then max(2 × measured keep-alive, 1.5 s);
  - `safe` (the mode's calm look) 3 s later;
  - `black` only when commanded.
- **Consumers:** the renderer holds 250 ms after the last bus frame, eases to safe after 2 s, and always runs its own output limiter. Hue holds 2 s, then fades over 5 s.
- **Scope:** the bus never carries screen-machine state. The mask state reaches the renderer only through K6.

**K4: room API.**
- `GET /healthz` (unauthenticated).
- `GET /api/v1/status`:
  - mode `{desired, observed, since, cause}`;
  - for each device (Maestro, Hue, Sonos, screens, TVs) `{desired, observed, observed_at, last_outcome}`;
  - safety state.
- `POST /api/v1/mode {mode: off|ambient|piano|disco|intro, idempotency_key}` returns `202 {transition_id}` with per-step outcomes. Maestro steps stay `unconfirmed` unless Art-Net readback confirms them.
- Also: `/blackout`, `/lighting/brightness`, `/sonos/{play|pause|volume}`, `/hue/reclaim`, and admin `/admin/{pair,devices,safety,patch,hold-devices}`.
- Every write and outcome is appended to `state/events.jsonl` with `cause{actor, device, corr}`.

**K5: the room claims screens** (extends the DCL ScreenStateStore and RP channels; refactor owns it).
- `PUT /api/screens/<id>/channel` with `Bearer ROOM_SERVICE_TOKEN`:
  `{owner:"room", mode:"live", source:{kind:"show", params:{fixture:"screen.north", scene_pack}}, profile:"hevc-2160p60|hevc-2160p30", min_rung:"hevc-2160p30", lease_s:120, idempotency_key, transition:{in:{effect,duration_s}, out:{effect,duration_s}}, policy:{reloads:"hold", device_power:"lock", overlays:"drop"}}`. The `overlays` value follows AV-D15.
- **Rank:** the claim ranks above api/alexa and below operator. Publishes still update `base` underneath. The base at release time is the return target.
- **Lease:** renewed every 30 s. Release with `DELETE …?owner=room`. `/glass` shows desired against applied. If `room_service` dies, the lease lapses after 120 s and the screen converges back to art.
- **ChannelService runs the DCL §2.5 handshake.**
  - Entry: hold the mask and overlays; take the page's anchor ack; create the session (K6); wait for `ready`; take `ack visible`; then `diverge`.
  - Exit: hold the mask; `converge`; republish `hold_pts`; take `ack visible`; then `DELETE`.
- **Reconcile is the only owner of sessions.** The room never calls sm-stream.
- **Builds on:** P5 step 2 (channels on the P4 store), the P4 hub (holds and acks) and P6B stage 1 (the page side). There is no interim polling path.
- **DevicesPort:**
  - `GET /api/screens/<id>/tv` reports IP Control readings: power, Art Mode, input and picture mode.
    - It also reports drift against the **baseline read over IP Control on 13 Oct**: FilmmakerMode, Warm2, BT.1886, colorSpace Auto, localDimming Standard, backlight 40, contrast 45, sharpness 10, and Game Mode as the owner set it. The baseline is stored in `config/tv_baseline.json`.
    - `FRAME_PRO_SETTINGS` now records this baseline in its "What the TVs are set to now" section (bd, 13 Oct), alongside its original recommendations.
  - `POST /api/screens/<id>/power {target: hdmi|art|standby}` returns `{observed}`. It is never called during a cut or while a claim is live (§2.2).
  - The wake is sent at least 60 s before the anchor handshake, in case it hot-plugs the kiosk.
  - Only sm-web talks to the TVs. Calls are serialised (one connection at a time), empty replies are retried, and timeouts are ≥5 s, because a poll has taken up to 3.5 s.
- **Gate:** the endpoint ships only after P3-reach, and only with the token.

**K6: stream session** (extends RP §4.6; bd owns it; names as in DCL §2.5).
- **Create:** `POST /api/sessions` with
  `{screen, source{kind:"show", params{fixture, scene_pack}}, anchor{image_url, fit, clip_t, mask{brightness, warm_hex, warm_alpha}, colour{primaries, transfer, matrix, range}}, encode{hevc, 3840x2160, fps 60|30, bitrate, gop, gpu}, transports ["ws-fmp4-mse"], ttl_s ≥120, idempotency_key}`.
- **Lifecycle:**
  - `ready` = the first IDR of the held anchor (hold limit 30 s);
  - `POST /api/sessions/{id}/diverge {transition}`, sent after the page's `ack visible {layer: live}`;
  - `POST /api/sessions/{id}/converge {target, mask, duration_s, effect}`: sm-stream reports the hold's first pts, which the server republishes as `exit.hold_pts`;
  - `POST /api/sessions/{id}/anchor` re-anchors after a stall and returns `anchor_pts`;
  - `DELETE` after `ack visible {layer: base}`.
- **Mode changes** are in-stream scene transitions, with no encoder restart.
- **Requirements:**
  - evenly paced frames at the session fps, with no missed deadlines in steady state (target: GPU-resident render→NVENC, with no raw pipe);
  - mask parity with the page: one shared compositing formula, golden-tested against page screenshots;
  - an output flash limiter;
  - optional lab stamping, off in production;
  - NVENC sessions counted against the per-system cap.
- **Security:**
  - Every control endpoint requires `Bearer STREAM_SERVICE_TOKEN`, a 0600 file held only by sm-web reconcile.
  - `anchor.image_url` must resolve to sm-web's own `/output/` paths: allowlisted, with no redirects and no other hosts.
  - Before K6 ships (M4), the owner confirms on the router that 8081 and 3001 are not forwarded from the WAN, and an external scan confirms it. This does not wait for P3-reach.
- **`/api/state` reports:** show age and look, limiter counts, pacing (late frames), and presented-frame stats.

### 3.5 Ports, and how Art-Net gets in (D22 → AV-D2)

| Port | Host | Use | Reach |
|---|---|---|---|
| 8700/tcp | WSL, reached through a Windows TCP portproxy | Room UI + API | LAN only (Windows Firewall scoped to the LAN). Never WAN-forwarded; never via Flask or ngrok. |
| 8701/tcp | WSL | Show bus (read-only) | localhost only (the renderer runs in the same WSL); renderer token |
| 6454/udp | Windows `artnet-relay` → WSL | Art-Net in; the relay answers ArtPoll | Windows Firewall: Maestro's IP only |
| outbound | WSL (NAT) | OSC 7672/udp; Hue 443/tcp + 2100/udp; Sonos 1400 (or 1443)/tcp, polled | Outbound UDP to LAN devices is untested; M2a (OSC) and M2b (Hue DTLS) prove it |
| 1516/tcp | TVs | IP Control, called by sm-web only | LAN |
| 5000, 8765 / 8081, 3001 | WSL | sm-web / sm-stream and lab (D16) | LAN only after P3-reach. 8081 and 3001 are confirmed not WAN-forwarded before M4 (K6). |

**Options for getting Art-Net in:**

| Option | Verdict |
|---|---|
| **Windows-side UDP relay** | **Recommended (AV-D2).** About 0.5 room-day to write and test, plus a 30-minute owner install. No hardware. The room shares the workstation's uptime, which the screens already do. |
| A dedicated Linux box, or the kiosk mini-PC as an independent unit | **Not now (AV-D8).** Either would keep room control up through workstation reboots. The box costs money; the mini-PC goes against the owner's "the mini-PC is meant to be dumb" rule, and bd notes it is dual-homed, so broadcast Art-Net would arrive twice. Revisit only if workstation reboots during shows become a problem. |
| WSL mirrored networking | **No.** It is a production network migration with an outage, needs Windows 11 22H2+ (build unknown), and inbound UDP is unproven (r5 §2–3). |
| Windows-native room service | **No.** It splits the runtime. |

**Until the relay is installed:**
- `artnet-synth` sends to 127.0.0.1.
- Maestro's "Add Node" also unicasts to a laptop recorder [A5] to capture real sessions.

### 3.6 Room control authentication (AV-D5)

**Pairing:**
- The first admin is minted only by a local CLI on the room host, never over the network.
- An admin issues a 6-digit code, valid for 10 minutes, single-use, and carrying the role it grants.
- The phone receives a random, named, revocable token in an `HttpOnly; SameSite=Strict` cookie. The server stores it hashed.
- POSTs require an `X-Room-Client` header and are rate-limited.

**Roles:**

| Role | Can do |
|---|---|
| household | modes, blackout, brightness, Sonos |
| admin | pairing, patch, safety, hold-devices |
| unpaired | `/pair` and `/healthz` only |

**Machine tokens** (0600 files):
- the renderer's bus token;
- `ROOM_SERVICE_TOKEN` for screen claims;
- later, modes-only tokens for a scheduler `room_mode` action and an Alexa intent.

**Transport:** plain HTTP on the LAN in v1, an accepted risk. A PWA needs LAN TLS and is deferred.

**Defence in depth:**
- **Guest isolation:** a guest SSID if the router supports it [A3].
- **Maestro's own AP:** change its password or disable it. Its OSC and web UI remain open on the LAN, a documented residual risk. With strobe and blinder trigger enables off on every group (§3.9 point 4), a stray `/triggers/strobe` does nothing.
- **Alexa:** only after RP's ingress and signature verification, as a deterministic intent for Off, Ambient and Piano.

### 3.7 Modes, Off, and the Hue hand-over

`config/room/modes.yaml` defines five modes:

| Mode | Notes |
|---|---|
| `off` | House normal. The idle state. |
| `ambient` | |
| `piano` | |
| `disco` | |
| `intro` | Later |

For each mode it sets:
- the Maestro show and Global Brightness;
- the Hue path: REST with long transitions for Ambient and Piano; Entertainment for Disco once gated;
- the Sonos action;
- whether screens are claimed, and with which scene pack and transitions;
- a **lease**: the mode lapses to `off` after 4 h without interaction, or at 01:30 (defaults to confirm).

**Enter a show:**
1. Record the desired mode.
2. **Snapshot Hue** to `state/hue_snapshot.json` and set `show_active`. The snapshot holds each light's on/off, dimming, xy or mirek and gradient, plus the active scene or smart scene.
3. Wake the TVs through DevicesPort, then claim the screens at least 60 s later (from M5).
4. Maestro: `/show/name`, `/show/play`, `/global/brightness`. **Never send `/live/*`.**
5. Hue:
   - over REST at ≤10 commands/s with explicit durations; or
   - over Entertainment at 50 Hz, with ≤10 colour lights and one stream per bridge.
6. Piano: pause the right Sonos group, resolving its coordinator on every call.
7. Report what each device actually did.

**Leave (`off`):**
1. Release the claims. The screens converge and cut back to art.
2. Maestro: Off cue, then `/global/brightness 0`.
3. Stop the Hue stream and wait ≥1 s.
4. Recall the snapshot scene, or set each light at ≤10/s, **including `on:false`**.
5. Verify no sooner than 1 s after the restore, then **re-assert once**. The bridge writes its own post-entertainment state 300–600 ms after an app's restore (r7 §6, low confidence; M1 confirms it on the installed firmware).
6. Clear `show_active`. Leave Sonos alone.

**Crash recovery:** at start-up, if `show_active` is set and the desired mode is `off`, restore the house. If the desired mode is a show, re-enter it. The bridge releases streamed lights by itself after about 10 s, but it does not restore them.

**Takeover:** if another app holds the bridge's stream, report `hue: taken_over`. Never fight it; offer one Reclaim.

**Owner set-up before shows:**
- one Entertainment area with ≤10 colour lights on the room's bridge;
- no Hue Sync running on the Frames;
- lights on wall switches or automations kept out of the area.

**Maestro is autonomous:** if the room service is down, the heads continue the current show. The physical fallback is Maestro's own UI plus a labelled mains isolator.

### 3.8 The light-sensor mask around shows

- **Live:** no mask (§3.3).
- **At the cuts:** the server holds `mask.set`, and the anchor carries the mask the page acked as on glass (DCL §2.5–2.6).
- **After a show:** the room hold (below) stays on for 60 s after the claim ends, then the art mask resumes with its normal smoothing.
- **Art mode while Hue is in a show** (with the screens still showing art; every mode until screens are claimable in M5): if the sensor sees Hue [A2], the art mask follows the room lighting.
  - Today that is slow, because of the 3 s poll and the EMA. It becomes fast once P4b ships: a 0.15 s snap, up to 20 Hz. Disco on Hue in the sensor's view could then flicker the screens.
  - M1 measures whether the sensor sees Hue, by logging lux through the Ambient and Disco steps.
  - The options (AV-D7):
    - (a) **accept it until P4b, and P4b ships with the room hold and the output slew limit** (recommended). SensorService holds brightness *and* warm values while the room's token-gated hold is renewed (TTL 120 s), and the room clears any hold at start-up when `show_active` is false. This needs no change to the legacy code before P4b;
    - (b) a brightness-only hold now, through today's `brightness-override`. It forces `warm_alpha 0` (`routes/display.py:124-136`), so the screens visibly lose their warm tint for the length of the show and snap to the live value afterwards. It also relies on an in-memory, unauthenticated endpoint. Only if the owner accepts both;
    - (c) no Hue shows while anyone is relying on the gallery.
- **The room never wakes TVs** through `events/throw`. That blocks a scheduler loop for up to 30 s and verifies nothing.

### 3.9 Safety

**Photosensitivity budget** (K7; r6 §9–10). At most **3 flashes in any 1 s across the whole field of view**.

- **v1 static split:** north ≤1/s, south ≤1/s, Hue ≤1/s. K7 v0 covers the screens and Hue.
- **The heads join at M7.** Before M7 the budget is re-split to include them (e.g. heads ≤1/s, both screens together ≤1/s, Hue ≤1/s), and the heads are film-tested.
- **Warnings:** above 2/s; never 2–3/s sustained for more than 5 s.

A flash is any of:
- **Luminance:** a pair of opposing luminance changes of ≥20 cd/m² where the darker state is below 160 cd/m².
  - It is computed from the Frames' luminance per code value, measured in FilmmakerMode at the production backlight. Live runs at full intensity in a dark room on 75-inch screens.
  - Until that is measured, use the stricter of ΔY ≥ 0.1 relative, or ΔY ≥ 0.05 (20 cd/m² at an assumed 400 cd/m² peak).
- **Red:** any transition to or from saturated red (R/(R+G+B) ≥ 0.8).
- **Cuts:** a hard cut, whatever its area. This includes the page's failure cuts and live-edge jumps.

**Patterns:**
- No more than 5 high-contrast light/dark stripe pairs over more than 25% of a screen if they oscillate, flash or reverse. Over 40% if they are stationary.
- The pixel strip is never drawn as alternating full-contrast bars.

**Renderer transitions** (in-stream crossfades, melts) count against the budget like any other content.

**Enforcement points:**
1. The show-state limiter in `room_service`.
2. **The renderer's output limiter** (bd). It works on downsampled luma and red. It is mandatory, because generative content and transition shaders can flash on their own.
3. The Hue adapter: ≤1 large transition per second, explicit durations, no on/off alternation.
4. **Maestro's configuration.** Photosafe here is a static configuration, not a runtime switch: the room can neither see nor limit Internal DMX, and no OSC command toggles trigger enables.
   - Strobe and blinder trigger enables are off on all four groups, G1 included.
   - No cue uses a Flash Bang, strobe or shutter FX palette, or a chase that can exceed 1 flash/s.
   - There is no strobe control in the room UI.
   - The configuration is exported, versioned, and changed only by a logged edit the owner signs.
5. **The art-mode mask path** (refactor, P4b). Brightness never reverses direction by ≥0.1 within 1 s. Overrides ramp over ≥1 s, at most one change per 5 s per screen. `maskon`, `maskoff` and `brightness-override` get a token before P4b ships; otherwise any LAN guest could strobe a screen.
6. **Page-side discontinuities.** Failure cuts, live-edge jumps and re-anchor cuts are bounded by DCL §2.5 (a 2 s stall rule; 3 fallbacks in 10 min → `failed`). The page counts them in `kiosk.state`. K7 caps them at 1 per 10 s per screen in steady state and alerts above that.

**Photosafe:**
- On by default, and re-armed at Off.
- Only an admin can override it, for at most 4 h, and the override is logged.

**Gates:**
- **G1a, before M2a's first music run on the real lights:**
  - the show-state limiter and the Hue REST limiter pass the K7 vectors on `fake-hue`;
  - a synthetic 10 Hz full-swing input gives ≤1 large transition/s on the real lights, filmed;
  - M1's restore tests have passed.
- **G1b, before any Entertainment stream (M2b):** in addition, the Entertainment limiter, the takeover test and the `kill -9` restore test pass on fakes.
- **G1c, before any show-driven frame reaches a TV (M4):**
  - the renderer limiter passes the K7 vectors;
  - IRIS passes offline on recorded NVENC output for every scene family and transition;
  - the Frames' luminance has been measured.
- **Acceptance:** IRIS on captured output, plus a filmed room test.

**Physical safety** (owner; spec §10, §12):
- **Beams:** pan/tilt limits keep them off seated eye level.
- **Rig:** plinths, cabling and cooling checked; a labelled mains isolator.
- **Blackout:**
  - sends `/global/brightness 0`, which is expected to work in any Maestro state (M2a proves it while stopped), and `/triggers/blackout`, which works only while a show is playing;
  - drives Hue and the screens' level to 0;
  - is then verified by readback.
- **The gasolier:** haze, the mirror-ball motor **and hanging the ball itself** stay gated. Nothing is hung from the gasolier until a competent installer approves the suspension and a secondary safety.
- **Wireless DMX:** the receivers' loss-of-signal behaviour is set (fade to dark recommended) and tested by switching the transmitter off (M7).

### 3.10 Failure behaviour (additions to the spec's §12 matrix)

| Failure | Response |
|---|---|
| Workstation or WSL down (e.g. Windows Update) | Heads continue on Maestro. Room control, Hue shows and the screens stop: the bridge frees streamed Hue lights after about 10 s, and the screens hard-cut to the still on stream loss. At boot, the relay task and `room_service` start (P2 boot task), and `room_service` restores the house from its Hue snapshot. The phone UI is unreachable meanwhile. |
| `room_service` down | systemd restarts it and the restore runs. The bridge frees streamed lights after about 10 s. The renderer goes to its safe look after 2 s. The lease lapses after 120 s, and the screens converge back to art. The room writes nothing else into screen state: the P4b room hold has a TTL and is cleared at start-up. |
| sm-stream or encoder crash | Hard cut to the still; reconcile retries through the entry handshake (DCL §2.7). |
| Powerline fails over to Wi-Fi | Live hard-cuts to the still (DCL §2.7). A room claim never re-enters below its 4K30 minimum rung (AV-D12), because a 1080p anchor would visibly soften at the cut, so the screen stays in art until the wired path has been healthy for 60 s. Hue, Maestro and the room are unaffected. Alert `kiosk.link_failover`. |
| Art-Net stale (including a dead relay) | Hold, then the calm look inside the stream; Hue fades to the mode fallback; `room.artnet_stale`. |
| Hue down or taken over | Reported, never shown as OK. Reclaim offered. |
| OSC or Sonos failure | The step reports `unconfirmed` or `failed`. The mode continues, and the Sonos app keeps working. |
| TV control fails (Wi-Fi) | DevicesPort retries an empty reply, verifies by IP Control readback, and reports failure. |
| Chrome death or TV unplug | The sway kiosk self-recovers. The page re-attaches from the claim with a new anchor. |
| Mask fetch fails (art mode) | Hold the last mask (P0.5). |

### 3.11 Observability

- **Room service:**
  - endpoints: `/healthz`, `/api/v1/status`;
  - `events.jsonl`, rotated after 14 days;
  - counters: Art-Net packets/s and age, Hue frames/s, limiter interventions, transition outcomes.
- **Alerts v1 (M3):** a second Deadman probe of the room's `/healthz`. This changes the kiosk-side Deadman, so it needs the owner's OK.
- **Alerts v2 (after P2):**
  - a cached `room` block in `/api/health`;
  - Siren `room.*` keys (`artnet_stale`, `hue_takeover`, `hue_restore_failed`, `osc_send_failed`, `sonos_unreachable`, `mode_transition_failed`, `service_down`), once Siren reads its paths from env;
  - sm-web adds `tv.settings_drift` from IP Control against `config/tv_baseline.json` (K5), because firmware updates reset picture settings.

---

## 4. Workstreams, ownership and rules

| Workstream | Session | Scope |
|---|---|---|
| W0 Decisions and devices | **owner** | §7 decisions; Hue link button; Maestro hands-on; router reservations; audio purchases; installing the Windows relay and portproxy; TV/kiosk approvals; the Frame luminance measurement; physical safety; signing K7 |
| W1 Room and virtual lighting | **room** (screen-machine-10) | `room_service`; K1–K4, K7, K8 (room side); adapters; supervisor; limiter; the Windows `artnet-relay` script (the owner installs it); programme coordination |
| W2 Screens and platform | **refactor** (screen-machine-12) | RP as re-scoped in §2.1; K5 including DevicesPort over IP Control; all changes to `sm/`, `routes/`, `src/`, `app.py`, `utils/` |
| W3 Renderer, streaming and kiosk | **bd** | `stream_service/`; ShowSource; anchor in/out; transition shaders; pacing; renderer limiter + IRIS; K6; DCL page design; kiosk (sway) infrastructure |

**Directory ownership** (everyone else treats these as read-only):

| Owner | Paths |
|---|---|
| room | `room_service/`, `deploy/room/`, `config/room/` (non-secret), `schema/{show_state,room_api}.*`, `docs/AV_PROGRAMME.md`, `docs/PORTS.md`, `docs/contracts/`, `docs/av/`, `tools/room/` |
| bd | `experiments/stream-latency/`, `stream_service/`, `schema/stream-api.*`, `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md`, `docs/KIOSK_INFRASTRUCTURE_NOTES.md`, `docs/FRAME_PRO_SETTINGS_2026-10.md`, and all of `media-server/` (the kiosk launchers including `kiosk-wayland.sh`, the units, network scripts and `deadman.py`). Refactor's kiosk URL edit (P3) and respawn limits (P5 step 3) go to bd as requests. |
| refactor | `docs/REFACTOR_PLAN.md`, `sm/`, `routes/` (code), `src/`, `app.py`, `overlay_ws_server.py`, `utils/`, `tests/`, `deploy/` (sm). bd supplies the live player for review. |
| screen-machine-85 (closed Game Mode spike) | Copies `ipctl.py` into `experiments/ipcontrol/` and hands its IP Control soak results to refactor (for DevicesPort), then stops. |
| Production processes only (never edited or staged by hand) | `routes/scheduler/*.json`, `scheduler/_vars.json`, `output/`, `logs/`, tooling-written logs |
| owner only | `.env`, tokens, Windows scripts, router, physical device tasks |

**Rules for this production checkout:**
1. Never run `git stash`, `checkout`, `reset`, `pull`, `clean` or `add -A`.
   - Commit only your own paths, with the owner's OK, in pathspec form: `git commit -m … -- <your paths>`. Other sessions' entries are already staged in the index (the `kiosk.service` rename), and a plain `git add <mine> && git commit` would sweep them in.
2. Never stop or `pkill` another session's process. No broad `pkill -f`. New services start as `python -m …`.
3. No pytest from the root before P1's guard, and no `import app` before P2. Room tests run under a guard that blocks non-loopback TCP **and UDP**.
4. **No device writes** (Art-Net, OSC, Hue, Sonos, TVs) from tests or ad-hoc shells.
   - Real output goes only through `room_service`, with `ROOM_REAL_DEVICES=1` (fakes are the default), in owner windows, under an admin `hold-devices` lease.
   - Never more than one Hue streamer per bridge.
5. **Device changes need the owner's OK.** That covers the production Chromes, kiosk packages and services, the TVs, Windows, the router and Maestro.
   - Sessions run kiosk commands over SSH themselves, in a quiet window agreed with bd.
   - Physical and Windows tasks are the owner's: the Hue link button, Maestro's front panel and re-pointing, the relay install and portproxy rules.
   - **Screen-touching windows go on one shared calendar** (`docs/av/windows.md`, kept by room), one at a time: V1 takeovers, real-light shows while the sensor sees them, the P2 cutover, the WSL-restart drill, IP Control setter tests and the M2b 24 h soak.
6. No unregistered ports. No room endpoint on Flask, ngrok or a WAN forward.
7. No secrets in git. Use 0600 files outside the repo, such as the existing `~/.config/screen-machine/ipcontrol/`.
8. Cross-boundary work goes as a request to the owning session. RP changes go to the refactor session; DCL changes to bd.
9. Services run from release directories under systemd. **No production show is ever served from the lab venv** (gate G2).
10. Safety settings change only with the owner's sign-off, and every change is logged. This covers the flash budget, Maestro triggers and pan/tilt limits.

**Test doubles** (all by room unless stated):
- `artnet-synth`, covering keep-alive-only, loss, stopped, sequence reset and off-by-one;
- JSONL recordings and a replayer;
- `show-synth` and `show-replay`, to bd on Fri 16 Oct;
- `fake-hue`, covering 429s, external stop and the post-stream rewrite;
- `osc-sink`;
- `fake-sonos`, including a UPnP-off 403;
- `fake-screens`;
- the IRIS harness (bd);
- the pytest TCP+UDP guard (refactor, P1).

---

## 5. Milestones

**Weeks:**

| Week | Dates |
|---|---|
| W0 | 14–16 Oct |
| W1 | 19–23 Oct |
| W2 | 26–30 Oct |
| W3 | 2–6 Nov |
| W4 | 9–13 Nov |
| W5 | 16–20 Nov |
| W6 | 23–27 Nov |
| W7 | 30 Nov–4 Dec |
| W8–W9 | 7–18 Dec |
| W10–W11 | 21 Dec–1 Jan (holidays) |
| W12 | 4–8 Jan |

Effort is in session-days (RP's "owner-days"); the owner column is the owner's own time. Estimates only.

| Milestone | Visible result | Target | room | bd | refactor | owner | Needs |
|---|---|---|---|---|---|---|---|
| **M0 Safe to build** | Decisions taken; inventories done; guards in place; kiosk infrastructure and planning docs committed | Mon 19 Oct | 2.5–3 | 0.5 | 0.5 | 1–1.5 | AV-D1–D6; Hue pairing; Maestro window |
| **M1 Hue follows the room** (synthetic, REST) | Off/Ambient/Disco from the workstation browser; Off restores the house exactly | **Thu 22 Oct** | 3.5–4.5 | — | — | 0.5 | M0; an owner window on the real lights (the owner accepts that the art mask may follow them) |
| **M2 Maestro drives Hue** | **a:** music → Maestro → Hue over REST. **b:** Disco on Entertainment. | **a: Fri 30 Oct; b: Wed 4 Nov** | 5.5–7.5 | — | — | 1–1.5 | Windows relay (AV-D2); patch built; K7 signed; **G1a** for a, **G1b** for b |
| **M2c Audio chain** | Sonos Connect (line out Fixed, grouped with the room's playback group, delay checked), piano and mic into the mixer, then into Maestro. Input calibrated on Maestro's level display. Continuous response to quiet piano, speech and Sonos music, with no source selection (spec Phase 1 steps 1 and 4). | By Fri 13 Nov | 0.5 | — | — | 1 | Audio procurement (§7) |
| **M3 Room by phone** | Paired phone: modes, blackout, brightness, Sonos, status; lease auto-off | Fri 13 Nov | 4.5–6 | — | — | 0.5–1 | The :8700 portproxy; AV-D5, AV-D10, AV-D11; M2c for Live Piano |
| **M4 A screen joins a show.** Vehicle V1: one production Chrome is driven over CDP to the lab player, then restored, in an owner window. | One TV: a show-driven scene from the real bus, evenly paced, while Hue follows Maestro. Anchor parity is proven on the renderer side. | Fri 13 Nov | 0.5–1 | 8–11 | — | 0.5–1 | K3/K6; real bus (M2a); **G1c**; 8081/3001 WAN check |
| **M5 Screens claimable** (devtest) | A room claim runs the full DCL §2.5 handshake in the production page on devtest; WAN closed; reloads held; verified TV wake | ≈Fri 4 Dec (range Tue 24 Nov – Thu 10 Dec) | — | — | ≈30–41 | 2–3 | AV-D6; the P1/P2 gate; P4 hub + shadow; P5 step 2; P3-reach, including P4 step 7 / D5 before 8765 closes; K6 implemented (M4); AV-D12, AV-D15 |
| **M6 Screens as fixtures** (production) | Both TVs run independent compositions in shows; failure drills pass | Mid-Dec at the earliest, then 7 evenings (likely into January) | 2–3 | 4–7 | 4–6 | 2 | M2b, M4, M5; P6A in production; K7; the shadow exit (RP §3.9 item 6) |
| M7 Physical heads | Spec Phase 0.3–1: the site survey (corner power, sightlines, floor mounting, gasolier structure) comes before any purchase | After the audition | 1–2 | — | — | 2–4 | Showroom audition; procurement; the budget re-split (§3.9) |
| M8 Hardening | 7 days unattended; Siren v2; runbook; restore drill; T8 | After M6 | 1–2 | 1–2 | 2–4 | 1 | — |

The refactor effort for M5 covers RP/DCL's own path to the end of P6B stage 1, plus the room's additions:
- RP/DCL: P0.5, P1, P2, the P4 hub core and shadow acks, P4b, P5 (overlays, content and channels, supervision), P3 (front and reach) and P6B stage 1;
- the room's additions (≈2–3.5): K5 on channels, and the T2/IP Control subset (DevicesPort).

bd's K6 work for M5 is counted in M4.

**Acceptance tests:**

**M0:**
- The pending `routes/admin_api.py` diff is reviewed, and the kiosk infrastructure is committed by explicit path, in pathspec form.
- The planning docs are committed by their owners: RP (refactor); the DCL, the KIOSK notes and FRAME_PRO (bd); this file and `docs/av/` (room).
- Chrome is held (D7).
- The Hue inventory is written, with no secrets in git.
- Maestro's firmware version is known, and its backups and exports are versioned.
- A test proves the guard blocks non-loopback TCP and UDP.
- K1, K3 and K7 v0 are acknowledged.

**M1** (owner present):
- Ten Off→Ambient→Off cycles return every light to its snapshot within 5 s, **including lights that were off**, verified by GET.
- After `kill -9`, the house is restored within 15 s.
- A change made in the Hue app mid-show is reported, and the Hue app and automations behave normally after Off.
- REST traffic stays at or below 10 commands/s.
- No unconfirmed step is shown as OK.
- No Windows or router change was needed.
- The lux log shows whether the sensor sees Hue. This is the input to AV-D7.
- The bridge's own post-restore write (r7 §6) is confirmed or ruled out on the installed firmware.

**M2a** (after G1a):
- The patch probe passes.
- Measured: whether Maestro needs an ArtPoll reply, plus 1 h of cadence, keep-alive and jitter data. T_stale is set from the measurement.
- The OSC group-index base is resolved.
- Readback works, or is documented as unavailable.
- Maestro's output is documented for stopped, paused, silent and the 2 s cue freeze.
- A 2 h music soak runs with no stale flapping.
- Blackout works while Maestro is stopped.
- The Maestro JSON is committed.

**M2b** (after G1b):
- Art-Net→Hue latency ≤150 ms at p95 (filmed at 240 fps).
- A synthetic 10 Hz strobe produces ≤1 flash/s on the Entertainment path.
- When the Hue app stops our stream, `taken_over` appears within 3 s and we do not fight back.
- After `kill -9`, the lights are restored.
- A 24 h soak passes.

**M3:**
- Live Piano from a paired phone pauses the **correct** Sonos group (observed within 3 s), loads the Piano show, and Maestro responds to the piano through the audio chain (M2c).
- An unpaired POST returns 401.
- The Sonos and Hue apps are unaffected.
- The lease auto-off works.
- RSS stays flat over 48 h.
- Deadman alerts on a stopped `room_service`.

**M4** (one TV per owner window, on V1; G1c passed before the first frame reaches a TV):
- A show-driven scene at 4K60 (or 4K30 if pacing is not yet solved) presents ≥99% of frames over 30 min with ≤1 stall per 10 min. The counts come from rVFC reconciled with `getVideoPlaybackQuality()`, since `fb_probe.py` is X11-only.
- **Renderer-side anchor parity:** the renderer's held anchor matches CDP screenshots of the page's still at full brightness and at a non-neutral mask (bd's shared formula and goldens). The on-glass matched cut in the production page is M5.
- When the bus dies, the renderer reaches its safe look within 2 s.
- Live 4K30 is re-run under sway on both TVs, one window each.
- The trail behind the heads is filmed.
- Two independent scenes on both TVs at once move to M6.

**M5:**
- The RP P2 and P3 exit criteria are met, and WAN 8765 is closed after D5.
- On devtest (V1), a room claim runs the full DCL §2.5 handshake: a matched cut-in, diverge, converge and a matched cut-out. The **grim capture** shows no visible change at either cut, at full brightness and at a non-neutral mask.
- A forced stall hard-cuts to the still, and live returns only through a re-anchor.
- A claim without the token is refused. K6 refuses a session without its token or with a foreign `image_url`.
- An external scan shows nothing room-related.
- No reload fires during a claim.
- A wake from Art Mode lands on HDMI at least 60 s before the handshake, as observed via IP Control, or reports failure.
- **With no claim, each screen's 24 h behaviour matches its baseline.**

**M6:**
- A 4 h unattended Ambient→Disco→Piano→Off run with no black or white flash outside commanded content.
- Drills pass:
  - kill sm-stream, `room_service` and Chrome;
  - pull the powerline (expected: a hard cut to the still, then art until the wired path is healthy, §3.10);
  - stop the Hue stream from the Hue app;
  - restart WSL in an owner window.
- Overlays follow AV-D15.
- IRIS passes on recorded show output.
- Then 7 evenings of normal use.

**M7:**
- The site survey comes before any purchase. Nothing is hung from the gasolier without the installer's approval.
- The heads work with the workstation and the LAN both off.
- The wireless receivers' loss-of-signal behaviour is set and tested.
- The heads are film-tested against the re-split flash budget.
- A cheap DMX test fixture can prove concurrent Internal + Art-Net output early.

**Critical paths:**
- **Value path:** M0 → M1 → [Windows relay] → Maestro patched → M2 → M2c → M3, about **16.5–21.5 room-days**. On it: the owner's Hue pairing, the Maestro window, the relay install, and buying the audio chain.
- **Screens path (to M6):** RP/DCL's own critical path, P0.5 → P1 → P2 → P4 hub → shadow acks (7 days) → P4b → P5 (with K5's room additions) → P6B stage 1 → stage 2 with the room claim. P3 and the DevicesPort subset run alongside.
  - That is about **34.5–47 refactor-days**. The refactor session is a single session, so this is the long pole.
  - bd's renderer work (M4) and P6A run in parallel and are not on the long pole.
  - Light push is on glass at the shadow exit, around 10–18 Nov.
- **The physical heads are on neither path.**

**What can start this week** (only the DMX heads are missing):

| Who | Work |
|---|---|
| room | Hue pairing and read-only inventory; `room_service` skeleton with guard and fakes; K1, K3, K7 v0; synthetic Art-Net → decoder → show-state; Hue REST adapter on fakes |
| owner | Maestro backup, export and firmware (step 8). Commissioning the virtual fixtures waits for K2 v0 (Tue 20). Its Patterns need audio, so a phone feeds the RCA input until M2c. |
| bd | ShowSource on `show-synth`; anchor hold, diverge and converge on a still; the two-process two-stream stopgap (ports per K1); the pacing fix |
| refactor | Fold §2.1 into RP and update D24; P0.5; P1 with the UDP guard |

---

## 6. Next 10 working days (Wed 14 – Tue 27 Oct)

1. **Owner, Wed 14 (about 30 min):** decide AV-D1 to AV-D6 (§7).
2. **refactor + bd, Wed 14:**
   - review the uncommitted `routes/admin_api.py` diff (it goes live at the next Flask restart);
   - bd commits the kiosk infrastructure in pathspec form, with the owner's OK;
   - the Chrome hold (D7), with the owner's OK: bd runs it over SSH.
3. **Owner + room, Wed 14–Thu 15:**
   - find the room's bridge;
   - the owner presses the link button, and room pairs with `generateclientkey`, storing the keys 0600 outside git;
   - write `docs/av/hue_inventory.md`: model, gamut, gradient or white-only per light; switches and automations; scenes; Entertainment areas; any active streamer.
4. **room, Wed 14–Fri 16:** build the `room_service` skeleton:
   - its own venv: aiohttp, aiohue, python-osc 1.10.2, soco 0.31.5, and hue-entertainment 0.1.2 pinned behind our own adapter interface;
   - the guard, the test doubles, `/healthz`, the event log, the deploy script and the unit file (installing it needs the owner's OK);
   - no device writes.
5. **room, Thu 15:**
   - publish `docs/PORTS.md` (K1 v0, including the 8081/3001 allocation) and the contracts index;
   - start `docs/av/windows.md`, the shared calendar of screen-touching windows;
   - send the §2.1 requests to refactor, and the §3.3 DCL additions and the K6 notes to bd. These are sent as proposals pending AV-D1 and AV-D6;
   - ask screen-machine-85 to hand over `ipctl.py` and its soak results (§4);
   - every session acknowledges.
6. **room, Fri 16:**
   - to bd: the K3 v0 schema, goldens, `show-synth` and `show-replay`;
   - to the owner: K7 v0 to sign.
7. **Owner, Wed 14–Fri 16: orders:**
   - **the audio chain** (§7 procurement), so M2c can land by Fri 13 Nov;
   - optionally, a small switch at the room's powerline adapter for Maestro, and a cheap DMX test fixture.
8. **Owner window, Thu 15–Mon 19 (1–2 h):**
   - **Maestro:**
     - read the firmware version;
     - **export the full backup and the stage/show/profile JSON**;
     - update to 1.5.1 if needed, then power-cycle;
     - set Ethernet to DHCP;
     - secure or disable its AP.
   - **Router:**
     - DHCP reservations at each device's **current** address (no address changes): Maestro, both bridges, the Sonos players, the TVs, the kiosk (.10/.92) and the workstation;
     - check whether guest isolation is available;
     - confirm which ports are forwarded from the WAN, including 8081 and 3001.
   - **Windows:** any new portproxy rule that K1 v0 needs.
   - **Sonos:** read the household's UPnP, Authentication and Guest-access settings.
   - **TVs: no change.** Game Mode stays Auto, which never engages under sway (spike §10). If the owner still wants Off, do one TV first in a quiet hour. bd then checks `swaymsg -t get_outputs` for 3840x2160@60 and IP Control's pictureMode before the second TV, and reverts to Auto if 4K60 is lost.
9. **refactor, from Wed 14:**
   - fold §2.1 into RP and update D24 and §3.5;
   - P0.5 once D27 items 4–5 are approved;
   - P1 with the UDP guard;
   - the K5 proposal, including DevicesPort over IP Control, by Fri 23.
10. **bd, from Wed 14:**
    - the §3.3 additions to DCL §2.5; update FRAME_PRO_SETTINGS to the IP Control baseline;
    - ShowSource with anchor hold, diverge and converge;
    - even pacing: try PyNvVideoCodec GPU-resident encode, or report the need for AV-D9;
    - two independent streams from two processes (4060 and P4000), starting with a **pacing test on the P4000**, which no lab run has used yet;
    - the K6 draft by Thu 22, once the owner has said go (bd won't start K6 before then);
    - in the first V1 window, re-run live 4K30 under sway.
11. **Owner, Mon 19 (15 min):** AV-D10 (Hue scope), with the inventory in hand.
12. **room, Mon 19–Wed 21:**
    - the Hue REST adapter: snapshot, recall, restore, verify and re-assert, crash recovery, eventstream, takeover detection, rate and transition limiting;
    - the mode machine;
    - UI v0 on localhost;
    - the synthetic Art-Net → Hue chain.
13. **room + owner, Tue 20–Thu 22:**
    - room delivers K2 patch v0 with probe values;
    - the owner builds the profiles and stage in Maestro, with strobe and blinder trigger enables off on all four groups;
    - capture runs go via "Add Node" to the laptop [A5].
14. **Owner window, Thu 22:** **M1 demo and acceptance** on the real Hue lights, logged in `windows.md`.
15. **Relay, Thu 22–Mon 26:**
    - **room:** write `artnet-relay` and test it against `artnet-synth`, which sends from Windows to WSL;
    - **owner, 30 min:**
      - install it as a scheduled task at boot;
      - add the inbound firewall rule (UDP 6454, Maestro's IP only) and the :8700 TCP portproxy with its LAN-scoped rule;
      - re-point Maestro's Art-Net node at the workstation's LAN IP.
16. **Owner, Fri 23 (15 min):** AV-D9 and AV-D14, with bd's pacing report.
17. **room, Mon 26–Tue 27:**
    - live Art-Net: the ArtPoll test, a 1 h cadence capture, the patch probe and T_stale;
    - the OSC adapter with readback tests;
    - the G1a tests on `fake-hue`.
    - M2a target: Fri 30 Oct.
18. **Owner, Tue 27:** decision batch 2 (AV-D7 with M1's lux log, AV-D11).

---

## 7. Owner decisions

RP decisions are referred to by their D-number. New ones are numbered AV-D.

**Decide this week (by Fri 16 Oct):**

| # | Decision | Options | Recommendation |
|---|---|---|---|
| AV-D1 | Programme shape, assumptions [A1]–[A5] | Keep RP as the programme · **the room as the platform, with a separate `room_service`** | Adopt. This overturns RP's "no microservices beyond the stream service", for the room only. |
| AV-D2 | Room host (resolves D22) | **WSL + Windows UDP relay** · a dedicated Linux box · the kiosk mini-PC · mirrored mode | **WSL on the workstation, with the Windows `artnet-relay` (§3.1).** No hardware. Room control stops while the workstation reboots; the heads carry on. |
| AV-D3 | Pending platform approvals | — | Approve: P0.5 (D27 items 4–5); the Chrome hold (D7); explicit-path commits of the kiosk infrastructure after the `admin_api.py` review. |
| AV-D4 | Hue access | — | Pair the room's bridge. Run REST tests in owner windows. Hue Sync off on the Frames if present. |
| AV-D5 | Photosensitivity and room auth (K7, §3.6) | Spec wording · **joint budget + pairing** | ≤3 flashes/s across the field of view, split 1/1/1 (heads re-split before M7); Maestro strobe and blinder trigger enables off on all groups; a slew limit and a token on the art-mask path; admin-only override ≤4 h, logged, re-armed at Off; gates G1a–c; paired phones with roles. |
| AV-D6 | Screens sequencing: the 13 Oct "WS-powered control layer" ask against screens in shows | **(a) RP/DCL order unchanged** (hub → shadow → P4b → P5 → live), with the room's K5 and DevicesPort folded into P5 step 2 and P6B · (b′) build P6B stage 1 on devtest during the 7-day shadow window: the matched cut on devtest about a week sooner, light push about 3–4 days later · (c) a fourth session takes the P4 hub now | **(a).** The draft recommended "channels first". That no longer works: the revised DCL handshake needs the hub's retained `mask.set`/`overlay.show` holds and the page's anchor acks, and channels sit on the P4 store, so pulling channels forward saves nothing. (a) keeps light push on glass at about 10–18 Nov, matches what refactor and bd already plan, and keeps RP's 7-day shadow gate before production claims. Screens become claimable on devtest at about 24 Nov–10 Dec. The room's value path does not depend on this choice. (c) risks collisions in `sm/`. |

**Decide by date:**

| # | Decision | Options → recommendation | By |
|---|---|---|---|
| AV-D7 | Art mask while Hue runs a show and the screens are still in art mode (§3.8) | **Accept until P4b; P4b ships with the room hold (brightness + warm, TTL, token) and the slew limit** · a brightness-only hold now via `brightness-override` (it strips the warm tint: `warm_alpha 0`, `display.py:124-136`) · no Hue shows while the gallery is relied on. Matters only if the sensor sees Hue, which M1 measures. | Tue 27 Oct |
| AV-D8 | A dedicated room host later | **Not now** · a small Linux box · the kiosk mini-PC as an independent unit (against the "dumb mini-PC" rule; dual-homed, so it needs de-duplication). Only if workstation reboots during shows become a real problem. | After M6 |
| AV-D9 | Even 4K60 pacing (relates to D21, the NVIDIA driver) | **PyNvVideoCodec GPU-resident encode first** (+~3 frames latency, acceptable) · driver ≥570 for PyAV/FFmpeg 8 zero-copy · stay at 4K30 | Fri 23 Oct (bd reports) |
| AV-D10 | Hue scope | **≤10 unswitched colour lights on one bridge streamed; white bulbs on slow REST; one fixture per light** | Tue 20 Oct |
| AV-D11 | Sonos path | **SoCo if UPnP stays on** (the advisory is fixed in S2 85.0-66270) · otherwise aiosonos behind the same adapter. Name the target group. | Tue 27 Oct |
| AV-D12 | Room claim (extends D12/D13/D27) | **Room above api/alexa, below operator; reloads held; TV power locked; transitions from the scene pack; minimum rung 4K30** (below it the screen stays in art, because a lower-resolution anchor softens visibly at the cut). Overlays: AV-D15. | Fri 6 Nov |
| AV-D15 | Overlays and the info panel while a screen is live (DCL §5 item 20, open, no default) | **(b) not shown during live: dropped and logged; normal overlays resume in art mode after release** (recommended for v1) · (a) the renderer draws them (a later K6 extension) · (c) by overlay type. Under every option the page draws nothing over the video; until decided it counts `overlays_suppressed`. | Fri 6 Nov |
| AV-D13 | TV control path | **IP Control for reads** (proven) · IP Control setters for power/Art Mode (owner-approved test) · samsungtvws with the T2 fixes · powerline to the One Connect boxes if Wi-Fi drops persist | W4 |
| AV-D14 | Renderer engine | **In-WSL CUDA inside the stream service** · TouchDesigner (Windows-native, would need a frame hand-off); revisit after M6 | Fri 23 Oct |

**Defaults that stand unless you object:**
- **Game Mode:** never used (closed in spike §10); Auto never engages under sway. Software never writes TV picture settings.
- **Idle state:** `off` (gallery).
- **Show lease:** 4 h, or until 01:30.
- **Virtual fixtures:**
  - one per Hue light;
  - each screen as an 8-pixel strip plus a control fixture;
  - a Room-Global readback fixture;
  - SM Pulse inside each screen's group, as a test item.
- **Maestro control:** Show mode with cues; never `/live/*`; blackout via `/global/brightness 0`.
- **Profiles:** live 4K60 once pacing is solved; 4K30 is the fallback rung. Art mode unchanged.
- **Not before M6:** room voice control, hanging the mirror ball and its motor, haze, audio DSP and beat-locked visuals, the AI director.
- **Procurement:**
  - **the audio chain (M2c):**
    - a mixer with a −10 dBV RCA output, or an MG06 plus a +4 dBu → −10 dBV attenuator, or a USB audio interface;
    - a microphone, stand and XLR cable;
    - a piano line-out cable (check the piano's outputs);
  - a **Gen 2** Sonos Connect;
  - no computers;
  - heads only after the site survey and the showroom audition;
  - the wireless DMX protocol chosen after the fixtures.
- **A Frame luminance measurement** (photometer or a calibrated phone, at production settings), in an owner window before M4. It is part of G1c.

---

## 8. Risks, shortcuts and what we're not doing

### 8.1 Risks

| Risk (likelihood / impact) | Mitigation |
|---|---|
| Sessions collide in the production checkout, or a git operation loses deployed work (H/H) | §4 rules; kiosk infrastructure committed this week; release directories; P2 early |
| The pending `admin_api.py` diff goes live unreviewed (M/M) | Review it before any Flask restart (step 2) |
| Maestro won't send Art-Net without an ArtPoll subscriber; its cadence is unknown (M/H) | Receiver answers ArtPoll; the relay sends a static reply; early capture; staleness taken from measurement |
| Maestro quirks: `/live/*` exits Show mode, triggers dead while stopped, 2 s cue freeze, DMX lost after an update (H/M) | Cues; Global Brightness; readback; a power cycle in the update runbook |
| Maestro's AP and OSC are unauthenticated (M/H) | Secure or disable the AP; guest isolation; documented residual risk |
| Hue taken over or not restored; the Python DTLS library is young (H/M) | Snapshot → wait → restore → verify → re-assert; detect, never fight; our own adapter interface; REST fallback; artnet-bridge as plan B (+1 d) |
| Visible step at the art↔live cut: colour, geometry or mask mismatch (M/H) | DCL §2.5: server-held mask, an anchor built from the page's acked on-glass values, one shared compositing formula with goldens; native scale; high-quality anchor I-frame; grim acceptance at a non-neutral mask |
| Live 4K60 pacing not achievable from WSL (M/M) | AV-D9: GPU-resident encode; GPU split; 4K30 fallback rung (proven under X11, re-run under sway) |
| The P4000 can't pace the south screen at 4K60 (M/M) | Test it before K6. Fallbacks: 4K30 on south, or both sessions on the 4060 (watch the 8-session NVENC cap) |
| A mid-show resync jump is a visible cut (M/M) | Even pacing; player ≤1.0×; jumps rare, counted, and capped as flashes (§3.9 point 6) |
| Sensor pumps the art mask during Hue shows; after P4b it could flicker with Disco (H/M) | M1 measures it; AV-D7; the P4b room hold and slew limit |
| The art-mode mask path becomes a remote strobe once P4b pushes fast overrides (L / severe) | §3.9 point 5: slew limit, and a token on the override endpoints before P4b ships |
| Three living plans drift apart (RP, DCL, this file) (H/M) | Contracts own the joins; DCL §2.5 is the single handshake text; a re-sync pass whenever RP or the DCL is revised |
| TV wake fails (Wi-Fi drops, Art Mode bugs) (M/M) | IP Control readback with retry; wake 60–90 s ahead; failures reported; AV-D13 |
| Photosensitive seizure (L / severe) | Joint budget; limiters including renderer transitions; IRIS; photosafe by default; G1 |
| WAN or guest control of devices (M / severe) | Room ports never WAN-forwarded, with LAN-scoped Windows Firewall rules; pairing; P3-reach before K5; service tokens |
| A production show served from the lab venv (M/H) | Gate G2 |
| Windows Update reboots the workstation during a show (M/M) | Heads continue on Maestro; screens cut to the still; the relay and `room_service` start at boot (P2 boot task) and restore the house; active hours set in Windows Update; AV-D8 if it keeps happening |
| WSL's IP changes on restart, or the relay dies silently (H/L) | The relay re-resolves the IP; `room.artnet_stale` alerts |
| Owner bandwidth; the holiday season during M6 (H/H) | Batched decisions with defaults; one device task at a time; M6's evenings may run into January |
| Scope creep, including scene art (M/M) | Deferred list below; gates; M4 accepted on stability, not art |

### 8.2 Shortcut ledger

| Shortcut | Saves now | Costs later |
|---|---|---|
| `room_service` on WSL localhost until the relay is in | M1 next week with no Windows change | No phone access or live Maestro until the relay and portproxy are installed |
| Room on the workstation via a Windows relay (AV-D2) | No hardware; no kiosk change | A Windows task to keep alive; room control shares the workstation's reboots |
| Synthetic show-state for M1 | A Hue win before Art-Net | Re-tuning against the real cadence in M2a |
| REST-only Hue until G1 | Safe and simple | Disco isn't snappy until M2b |
| Accept art-mask movement during Hue shows until P4b (AV-D7) | No legacy change before P4b | The screens follow the room lighting, slowly, until the room hold ships |
| Screens only via V1 before M5 | A screen demo in W4 with no production code | Each run takes one gallery TV off art in an owner window (navigate, then restore) |
| Static 1/1/1 flash split | Simple and provable | Less headroom (dynamic sharing later, about a day) |
| HTTP plus a pairing cookie | No LAN TLS | Sniffable without guest isolation; no PWA |
| No Siren in `room_service` before P2 | No legacy edit | Deadman and the UI are the only alerts for about 6 weeks |
| Screens after the light push (AV-D6 a) | No churn in RP or the DCL; the owner's light ask lands first | Screens in shows from mid-December, not sooner |

### 8.3 Not doing, and deferred

**Not doing:**
- WebRTC/MediaMTX in v1;
- WSL mirrored mode;
- TouchDesigner in v1;
- Home Assistant;
- node-sonos-http-api;
- artnet-bridge in production;
- Maestro's undocumented REST/WS;
- `/live/*` during shows;
- show-state on the Flask hub;
- room endpoints behind Flask, ngrok or forwards;
- logic on the mini-PC;
- **Game Mode or any TV setting switch;**
- **page-side fades, masks or overlays in live mode;**
- pixel-perfect cross-screen sync;
- Dante;
- automatic genre or mode switching.

**Deferred** (each gated separately):
- beat-locked visuals, audio DSP and the AI Show Director;
- hanging the mirror ball, its motor, and haze (gasolier gate, §3.9);
- Alexa room control (after ingress and signature verification);
- a scheduler `room_mode` action (after P2; non-blocking, 1 s timeout);
- a PWA;
- the second Hue bridge;
- Sonos GENA events;
- T1, T3–T8, as RP schedules them.

---

## Appendix: what changed on the evening of 13 Oct

These owner directives and findings arrived while this programme was being written. They override earlier text in the evidence files. RP and the DCL have since been revised to match, except where §2.1 and §2.2 list an update still due.

| Topic | Superseded | Now |
|---|---|---|
| Game Mode | Off, or a per-mode switch via HDMI content type / IP Control | **Never used** (Game Bar + flicker). Auto never engages under sway. Software never writes picture settings. Spike §10. |
| Art↔live transition | Page crossfade | **Hard cut** between matching frames, using the anchor-still handshake of DCL §2.5 (`diverge`, `converge` + `hold_pts`, re-anchor). The renderer owns all transition effects. |
| Live compositing | Mask, warm layer and overlays drawn over the live layer | **Video only.** The renderer composites everything. Full intensity, no mask. |
| Kiosk | X11, one X screen per TV, 4K30 live | **sway 1.9.** Zero-copy VA-API. **4K60 on glass** (0.03% dropped, pre-made). Live 4K60 limited by renderer pacing. The lab Chrome and `fb_probe.py` are X11-only, so V1 is a production Chrome over CDP. |
| TV state | "No network API reads or sets Game Mode or Input Signal Plus" (RP §3.5) | **IP Control** reads power, Art Mode, input, picture mode (FilmmakerMode) and settings, and can set Game Mode. Input Signal Plus is unchecked. |
| Mask at the cut | — | The server holds `mask.set`; the anchor carries the page's acked on-glass mask (DCL §2.5–2.6). |
| Screens sequencing (AV-D6) | Channels first, ahead of the P4 hub | RP/DCL order, because the revised handshake needs the hub. |
| Mask during Hue shows (AV-D7) | Freeze via `brightness-override` | Dropped: it forces `warm_alpha 0`. Accept until P4b's room hold. |
| Room host (AV-D2) | Buy a room box (and a staging kiosk) | No purchases: `room_service` in WSL, with a Windows UDP relay for Art-Net (owner, 13 Oct). |
