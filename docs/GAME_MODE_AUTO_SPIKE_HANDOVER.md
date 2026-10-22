# Handover: per-mode Game Mode switching on the Frame TVs (spike)

2026-10-13, from the room-AV planning session (screen-machine-10). Scope: **this spike only.** The wider room-AV programme is being written separately and will consume this spike's result as one input.

## 1. The question

Can the kiosk mini-PC switch each Samsung Frame Pro **into Game Mode when the room goes into live generative video, and back out (to the accurate Movie picture) for still art**, automatically and reliably, without anyone touching the TV?

The owner wants proof that we **can switch Game Mode on and off**. Then they want a recommendation on whether to build it in.

## 2. Owner position (verbatim, 2026-10-13)

- "I've turned game mode off, NB, as it messes with the colour settings & gamut. I think v low latency isn't super critical -- unless there is a reliable way to set game mode on from the hdmi signal. If so, we can switch to game mode when we go into live display, and turn it off when we're in 'art mode'."
- "the screens have a 'game mode = auto' setting, which I presume means that source selects. Investigate whether that is feasible, and how the mini PC might send those signals."
- "we want to confirm we can switch game mode on and off."

So the default stays **Game Mode Off** (decision D24 in `docs/REFACTOR_PLAN.md`). Per-mode switching is wanted only if it is reliable and automatic. Lower latency is a nice-to-have, not a requirement.

## 3. Hardware and where things stand

| Item | Fact | Evidence |
|---|---|---|
| TVs | 2 × Samsung Frame Pro QE75LS03FWUXXU (2025, model code 25_RSM_FTV), at 192.168.1.150 and .151, each fed through its Wireless One Connect box | TV `GET http://<ip>:8001/api/v2/` (read-only, 2026-10-13) |
| TV network | Both report `networkType: wireless`. The owner says the local TV-control tooling (`routes/samsung_utils.py`, vendored `samsungtvws`) suffers "lots of connection drops" | same; owner |
| Kiosk | AZW/Beelink MINI S, Intel N95 (i915), Ubuntu 24.04, Xorg 21.1.12 modesetting. Since 2026-10-13 it runs one X screen per TV (ZaphodHeads): `DISPLAY=:0.0` → `HDMI-1`, `DISPLAY=:0.1` → `HDMI-2`. The notes say :0.0 is north; **confirm which TV reacts** | owner's xrandr output; `docs/KIOSK_INFRASTRUCTURE_NOTES.md` |
| Display mode now | Both outputs at 3840×2160 @ **60 Hz**, with Game Mode **Off** (4K60 first appeared when Game Mode was briefly On) | owner's xrandr output, ~21:00 |
| Prior research | `docs/FRAME_PRO_SETTINGS_2026-10.md` §Q3 (around lines 95–117): Game Mode On forces the Game picture mode; consoles use Auto; Game Mode On/Auto also enables FreeSync; true 4:4:4 needs the PC label plus Game Mode | doc |

### What the TVs advertise (EDID), decoded

The 2026-10-13 capture is the owner's read-only `cat /sys/class/drm/card1-HDMI-A-*/edid`, identical on both outputs. The 2026-10-11 capture is `docs/kiosk-captures/edid-2026-10-11.txt`.

| Field | 2026-10-11 | 2026-10-13 (Game Mode Off) |
|---|---|---|
| HDMI 1.4 VSDB byte 8 | `0x20`: CNC bits all 0, no content types declared | `0x28`: **CNC3 = 1, the TV declares support for content type "Game"** |
| HDMI Forum VSDB (OUI C4-5D-D8) | absent (max TMDS 340 MHz) | present: 600 MHz, SCDC, FRL 6, **ALLM = 1**, VRR 48–144 Hz |
| Size | 256 bytes | 512 bytes (HF-EEODB, 3 extensions) |

The likely cause is a TV setting change (Game Mode or Input Signal Plus). Nobody has confirmed which.

### What the kiosk can send

- **HDMI AVI InfoFrame "content type": yes.** Both outputs expose a RandR property `content type`, currently `No Data`. Supported values are `No Data, Graphics, Photo, Cinema, Game` (owner's xrandr; also `docs/kiosk-captures/xrandr-verbose-2026-10-11.txt`). This is the i915 KMS connector property that sets ITC/CN in the AVI InfoFrame.
- **ALLM (HDMI 2.1 Auto Low Latency Mode): no.** No i915 ALLM support was found in any Linux kernel. Only AMD has ALLM patches, still in review in Aug–Oct 2026, and the NVIDIA open driver has none either (web check, 2026-10-13: Phoronix and PCGH articles; NVIDIA dev forum "ALLM on Linux"). Consoles trigger Samsung Auto Game Mode with ALLM.
- **SPD InfoFrame source type and HDMI-CEC:** not available or not configurable from this box, as far as is known (unverified).

### The hypothesis to test

With the TV's **Game Mode = Auto**, setting `content type = Game` makes the TV enter Game Mode, and `No Data` returns it to Movie. The TV's EDID now *declares* the Game content type. Nobody knows whether Samsung's Auto logic *acts* on it or only on ALLM. Only the test settles it.

## 4. The test (owner-run; the agent cannot see the TV)

**Constraints.**
- The test changes what the TV shows, so it needs the owner present and watching.
- Claude Code's auto-mode safety check blocked a read-only SSH from WSL to the kiosk on 2026-10-13. So either the owner runs the commands from their own terminal (they did so for the EDID read), or the owner adds a permission rule for SSH to 192.168.1.92.
- Do **not** restart the kiosk, X or Chrome.
- Do not change any other TV setting.

**Steps.**
1. On the **north** TV only (or whichever TV is on `:0.0`): Settings › Advanced Features › Game Mode Settings › **Game Mode = Auto**.
2. From the owner's terminal:
   ```bash
   ssh gjbm2@192.168.1.92 '
   pids(){ pgrep -f "google-chrome-stable.*--kiosk" | tr "\n" " "; }
   echo "$(date +%T) chrome pids before: $(pids)"
   echo "$(date +%T) -> content type = Game";    DISPLAY=:0.0 xrandr --output HDMI-1 --set "content type" Game
   sleep 20; DISPLAY=:0.0 xrandr --prop | grep "content type"
   echo "$(date +%T) -> content type = No Data"; DISPLAY=:0.0 xrandr --output HDMI-1 --set "content type" "No Data"
   sleep 20; DISPLAY=:0.0 xrandr --prop | grep "content type"
   echo "$(date +%T) chrome pids after:  $(pids)"'
   ```
3. Observe and record:
   - Does a Game Mode banner appear, or does the quick menu's Picture Mode show **Game**? How long until it switches, and is there a blank? How long?
   - On `No Data`, does it return to **Movie** with the same colour/gamut look as before?
   - Are the Chrome PIDs unchanged? Each kiosk window must survive. `kiosk-loop.sh` relaunches both Chromes if the set of connected outputs changes, and a modeset must not look like an unplug.
   - Optional repeat with `Graphics` and with `Cinema`. Note each effect: some TVs switch processing on IT content.
4. **Revert:** run the `No Data` line again, then set the TV's Game Mode back to **Off**, the owner's default.

## 5. If it works, characterise it before recommending it

- **Repeatability:** 10 on/off cycles. Measure switch time and blank duration (phone slow-motion is fine).
- **Persistence and interference:**
  - Does Auto survive TV standby, Art Mode and a TV reboot? Firmware updates reset picture settings (FRAME_PRO doc).
  - Does `content type` reset to `No Data` after an X restart, a Chrome relaunch or a TV hot-plug? Expected: it resets on X restart, so the property must be re-applied.
- **Side effects of Auto when idle (No Data):**
  - Is the Movie picture identical to Game Mode Off? Compare a test still side by side with the south TV.
  - Does Auto enable FreeSync/VRR? It should be harmless, because the source does not do VRR.
- **Both TVs:** south via `DISPLAY=:0.1 xrandr --output HDMI-2 …`.
- **Latency gain:** optional. Use the lab's `/clock.html` photo method (`experiments/stream-latency/README.md` §4) with Game vs Movie.

## 6. Integration notes (proposals only, not to be built in this spike)

- **Trigger:** the room/show supervisor decides "live show" vs "art". Today the server reaches the kiosk only through the token-gated admin API (`routes/admin_api.py`, SSH to the media server; `set-url`, `restart-screen`, etc.). A small new admin action, e.g. `set-content-type <screen> game|nodata`, fits the existing pattern.
- **Dumb host:** the owner's direction is "the mini-PC is meant to be dumb" (`docs/REFACTOR_PLAN.md` §2 principle 11). Setting an HDMI output property is host display configuration, comparable to `kiosk-loop.sh`'s xrandr layout. It must not become display logic on the box.
- **Re-assert:** after any X or Chrome relaunch, or at kiosk-loop start, re-apply the desired content type. The server holds the desired state; the box just applies it.
- **Display page:** the page cannot do this itself; a browser has no access to HDMI InfoFrames.

## 7. If it does not work: fallbacks to evaluate

Ranked by expected reliability.

1. **Inline ALLM injector controllable over LAN or RS-232:** for example an HDFury device between the kiosk and each One Connect box.
   - Check: the exact model and its "ALLM inject" feature, its IP API, 4K60/HDR passthrough with the new EDID (600 MHz HF-VSDB), added latency, whether toggling causes HPD (which would relaunch both Chromes), and UK price ×2.
2. **Network control of the TV:**
   - **SmartThings cloud API:** picture mode exists on some models. A 2025 S95F report says its status reads stale. No evidence was found that Game Mode is exposed.
   - **Local Tizen remote** (`samsungtvws` key sequences): fragile. Connections already drop, and the TVs are on Wi-Fi.
3. **Per-input memory trick:** Samsung keeps picture settings per input.
   - Feed the same output into two One Connect inputs via a splitter. Configure one input as Game and the other as Movie.
   - Switch input over the network.
   - Risks: splitter EDID/HDCP/4K60 handling, HPD-induced kiosk relaunch, and flaky input switching.
4. **Accept Game Mode Off permanently:** this is the owner's stated fallback. Screens then trail the room lights by roughly 0.2–0.3 s in shows; the programme designs visuals for "smooth correlation, not beat sync".

A background research run started at about 20:45 covers these routes and whether Samsung Auto honours content type. It also adversarially verifies the claims and writes a safe test plan. **Its findings will be appended as §9 when it completes.** If §9 is missing when you start, re-check this file later or ask the owner.

## 8. Rules and deliverables

**Rules** (this checkout is production):
- Read-only on the repo except your own results file.
- Never run `git stash`, `checkout`, `reset` or `pull`.
- Never restart Flask, the kiosk, X or Chrome.
- Never change TV settings yourself. The owner does it, or approves each change.
- Don't read `.env` or `*token*.txt`.
- Avoid opening `samsungtvws` token sessions unless needed. A rejected or stale token pops an "allow device" dialog on the screen.
- Don't touch Windows.
- The kiosk docs (`KIOSK_INFRASTRUCTURE_NOTES.md`, `FRAME_PRO_SETTINGS_2026-10.md`) are maintained by the streaming session (bd). Propose additions to them rather than rewriting them.

**Deliverables:**
1. A dated results section in this file: what was run, what the TV did, timings, PIDs, and the revert confirmed.
2. A one-paragraph verdict: "per-mode Game Mode is / is not achievable via ___", plus a recommendation for D24 (Off; or Auto + content type; or other).
3. If successful, the integration spec from §6, tightened. If not, the ranked fallback with cost and the next test.

## 9. Background research results (added 2026-10-13, ~21:40, by screen-machine-10)

Four-agent research run with an adversarial verifier. Full evidence: `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/954809c3-b227-4c7a-9aec-b8a50dbbfd3f/scratchpad/gamemode_verdict.md` (session scratchpad; may not survive long, so the essentials are below). **Caveat:** the agents worked from the 2026-10-11 EDID. Their conclusions about the TV were revised against the 2026-10-13 EDID in §3, and against your IP Control finding.

**Verdict.** Nothing documented makes "Game Mode = Auto" switch from this mini-PC today. Content type is still worth the free test.
- Samsung documents only **ALLM** plus console recognition as the Auto trigger:
  - UK spec sheet for this model: "Auto Game Mode (ALLM): Yes".
  - e-Manual BN81-27185C-660, p.123: "When you connect the other game source such as PC, set the Game Mode to On".
  - Samsung CA support (Jun 2026): "When connecting to a PC, Game Mode will not automatically turn on".
- No document or test was found either way for the AVI content type (CN = Game).
- Point in favour: the current EDID declares CNC3 = Game. The linuxhw EDID dataset shows the same three-variant pattern across 2023–25 Samsung families, and the variant with ALLM + CNC3 matches the Game Mode Auto/On state (inference). Both TVs are on Auto now (your IP Control read).

**Confirmed facts that matter for the test and the build.**
- **i915 cannot send ALLM** in any 2026 kernel (mainline 7.3-rc7, linux-next-20261013, drm-tip). Its SPD InfoFrame is hard-coded to 'Intel' / SDI = PC. The kiosk runs 6.8.0-94-generic per maintenance.log; confirm with `uname -r`.
- **Changing `content type` forces a full modeset on that pipe**, so the TV blanks briefly. Sources: `intel_atomic.c` sets `mode_changed`; the AVI InfoFrame is in the fastset compare. Whether the One Connect box drops HPD on that re-sync (which would make kiosk-loop relaunch BOTH Chromes) is unknown. Watch the Chrome PIDs and `/var/log/kiosk.log` "connector set" lines.
- **xrandr's exit status and `--prop` readback do not prove the kernel applied the change**: the modesetting DDX ignores `drmModeConnectorSetProperty`'s return. Verify instead with the TV's IP Control readback (`gameModeControl` / `getTVStates.pictureMode`), which is better than eyes. A visible re-sync blank is secondary evidence.
- **Use `xrandr --current`** for any reads during the test, so X does not re-probe the outputs.
- **Persistence:** the code suggests the value persists across X restarts (the kernel keeps it) and resets only on reboot. Re-assert it from the server anyway.
- **amdgpu ALLM is merged for 7.4** (commit 8b607d6f), but only while Gaming-VRR is active. An AMD kiosk swap gives no clean switch.
- **HDFury VRRoom / Vertex2 / Diva can force ALLM ON/OFF/FOLLOW** toward the TV. It is documented in the VRRoom manual and the Vertex2 firmware notes.
  - Network control of that mode is **undocumented**: the RS-232/IP list has only the EDID ALLM flag. `GET /cmd?allmmode=N` is plausible but untested.
  - About £350–525 per unit.
  - No report was found of forced ALLM switching a Samsung through a Wireless One Connect box.
- **Game Mode per HDMI input:** not documented (picture settings are per input). Treat it as unverified.
- **Revert behaviour:** HDMI.org says the display reverts to its previous mode when ALLM clears. Samsung documents nothing. Note that the TVs' current picture mode reads **FilmmakerMode**, not Movie. Record the revert target precisely.

**Re-ranked routes** (lead session's view, combining the research with your IP Control finding):
1. **TV at Auto + kiosk content type Game / No Data.** The TV setting never changes, so the EDID stays constant. The only disturbance is a modeset on one pipe. Verify via IP Control readback.
2. **IP Control `gameModeControl` On/Off directly** (setter untested on HDMI).
   - Simplest if it works.
   - **But** changing the Game Mode *setting* changed the EDID on 2026-10-13. That probably brings an HPD pulse, then kiosk-loop relaunches both Chromes, and the link may also move between 4K60 and 4K30.
   - Test whether the EDID and Chrome PIDs change when toggling Auto ↔ On ↔ Off.
3. **One returnable HDFury unit** on the north line: fixed custom EDID, TV at Auto, toggle ALLM in the web UI. Capture the HTTP request for automation only if it works.
4. Out-of-tree i915 ALLM patch: days of work plus a rebuild on every kernel update. Not recommended.
5. **Keep Game Mode Off** (D24): the owner's stated fallback.

**Additions to the §4 test** (from the verifier's safe plan):
- Photograph the north TV's Picture Mode, Expert Settings and Game Mode Settings first; they are the revert reference.
- Baseline read: `uname -r`, the EDID, `xrandr --current --prop`, the Chrome PIDs, and `tail /var/log/kiosk.log`.
- After switching the TV to Auto, re-read all of these. An EDID change or new PIDs means an HPD happened.
- Optionally try `Cinema`, which may trigger FILMMAKER MODE (Samsung says a switched mode can stick).
- **Abort:** if the screen stays black for more than 15 s, re-send `"No Data"`, then `DISPLAY=:0.0 xrandr --output HDMI-1 --auto`. Rebooting the kiosk is the last resort, and the owner's call.

## 10. Results and verdict (2026-10-13, 19:05–19:40 UTC, by screen-machine-85)

### Verdict

**Per-mode Game Mode is technically achievable, but the owner has rejected it.** Switching works over the LAN through Samsung IP Control (`gameModeControl`). Each switch is confirmed in about 1.7 s and does not disturb the kiosk. However, Samsung shows its **Game Bar overlay and the picture flickers** on every switch. Owner, 2026-10-13: "yuck no no o no ... it shows a horrible game bar thing and flickers ... we don't use game mode."

**D24 is closed: Game Mode is never used, live or art.** The HDMI route (§4 content type + Auto) is moot, and on the new sway kiosk it is impossible anyway (see below). Both TVs currently read `gameMode: Auto`, not Off. Setting them to Off is the owner's call, and the timing should be coordinated with bd because of the open 4K60/HF-VSDB test (§9).

### Owner direction for the renderer and display architecture (replaces any Game Mode transition)

> "we need to architect the transition so it's silky smooth. we COULD fade through black OR we could be cleverer -- e.g. have the real time rendering start with the current live art page and then transition FROM that e.g. cross fade or whatever into it's generative rendering, and then when it flips back again, cross-fades back over x seconds into the art mode still, then mode switch. ... needs to be factored into the renderer architecture then, but no game mode"

**Owner correction, same evening:**

> "the cross fading (intro/ outro) design can't rely on the chromium renderer to handle the fade -- mini pc isn't powerful enough. instead it'll need to switch over instantaneously from art mode to live mode where live mode video is indistinguishable (notwithstanding any minor mp4 compression artefacts)"

So the page never fades. **Every transition is a hard cut between two pictures that match, and all visible change happens inside the rendered stream.** What this implies (proposals for `DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` and the stream service; not built here):

- **Art → live.**
  - The server hands the renderer the exact on-glass still: the same source asset and the same crop, matte and position.
  - The stream opens *on that still* and holds it.
  - The page makes the `<video>` visible on its first presented frame (`requestVideoFrameCallback`), in one step. Because the frames match, being one frame early or late is invisible.
  - The page acks `on_glass`. Only after that ack does the server let the renderer start evolving away from the still, so the divergence can never happen before the cut.
- **Live → art.**
  - The server tells the renderer the target still (normally the next art still) and how long to converge.
  - The stream converges onto it and *holds* it for a margin of a second or two.
  - The page, with the still already decoded (`img.decode()`) underneath, hard-cuts to it, acks, and only then detaches the stream.
- **Failure paths.** A stall, a crash or a lost stream can only hard-cut to the still. That cut is visible, which is acceptable for a fault. The design's "fade live out over 500 ms" stall rule becomes a cut.
- **What "indistinguishable" requires** (acceptance test: compare an on-glass capture of the still with the stream's anchor frame on the kiosk itself, e.g. with `grim` under sway):
  - **Geometry:** native 3840×2160 with no scaling on either layer, and identical crop and matte.
  - **Colour:** the still is sRGB RGB as Chrome draws an `<img>`; the stream is HEVC 4:2:0. They need matching primaries, transfer and matrix tags, with the range handled correctly. Chrome's YUV→RGB conversion must match how the still is drawn.
  - **Quality:** the anchor GOP needs to be high quality, e.g. a high-quality I-frame and extra bitrate while the frame is static. 4:2:0 chroma softening counts as the "minor compression artefacts" the owner accepted.
- **Where compositing happens (owner directive, relayed by bd):**

  > "art mode: all the compositing happens in chromium / DOM. live mode: all the compositing happens in live renderer (local GPU) and then _all_ the display browser should do is stream mp4 4K at 60fps."

  - Live mode is full intensity ("a lighting rig, not a simulated artwork"). The brightness mask is art-mode only.
  - In live mode nothing is drawn over the `<video>`: no mask, overlay, HUD or CSS effect.
  - **Consequence for the cut:** the still is on glass *with* the art-mode mask at its current level, so the stream's anchor frame must be rendered with that same dimming. The renderer then ramps to full intensity inside the stream.
  - **On exit:** the renderer ramps back down to the mask level the page will apply before the cut back.
  - **What the server must hand the renderer:** the mask state at each cut, as well as the still.
- **Transitions are a renderer feature.**
  - Owner: "our rt renderer has to do all the smarts with the cross-fade (or whatever other transition we decide on -- could be more fun that a cross fade -- could be a 'screen melt' effect or whatever we can code up and deliver at 60fps 4K from this NVDIA card)".
  - The renderer therefore needs both endpoints as GPU textures: the anchor still exactly as on glass, and the generative frames.
  - Transitions are shaders on the workstation, e.g. crossfade, melt, dissolve or wipe, selectable per transition.
  - **4K60 on glass is solved on the kiosk** (bd, 2026-10-13, sway): pre-made HEVC 3840×2160@60 at 30 Mbit/s on both TVs dropped 0.03%, rAF held 60.0 fps, and it survived a reboot. What remains is on the workstation side: the live 4K60 lab stream drops 6–7%. Its generator misses the 16.7 ms deadline about 2% of the time (render plus a 12 MB raw-frame pipe write in WSL), and the lab player's catch-up speed-up then forces drops. The renderer must pace frames evenly (keep frames on the GPU from render to NVENC), and the player must never exceed 1.0x.
- **No TV-side change in any transition.** TV picture settings stay fixed. There are no switches of Game Mode, picture mode or input.

### What was found and run

**1. Samsung IP Control (JSON-RPC over HTTPS on port 1516) is live on both TVs. It is the only local API that exposes picture or Game Mode state.**
- **Server:** `Samsung IP Control Server/1.0`, certificate `CN = Samsung IP Control G2`.
- **Paired** with the owner accepting the on-screen prompt on both TVs at 19:16 UTC.
  - Tokens are stored outside the repo in `~/.config/screen-machine/ipcontrol/<ip>.token` (mode 600), on the WSL host.
  - A request with a missing or stale token returns a bare `{"code": -32700, "message": "Parse error"}`.
- **Protocol reference:** TheFab21/ha-samsungtv-smart, `IP_Control_Protocol_Reference.md` and `notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md` (2025 Frame decompile).
- **Example getter:**
  ```bash
  curl -sk -X POST -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data '{"jsonrpc":"2.0","id":"1","method":"getTVStates","params":{"AccessToken":"<token>"}}' https://192.168.1.150:1516/
  ```
  Setters add the field, e.g. `"gameMode":"On"`.
- **Read on both TVs** (identical, 19:16 and 19:37 UTC):
  - `getDeviceInformation` → `25_RSM_FTV`, firmware `T-RSMFDEUC-0090-1296.8`.
  - `getTVStates` → `pictureMode: FilmmakerMode`, `inputSource: HDMI1`.
  - `gameModeControl` → `Auto`.
  - `getVideoStates` → contrast 45, sharpness 10, brightness 0, color 25, tint 0.
  - `colorTone: Warm2`, `gammaMode: BT.1886`, `colorSpace: Auto`, `localDimming: Standard`, `backlight: 40`.
  - Off: `autoMotionPlus`, `contrastEnhancer`, `digitalCleanView`, `brightnessOptimization` and `filmMode`.
  - Also readable: `artModeControl` and `powerControl`.
- **Corrections to `REFACTOR_PLAN.md`:**
  - "No network API reads or sets Game Mode or Input Signal Plus" is wrong for Game Mode, which is readable and settable. Input Signal Plus was not checked.
  - Picture mode is FilmmakerMode, not Movie.
- **Ruled out for reading state:**
  - REST `:8001/api/v2/`.
  - UPnP DMR `:9197`: only volume, mute and aspect ratio.
  - Samsung UPnP services `:7678` (DIAL), `:9119` (screen sharing) and `:9110` (IPControlService, with an empty SCPD).
  - Local OCF/CoAP (UDP 5683). It lists 103 resources, including `/sec/tv/mode/picture`, but every `/sec/tv/*` resource is DTLS-secured and owned by SmartThings.
  - SmartThings cloud: picture mode only (stale unless refreshed first), no Game Mode, 12 requests/min per device, 24 h PATs.

**2. Setter test, north TV, Auto → On → Auto, hidden behind a black overlay fade on the production page via CDP.** Owner present.
- **Clean run, 19:35:56–19:36:35 UTC.** The kiosk was quiet by arrangement with bd.

  | Step | Result |
  |---|---|
  | `gameMode` set to On | Accepted. The TV reported `pictureMode: Game` 1.72 s after the request was sent (765 ms RPC round trip) |
  | Back to Auto | The TV reported FilmmakerMode after 1.70 s |
  | Kiosk during the switches | Zero DRM uevents, Chrome main PIDs unchanged, EDID `993716ac` unchanged on both connectors, sway outputs unchanged |
  | Visible result | Samsung Game Bar overlay and flicker (owner), so rejected |

- **An earlier run at 19:33:49 UTC is void.** bd's hotplug test restarted `kiosk.service` in the same second.

**3. The HDMI route (content type) is impossible on the sway kiosk.**
- **Hard-coded in wlroots.** Since about 19:17 UTC the kiosk has been running on Wayland (sway 1.9 on wlroots 0.17.1, `kiosk.service`). wlroots writes connector `content type` = **Graphics** on every commit (`backend/drm/atomic.c`), and is still hard-coded in wlroots `master`. sway only reports a client's `wp_content_type_v1` over IPC; it never passes it to the connector.
- **The TVs see no difference.** They now receive IT-content/Graphics instead of No Data, yet every IP Control readback was identical before and after the X → sway switch (pictureMode, all video states). `color` and `tint` are still reported; a 2018 Samsung drops them when it treats an input as a PC.

**4. Game Mode latency, for the record.**
- **Published figures:** rtings measured 9 ms on Micro-HDMI in Game Mode and 69 ms outside it. The Wireless One Connect box measures 35–45 ms, which is probably in Game Mode, though none of the reviews says so.
- **Not published:** a figure for the box with Game Mode off. Assuming the box's roughly 30 ms overhead adds on top, it would be about 95–100 ms, so Game Mode saves about 55–60 ms, not the 25–35 ms in D24.
- **Owner:** not worth it ("50ms probably isn't worth the hassle").

**5. IP Control reliability over the TVs' Wi-Fi** (each poll = `gameModeControl` + `getTVStates`, every 2 s, from WSL).
- **First 10 minutes (19:19–19:29 UTC):**
  - South: 301/301 OK.
  - North: 300/301 OK. The one failure was a reply envelope with no result, not a timeout.
  - Round trip per poll: p50 about 660 ms, p95 770–850 ms, max 3.5 s.
- **Total over two runs (19:19–19:29 and 19:37–19:44 UTC):** about 1,200 polls (about 2,400 calls) across both TVs. 2 empty-result replies, 0 timeouts or connection failures, slowest poll 3.5 s. The TV serves one connection at a time, so callers must serialise and retry an empty reply.

### Suggested follow-ups (owner's call)
- **IP Control as the observed-state TV adapter** (§6 dumb-host rule kept: the server talks to the TVs, not the kiosk):
  - **Read-only monitoring:** power, art mode, input, picture mode, and drift of the `FRAME_PRO_SETTINGS` values after firmware updates, which reset picture settings.
  - **Optional, owner-approved writes:** re-asserting those settings, using the expert-picture setters listed in the decompile.
- **Game Mode Off on both TVs,** when bd's 4K60/HF-VSDB test allows.
- **Bring this into the design doc:** the owner's transition direction above, via its maintainer.
- **The scratch tools** `ipctl.py` (pair/get/survey/watch/set) and `gm_fade_test.py` live in the screen-machine-85 session's scratchpad. Copy them to `experiments/` if they are wanted.
