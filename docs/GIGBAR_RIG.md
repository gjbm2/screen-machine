# The GigBAR rig: two Chauvet GigBAR MOVE + ILS and an Antari Z-350 Fazer over Art-Net (26 Nov 2026)

The portable lights rig: two **Chauvet DJ GigBAR MOVE + ILS** and an **Antari Z-350 Fazer** (a water-based hazer)
on one Ethernet DMX node, driven by show_service's Art-Net output. It runs on the laptop first (the karaoke party, 5
Dec, with the node wired straight to the laptop), and later on PC22 (the same node on the house LAN).

| What | Where | Shared? |
|---|---|---|
| Fixture types (OFL JSON, hand-written from the manuals) | `config/room/fixture-types/ofl/chauvet-dj/gigbar-move-ils-*.json`, `.../antari/z-350.json` | yes, every deployment |
| The laptop's node, universe, fixtures and groups | `deployments/laptop/lights/fixtures.yaml` | laptop only |
| PC22's | `deployments/finsbury/lights/fixtures.yaml` (c8's file): copy the blocks from the laptop's (§6) | finsbury only |

Nothing here is on OFL (open-fixture-library.org has neither fixture), so the type files were written from the
manuals below and checked with `tools/show/add_fixture.py --file <file>` (show_service's own loader, no problem lines).

**Sources:**
- GigBAR MOVE + ILS User Manual **Rev. 10** (EU, 11/2023, item 10051948, "Version 2"): 52/30/3-channel modes.
  https://www.farnell.com/datasheets/4127597.pdf
- The same manual, **Rev. 8** (older units): 50/28/3-channel modes. Only the laser block differs (§1.2).
  https://goknight.com/content/documentation/GigBAR_MOVE_ILS_US_UM_ML5_Rev8.pdf
- The **US version** manual, **Rev. 12** (04/2024): 51/29/3-channel modes. Its laser is a different laser (§1.4):
  colour and one of 32 patterns, no strobe or rotation. Patched as `mode: part-51ch` of the laser type.
  https://www.chauvetdj.com/wp-content/uploads/2022/05/GigBAR_MOVE_ILS_UM_US_Rev12.pdf
- The **EU version** manual, **Rev. 11** (12/2023): the same charts as Rev. 10.
  https://www.chauvetdj.com/wp-content/uploads/2022/05/GigBAR_MOVE_ILS_UM_EU_Rev11.pdf
- Antari Z-350 Fazer User Manual: https://antari.com/wp-content/uploads/Z-350.pdf, and Antari's Z-350 flyer.

## 1. The lights

### 1.1 GigBAR MOVE + ILS: what is on the bar

Left to right on the front: **Par 1, Spot 1, Derby 1, laser, Derby 2, Spot 2, Par 2**, plus four white strobe zones.

| Part | Light | show_service sees |
|---|---|---|
| 2 pars | RGBAW + UV LEDs (3 x 6 W), 22° beam | a colour-mixing lamp: R, G, B, amber, white, UV, and a strobe |
| 2 derbies | RGBW LEDs (2 x 6.5 W each colour), 131° coverage, motorised | colour mixing, strobe; the motor is pinned |
| 2 spots (moving heads) | 32 W cool-white LED, 17° beam, pan 540°, tilt 270° (menu; spec sheet 180°), 16-bit | a mover: pan/tilt, speed, a 10-slot colour wheel, a 9-gobo wheel with shake and scroll, a dimmer, a shutter |
| 4 strobe zones | cool-white LEDs, 5 W each | four intensity channels and a strobe (one fixture per bar) |
| laser | class 3R: red 650 nm, green 520 nm, blue 450 nm | **not patched** (owner: tbc); the type makes every channel a page-only macro |

show_service drives one colour and one pan/tilt per fixture, so each bar is **patched part by part**: seven fixtures
per bar at the bar's start address plus each part's offset.
- **The strobe zones are patched** (owner, 26 Nov). They have intensity channels and no colour, so they light white
  at every look's level (191 of 255 in a typical look), not only on hits. Watch that on the virtual GigBAR (§7).
- **The laser is left unpatched (owner: tbc).** The manual requires 3 m between the floor and the lowest beam, and 3 m
  between the beams and the audience. A room party can't meet that, so keep it off. Its channels stay at 0, which is
  blackout.

### 1.2 The 52-channel mode (Rev. 10) and the part offsets

| Bar ch. | Part (offset) | Channels |
|---|---|---|
| 1-7 | Par 1 (+0) | red, green, blue, amber, white, UV, strobe (000-250 slow to fast, 251-255 to sound) |
| 8-14 | Par 2 (+7) | the same |
| 15-20 | Derby 1 (+14) | red, green, blue, white, strobe, rotation (0 stop, 1-127 CW slow to fast, 128 stop, 129-255 CCW) |
| 21-26 | Derby 2 (+20) | the same |
| 27-31 | Strobes (+26) | white LED 1-4 dimmers, strobe speed |
| 32-34 | Laser (+31) | colour (0-5 blackout, then R, G, B, RG, RB, GB, RGB), laser strobe, rotation |
| 35-43 | Spot 1 (+34) | pan, pan fine, tilt, tilt fine, pan/tilt speed, colour wheel, gobo wheel, dimmer, shutter |
| 44-52 | Spot 2 (+43) | the same |

- **50CH units (Rev. 8):** the laser is one channel (32: 0 blackout, 1-36 image colour, then single colours and
  pairs), so the spots start at **33 and 42** (offsets +32 and +41). Use type `gigbar-laser-50` if you ever patch the
  laser, and move the spot addresses.
- **30CH mode** drives both sides as one (one par, one derby, one spot). **3CH mode** only picks the bar's own
  programmes. Neither is used here.

**Spot colour wheel:** white 0-6, red 7-13, orange 14-20, yellow 21-27, green 28-34, blue 35-41, CTO 42-48, cyan
49-55, magenta 56-62, lime 63-64, colour index 65-189, scroll CW 190-221, stop 222-223, scroll CCW 224-255.

**Gobo wheel:** open 0-5, gobos 1-9 in steps of 6 (gobo 9 is 54-63), shakes 64-117 (gobo 9 first), open 118-127,
scroll CW 128-189, stop 190-193, scroll CCW 194-255.

**Shutter:** closed 0-3, open 4-7, strobe 8-76, pulse 77-145, random 146-215, open 216-255.

### 1.3 Antari Z-350 Fazer

| Ch. | Function |
|---|---|
| 1 | haze: 0-4 off, 5-255 output 1-100% |
| 2 | fan: 0-4 is 20%, 5-255 is 21-100% |

- **The look engine never drives haze; the room's atmosphere does** (owner, 26 Nov; `show_service/atmosphere.py`,
  show.yaml `lights.atmosphere`). The feel says how thick the air should be (vibes.yaml `haze`: off, light, medium
  or thick; the laptop's Karaoke mode plays the `singalong` feel, which is `light`). The director then holds that density
  from a model of the air:
  - the room's volume, from space.yaml `room.size`;
  - how fast it clears, from space.yaml `room.haze_half_life_s`. To set it, haze the room, stop the hazer, and time
    how long the haze takes to halve. 150 s is provisional.
  - It fills fast, with the fan up, then holds at a low output; off sends 0. It never runs above 60 % for more than
    30 s at a time, so a crash mid-fill can't leave the hazer at full.
  - `GET /show/api/v1/haze` shows the estimate, the output and the phase. `PUT {"level": "medium"}` (or `null` for
    the feel's) sets a person's level until the mode changes.
  - The levels are looks, by how far you can see: light about 60 m (beams show, the room looks clear), medium about
    20 m, thick about 5 m (a fog). Their sizes in show.yaml are a guess. **Calibrate on the day:** set haze to
    light, and when the air looks right read `estimate_ml_m3`; that is `light`, medium is 3 x it, thick 12 x.
  - There is no readback. Say what the room is by eye with `PUT /show/api/v1/haze/now {"level": "light"}`, and
    `{"warm": false}` when the hazer has just been switched on. The estimate is kept across a restart.
- While the haze is off, the fixture's `pin` holds both channels (Haze 0 = off), and the Lights page's More can pick a
  level by hand.
- **Facts from the manual and flyer:**
  - It warms up for about 4.5 min. Status LED: green flashing = heating, solid green = ready, amber flashing =
    hazing, solid amber = DMX received, solid red = overheating.
  - It uses 3 ml of fluid a minute from a 1.3 L tank (about 7 h at full output). Use **Antari FLG fluid only**.
  - It draws 750 W at 240 V.
  - With DMX present it starts heating by itself. **When DMX stops, it runs a self-clean and sleeps.** show_service
    resends every universe at least once a second, so a running rig keeps it awake.
  - Antari's rules: adults only, **never leave it running unattended**, keep 50 cm of space round it, never add
    flammable liquid, and drain the tank before transport.

### 1.4 What the unit really does (researched 27 Nov)

From the manuals and footage ("Chauvet DJ Gigbar Move + and ILS Command Review" by tarekith on YouTube, from 31:45;
Guitar Center's overview video; both show US units). This is the reference for the lights' scenes, effects and
transitions and for the 3D room. Read it before writing any.

- **Two different lasers are sold under this bar's name, and the DMX chart tells them apart** (corrected 27 Nov;
  the first write-up here described the US unit's figures and gave them the EU unit's controls).
  - **EU unit: 52-channel chart (30CH, 3CH). Ours, as far as we know.** Colour, strobe, rotation. No patterns.
    - It is a **grating laser**: each lit diode is split into a burst of thin beams. Needles in haze, a field of dots
      on the ceiling and walls. The rotation channel turns the burst.
    - Evidence: the EU chart has rotation and no pattern channel; a retailer's data sheet lists it as a grating laser
      covering 93 degrees; its 80 mW diodes are Class 3R only because each beam is under 5 mW (the manual's laser
      data); footage of the sibling GigBAR Move ILS shows exactly this burst.
    - **Not confirmed:** nobody here has seen an EU unit's laser under DMX. How many beams, how fast it turns and
      whether the burst is a square grid are estimates (the 3D room draws 9 x 9 beams a colour).
  - **US unit: 51-channel chart (29CH, 3CH).** Colour (with an "image colour" setting) and one of 32 patterns, 8 DMX
    values each. No strobe, no rotation.
    - It throws a few fat beams from two apertures. Each lands as a small, sharp line figure, in mirrored pairs.
    - Evidence: the review and overview videos below are all US units; a DMX tutorial for the unit shows the
      "Laser Control" and "Laser Patterns" attributes ("Program The Chauvet Gigbar Move + ILS in SoundSwitch").
  - **Both:** two or three diodes together are that many colours of beams side by side, not one mixed colour (a
    grating bends each colour by its own angle). A look's colour lights the one diode nearest it.
  - **Both:** every pulse is longer than 0.25 s (under 4 a second). Nothing gates the laser on the beat.
  - **To tell which you have:** MENU → DMX. A list with 52CH and 30CH is the EU unit; 51CH and 29CH is the US one.
- **A derby throws a fan** of about a dozen soft streaks. With two colours on, they show as separate coloured streaks.
- **Colour is one family per look.** In every look in the footage the pars, derbies and spots are one hue, and the
  laser is one colour too (it looks white in the footage only where the camera is overexposed). The unit's own menu limits
  its auto shows to 3, 4 or 6 colours ("Pars Color: Tri, Quad, Hex").
- **The unit's own shows are line-ups.** Its AUTO menu is every combination of the parts: pars, derby, laser, strobe
  and spots (for example "L+SP": laser and moving heads; "PD": pars and derby). In the footage two or three parts are
  usually lit, against a dark room. Not every part plays all the time.
- **Transitions between its programmes are snap or fade** ("Mode: Snap/Fade").
- **The spots mirror each other:** crossing, splayed apart, level and opposite, in the look's colour.

## 2. Wiring and the network

### 2.1 DMX chain

**node DMX out → Bar A in → Bar A out → Bar B in → Bar B out → hazer.**
- Every connector is 3-pin XLR. Use DMX cable, not mic cable.
- The Z-350 has a single DMX socket, so it goes **last**. Put a terminator on a bar's out if the chain misbehaves.
- Power: each bar is 248 W at most. Give the hazer (750 W) its own socket.

### 2.2 Addresses

| Unit | Mode / setting | Start | Channels |
|---|---|---|---|
| Bar A (left as the room faces the screen) | **52CH** | 001 | 1-52 |
| Bar B (right) | **52CH** | 101 | 101-152 |
| Hazer | rear DIP switch | 201 | 201-202 |

The universe is Art-Net Port-Address **1** (Net 0, Sub-Net 0, Universe 1). show_service refuses 0, which is
room_service's virtual universe.

If the bars end up the other way round, swap their start addresses on the bars; nothing in the files needs to change.

### 2.3 The laptop (the party): node wired straight to the laptop

1. **USB-C Ethernet adapter** on the laptop (it has no wired NIC: 25 Nov). In Windows Settings → Network → Ethernet
   → IP assignment → Manual, IPv4:
   - address `192.168.50.1`, mask `255.255.255.0`;
   - **no gateway and no DNS**, so Windows never sends internet traffic that way.
2. **The node:** static IP `192.168.50.10`, mask `255.255.255.0`. Protocol Art-Net. Its DMX port is an **output** on
   universe 1 (Port-Address 1).
   - **Done on the laptop, 26 Nov, over the network** (`tools/artnet_node_setup.py`; no OLED menus). The Pknight
     (`CR011R_001`) came at **10.201.6.100/8, Port-Address 0**. ArtIpProg moved it to 192.168.50.10/24, and ArtAddress
     moved it to Port-Address 1. It then reported "outputting". The adapter's 192.168.50.1/24 was set from an elevated
     PowerShell: `Set-NetIPInterface -Dhcp Disabled` and `New-NetIPAddress` on the ASIX AX88179 adapter.
   - If the node comes back on its factory settings after a power cycle, run the tool again: `find`, then `ip` from a
     temporary 10.0.0.250/8 on the adapter, then `universe`.
   - If its page counts universes from 1 (as sACN does), check what it reports: after step 4, `GET
     /show/api/v1/rig` `.nodes.node-dmx` lists its replies, and `GET /show/api/v1/rig/frame?output=dmx-a` shows what
     is sent.
3. **WSL stays in NAT mode** (the mic needs it). show_service sends unicast to 192.168.50.10:6454, and Windows
   routes it out of the adapter. No firewall rule, no portproxy and no broadcast is needed.
   - ArtPollReplies may not reach WSL. That only affects the health line ("no replies yet"); frames go out anyway.
4. **Check:** run `ping 192.168.50.10` from WSL. Then `GET /show/api/v1/rig` should show `outputs.dmx-a.state:
   sending` (it already does: the file is live and frames go to 192.168.50.10 whether or not a node is there).

### 2.4 PC22 (later): the node on the house LAN

1. Give the node a fixed LAN address: a DHCP reservation, or a static address outside the router's pool.
2. Add the blocks to finsbury's fixtures.yaml (§6) with that address as `last_ip`.
3. **The universe:** MaestroDMX also sends Art-Net on the LAN (patch.yaml: Port-Address 0 with Offset Universes
   off). Port-Address 1 is free while that holds. If Maestro's offset is ever switched on, move this rig to 2, or
   the node will merge Maestro's frames into the bars.
4. Art-Net out from WSL is unicast (docs/PORTS.md: no new listening port). PC22's udp 6454 relay goes to
   room_service, so the node's replies only reach show_service once `rig.artnet.reply_bind` is set (README S5.7).

## 3. Setting up each unit

**GigBAR MOVE + ILS** (four buttons under the display):
1. MENU → **DMX** → ENTER → ENTER → choose **52CH** → ENTER → **Address** → 001 (bar A) or 101 (bar B) → ENTER.
   - If the list offers 50CH and not 52CH, it is an older unit: patch the spots at +32 and +41 (§1.2).
   - If it offers 51CH, it is the US unit (§1.4): its laser is two channels and the spots sit one channel lower
     (+33 and +42). Patch the laser as `mode: part-51ch`; the rest of that chart isn't verified here.
   - **Tilt the laser** by hand so its window faces straight up and a touch back towards the wall (1.5 degrees):
     its burst is 93 degrees wide, so no beam then leans more than 45 degrees into the room (owner, 27 Nov: clear
     of a mirror ball in the middle). Set the same tilt on the Lights page (the laser's row, "How it is tilted"):
     the 3D room draws from it.
   - The value shows yellow while DMX arrives and white without it.
2. MENU → **SETUP**:
   - **Pan Range 540** and **Tilt Range 270**, to match the type file. The spec sheet says 540° x 180°, but the
     menu offers 270, and upright heads at 180 can't reach below horizontal, so not the floor (§8). If a real bar
     won't do 270, set 180 here and in the spot type.
   - Pan/Tilt Reverse OFF for now; fix aiming on the Lights page.
   - **DFI OFF**, and RF and FOOT **OFF**, so a remote or another bar can't take it over.
3. With DMX, the bar **holds the last frame when the signal is lost** (manual). In master/slave mode it blacks out.
4. The manual says the bar isn't for continuous use: give the lasers breaks. It is overhead-mount only; a tripod is
   fine.

**Z-350:**
1. DIP switch for address **201**. The switches are binary: 201 = 128 + 64 + 8 + 1, so on a standard 1-2-4-...-256
   bank that is switches **1, 4, 7, 8 ON**. The manual doesn't draw the bank, so check against the label on the unit.
2. Turn the rear **SMOKE knob to 0**, so it only hazes when DMX asks. The manual doesn't say which wins when both are
   set.
3. Fill it with FLG, switch on, and wait for solid green, then solid amber with DMX.

## 4. First light: the checks to do on the day

On the Lights page (`/lights`), **Identify** each fixture (3 s of full white) and check these.

These are **assumptions in the type files**, because the manuals are vague:
1. **The par, derby and strobe-zone "strobe" channel at 0 is steady** (no strobe). The manual lists 000-250 as
   "strobe speed, slow to fast"; Chauvet's other bars use 0-9 as no strobe. If a par flickers at rest, the type
   needs a pin on that channel (tell the show session).
2. **Pan/tilt speed: 0 = fastest** (Chauvet's usual). The manual doesn't say which way it runs. If the heads crawl,
   flip it in `gigbar-move-ils-spot.json`.
3. **Tilt 270°**, with Tilt Range 270 in the menu (above), and that a head at tilt centre points straight up.
4. The colour-wheel hexes are nominal. Name or rename slots on the Lights page if they look off.

Then set up the spots on the Lights page (Move it and set it up, docs/LIGHTS.md §2.6):
- set where each spot rests;
- set its **limits**, so it never tilts down into faces (a children's party);
- place the bars on the Room map, so the position scenes aim sensibly.

The derby motors are pinned to a slow clockwise turn (`Rotation: 16`). 0 stops them; raise the pin for faster.

## 5. Safety notes for a house party

- **Haze and smoke alarms:** haze can set off optical smoke detectors. Know where they are, and keep the haze level
  low.
- Ventilate the room, keep the machine 50 cm from anything, and keep an adult with it at all times (Antari).
- **No lasers** (§1.1).
- **Strobes:** light effects can strobe the spots' shutters and the par and derby strobe channels. They fire only in a
  feel with `strobe: true`. The `singalong` feel (the laptop's Karaoke mode) spends one of its 6 big moments an hour on
  each one: the last chorus, a peak or a drop (owner, 26 Nov). If anyone may be photosensitive, set each light's
  strobe limit (`strobe_hz_max`, which must be above 0) low on the Lights page: about 3 Hz at most, or 1-2 Hz to be
  safe.

## 6. Moving the rig to PC22

The types are shared, so only the site file changes. Copy `types`, `fixtures` and `groups` from
`deployments/laptop/lights/fixtures.yaml` into `deployments/finsbury/lights/fixtures.yaml` (tell c8, who owns that
file). Then:
- add a `nodes` entry with the node's LAN `last_ip`;
- add an `outputs` entry: `driver: artnet`, `port_address: 1` (§2.4), `enabled: true`;
- check that the fixture ids don't clash with the ones already there;
- give the parts `placement` (`above`, `opposite`) or `role` as they sit in the drawing room;
- run `python -m smconfig check`.

## 7. The virtual GigBAR: a second Art-Net node on the Windows side

A real Art-Net node, run as Windows Python on this laptop, with a view of the two bars and the hazer that a Windows
browser opens. It hears the rig exactly as the Pknight will: show_service polls it and sends it the same ArtDmx
packets, byte for byte, frame for frame, as `node-dmx` (`outputs.dmx-a.also_nodes`). Nothing in it makes up DMX.

```
show_service (WSL 172.24.x)  --unicast ArtDmx, ArtPoll-->  node-dmx    192.168.50.10:6454  (the Pknight, via Windows NAT)
                             --the same packets-------->  gigbar-view  windows:6454        (the virtual node)
                             <--ArtPollReply------------  (each node answers; show_service matches it by name)
virtual node: Art-Net in -> DMX out at 40 Hz (holds the last frame when Art-Net stops) -> /dmx -> the view
```

- **Run it:** `tools/virtual_node/run-windows.sh start` (also `stop`, `status`, `log`). Then open
  **http://127.0.0.1:8705/** in Brave on Windows.
- **Two views** of the same DMX (both read `gigbar_model.js`: the set-up, the channel meanings, the parts' motion):
  - **the board** (`/`): one panel per part, in the bar's order, with what it shows, what it's doing in words, and its
    raw channels;
  - **3D** (`/3d`, three.js 0.187 vendored in `tools/virtual_node/vendor/three`, MIT, so it works offline): the bars on
    tripods at the far wall of a plain room. The heads pan on their yokes and tilt in them, and each one projects its
    colour and gobo. The pars wash, the derbies throw turning beam fans, the strobes glow, and the laser draws lines.
    A side panel says what each part is doing. Drag to orbit and use the wheel to zoom; there are Front, Side and
    Above views. Beams show plainly by default; tick "beams as the haze allows" to see them as the room would.
  - The head orientation is an assumption: tilt centre is straight up, + tilts towards the room, and + pan turns to
    the room's right. Check it against the real bars and use the Board's reverse switches.
- **What it is:**
  - `virtual_node.py` listens on Windows udp 0.0.0.0:6454 and answers ArtPoll with an Art-Net 4 ArtPollReply (short
    name "SM Virtual Node", universe 1). show_service shows it under `GET /show/api/v1/rig` `.nodes.gigbar-view`.
  - It takes ArtDmx for Port-Address 1 and honours ArtAddress failsafe commands.
  - Its DMX side refreshes at 40 Hz whatever rate Art-Net arrives at. After 4 s without Art-Net it holds
    (`--failsafe zero` to black out instead).
  - Flags: `--port 6455` (PC22), `--universe`, `--reply-to-6454` (answer polls on port 6454, as some nodes do).
- **The view** reads only that DMX output. Set each bar there as you set the real one: start address, 52CH or 50CH,
  pan/tilt range and reverse. Set the hazer's DIP address too. A patch mistake then shows as it would in the room.
  - It draws each part from what its channels mean in the manuals: pars and derbies, the strobe zones, the laser, the
    spots' pan/tilt, colour wheel, gobos and shutter, and haze building in the air (beams show in haze).
  - It also shows each bar's raw channels and the node's state: packets a second, sources, sequence jumps, merging,
    polls answered.
  - **The mechanical speeds are estimates** (the manuals don't give them): pan, tilt and wheel travel, the derby and
    laser motors, and the hazer's 4.5 min warm-up (set-up can skip it).
- **What it proves:** show_service's packets and timing, the patch, both nodes on one universe, and real Art-Net
  over a real (virtual) Ethernet segment between two hosts.
- **What it doesn't prove:** Windows routing out of the USB adapter, the Pknight's settings, the cables, or the
  bars' firmware and menu settings. The view follows our reading of the manual, as the type files do. See the list of
  failure points below.
- **Stop sending to it:** remove `gigbar-view` from `outputs.dmx-a.also_nodes`. It's re-read within 2 s, and the
  real node's frames don't change.

### 7.1 What the virtual test can't catch (check these with the real kit)

1. **Path:**
   - Windows routing to 192.168.50.10 out of the USB adapter: the Wi-Fi's /19 (192.168.0.0-31.255), Tailscale.
   - The adapter's static IP, which Windows drops when there's no link.
   - USB selective suspend during a long run.
2. **The Pknight CR011R** (its manual; set on its OLED, hold Enter 3 s to save each setting):
   - Transmit mode **Artnet -> dmx** (the blue LED steady).
   - Port universe **1**, SubNet 0, Net 0.
   - Local IP 192.168.50.10, mask 255.255.255.0.
   - If its default IP is inside the Wi-Fi's /19, set it with Wi-Fi off.
   - If it mishandles short frames, set `full_512: true` on `node-dmx`.
3. **Cables:**
   - Halo's three 3-pin leads (2 x 10 m, 1 x 3 m) are exactly the chain node → A → B → hazer, with no spare.
   - The node's socket is 3-pin female (the normal output).
   - Check the hazer's socket is 3-pin.
4. **The bars:**
   - The menu's mode list (52CH, 50CH or 51CH: ask Halo for a photo).
   - The §4 assumptions.
   - The bars in DMX mode, with RF, FOOT and D-Fi off: Halo's quote includes RF remotes and foot controllers.
   - The laser key left out.

## 8. The laptop rig's room (provisional, 26 Nov)

`deployments/laptop/lights/space.yaml` (hand file):
- **The room:** 5 m east–west by 10 m north–south, with a 4 m ceiling.
- **The bars:** on tripods in the east corners, A NE and B SE, 10 m apart. The bar bodies are at 2.5 m, 0.4 m in from
  the walls, each turned to the room's centre.
- **The karaoke picture:** about 2 m wide, projected on the east wall between the bars (assumed 16:9, centre 2 m up).
- **The heads:** hand-set poses, standing upright with pan 0 facing the room's centre (not taught).
- **Their rest** (`fixtures.setup.yaml` `home`, set through the setup API): the middle of the west wall at 3 m,
  opposite the picture and above people's heads.

**Tilt finding:** with the spec sheet's 180° tilt, an upright head reaches only from straight up down to horizontal
(2.68 m). It could never light the floor: the dance floor's centre is about 28° below horizontal. Most moving-head
scenes work on the floor (pools, sweeps, figure-of-eight, ballyhoo), so they clamped the heads at horizontal and they
sat still. **The spot type now assumes the menu's Tilt Range 270** (45° below horizontal; owner, 26 Nov). Set 270 on
each bar's menu. If a real bar won't do it, put 180 back in `gigbar-move-ils-spot.json` (`tiltMax`, and the Tilt
channel's `angleEnd`) and on the virtual board.

The 3D view (§7) uses the same room. Its default view is a cutaway from above the west wall.

**Testing without music in the room:** show_service's silent test source plays a file straight into its ears, with
no speakers and no mic (`POST /show/api/v1/test {"source": "audio", "piece": "<id>"}`; the pieces are listed by
`GET /show/api/v1/test`). A Windows loopback (what the laptop plays) needs the `soundcard` library on the Windows side:
not installed.

