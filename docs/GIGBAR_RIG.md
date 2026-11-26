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
- A US **Rev. 13** (07/2024) lists 51/29/3 channels. I could not get that PDF, so a 51CH unit is **not covered**.
- Antari Z-350 Fazer User Manual: https://antari.com/wp-content/uploads/Z-350.pdf, and Antari's Z-350 flyer.

## 1. The lights

### 1.1 GigBAR MOVE + ILS: what is on the bar

Left to right on the front: **Par 1, Spot 1, Derby 1, laser, Derby 2, Spot 2, Par 2**, plus four white strobe zones.

| Part | Light | show_service sees |
|---|---|---|
| 2 pars | RGBAW + UV LEDs (3 x 6 W), 22° beam | a colour-mixing lamp: R, G, B, amber, white, UV, and a strobe |
| 2 derbies | RGBW LEDs (2 x 6.5 W each colour), 131° coverage, motorised | colour mixing, strobe; the motor is pinned |
| 2 spots (moving heads) | 32 W cool-white LED, 17° beam, pan 540°, tilt 180°, 16-bit | a mover: pan/tilt, speed, a 10-slot colour wheel, a 9-gobo wheel with shake and scroll, a dimmer, a shutter |
| 4 strobe zones | cool-white LEDs, 5 W each | **not patched** (see below) |
| laser | class 3R: red 650 nm, green 520 nm, blue 450 nm | **not patched**; the type makes every channel a page-only macro |

show_service drives one colour and one pan/tilt per fixture, so each bar is **patched part by part**: six fixtures
per bar at the bar's start address plus each part's offset.
- **The strobe zones are left unpatched.** There is no strobe-only role yet; patched, they would glow white with
  every look. Their channels stay at 0, which is dark.
- **The laser is left unpatched too.** The manual requires 3 m between the floor and the lowest beam, and 3 m
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

- **The look engine never drives haze.** Both channels are macros; the fixture's `pin` holds them (Haze 0 = off).
- To haze, set `pin: {Haze: <5-255>, Fan: <0-255>}` on `hazer` in fixtures.yaml. It is re-read within 2 s. Start
  around Haze 40.
- The Lights page's More can also pick a level by hand.
- **Facts from the manual and flyer:**
  - It warms up for about 4.5 min. Status LED: green flashing = heating, solid green = ready, amber flashing =
    hazing, solid amber = DMX received, solid red = overheating.
  - It uses 3 ml of fluid a minute from a 1.3 L tank (about 7 h at full output). Use **Antari FLG fluid only**.
  - It draws 750 W at 240 V.
  - With DMX present it starts heating by itself. **When DMX stops, it runs a self-clean and sleeps.** show_service
    resends every universe at least once a second, so a running rig keeps it awake.
  - Antari's rules: adults only, **never leave it running unattended**, keep 50 cm of space round it, never add
    flammable liquid, and drain the tank before transport.

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
   - If it offers 51CH, stop: that chart isn't verified here.
   - The value shows yellow while DMX arrives and white without it.
2. MENU → **SETUP**:
   - **Pan Range 540** and **Tilt Range 180**, to match the type file (the spec sheet's 540° x 180°). If you prefer
     270° tilt, set the spot's tilt range on the Lights page instead.
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
3. **Tilt 180°**, with Tilt Range 180 in the menu (above).
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
- **Strobes:** light effects can strobe the spots' shutters and the par and derby strobe channels. If anyone may be
  photosensitive, set each light's strobe limit (`strobe_hz_max`, which must be above 0) low on the Lights page,
  for example 1-2 Hz.

## 6. Moving the rig to PC22

The types are shared, so only the site file changes. Copy `types`, `fixtures` and `groups` from
`deployments/laptop/lights/fixtures.yaml` into `deployments/finsbury/lights/fixtures.yaml` (tell c8, who owns that
file). Then:
- add a `nodes` entry with the node's LAN `last_ip`;
- add an `outputs` entry: `driver: artnet`, `port_address: 1` (§2.4), `enabled: true`;
- check that the fixture ids don't clash with the ones already there;
- give the parts `placement` (`above`, `opposite`) or `role` as they sit in the drawing room;
- run `python -m smconfig check`.
