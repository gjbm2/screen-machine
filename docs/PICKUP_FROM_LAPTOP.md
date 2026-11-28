# Picking up the laptop's rig work on PC22

Written for whoever brings PC22 (`finsbury`) up to date with the laptop's work: the owner, or a session on PC22.
The work is the karaoke party rig, built and tested on the laptop (`deployments/laptop/`, 26 Nov 2026). It covers:
- two GigBAR MOVE + ILS bars and an Antari Z-350 hazer on Art-Net;
- a virtual Art-Net node and a 3D view of the room;
- haze;
- house lights;
- Karaoke as a mode;
- one transition for every mode change.

PC22 will run the same rig later (docs/GIGBAR_RIG.md §2.4, §6). It will also get its own 3D view: the two bars, the
north and south screens where they hang, the Hue lamps and the glitterball.

The code is shared and lands with a pull. Each site's settings live in its own `deployments/<id>/` files, and
nothing in `deployments/finsbury/` was changed from the laptop. **After the pull PC22 runs as before, apart from the
behaviour changes in §1.** Each feature in §2–§4 is switched on by editing finsbury's own files. Tell the session
that owns a file before editing it (docs/AV_PROGRAMME.md §11). Then run `python -m smconfig check` and restart what
the file feeds. Restarting Flask means telling the other sessions first.

## 1. What changes on PC22 with no config edit

| Change | What PC22 sees | Where |
|---|---|---|
| **DMX lights fade on every change of state.** A light taken off or handed back to the music, the show starting and the show stopping all ease over one transition. Without a mode `transition_s` that is 3 s (`looks.TRANSITION_S`). | When pinspots or any DMX lights are patched, they fade where they used to cut. **The show stopping now fades over 3 s, not 5 s.** A mover still dims where it points, then glides home. Hue lamps don't change: room_service's Hue output fades them as before. | show_service/looks.py `_transition` |
| **Silence follows the mode.** Each vibe's `idle_motion` sets how much the lights move with no live beat; a stale beat grid no longer drives them (`GRID_HOLD_S`). | In silence, calm feels are gentler and disco livelier, and nothing races. | config/room/vibes.yaml, looks.py `_idle_clock` |
| **Movers sway** gently while their scene holds a position (`show.yaml lights.groups.movers.sway`). | Only a fixture in a group called `movers`. PC22 has none until the bars come. | config/room/show.yaml |
| **Scenes aim at walls named from the room's front** (`wall-front`, `wall-back`, `wall-left`, `wall-right`), not compass walls. | finsbury's `front: west` means `wall-back` is the east wall. Only movers aim, so this matters once the bars come. | show_service/space.py, config/live/light_scenes.yaml |
| **New config words, all optional:** a mode's `karaoke` and `transition_s`; fixtures.yaml outputs' `also_nodes` and `last_ip: windows`; vibes' `idle_motion`, `haze`, `strobe`; space.yaml `room.haze_half_life_s`. | Nothing until used. | |
| **New pages and tools:** `/lights/room` (the 3D room, §4) and the virtual node `tools/virtual_node/` (GIGBAR_RIG §7). | Available, empty until §4 is done. | |
| **The audio agent can send what Windows plays** (`tools/show_sender/pc22_agent.py`, the "loopback" device; Karaoke › Settings switches it). | The mic stays the default. Loopback needs `pip install soundcard` in PC22's Windows Python first. Re-copy the agent through Spectre's audio-agent card (`deploy-pc22.sh`). | |

## 2. One transition for a mode change (recommended)

A mode's `transition_s` is sent with every change the mode makes, as `opts.over_s`, so every device eases over the
same time:
- show_service's DMX lights, including the show starting or stopping;
- the Hue stream's lamps joining and leaving;
- Sonos volume.

An entry's own `opts.over_s` still wins (finsbury's Sonos entries set 5). Add one line at the top of
`deployments/finsbury/room/modes.json`, as the laptop has:

```json
"default": "gallery",
"transition_s": 3,
```

A mode can set its own (`"transition_s": 6` on Ambient, say), and modes built on it with `extends` inherit it.
Without it, each device keeps its own default: Hue `patch.yaml hue.fade_s` (3 s), Sonos `ramp_s`, DMX 3 s.
**Not covered yet:** the screens' art ↔ live cut has its own timing.

## 3. Karaoke as a mode (when PC22 hosts karaoke)

Give one mode `"karaoke": true`. The laptop's `singalong` ("Karaoke") in `deployments/laptop/room/modes.json` is the
template: screens live, the mic, the show's `singalong` feel, `"karaoke": true`.
- Entering it readies the room; nobody's song plays until someone presses Play.
- Leaving it for another mode stops the player.
- Starting a song from another mode takes the room into it, and Stop puts the earlier mode back.

With no such mode, the karaoke player behaves as it always has. Its house lights entry (`"light.house-*": "off"`)
only applies where house lights are patched.

## 4. PC22's own 3D room (`/lights/room`)

The page draws the room from show_service's rig and room model, read through room_service:
- the room's **size**;
- every placed **fixture**;
- each **screen's plane**, playing its own live stream, or its art when it isn't live;
- **Hue lamps** in show_service's last colour for them;
- the **glitterball**;
- **haze**.

The lights come from the virtual node's DMX, the same wire the real node gets. finsbury's `lights/space.yaml` places
nothing yet, so the page shows an empty room. To build it:

1. **The room:** measure `room.size: [east-west, north-south, floor-to-ceiling]` in metres, from the south-west floor
   corner. `front: west` is already set.
2. **The screens** (`appliances`). Each is its fixture id with its centre and plane. The screens' wall is the west
   wall, so they face east (normal `[1, 0, 0]`). Seen from the room, the north screen is on the right and the south
   on the left. Use the real picture sizes (a 65" Frame is about 1.43 × 0.80 m):
   ```yaml
   screen.north: {at: [0.0, <y>, <centre height>], plane: {normal: [1.0, 0.0, 0.0], up: [0.0, 0.0, 1.0], size: [1.43, 0.80]}}
   screen.south: {at: [0.0, <y>, <centre height>], plane: {normal: [1.0, 0.0, 0.0], up: [0.0, 0.0, 1.0], size: [1.43, 0.80]}}
   ```
   Each plays its live stream from room_service as a viewer, and its art (`/output/<screen>.jpg`) when it isn't live.
3. **The Hue lamps:** `hue.ne`, `hue.nw`, `hue.se`, `hue.sw: {at: [x, y, z]}`.
4. **The glitterball:** under `points`, `mirror-ball: {label: Mirror ball, at: [x, y, z], device: mirror_ball}`. The
   page spins it at a fixed rate; it doesn't yet read room_service's `mirror_ball` state.
5. **The two GigBARs:** patch them first (GIGBAR_RIG §6). Then place every part: `<bar>.par1`, `.derby1`, `.strobe`,
   `.derby2`, `.par2` with `at`, and `.spot1` and `.spot2` with a `pose`. Copy the laptop's block in
   `deployments/laptop/lights/space.yaml` as the pattern:
   - The parts sit along the bar: par 1 at one end, par 2 at the other, about 0.91 m apart. The spots are 0.18 m
     above the bar.
   - A spot's `pose.rot` is `[0, 0, yaw]` in radians, yaw being the direction its pan 0 faces. Measure it, or find it
     on the Lights page's aim tools. GIGBAR_RIG §8 explains the laptop's poses and the heads' tilt.
   - The laptop's poses are hand-set and provisional.
6. **Points the scenes aim at.** These are optional:
   - `dance-floor`, a `screen` point, a `singer` point;
   - a wall point for the heads' rest (the laptop has `west-wall`; on PC22 the wall facing the screens is the east
     wall).

   Each scene's comment in `config/live/light_scenes.yaml` names the points it uses.
7. **The wire, on PC22's Windows** (a Windows browser, local to the node):
   - Run the virtual node with `--port 6455`. PC22's udp 6454 already has a relay to room_service (GIGBAR_RIG §2.4,
     §7).
   - Add a `gigbar-view` node in finsbury's fixtures.yaml (`match: {short_name: "SM Virtual Node"}`, `last_ip:
     windows`, `port: 6455`). List it in the bars' output's `also_nodes`.
   - It needs a **Spectre card** (CLAUDE.md: every long-running Windows piece gets one), and its port registered in
     `docs/PORTS.md` (8705 HTTP is already listed for the laptop).
   - Open `/lights/room` in a Windows browser on PC22. A browser elsewhere on the LAN can't reach the node's
     `127.0.0.1:8705`: that would need the node to listen on the LAN, plus a Spectre LAN rule. `?node=<url>` points
     the page at another node.
8. **Haze, when a hazer is patched:** set `room.haze_half_life_s` in space.yaml, and time it by eye (GIGBAR_RIG and
   show.yaml `lights.atmosphere` say how). The page's Haze bar shows show_service's modelled haze; drag it to correct
   the model by eye.

Then run `python -m smconfig check`. show_service re-reads space.yaml and fixtures.yaml by itself, and the page
re-reads the room every 5 s. Missing pieces show as "problems" in the page's header.

**Not modelled yet:**
- daylight and the blinds;
- the lamps' real Hue output (the page shows show_service's last colour, not the bridge's);
- the glitterball's motor;
- the pools of light dimming in haze.

## 4a. The room editor and the studio: the glitterball and pinspots (for the weekend, 28–29 Nov)

Light setup › **Room** is now the one editor for the room: 3D, Plan, Front and Side views; walk or orbit; every
thing selectable, renamed, moved, with undo (docs/LIGHTS.md §3.6b). Its **studio** puts things in the room
**planned** (drawn, driving nothing) and **connects** them to the real ones when ready. For PC22's glitterball and
pinspots:
1. **Room › ＋ Add to the room… › "Mirror ball"** (or "glitterball"); click where it hangs. The first one is named
   `mirror-ball`, the name the scenes aim moving heads at. In its panel, **Connect it to… › Glitterball**: it then
   stands for the remote's subsystem `mirror_ball`.
2. **＋ Add › "Pinspot"** (or "glitter spot"), click where each hangs; **⌖ Point at…** and click the ball (a pinspot
   points at the nearest ball until told). **Connect it to… › Pinspots** (subsystem `pinspots`). Ctrl+drag copies one.
3. **Lights › Mode, "Room as in Disco"**: the preview shows them as the mode sets them (Disco: pinspots on, white,
   level 8; the ball spinning). Normal: the ball still, pinspots off. A pinspot or head beam on the ball throws
   glitter round the room (an estimate: 220 facets, 0.1 turns a second; docs/LIGHTS.md).
4. **In each mode** (the device's panel) shows what each mode does with it; change it in Settings › Modes.
Where the show runs them (Party, Disco: `director.glitterball`, `director.pinspots`), the preview shows them on and
spinning; the scenes the director picks for them (light_scenes.yaml `appliances-resting`, `glitter-slow`,
`disco-*`) are not yet drawn in the preview.

Before it is useful on PC22:
- **Nothing needs editing for the glitterball and pinspots**: they are room_service remotes already.
- **finsbury's planned GigBARs** (`gigbar.se`, `gigbar.ne`, `kind: gigbar-2` in space.added.yaml, from the older
  view) show in the Room list as planned things with nothing drawn. To place them as bars, copy the laptop's
  `layouts: gigbar-move` block (deployments/laptop/lights/space.yaml) into finsbury's space.yaml, then ＋ Add ›
  "Gigbar move" for each and take the old two out of the room. Their parts register at their address when connected.
- Things finsbury's own space.yaml sets show locked ("set in the room file"); move them to the page file (as the
  laptop's were, 27 Nov) to edit them in the editor.

## 5. Checks after the pull

- `python -m smconfig check` is `ok` for finsbury. It was `ok` on the laptop with the shared code as committed.
- Switch modes once. The Hue lamps and the screens should behave as before. Any DMX lights should fade rather than
  cut, over `transition_s` if you set it.
- The karaoke page plays as before. Its Settings has a mic/loopback switch, and the mic is the one to leave on.
