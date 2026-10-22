# Lights: movers, room space and flow (design v1, 18 Oct)

The build spec for the next step of the lights. Five builders implement it in parallel from this page alone: **encode**,
**space**, **engine**, **catalogue** and **page** (§11 has the files each one owns). It extends what was built on
18 Oct (23540c83, 39088af8; show_service/README.md S5.23, S5.24; SHOW_ENGINE §9.1-§9.2). Every number marked
*starting point* is a design default to tune by eye; room facts (sizes, positions, speeds) are never invented: they
are measured or set on the page, and until then the code degrades.

## 0. What the owner asked (18 Oct), and the shape of the answer

- "Lights have much simpler vector space than a screen (even if they have gobos and movement) but they are still
  basically an output device that requires **scenes, effects & transitions**."
- "Part of what a 'scene' means for a moving fixture is **movement data** -- speed, direction, location, etc. plus
  gobos if any, plus focus/zoom etc."
- "**Most of the fixtures will be moving ones.**" Movers are the primary case.
- "*Direction* may be a general instruction from the a.d., e.g. 'upwards' could be meaningful for some scenes, effects
  or transitions as well as lights."
- "Light console page needs to be able to **move lights manually during registration** so user can store settings /
  configure positions." Manual control is part of adding or editing a light, not an expert screen.
- Appliances are registered in the app (Art-Net is only the transport). The art director knows which scene, effect and
  transition runs on each group of appliances. Lights change in lockstep with the screens. A song-end fade is a
  fade-dark transition.

The answer, in seven pieces:

1. **Capabilities** (§2). Every fixture is a set of normalised attributes (pan and tilt in degrees, zoom in beam
   degrees, gobo slots, spins, prism, beam, shutter, colour). The OFL file (or a generic type) says which it has and how
   each maps to DMX. Layers are capability-aware: a light that lacks an attribute ignores that layer.
2. **Room space** (§3). One room frame in metres (x east, y north, z up), with the room's front. Every appliance has a
   position: screens are rectangles on walls, lamps and movers are points, and the piano and mirror ball are named
   points. A direction therefore means the same thing everywhere.
3. **Mover pose** (§3.6). Teach each mover 3-4 room points once. The pose is solved by least squares, and from then on
   any mover can aim at any room point by inverse kinematics.
4. **Flow** (§4). One instruction from the director on room.direction: `flow {dir, at, speed, spread, coherence,
   symmetry, period, anchor, ...}`. Every appliance kind reads it: screens in their own plane, movers as aim and
   pattern orientation, lamps as an intensity wave ordered by position, the mirror-ball motor as its rotation, and
   transitions as a sweep across the room.
5. **Engine layers** (§5). Position patterns via the space, beam, gobo, prism, colour wheel, CCT and shutter (effects
   only). The engine also handles mechanical timing (speed limits, pre-roll, dark moves, smooth degradation) and the
   jog hold.
6. **Catalogues and programs** (§6, §7). Light scenes, effects and transitions are tagged on the screens' scales. The
   director runs one program per light group (`{scene, effects, transition, flow}`), published as
   `LookData.light_programs`.
7. **The page** (§8). A Room map, and a **Move it** panel inside Add/Edit that drives the real light. From it the
   owner stores home, limits, named positions, the teach points and the default beam.

## 1. Rules for every builder

- Production repo, other sessions editing other files. Never touch show_service/receiver.py, clock.py, dsp.py,
  beatproc.py, listener.py, cues.py (3a's) or stream_service/ (0b's). No service restarts, commits, deploys, git
  stash/checkout/reset/clean/add.
- Owner rules: a prototype that works first; no gates, trips or self-off by default (a safety limit is per-fixture
  calibration the owner sets, never a default refusal); degrade, never refuse; no auth; everything in config, no
  special cases (nothing branches on a fixture, group, scene, point, mode or vibe name); no invented config values
  (commented starting points only); plain Room-page words in the UI ("Lights", "Point it at…", "Upwards"), never
  internals; no JSON or raw config on pages.
- **Cross-module imports are lazy and degrade**, as looks.py already does for fixtures.select. The five parts land in
  any order. A missing `fixture_encode` falls back to colour_mix.encode; a missing `space` means no positions (group
  order, pan/tilt-space patterns); a missing `flow` means a still flow; missing `light_programs` means the
  `light_scene` compat path.
- **contract.py is shared.** Use Edit only (never rewrite the file), re-read just before each edit, and put new blocks
  at your anchor (§11.2).
- Tests are crash-level and real, run targeted only:
  `flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python -m pytest -q -p no:cacheprovider <files>`.
  TypeScript, once at the end: `flock /tmp/claude-1000/sm-build.lock nice -n 19 npx tsc --noEmit -p tsconfig.app.json`.
- Match the surrounding style: module docstrings that say the rules, page-words problem lines, starting points marked
  where they are defined.

## 2. Capability model and encoding (builder: encode)

### 2.1 The attribute vocabulary

`contract.ATTRS` lists the words that layers, the hold (jog) and the frame use. Each has a normalised unit. A fixture
has an attribute when its type has at least one DMX range serving it.

| attr | unit / values | when nothing sets it | OFL capability types |
|---|---|---|---|
| `intensity` | 0..1 | the frame's (today's path) | Intensity, ColorIntensity (no dimmer) |
| `colour` | sRGB at full value | the frame's | ColorIntensity (emitters), WheelSlot on a colour wheel |
| `colour_slot` | colour-wheel slot number (1-based), or null = nearest to `colour` | null | WheelSlot (colour wheel) |
| `colour_spin` | -1..1 (+ = CW), share of fastest | 0 | WheelRotation on a colour wheel |
| `cct` | kelvin, or null = the channel's default | null | ColorTemperature |
| `shutter` | `{mode: open\|closed\|strobe\|pulse\|random, hz: float\|null}` | `{mode: open}` | ShutterStrobe, StrobeSpeed |
| `pan` | degrees, **centred**: 0 = the middle of the pan range | setup `home.pan`, else 0 | Pan (+ fine) |
| `tilt` | degrees, centred: 0 = the middle of the tilt range | setup `home.tilt`, else 0 | Tilt (+ fine) |
| `pan_spin`, `tilt_spin` | -1..1 | 0 | PanContinuous, TiltContinuous |
| `pt_speed` | 0..1, 1 = the fixture's fastest | show.yaml `lights.motion.pt_speed` | PanTiltSpeed (speed, or duration inverted) |
| `zoom` | beam angle, degrees | setup `beam.zoom`, else the middle of the range | Zoom, BeamAngle |
| `focus` | 0..1, near → far | setup `beam.focus`, else 0.5 | Focus |
| `iris` | 0..1, closed → open | setup `beam.iris`, else 1 | Iris, WheelSlot of type Iris |
| `frost` | 0..1 | setup `beam.frost`, else 0 | Frost, WheelSlot of type Frost |
| `gobo` / `gobo2` | slot number on the first / second gobo wheel (null = its Open slot) | null | WheelSlot (wheel with Gobo slots) |
| `gobo_spin` / `gobo2_spin` | -1..1 (+ = CW): the gobo image turning | 0 | WheelSlotRotation |
| `gobo_shake` / `gobo2_shake` | 0..1 shake speed (0 = still) | 0 | WheelShake |
| `gobo_scroll` | -1..1: the gobo wheel scrolling | 0 | WheelRotation on a gobo wheel |
| `prism` | 0 = out, n = prism slot / 1 = in | 0 | Prism, WheelSlot of type Prism |
| `prism_spin` | -1..1 | 0 | PrismRotation, Prism.speed |
| `macro` | `{<channel name>: <range word>}` (hold only, never a layer) | the channel default / `pin` | Effect*, ColorPreset, Maintenance, Generic, SoundSensitivity, Fog*, Blade*, BeamPosition, Rotation, Speed, Time, IrisEffect, FrostEffect |

`NoFunction` ranges are gaps. Fine channels (`fineChannelAliases`) give 16-bit values, MSB first, and a second fine
stays at its default. Switching channels keep today's rule (ofl.py).

### 2.2 OFL → ranges

`ofl.parse` keeps today's `attr`, `emitter`, `fine` and `default` for every channel. The one change in behaviour: Pan,
Tilt and the rest are now driven (§2.4) instead of held. It also adds `ranges`, which lists every capability on the
channel, normalised:

```python
class CapRange(TypedDict):          # contract (encode)
    dmx: list[int]                  # [lo, hi] at the channel's full resolution (0..65535 for 16-bit)
    cap: str                        # the OFL capability type ("Pan", "WheelSlot", "WheelShake", ...)
    attr: str                       # the ATTRS word it serves ("macro" for page-only ones)
    lo: float                       # the attr's value at dmx lo (degrees, -1..1 spin, 0..1, kelvin, hz)
    hi: float                       # ...and at dmx hi (equal for a stepped capability)
    slot: NotRequired[int]          # WheelSlot / WheelShake / WheelSlotRotation: the slot number
    mode: NotRequired[str]          # shutter: open | closed | strobe | pulse | random | ramp | burst
    word: NotRequired[str]          # page words: the OFL comment, effectName, shutterEffect or the slot's name
```

`AttrChannel` gains `ranges: list[CapRange]`. `TypeInfo` gains `wheels: {name: {kind: colour|gobo|prism|other,
slots: [{slot, type, name, hex?}]}}` and `physical: {lens_deg: [min, max] | None, pan_deg: float | None, tilt_deg:
float | None}`. `TypeInfo.attrs` becomes the full ATTRS list this type can show.

Reading OFL values, with the entities from capability-types.md:

- **Angles** (`angle`, `angleStart`/`angleEnd`) are read as `"<n>deg"`. A Pan channel's ranges are centred on the
  middle of their union (0..540° becomes -270..+270). Percent angles mean the range isn't known in degrees. Then
  setup `range.pan`/`range.tilt` is used, else `UNKNOWN_PAN_DEG = 540` / `UNKNOWN_TILT_DEG = 270` (code starting
  points), with a problem line: "pan range not in the file: 540° assumed (set it on the Lights page)".
- **RotationSpeed** is a signed share of the fastest: `fast CW` +1, `slow CW` +0.01, `stop` 0, `slow CCW` -0.01,
  `fast CCW` -1, and `"n%"` is n/100. Hz or rpm values are divided by the largest |value| among that attr's ranges.
- **Speed** (PanTiltSpeed, StrobeSpeed, EffectSpeed): `fast` 1, `slow` 0.01, `stop` 0, %. For StrobeSpeed and
  ShutterStrobe, Hz values are kept in Hz (`hz` attr). For PanTiltSpeed given as `duration` (Time), the value is
  `1 - duration share`.
- **BeamAngle**: `"<n>deg"`. Keywords and % are fractions of `physical.lens.degreesMinMax`, else `ZOOM_UNKNOWN_DEG =
  (10, 40)` (starting point, with a problem line).
- **Distance** (Focus): `near` 0 → `far` 1, %. Metres are normalised by that channel's min and max.
- **IrisPercent**, **Percent** (frost) map to 0..1. **ColorTemperature**: kelvin as is. Keywords and % map
  `warm/CTO` → 2700 K and `cold/CTB` → 6500 K (starting points, with a problem line).
- **Wheels**: the first wheel with Color slots is the colour wheel, as today. The first wheel with Gobo slots is
  `gobo` and the second is `gobo2`. A wheel of Prism slots is `prism` (Open = 0, then slots 1..n). A WheelSlot range
  covering a whole slot gets `slot`; a range between slots (a split) is skipped, as today.
- **WheelRotation** on a gobo wheel is `gobo_scroll`; on a colour wheel it is `colour_spin`.
- **ShutterStrobe** `shutterEffect` maps to `mode`: Open → open, Closed → closed, Strobe → strobe, Pulse → pulse,
  Lightning / Spikes / `randomTiming` → random, RampUp/RampDown/RampUpDown → ramp, Burst → burst. `speedStart`/
  `speedEnd` in Hz become lo/hi. The default stays the start of the first Open range.
- A channel whose capabilities serve **several attrs** (a gobo wheel channel with slots, shake ranges and scroll) keeps
  every range. The encoder picks the range at frame time (§2.4).
- **Defaults** stay ofl.py's rules. A Pan or Tilt channel with no `defaultValue` rests at its centre (the owner's
  18 Oct ruling: never aimed at 0, 0), with the fine byte too.

### 2.3 Fixture capabilities (what the engine and the page read)

```python
class FixtureCaps(TypedDict):       # contract (encode); fixture_encode.caps(type, setup)
    attrs: list[str]                # ATTRS this fixture can show (after setup overrides)
    pan: Optional[dict]             # {"min": deg, "max": deg, "fine": bool, "limits": [lo, hi] | None}  centred
    tilt: Optional[dict]            # the same
    pt_speed: bool
    zoom: Optional[dict]            # {"min": deg, "max": deg, "known": bool}
    focus: bool
    iris: bool
    frost: bool
    colour: str                     # "mix" (emitters) | "wheel" | "none"
    colour_slots: list[dict]        # [{slot, name, hex}] (setup names win)
    gobo_slots: list[dict]          # [{slot, name, shake: bool, open: bool}] (setup names win); [] = no gobo wheel
    gobo2_slots: list[dict]
    prism_slots: list[dict]         # [{slot, name, facets}]; a plain Prism channel: [{slot: 1, name: "Prism"}]
    spins: list[str]                # the spin attrs present: gobo_spin, gobo2_spin, prism_spin, colour_spin, ...
    cct: Optional[dict]             # {"min_k", "max_k"}
    shutter: list[str]              # the modes it has: open, closed, strobe, pulse, random, ...
    strobe_hz: Optional[list[float]]   # [lo, hi]
    macros: list[dict]              # [{channel, name, choices: [{word, dmx}]}]: page "More" only
    moves: bool                     # has pan and tilt
```

`RigFixtureRow` gains `caps: FixtureCaps` and `setup: FixtureSetup`; `RigTypeRow` gains `caps`. The page builds its
controls from `caps` alone (§8.3).

### 2.4 The encoder: `show_service/fixture_encode.py` (new, pure)

```python
def plan(t: TypeInfo) -> EncodePlan                       # cached by identity, like colour_mix._plan
def caps(t: TypeInfo, setup: Optional[FixtureSetup] = None) -> FixtureCaps
def encode(f: FixtureInfo, t: TypeInfo, intensity: float, colour: Sequence[float],
           attrs: Optional[Mapping[str, Any]] = None) -> bytes
def neutral(caps: FixtureCaps, setup: Optional[FixtureSetup]) -> dict[str, Any]   # every attr at its resting value
def to_dmx(r: CapRange, value: float) -> int              # full-resolution value inside r
```

`encode` with `attrs=None`, or attrs holding only intensity and colour, returns **exactly** colour_mix.encode's bytes
(a test). With attrs it does this:

1. Start from colour_mix.encode's bytes: template, pins, emitters, intensity, and the colour wheel. When `colour_slot`
   is set, it wins over the nearest slot.
2. **Per channel**, choose one range:
   - The attrs a channel serves are tried in `ATTR_PRIORITY = ("shutter", "gobo_shake", "gobo2_shake", "gobo_spin",
     "gobo2_spin", "gobo_scroll", "colour_spin", "prism_spin", "prism", "gobo", "gobo2", "colour_slot",
     "pan_spin", "tilt_spin")`. The first one whose value is active (not neutral: spin ≠ 0, shake > 0, prism > 0, a
     slot set, shutter not open) **and** has a matching range on this channel wins.
   - Then come the continuous attrs (pan, tilt, zoom, focus, iris, frost, cct, pt_speed).
   - A channel none of them claims keeps its template byte.
3. **Value to DMX:**
   - Continuous: the range whose [lo, hi] holds the value, linear between dmx lo and hi. A value outside every range
     clamps to the nearest end.
   - Slots: the middle of the slot's range. Shake: the range for that slot (or wheel-wide), with its speed value
     interpolated.
   - Signed spins: the range whose sign matches. 0 is the stop range's middle, else the default.
   - Shutter: the range of that mode, `hz` interpolated. Open is the start of the first open range (today's default).
   - Pan and tilt are clamped to the fixture's range (never to the owner's soft limits: those are the engine's, §5.5).
4. **Bytes:** a 16-bit value goes to (coarse, fine) MSB first. When the mode lacks the fine channel, only the coarse
   byte is written. `macro` values look up the channel's range by `word` and send its `dmx[0]`.

Costs: plan and templates are cached by identity, as colour_mix's are. A frame with only movement changes does no
colour solve (colour_mix's per-fixture colour cache stays).

### 2.5 Generic types

fixtures.py keeps every `GENERIC_TYPES` entry. The existing `generic-mover` (Pan, Tilt, Dimmer, R, G, B) is **the
basic mover**, now driven. One full type is added:

```python
"generic-mover-full": ("Moving head: pan, tilt (fine), speed, dimmer, shutter, colour wheel, gobo, gobo spin, prism, "
                       "zoom, focus (13 channels)",
                       ("Pan", "Pan fine", "Tilt", "Tilt fine", "Pan/Tilt speed", "Dimmer", "Shutter", "Colour wheel",
                        "Gobo", "Gobo rotation", "Prism", "Zoom", "Focus")),
```

`GENERIC_RANGES` gives each generic channel word its ranges. They are starting points for a typical head, every one
commented as such. An OFL file is the real answer, and setup `range` / `slots` override.

```python
GENERIC_RANGES = {   # starting points: a common budget mover's layout; check the light's manual
  "Pan": [{"dmx": [0, 255], "cap": "Pan", "attr": "pan", "lo": -270, "hi": 270}],          # 540° (setup range wins)
  "Tilt": [{"dmx": [0, 255], "cap": "Tilt", "attr": "tilt", "lo": -135, "hi": 135}],       # 270°
  "Pan/Tilt speed": [{"dmx": [0, 255], "cap": "PanTiltSpeed", "attr": "pt_speed", "lo": 1.0, "hi": 0.0}],  # 0 = fast
  "Shutter": [{"dmx": [0, 7], "cap": "ShutterStrobe", "attr": "shutter", "mode": "closed", "lo": 0, "hi": 0},
              {"dmx": [8, 15], "cap": "ShutterStrobe", "attr": "shutter", "mode": "open", "lo": 0, "hi": 0},
              {"dmx": [16, 131], "cap": "ShutterStrobe", "attr": "shutter", "mode": "strobe", "lo": 1, "hi": 20},
              {"dmx": [132, 255], "cap": "ShutterStrobe", "attr": "shutter", "mode": "open", "lo": 0, "hi": 0}],
  "Colour wheel": "slots:8",    # 8 equal slots over 0..127 (slot 1 Open), then colour_spin: 128..191 CW, 192..255 CCW
  "Gobo": "slots:8",            # 8 equal slots over 0..63 (slot 1 Open), shake 64..127 per slot, gobo_scroll above
  "Gobo rotation": [{"dmx": [0, 3], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0, "hi": 0},
                    {"dmx": [4, 127], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0.01, "hi": 1.0},
                    {"dmx": [128, 131], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0, "hi": 0},
                    {"dmx": [132, 255], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": -0.01, "hi": -1.0}],
  "Prism": [{"dmx": [0, 127], "cap": "WheelSlot", "attr": "prism", "lo": 0, "hi": 0, "slot": 0},
            {"dmx": [128, 255], "cap": "Prism", "attr": "prism", "lo": 1, "hi": 1, "slot": 1}],
  "Zoom": [{"dmx": [0, 255], "cap": "Zoom", "attr": "zoom", "lo": 10, "hi": 40}],          # ZOOM_UNKNOWN_DEG
  "Focus": [{"dmx": [0, 255], "cap": "Focus", "attr": "focus", "lo": 0.0, "hi": 1.0}],
}
GENERIC_DEFAULTS = {"Pan": 128, "Tilt": 128, "Shutter": 8}    # centre, centre, open
```

The generic fine channels pair with Pan/Tilt (`fine` offsets). The generic wheel slot count is setup `slots.count`
(default 8, starting point), so the page can say "It has 6 patterns" and the slots re-divide.

### 2.6 Per-fixture setup: `config/room/fixtures.setup.yaml` (new; written only by the API)

This holds settings for **any** fixture: hand-file fixtures too, so a mover written in fixtures.yaml can still be set
up on the page. It is keyed by fixture id and written whole and atomically (fixtures.write_added's pattern, with its
own header). The Registry merges it into `FixtureInfo.setup` and hot-reloads on it.

```python
class FixtureSetup(TypedDict, total=False):   # contract (encode)
    home: dict                 # {"pan": deg, "tilt": deg}: where it rests (house, inactive, uncalibrated patterns)
    limits: dict               # {"pan": [lo, hi], "tilt": [lo, hi]}: owner-set soft limits, centred degrees; absent =
                               # the full range (no default limits)
    max_speed: dict            # {"pan": deg_s, "tilt": deg_s}: owner-measured; absent = lights.motion's starting point
    range: dict                # {"pan": deg, "tilt": deg}: the full mechanical range when the type doesn't say
    beam: dict                 # {"zoom": deg, "focus": 0..1, "iris": 0..1, "frost": 0..1}: its default beam
    slots: dict                # {"gobo": {"<n>": "Stars"}, "gobo2": {...}, "colour": {"<n>": {"name", "hex"}},
                               #  "prism": {...}, "count": {"gobo": n, "colour": n}}  (count: generic types only)
    positions: dict            # {"<name>": {"pan": deg, "tilt": deg}}: named positions in its own pan/tilt
    strobe_hz_max: float       # owner-set, per light; absent = none
    jog_flip: dict             # {"pan": bool, "tilt": bool}: the page's drag direction (page convenience only)
```

fixtures.py adds:

- `SETUP_KEYS`.
- `SetupStore(path=contract.FIXTURES_SETUP_FILE)` with `.read() -> dict[str, FixtureSetup]` and `.patch(fid: str,
  patch: dict) -> FixtureSetup`. A key set to null removes it; `positions`/`slots` merge per entry, with null removing
  an entry. The result is validated (problems raise `Refused("bad_request", ...)`) and written atomically.
- `Registry` reads the setup file on each poll (mtime/size) and puts `setup` (default `{}`) on every FixtureInfo.
  An id that isn't in the registry is kept in the file and listed as a problem ("setup for x: no such light").

### 2.7 Selectors

`fixtures.select(expr, rig, *, lamps=None, spatial=None)` gains these keys (`SELECTOR_KEYS`):

- `cap:<attr>`: the fixture's type can show that attr (`cap:pan`, `cap:gobo`, `cap:zoom`).
- `side:<word>`, `height:<low|mid|high>`: spatial. The term is true when `spatial(fid, key, value)` says so, and
  false without a `spatial` callable (no problem line: an unplaced light is on no side). space.py provides the
  callable (§3.4).

### 2.8 Tests (encode): test_ofl.py, test_fixture_encode.py (new), test_fixtures.py

- **Spot fixture.** Test_ofl's Intimidator Spot 110, extended with a Gobo Rotation channel (stop 0-4, CW 5-127, stop
  128-131, CCW 132-255) and a Prism channel:
  - pan 0° → Pan/Pan fine = (0x80, 0x00); -270° → (0, 0); +270° → (255, 255); +275° clamps to (255, 255);
  - tilt ±135° → the ends;
  - gobo slot 3 → 79 (the middle of 64-95);
  - `gobo_spin` +1 → 127, -1 → 255, 0 → 2;
  - shutter `{strobe, hz: 11.5}` → 73 or 74;
  - shutter open → 0 (the first Open range's start);
  - `pt_speed` 1 → 0 (speedStart fast).
- **Parity.** `encode(attrs=None)` == colour_mix.encode for every generic type and both test_ofl fixtures. Random
  intensity and colour, 200 cases.
- **Shared channels.** A gobo channel with slots, shake and scroll: shake 0.5 on slot 2 picks the shake range, and
  shake 0 picks the static slot. ATTR_PRIORITY order holds.
- **Unknowns.** A percent-angle Pan gives 540° and a problem line; setup `range.pan: 630` gives 630°.
- **Generic movers.** `generic-mover` and `generic-mover-full` produce exact byte strings for a fixed attrs dict;
  their caps list pan, tilt, zoom, gobo and so on; `slots.count: 6` re-divides the generic wheel.
- **caps.** caps for the RGBW par: `moves` false, `attrs` ⊇ {intensity, colour}, no pan. The spot: `colour` "wheel"
  with 8 colour_slots.
- **SetupStore.** patch/merge/null-removal; an atomic write that rereads equal; a bad value refused with nothing
  written; Registry merging `setup` and reloading on the file.
- **Selectors.** `cap:pan` selects only the movers; `side:north` with a fake spatial callable; without one, nothing.

## 3. Room space (builder: space)

### 3.1 The frame

- Metres, right-handed: **x east, y north, z up**. The origin is the room's south-west floor corner. The room is the
  box [0, X] × [0, Y] × [0, Z] once its size is known.
- **front**: the compass word of the wall people face. The code default is `north`, the frame's convention, not a room
  fact. **right** = front × up (front north → right is east). **left/right** are always seen facing the front.
- **cw/ccw**: clockwise seen from above (the floor plan): N → E → S → W.
- **Surfaces** come from the room size: `floor` (z=0, normal up), `ceiling` (z=Z, normal down), `wall-n`, `wall-e`,
  `wall-s`, `wall-w` (normals into the room). Each has an origin, a normal, and in-plane unit axes `u` (along the
  surface, left to right as seen from inside the room facing it; on the floor and ceiling, east) and `v` (on walls
  up, on the floor and ceiling north). More named surfaces (a TV wall, a truss) can be added by hand.

### 3.2 `config/room/space.yaml` (hand-written; ships with every room fact commented)

```yaml
# config/room/space.yaml: the room in metres (docs/LIGHTS.md §3). x east, y north, z up; origin the south-west floor
# corner. Hand-written; the Lights page's Room map writes config/room/space.added.yaml (merged under this file: this
# file wins on a clash). show_service re-reads both when they change. Nothing here is measured yet: set the room on the
# Room map (or here), never guess.
version: 1
room:
  # size: [<east-west m>, <north-south m>, <floor to ceiling m>]   # measure
  front: north                     # the wall people face (left/right are as seen facing it); the frame's default
appliances: {}                     # fixture id or room device -> where it is (the page writes these; examples:)
  # screen.north: {at: [<x>, <y>, <z centre>], plane: {normal: [0, -1, 0], up: [0, 0, 1], size: [<w>, <h>]}}
  # hue.ne: {at: [<x>, <y>, <z>]}
  # dmx.mover-1: {at: [<x>, <y>, <z>], mount: ceiling}            # ceiling | floor | wall | truss (the solve's hint)
points: {}                         # named places lights can aim at (the page writes these too; examples:)
  # piano: {label: The piano, at: [<x>, <y>, <z>]}
  # mirror-ball: {label: Mirror ball, at: [<x>, <y>, <z>], device: mirror_ball}
regions: {}                        # named areas on a surface, e.g. a dance floor (centre + size on that surface)
  # dance-floor: {label: Dance floor, surface: floor, centre: [<x>, <y>, 0], size: [<w>, <d>]}
surfaces: {}                       # extra planes beyond floor, ceiling and the four walls
  # tv-wall: {label: TV wall, origin: [...], normal: [...], up: [...], size: [<w>, <h>]}
```

`config/room/space.added.yaml` has the same keys, written only by the API (whole, atomic, with a header saying so).
Its `appliances.<id>` may also carry `teach: [TeachSample]` and `pose: MoverPose` (§3.6). Ids follow fixtures.py's
ID_RE. Labels are page words.

### 3.3 Shapes (contract, the space block)

```python
FLOW_DIRS = ("still", "up", "down", "left", "right", "cw", "ccw", "outward", "inward", "towards", "away")
FLOW_OPPOSITE = {"up": "down", "left": "right", "cw": "ccw", "outward": "inward", "towards": "away"}  # and back
SPACE_FILE = CONFIG_ROOM_DIR / "space.yaml"; SPACE_ADDED_FILE = CONFIG_ROOM_DIR / "space.added.yaml"
MOUNTS = ("ceiling", "floor", "wall", "truss")

class Surface(TypedDict):
    name: str; label: str
    origin: list[float]; normal: list[float]; u: list[float]; v: list[float]
    size: Optional[list[float]]          # [w, h] along u, v from origin; None = unbounded

class SpaceView(TypedDict):
    revision: str; error: Optional[str]; problems: list[str]
    room: dict                           # {"size": [X, Y, Z] | None, "front": str, "centre": [x, y, z] | None,
                                         #  "right": [x, y, z]}
    surfaces: dict[str, Surface]
    points: dict[str, dict]              # name -> {"label", "at", "device"?, "from": "hand" | "page" | <fixture id>}
    regions: dict[str, dict]             # name -> {"label", "surface", "centre", "size"}
    appliances: dict[str, dict]          # id -> {"at", "kind"?, "mount"?, "plane"?: {normal, up, size},
                                         #        "teach": [TeachSample], "pose": MoverPose | None, "editable": bool}
    extent: Optional[list[list[float]]]  # [[min xyz], [max xyz]]: the room box, else the placed things' bounding box

class TeachSample(TypedDict):
    pan: float; tilt: float              # centred degrees, as it was held
    point: Optional[str]                 # the name it was pointed at
    at: list[float]                      # that point's room position when taught (kept if the point moves later)
    t: str                               # ISO time

class MoverPose(TypedDict):
    at: list[float]                      # solved position, m
    rot: list[float]                     # rotation vector (axis × angle, rad): fixture frame -> room
    tilt_offset_deg: float
    pan_sign: int; tilt_sign: int        # +1 | -1 (inverted pan or tilt)
    rms_deg: float; worst_deg: float; samples: int
    solved_at: str

class PoseFit(TypedDict):
    pose: Optional[MoverPose]
    rms_deg: Optional[float]; worst_deg: Optional[float]; worst_index: Optional[int]
    spread_deg: float                    # the widest angle between two taught directions
    samples: int
    level: str                           # "good" | "ok" | "rough" | "bad" | "need_more"
    words: str                           # page words (§3.6)

class ScreenFlow(TypedDict):             # the flow as one screen sees it (§4.4); FlowData.screens values
    fixture: str                         # the screen fixture id ("screen.north")
    x: float; y: float                   # the flow in the screen's plane: x right, y up, as a viewer facing it sees
    angle_deg: float                     # atan2(y, x): 0 = towards its right, 90 = up (counter-clockwise as seen)
    inplane: float                       # 0..1: |(x, y)|
    depth: float                         # -1..1: + toward the viewer (out of the screen), - into it
    radial: int                          # +1 expand from the screen's centre (outward, away), -1 contract (inward,
                                         # towards), 0 otherwise
    spin: int                            # +1 cw, -1 ccw (cw/ccw flows; the same sense on every screen), else 0
    phase: float                         # 0..1: the screen's place along the flow (flow_coord of its centre)
```

### 3.4 `show_service/space.py` (new, pure maths plus one store)

```python
class Refused(ValueError): code: str; detail: str          # as fixtures.Refused
class Store:                                               # one per process: shared()
    def poll(self, force: bool = False) -> bool            # mtime/size of both files; a bad file keeps the last good
    def view(self) -> SpaceView                            # swapped by reference
    def subscribe(self, fn: Callable[["Store"], None]) -> None   # weak, as light_scenes.Catalogue
    # writers (show-brain only; each validates, writes space.added.yaml whole and atomically, then polls):
    def set_room(self, size: Optional[list[float]], front: Optional[str]) -> SpaceView
    def set_appliance(self, aid: str, *, at=..., mount=..., plane=...) -> dict   # a key given as None clears it
    def remove_appliance(self, aid: str) -> None
    def set_point(self, name: str, at: list[float], label: Optional[str] = None, source: str = "page") -> dict
    def remove_point(self, name: str) -> None
    def set_region(self, name: str, surface: str, centre: list[float], size: list[float], label=None) -> dict
    def add_sample(self, aid: str, s: TeachSample) -> list[TeachSample]
    def remove_sample(self, aid: str, index: int) -> list[TeachSample]
    def clear_samples(self, aid: str) -> None
    def set_pose(self, aid: str, pose: Optional[MoverPose]) -> None
def shared() -> Store

def resolve(view, name: str) -> Optional[Vec3]   # points, then regions (centre), appliances (at), surfaces (centre)
def region_of(view, name: str) -> Optional[tuple[Surface, Vec3, list[float]]]   # (surface, centre, size)
def surface_of(view, name: str) -> Optional[Surface]                            # also a point's nearest surface
def surface_point(s: Surface, u: float, v: float) -> Vec3
def surface_uv(s: Surface, p: Vec3) -> tuple[float, float]
def flow_vector(view, dir: str, centre: Optional[Vec3], p: Vec3) -> Vec3       # §3.5 (unit, or 0)
def flow_coord(view, dir: str, centre: Optional[Vec3], p: Vec3) -> float       # §3.5, 0..1
def lateral(view, p: Vec3) -> float              # signed distance right of the front axis through the centre (m)
def screen_flow(view, screen_id: str, dir: str, centre: Optional[Vec3]) -> Optional[ScreenFlow]
def spatial_fn(view) -> Callable[[str, str, str], bool]   # fixtures.select's `spatial`: side:<n|e|s|w|front|back|
                                                 # left|right> (which half of the room), height:<low|mid|high> (thirds)
def solve_pose(samples: list[TeachSample], *, at_hint: Optional[Vec3], mount: Optional[str],
               pan_range: tuple[float, float], tilt_range: tuple[float, float],
               cfg: Optional[dict] = None) -> PoseFit
def beam_dir(pose: MoverPose, pan: float, tilt: float) -> Vec3
def aim(pose: MoverPose, q: Vec3, *, current: tuple[float, float], pan_range, tilt_range,
        limits: Optional[dict] = None) -> Aim     # Aim = {"pan", "tilt", "reachable": bool, "error_deg": float}
def ray_hit(view, origin: Vec3, d: Vec3) -> Optional[tuple[Vec3, str]]   # the first surface hit (bounded surfaces)
```

`Vec3` is a `tuple[float, float, float]`. **The hot path is pure Python math**: `aim`, `beam_dir`, `flow_*`,
`surface_*`, run per fixture per frame. NumPy and SciPy are for `solve_pose` only. `aim` costs under 20 µs.

### 3.5 Flow geometry

c = `centre` (the flow's `at`, else the room centre, else the extent's centre). f̂ = front, r̂ = right, ẑ = up.
R = the largest distance from c to a corner of the extent; D(X) = the largest distance from X to a corner.

| dir | `flow_vector(p)` | `flow_coord(p)` (0 = where the wave starts) |
|---|---|---|
| still | 0 | 0 |
| up / down | ±ẑ | z/Z, or 1 - z/Z |
| right / left | ±r̂ | ((p-c)·r̂ + h)/2h with h the extent's half-width along r̂; left = 1 - that |
| cw / ccw | ±norm((p-c)ₕ × ẑ) (0 at c) | a = atan2((p-c)·r̂, (p-c)·f̂) mod 2π; cw = a/2π, ccw = 1 - a/2π (front = 0) |
| outward / inward | ±norm(p-c) | \|p-c\|/R, or 1 - that |
| towards / away (X = c) | ±norm(X-p) | 1 - \|p-X\|/D(X), or \|p-X\|/D(X) |

Fallbacks: with no extent, coords come from the placed things' bounding box. A fixture with no position takes
`i/n` in its group's order (engine side).

**Symmetry `mirror`**: coords use |lateral| in place of the signed coordinate where the dir is left/right, cw/ccw or
outward, so the two halves fire together. Patterns mirror across the front axis (§5.4).

**screen_flow**: s = the screen's centre, n̂ = its plane normal (into the room), û = its up, and its right
r̂ₛ = û × n̂ (a screen on the west wall, normal +x, has right = north). Then d = flow_vector(s), x = d·r̂ₛ, y = d·û,
depth = d·n̂, angle = atan2(y, x), inplane = |(x, y)|, phase = flow_coord(s). In this room both screens face east
(owner, 22 Oct), so a flow towards north reads "right" (0°) on both, and runs from the south screen to the north
screen in phase. Screens facing each other would see one flow at 0° and 180°.

### 3.6 Mover pose: the teach-and-solve

**Model.** A mover at P with rotation R (fixture frame → room). In the fixture frame, z_f is the pan axis (out of the
base: down for a hung head) and the beam is
b_f(θ, φ) = (sin φ cos θ, sin φ sin θ, cos φ), where θ = pan_sign·pan and φ = tilt_sign·tilt + tilt_offset (centred
degrees, so tilt 0 + offset 0 is the beam along the pan axis). The room beam is d = R·b_f. A pan offset is a rotation
about z_f, so it is absorbed in R.

**Solve** (`solve_pose`):
- Unknowns: P (3), R as a rotation vector (3) and tilt_offset (1).
- Each sample gives the residual d(pan, tilt) - norm(at - P), 3 components (|·| ≈ the angle).
- With `at_hint` (the map placement), three prior residuals (P - at_hint)/`pos_sigma_m` are added. Without it, the
  room's centre at ceiling height (mount ceiling, else mid-height) with sigma 2 m.
- `scipy.optimize.least_squares`, multi-start: pan_sign × tilt_sign (4) × mount flip (z_f up or down: 2) × yaw
  0..315° in 45° steps (8). The best cost wins.
- Error per sample: 2·asin(|r|/2) in degrees. Report rms and worst.

| level | when | words (page) |
|---|---|---|
| need_more | fewer than 3 samples | "Point it at 3 places so it learns where it hangs (n so far)." |
| need_more | spread_deg < `min_spread_deg` | "Point it at places further apart." |
| good | rms ≤ fit_deg[0] | "Spot on (within 1°)." |
| ok | rms ≤ fit_deg[1] | "Good (within N°)." |
| rough | rms ≤ fit_deg[2] | "Rough: point it at one more place." |
| bad | worse | "It doesn't fit: check the places on the map, and where the light hangs." |

The words take the thresholds from config. A rough or bad fit is still stored and used (degrade): the page says so.

**Aim** (`aim`):
- b_f = Rᵀ·norm(q - P), φ_t = acos(b_f.z), θ_t = atan2(b_f.y, b_f.x).
- Candidates: (θ_t, φ_t) and (θ_t + 180°, -φ_t), each with pan + k·360° for k in -2..2. Each is mapped back to
  command degrees through the signs and offset.
- Keep the candidates inside the fixture range ∩ `limits`. Choose the one with the least travel time
  max(|Δpan|/v_pan, |Δtilt|/v_tilt) from `current` (continuity: no flips while the current branch still reaches).
- None in range: the nearest reachable point (clamped), with `reachable: False` and the angular error. The engine
  lists it in stats, and nothing refuses.

`beam_dir` is the forward model. `ray_hit` intersects with the bounded surfaces (the room box first, then the named
surfaces) and returns the nearest hit along d > 0.

**Named positions become room points.** When a calibrated mover's position is saved under a new name, the page route
(§8.1) casts `ray_hit` from its pose. When the ray lands on a surface, `set_point(name, hit, source=<fixture id>)`
makes it a room point every calibrated mover can aim at.

### 3.7 Tests (space): test_space.py (new)

- **Synthetic room** 6 × 8 × 3 (front north). flow_coord for every dir at known points: right gives the east wall 1
  and the west wall 0; cw gives north of centre 0, east 0.25, south 0.5, west 0.75; outward gives the centre 0 and a
  corner 1; towards X gives X 1. flow_vector is unit length (0 for still and at the centre).
- **Screens.** North screen (3, 8, 1.5), normal (0, -1, 0), up z: flow right → angle 0, inplane 1, depth 0. South
  screen (normal +y): right → angle 180. Up → 90 on both. Outward → depth -1 and radial +1 on the north screen.
  cw → spin +1.
- **Mirror.** Points at lateral ±2 m get equal coords under `mirror` for left, right and outward.
- **Solve.** A synthetic pose (P = (2, 3, 2.9), hung, yaw 37°, tilt_offset 3°, pan_sign -1) and 4 room points (floor
  and walls), with σ 0.3° noise on pan/tilt:
  - rms < 1°, |P - P_true| < 0.1 m, level good;
  - 3 points plus at_hint within 0.3 m: rms < 1.5°;
  - nearly collinear points: need_more with the "further apart" words;
  - a wrong point (2 m off): worst_index names it, level rough or bad.
- **Aim.** For 200 random points within range, angle(beam_dir(aim(q)), q - P) < 0.05°. From `current` pan +200°, the
  chosen pan stays on the same turn. A point out of range: reachable False, clamped to the limits.
- **ray_hit.** Straight down from (2, 3, 2.9) → (2, 3, 0) on the floor; a beam at the north wall → wall-n.
- **Store.** Hand plus added merge (the hand file wins with a problem line); a bad file keeps the last good; writes
  are atomic and reread equal; the weak subscriber is notified; Refused for a bad id or a non-finite coordinate, with
  nothing written.
- **spatial_fn.** side:north/south, left/right by front, height thirds.

## 4. Flow (builders: catalogue for the decision, space for the maths)

### 4.1 The shape: `room.direction.flow` and `room.look.flow`

```python
class FlowData(TypedDict):          # contract (catalogue)
    id: int                         # +1 per change of dir, at or symmetry (not of speed or spread)
    dir: str                        # FLOW_DIRS
    at: Optional[str]               # a space name: towards/away's target; cw/ccw/outward/inward's centre; None = the
                                    # room's centre (or show.yaml lights.flow.centre)
    at_xyz: Optional[list[float]]   # that point resolved (m); None without a space
    speed: float                    # 0..1
    spread: float                   # 0..1: 0 unison, 1 one whole cycle across the room
    coherence: float                # 0..1: how strictly every appliance should follow it (events 1.0)
    symmetry: str                   # "none" | "mirror"
    period_beats: Optional[float]   # the wave's crossing time, a power of two of beats; None without a clock grid
    period_s: float                 # the same in seconds (with no grid, the one to use)
    anchor_beat: Optional[float]    # the clock's beat number at at_ms (a bar line): phase 0 leaves the start then
    at_ms: float                    # when it takes effect (host time, >= now + the screens' lead, on a bar line)
    source: str                     # FLOW_SOURCES: "structure" | "event" | "pitch" | "listener" | "moods" | "none"
    why: str                        # short words: "the song builds", "rising melody", "a drop"
    screens: dict[str, ScreenFlow]  # keyed by the renderer's screen name (FixtureInfo.screen: "north-screen")
FLOW_SOURCES = ("structure", "event", "pitch", "listener", "moods", "none")
```

**The shared phase.** Every consumer (the look engine per fixture, the renderer per screen) computes:

```
wave(t, p) = frac( (beat(t) - anchor_beat) / period_beats  -  spread × flow_coord(p) )   # with a grid
           = frac( (t - at_ms/1000) / period_s           -  spread × flow_coord(p) )   # without
```

Each evaluates it at its **own** photon time (the engine at t_send + its latency; the renderer at its live lead). A
wave therefore crosses the room on the beat, latency compensated, through screens and lights alike.

### 4.2 The decision (catalogue: `show_service/flow.py`, new and pure, driven by director.py)

```python
class FlowDecider:
    def __init__(self, cfg: dict) -> None
    def votes(self, *, phase: Optional[str], register: Optional[tuple[float, float, float]],   # (t_s, register, conf)
              trajectory: Optional[dict], moods: list[str], energy: float) -> dict[str, float]
    def event(self, kind: str, at_s: float, grid_bar_s: Optional[float]) -> None   # drop | build | track_change ...
    def step(self, now_s: float, votes: dict[str, float]) -> tuple[str, str, float, str]   # (dir, source, coherence,
                                                                                          #  why)
    def view(self) -> dict          # for the snapshot: votes, current, candidate, since, hold
```

- **Votes** each tick (10 Hz). Each source adds weight × confidence to one dir (show.yaml `lights.flow`, §12):
  - **structure**: music.context.section.phase → `structure` map. Weight `sources.structure`.
  - **pitch**: the least-squares slope of `harmony.register` over `pitch.window_s` (samples with harmony conf ≥
    `pitch.min_conf` and the gate open). A slope ≥ `pitch.slope` votes up, ≤ -slope votes down, with weight
    `sources.pitch` × min(1, |slope| / (2 × slope)). The piano's MIDI feeds harmony while it plays, so a rising run
    reads upwards.
  - **listener**: beliefs.trajectory.state at conf ≥ LLM_CONF → `listener` map, weight `sources.listener` × conf.
  - **moods**: room.direction's first 3 moods → `moods` map, each with weight `sources.moods` / 3.
- **Net score**: net[d] = score[d] - score[opposite(d)]. The leader is max net. Under `min_score` (or energy under
  `still_below_energy`) the leader is `still`.
- **Hysteresis and dwell**, as the band words: a new leader must beat the current dir's net by `margin` and hold that
  for `dwell_s` before it lands.
- **Events bypass the dwell.** `events.drop`: dir for `bars`, then `then`. `events.build`: up while the phase is build.
  `events.track_change`: the voted dir lands at the new song (`sweep_beats` goes on the event, §7.3). Coherence =
  `coherence.events`. Otherwise coherence = max(`coherence.floor`, min(1, net / (2 × min_score))).
- **speed** = clamp(`speed.pace` × room.direction.pace + `speed.energy` × energy). **spread** = the look's spread
  (`spread: look.spread`). **symmetry** = `lights.flow.symmetry`.
- **Timing.**
  - at_ms is the next bar line ≥ now + `structure.events.min_lead_s` (the director's `Grid.next_line`); an event's
    own at_ms when it has one.
  - period_beats = cross_beats[0] × (cross_beats[1]/cross_beats[0]) ** speed, rounded to a power of two (looks.py's
    `_period` rule).
  - period_s from `cross_s` the same way, or period_beats × beat_s with a grid.
  - The director fills `at_xyz` and `screens` with space.resolve and space.screen_flow for each screen fixture.
- room.direction carries `flow` always (neutral too: still). It publishes at once on a new `flow.id` (added to
  `_differs`' words). room.look carries the same object as `LookData.flow`.

### 4.3 How each appliance reads it

| appliance | reads | does |
|---|---|---|
| **lamps** (intensity only) | intensity layers with `order: flow` | phase lag = spread × flow_coord(p): a wave travels the room along the flow. `still` = unison |
| **movers** | position layers (`along: flow`, `rotate: flow`, `centre: flow`) | sweeps lie along the flow projected on their surface; circles turn cw/ccw by it; `up`/`down` lift or sink the aim (`lift` layer); `outward` fans out from `at`; `inward`/`towards` converge on `at`; gobo and prism `spin: flow` take its sign (cw +) and speed |
| **screens** | `flow.screens[<screen>]` (renderer, §9) | content direction params, wipe angle, particle flow, spin, radial zoom; transitions start at their phase in a sweep |
| **mirror-ball motor** (room device) | cw/ccw + speed | PUT room targets `mirror_ball_motor.direction`/`.speed` (§4.4) |
| **transitions** (lights and screens) | the program's transition kind `sweep`; events' `sweep_beats` | each appliance switches at at_ms + phase × sweep_beats beats |
| **blinds** (optional, off) | up/down at a big song change | PUT room targets (§4.4) |

### 4.4 Mirror ball and blinds (catalogue, director.py)

- **Mirror ball** (show.yaml `lights.flow.mirror_ball`). The flow turns it, never powers it.
  - The director acts only while the room member `power_member` reads `power_on` (RoomView.members[...].believed) and
    the flow is cw or ccw.
  - At most once per `min_gap_s` (mechanical: the motor's reversal), at a bar line, it sends
    `ctx.room_put("/api/v1/room/targets", {"values": {direction_target: directions[dir], speed_target: <word from
    speeds by flow.speed>}, "source": "show"})`.
  - Other dirs leave it as it is. Failures are logged (no retry storm: the next change tries again).
- **Blinds** (`lights.flow.blinds`, `enabled: false`). When on, a big song change (TrackTransition change "big") with
  flow up or down sends `targets` to the matching value. The targets and values are commented examples until the
  owner sets them.

### 4.5 Tests (catalogue, test_flow.py, new)

- Votes per source. Opposites cancel (up 0.6 and down 0.5 give up at 0.1, still under min_score). Dwell and margin
  hold a flapping input. A drop event lands outward at its at_ms, then down after `bars`. A rising register ramp votes
  up and a falling one down.
- Timing: at_ms on a bar line ≥ lead; period_beats is a power of two; anchor_beat is the beat at at_ms.
- The director publishes `flow` on room.direction and room.look, with a new id only on a dir change. The mirror-ball
  put is made only while spinning, at most once per gap, and with the configured words.

## 5. Engine layers (builder: engine; looks.py, outputs.py hooks)

### 5.1 configure and evaluate

`LookEngine.configure(looks, rig, *, lamps=None, lights=None, space=None)`. `lights` is show.yaml's `lights` block
(§12); `space` is a SpaceView (None: no positions). outputs.Rig passes them, polling `space.shared()` in `_sync` and
reconfiguring on its revision. `_Slot` gains:

- `caps` (fixture_encode.caps), `setup`, `pos` (space appliance `at`, or the pose's `at`), `pose`;
- `pos_s` (LatencyView `position_ms`), `group` (§7.1, from `light_scenes.group_members`);
- `enc` (true for a type with any ranges beyond intensity, colour and wheel);
- `max_speed` (setup, else `lights.motion.default_max_speed_deg_s`);
- `home`, `limits`, `range`, `beam`.

Per fixture, precedence each frame (the first that applies):

1. **held** (§5.7): the hold's values, smoothed. Nothing else.
2. **identify**: full white, shutter open, position unchanged (as today plus the position).
3. **follow** off or a colour: as today. A mover holds its last position.
4. **inactive / House / Low**: house intensity and colour, as today. A mover glides home at its speed
   (`busy` until it lands).
5. **active**:
   - the group's program (`LookData.light_programs[group]`, else the compat `light_scene`);
   - scene layers (crossfading per group, §5.6), then effect overlays, then hits, master and caps;
   - then the motion stage (§5.5) and encode with `fixture_encode.encode(f, t, intensity, colour, attrs)` for an
     `enc` type (colour_mix.encode otherwise, unchanged).

Times per fixture: ti = t + rise (intensity), tc = t + colour, **tp = t + position_ms** (position, beam, gobo and
prism layers evaluate at tp). Hits stay at once.

### 5.2 Layer kinds

A layer's `kind` defaults from `attr` (today's `attr: intensity` → kind `intensity`), so every existing scene loads
unchanged. `select` defaults to `"*"` (and so does a `hits` block's) and is intersected with the group's members and with the
members that have the kind's capability. **Capability-aware**: a member without the attrs is skipped silently (stats count it), never a
problem line.

Common keys, as today: `id, enabled, select, phase, period_bars, period_s, subdivide, needs, without_beat, depth,
depth_scale, spread, attack_ms, decay_ms`. New common keys:

- `order`: `group` (default) | `flow` | `mirror`;
- `spread_mode`: `unison` | `chase` | `mirror`;
- `flow_dir`: a FLOW_DIRS word. An effect uses it to run its own direction instead of the program's flow.

**An oscillator spec** may stand in for any continuous value: `{fx, phase, period_bars | period_s, subdivide,
between: [a, b], spread, order}`. The value is a + (b - a) × shape at the member's phase, using today's machinery
(glides, fades, coarsening).

| kind | keys | attrs it sets | combine |
|---|---|---|---|
| `intensity` | today's, + `order` | intensity | multiply (as today), HTP for hits |
| `position` | `mode`, `on`, `centre`, `size`, `size_deg`, `along`, `rotate`, `fan`, `ease`, `points`, `hop`, `lift`, `dark_move` | pan, tilt | LTP in file order: pos = lerp(previous, own, depth) in pan/tilt |
| `beam` | `zoom` (deg, `narrow`/`wide`/`mid`, `default`, or an oscillator), `focus`, `iris`, `frost` | zoom, focus, iris, frost | LTP, glided over the transition |
| `gobo` | `wheel` (`gobo`/`gobo2`), `slot`, `change_on`, `spin`, `spin_scale`, `shake` | gobo*, gobo*_spin, gobo*_shake | LTP |
| `prism` | `in` (bool, or `accents`: in while the accents style is beat/full), `spin`, `spin_scale` | prism, prism_spin | LTP |
| `colour` | `role` (base/accent/highlight from LookData.palette.roles), `depth` (number or oscillator) | colour | OKLab mix toward the role colour, LTP |
| `colour_wheel` | `mode`: `follow` (default: nearest slot, today) \| `step` (`step_on` beat/bar) \| `spin` (`spin`) | colour_slot, colour_spin | LTP |
| `cct` | `k` (kelvin, or `key_cct` = the look's) | cct | LTP |
| `shutter` | `mode` (`strobe`/`pulse`/`random`), `hz` (number, or [lo, hi] by energy) | shutter | **effects only** (a scene's shutter layer is a problem line and left out) |

**Word values.**
- `spin`: a number in -1..1; `flow` (cw → +, ccw → -, any other dir +, size = flow.speed); `pace` (look
  motion.speed, cw).
- `slot`:
  - a slot name (case-insensitive substring of the setup or OFL slot name: "star" matches "Stars");
  - an index;
  - a list (candidates, stepping on `change_on`: none/bar/phrase/section/look);
  - `auto` (every non-Open slot in turn).

`LOOK_INPUTS` gains `flow.speed`, `flow.spread` and `flow.coherence`.

### 5.3 Targets

A target is a name or a list of names (the first that resolves wins). For a given fixture:

1. its own `setup.positions[name]` (works uncalibrated);
2. with a pose: `space.resolve(name)`, aimed through IK;
3. special words:
   - `home`;
   - `flow` (the flow's `at`);
   - `centre` (the room's centre on the layer's surface);
   - `up` / `down` (the ceiling or floor point above or below the fixture);
4. none of these: the layer contributes nothing for that fixture (it stays where the layers below put it), counted in
   stats as `unresolved`.

`on` (the pattern's surface) is a surface, region or point name. A point's surface is the room surface nearest to it.
Defaults: circle/figure8/drift/ballyhoo → `floor`; sweep → the surface of `centre`.

### 5.4 Position modes

All patterns are computed in a surface's (u, v) metres and mapped by `surface_point` → `aim`. **Uncalibrated** (no
pose), the same pattern runs in the fixture's own (pan, tilt) degrees around `home`, using `size_deg`, with u → pan
and v → tilt. Phase w = the member's oscillator cycle (`phase`, `period_*`, `needs`, spread lag by `order`), eased by
`ease` (linear | sine | smooth).

| mode | path (u, v offsets from the centre, size s) |
|---|---|
| `aim` | (0, 0): at `centre` |
| `fan` | members spread along the `along` axis: offset s × (2i/(n-1) - 1) × `fan` (static) |
| `sweep` | s × E(w) along `along` (a back-and-forth with E the ease of a triangle) |
| `circle` | s(cos 2πw, ±sin 2πw), sign from `rotate` (cw/ccw seen from inside, facing the surface; on the floor and ceiling, seen from above) |
| `figure8` | s(sin 2πw, ½ sin 4πw) |
| `drift` | s × Σ₃ aₖ sin(2π fₖ w + φᵢₖ): three incommensurate sines, a per-member seed (slow wander) |
| `ballyhoo` | s(sin 2πw, ⅔ sin 3πw + ⅓): big, fast, sweeping |
| `bounce` | hops between `points` (targets) on `hop` (beat/bar), pre-rolled (§5.5) |
| `lift` | the target rises toward the ceiling (up flow) or sinks toward the floor (down flow): the target z moves by `lift` × depth × flow sign, in metres |
| `tilt_wave` | tilt = home.tilt + size_deg × (2·shape(w) - 1), with the layer's `fx` (default sine): a "Mexican wave" along `order: flow` |
| `home` | home |

- `along`: `flow` (the flow vector projected onto the surface, normalised; `still` or zero falls back to the
  surface's u), a FLOW_DIRS word, or `u`/`v`.
- `rotate`: `cw`, `ccw` or `flow` (cw/ccw from the flow, else `cw`).
- **Spread**: `chase` lags member i by spread × i/n (or × flow_coord with `order: flow`). `mirror`: members right of
  the front axis (space.lateral > 0) negate u and take their left partner's phase. `unison`: no lag.

### 5.5 Mechanical timing

- **Slew.** Commanded pan and tilt move at most `max_speed` × dt per frame, with an acceleration allowance (a
  smoothstep over `accel_s` from rest). The light is never asked to go faster than it can, so the latency model holds.
  `pt_speed` is sent at `lights.motion.pt_speed` (the fixture's fastest; the engine does the smoothing).
- **Soft limits.** Every commanded value is clamped to `setup.limits` (when set) and the range. IK picks within them.
  A clamped layer is listed in stats as `clamped`; no light is refused.
- **Pre-roll** for beat-locked moves (bounce, snaps, a cut that lands on a line):
  - travel = max(|Δpan|/v_pan, |Δtilt|/v_tilt) + accel_s;
  - the target switches at (the line - travel), evaluated at tp (the position latency is already in tp), so the beam
    **arrives** on the beat.
  - When travel > `max_travel_share` × the hop's period, the hop period doubles (on the grid) until it fits. The
    layer is listed under `slowed`.
- **Degrade, never trip.** A pattern whose peak angular speed (sampled over one cycle at configure, and again when
  its size or period changes) exceeds max_speed × 0.95:
  - first shrinks, down to `min_size_share`;
  - then doubles its period (on the grid) and re-fits.
  The commanded speed never exceeds the limit. Stats `slowed` lists each such layer with its size and period factor.
- **Dark moves.**
  - When the program's transition says dark moves, or a scene change asks a mover to jump more than
    `dark_move_min_deg`, intensity goes to 0 at least `rise/fall latency` before the move starts.
  - The move runs at max speed, and intensity comes back at arrival + `settle_ms`, through the normal rise limit.
  - For a dark-move cut, the move is pre-rolled so the light comes up **at** at_ms.
- **busy**: true while any member of the output is moving under a glide to home or a hold. The runner plans at the
  active rate while busy (§5.8).

### 5.6 Programs, effects and transitions in the engine

- **Per group.** LookData.light_programs[g] names the scene. Scenes are compiled per (scene, group) at configure and
  when the catalogue or space changes, as light scenes are today (`_compile_scenes` per group; a layer's `(i, n)`
  is within the group). The scene crossfade key is per group (`SCENE_FADE + group`). No programs → today's
  `light_scene` path for every lamp.
- **Effects** (LookData.light_programs[g].effects, compiled per (effect, group)):
  - each run's envelope e(t): up over `fade.in_beats` from at_ms, held to until_ms, down over `fade.out_beats`
    (`until_ms` null: held while the run is listed);
  - intensity effect layers multiply as layers do, with depth × e;
  - other kinds are LTP over the scene, blended by e (position lerp; discrete attrs when e ≥ 0.5);
  - effect `hits` join the scene's (HTP);
  - an effect's `caps.flash_hz_max` applies while it runs, and setup `strobe_hz_max` always caps that light's
    strobe hz.
- **Transitions** (LightTransitionRun, §7.2), by `kind`:
  - `cut`: the new scene at at_ms; positions travel at max speed.
  - `dark_move`: as §5.5.
  - `crossfade`: intensity crossfades (today); positions lerp from the old scene's command to the new one's over
    beats/seconds, within the slew.
  - `fade_dark`: intensity × (1 - x) over out_s ending at at_ms, toward the group's `dark_level` (`floor` = the look's
    floor); positions move while dark; then fade in over in_s from `in_at_ms` (or at_ms + `dark_max_s`).
  - `sweep`: each member's crossfade starts at at_ms + flow_coord(p) × `wave_beats` beats, along the program's
    flow.

### 5.7 The hold (jog and registration)

```python
LookEngine.hold(fixture_id: str, values: Mapping[str, Any], *, idle_s: float, smooth_ms: float,
                free: bool = False) -> dict          # any thread; merges values into the held target
LookEngine.release(fixture_id: str) -> bool
LookEngine.holds() -> dict[str, HoldView]

class HoldView(TypedDict):          # contract (engine)
    target: dict[str, Any]          # what was asked (attr -> value), after clamping
    now: dict[str, Any]             # the smoothed values on the last frame
    until_s: float                  # monotonic: released at this time unless another hold call comes
    free: bool                      # soft limits ignored (the page's Set limits panel)
```

- **Starting a hold.** The first call for a fixture starts from its current frame attrs (where it is now), with
  intensity 1 and shutter open unless the call sets them. Later calls merge.
- **Smoothing.** pan/tilt glide toward the target (first order, tau `smooth_ms`) within max_speed. Intensity, zoom,
  focus, iris and frost glide over `smooth_ms`. Discrete attrs change at once.
- **Ending.** It ends on `release`, or at `until_s` = last call + `idle_s`. Then the fixture returns to its program
  through the normal motion stage (a glide, never a jump).
- **A held fixture is out of everything:** layers, effects, hits, master, caps, follow, identify.

### 5.8 outputs.py hooks (engine) and the frame

- `FixtureFrame` gains `attrs: dict[str, Any]` (the normalised values sent: pan, tilt, zoom, gobo, ...), present for
  `enc` types. `Frame` gains `busy: bool`.
- `_Runner._run`: `active = bool(frame.get("active") or frame.get("busy"))`, so an output with a held or gliding
  light frames at its active rate (artnet: the grid, ≤ 43 Hz).
- `Rig.configure` reads show.yaml `lights` (dirty → engine reconfigure). `Rig._sync` polls `space.shared()` (lazy
  import) and reconfigures on a new revision.
- New Rig methods for api.py:

```python
Rig.jog(fid: str, values: Mapping[str, Any], *, release: bool = False, free: bool = False) -> dict
    # any thread, no brain hop: validates attrs against caps (unknown ones listed, ignored), clamps, engine.hold or
    # release, wakes the runner. -> {"held": bool, "until_s": float | None, "idle_s": float, "target": {...},
    #    "now": {...}, "unknown": [attr], "why": str | None}; ValueError("bad_request: ...") for a non-mapping
Rig.holds() -> dict[str, HoldView]
Rig.caps_of(fid: str) -> Optional[FixtureCaps]
Rig.reload_now() -> None            # show-brain: force the registry and space reload at the next _sync (an API write)
Rig.live_view() -> LiveView         # any thread (§8.1): from the runners' last frames and the space poses
```

- `frame_view` adds `attrs` per fixture. `snapshot` adds `engine.stats()["motion"]`: {per fixture: slowed, clamped,
  unresolved, unreachable, held}.

### 5.9 Tests (engine): test_looks.py additions, test_looks_motion.py (new), test_outputs.py additions

- **Capability-aware.** An RGB par and a Hue lamp in a mover scene: no attrs, bytes == the colour-only path.
- **Uncalibrated circle.** pan/tilt = home ± size_deg on the circle at the phase.
- **Calibrated circle on the ceiling** (synthetic pose): every beam hit (space.ray_hit) is within 2 cm of radius r
  around the centre on the ceiling.
- **Slew.** A 180° jump never exceeds max_speed × dt per frame.
- **Pre-roll.** A bounce between points 60° apart at 120 bpm, max 180°/s; a simulated fixture that follows the
  command at max speed after position_ms reaches each point within one frame (23 ms) of the beat. The same at
  240 bpm gives the hop period doubled and `slowed` listed.
- **Degrade.** A circle needing 400°/s at max 180: peak commanded speed ≤ 180 × 1.0 (+1 frame of rounding), size ≥
  min_size_share, then the period doubled.
- **Dark move.** Intensity is 0 on every frame where the beam moves more than 0.5° per frame during the move, and
  rises after arrival + settle. A dark-move cut lights at at_ms (± 1 frame).
- **Hold.** The hold overrides the scene and effects; release returns through a glide; idle_s releases; `busy` is
  true while held; a held fixture ignores identify and follow; hold `free` ignores the limits.
- **Inactive.** A mover glides home, with `busy` until it lands.
- **Programs.** Two groups with two scenes; the per-group crossfade; the compat path with no programs.
- **Flow order.** Under "right", the west lamp leads the east one by spread × Δcoord cycles. `mirror` makes the two
  sides equal.
- **Outputs.** jog through Rig on a fake runner: frames at the active rate while held; frame_view carries attrs.

## 6. Catalogues (builder: catalogue)

### 6.1 Shared rules

There are three hot-reloaded files, read by the one `light_scenes.Catalogue` (`shared()`). It gains `effects`,
`transitions` and one revision over all three; a bad file keeps that file's last good entries. Every entry:

- `label`: page words.
- `needs: [attr]`: hard. The entry fits a group only if at least one member has every listed attr.
- `layers`: §5.2.
- `hits`, `caps`: as today.
- `tags`: today's scales (energy, moods, genre_fit with "*", valence, organic, density, weight) plus:
  - `flow: {dirs: [FLOW_DIRS], honours: bool}`. `dirs` are the flows it suits; `honours` true means its layers bind
    to the live flow (it fits any dir).
  - transitions also take `transition_style` (vocab).

**Weight for a group** = tags.scene_weight (tags.py by path, as today; called with `flow` removed from the direction,
so a later flow descriptor in tags.py never counts twice) × **cap_fit** × **flow_fit**:

- cap_fit = 0.3 + 0.7 × |the expressive attrs the entry drives ∩ the group's| / |the group's expressive attrs|.
  Expressive = {pan, tilt, gobo, prism, zoom, colour_slot, intensity}, and an entry with none of them counts
  intensity.
- flow_fit = 1 when dir ∈ dirs or `honours`; 0.6 otherwise (`FLOW_MISMATCH`, starting point). An untagged entry
  gets 1.

### 6.2 `config/live/light_scenes.yaml`: the 14 new scenes

The existing 10 scenes stay as they are (`needs: []`, intensity only). Append:

```yaml
  # -- movers (docs/LIGHTS.md §6.2). Targets are Room map names: a light with no such place keeps the layers below. --
  movers-still-pools:
    label: Still pools of light
    needs: [pan, tilt]
    layers:
      - {id: aim, kind: position, mode: fan, centre: [dance-floor, piano, centre], on: floor, size: 1.5, size_deg: 25,
         fan: 1.0, along: right}
      - {id: beam, kind: beam, zoom: wide, frost: 0.3, iris: 1.0}
      - {id: breathe, fx: sine, phase: bar, period_bars: [8, 4], depth: 0.25, needs: none}
    tags: {energy: [0.0, 0.35], moods: [calm, warm, elegant, dreamy], genre_fit: {classical: 0.9, piano: 0.9, jazz: 0.8,
           ambient: 0.8, "*": 0.3}, valence: 0.5, organic: 0.8, density: 0.1, flow: {dirs: [still, inward], honours: false}}

  movers-slow-drift:
    label: Slow drift across the ceiling
    needs: [pan, tilt]
    layers:
      - {id: drift, kind: position, mode: drift, on: ceiling, centre: centre, size: 1.5, size_deg: 20, phase: phrase,
         needs: none, spread_mode: chase, spread: 0.5}
      - {id: beam, kind: beam, zoom: wide, frost: 0.2}
      - {id: gobo, kind: gobo, slot: auto, change_on: section, spin: flow, spin_scale: 0.15}
    tags: {energy: [0.05, 0.45], moods: [dreamy, liquid, cosmic, calm], genre_fit: {ambient: 0.9, downtempo: 0.85,
           soundtrack: 0.8, classical: 0.6, "*": 0.3}, valence: 0.5, organic: 0.5, density: 0.2,
           flow: {dirs: [still, left, right, cw, ccw], honours: true}}

  movers-ceiling-circles:
    label: Circles on the ceiling
    needs: [pan, tilt]
    layers:
      - {id: circle, kind: position, mode: circle, on: ceiling, centre: [mirror-ball, centre], size: 1.0, size_deg: 18,
         rotate: flow, phase: bar, period_bars: [4, 1], needs: grid, spread_mode: chase, spread: 0.5, order: flow}
      - {id: gobo, kind: gobo, slot: auto, change_on: phrase, spin: flow, spin_scale: 0.4}
      - {id: beam, kind: beam, zoom: 15}
      - {id: pulse, fx: swell, phase: beat, subdivide: true, depth: swell, needs: beat, without_beat: energy,
         attack_ms: 60, decay_ms: 300}
    tags: {energy: [0.35, 0.8], moods: [cosmic, hypnotic, dreamy, geometric], genre_fit: {trance: 0.9, techno: 0.85,
           electronic: 0.85, house: 0.8, ambient: 0.6, "*": 0.35}, valence: 0.55, organic: 0.25, density: 0.55,
           flow: {dirs: [cw, ccw], honours: true}}

  movers-converge-ball:
    label: Every beam on the mirror ball
    needs: [pan, tilt]
    layers:
      - {id: ball, kind: position, mode: aim, centre: [mirror-ball, flow, centre]}
      - {id: beam, kind: beam, zoom: narrow, frost: 0.0, iris: 0.6}
      - {id: pulse, fx: swell, phase: beat, depth: 0.6, needs: beat, without_beat: energy, attack_ms: 40,
         decay_ms: 200}
    hits: {styles: [beat, full], min_strength: 0.3, depth: 0.6, decay_ms: 100, drop: {depth: 1.0, decay_ms: 500}}
    tags: {energy: [0.5, 1.0], moods: [euphoric, retro, bright, playful], genre_fit: {disco: 0.95, house: 0.85,
           funk: 0.8, pop: 0.75, "*": 0.35}, valence: 0.8, organic: 0.3, density: 0.7,
           flow: {dirs: [inward, towards], honours: false}}

  movers-floor-sweep:
    label: Sweeping the floor
    needs: [pan, tilt]
    layers:
      - {id: sweep, kind: position, mode: sweep, on: floor, centre: [dance-floor, centre], along: flow, size: 2.0,
         size_deg: 30, ease: sine, phase: bar, period_bars: [2, 1], needs: grid, spread_mode: chase, spread: 0.25,
         order: flow}
      - {id: beam, kind: beam, zoom: 12}
      - {id: chase, fx: swell, phase: beat, depth: 0.5, needs: beat, without_beat: energy, order: flow, spread: 1.0,
         attack_ms: 40, decay_ms: 250}
    tags: {energy: [0.45, 0.9], moods: [playful, bright, geometric, hypnotic], genre_fit: {house: 0.85, techno: 0.8,
           pop: 0.75, funk: 0.75, latin: 0.75, "*": 0.35}, valence: 0.7, organic: 0.3, density: 0.65,
           flow: {dirs: [left, right], honours: true}}

  movers-wall-wash-wave:
    label: Soft walls with a wave
    needs: [pan, tilt]
    layers:
      - {id: walls, kind: position, mode: fan, centre: centre, on: wall-n, along: flow, size: 2.5, size_deg: 40,
         fan: 1.0}
      - {id: beam, kind: beam, zoom: wide, frost: 0.6}
      - {id: wave, fx: sine, phase: bar, period_bars: [4, 1], depth: 0.5, needs: grid, order: flow, spread: 1.0}
    tags: {energy: [0.2, 0.6], moods: [warm, elegant, mellow, liquid], genre_fit: {soul: 0.85, jazz: 0.8, rnb: 0.8,
           downtempo: 0.75, gospel: 0.7, "*": 0.35}, valence: 0.6, organic: 0.6, density: 0.35,
           flow: {dirs: [left, right, cw, ccw], honours: true}}

  movers-fan-up:
    label: Beams fanning upwards
    needs: [pan, tilt]
    layers:
      - {id: fan, kind: position, mode: fan, centre: [dance-floor, centre], on: floor, along: right, size: 2.0,
         size_deg: 35, fan: 1.0}
      - {id: lift, kind: position, mode: lift, lift: 2.5, depth: {fx: saw, phase: bar, period_bars: [8, 2],
         between: [0.0, 1.0]}}
      - {id: beam, kind: beam, zoom: narrow}
      - {id: build, fx: swell, phase: beat, subdivide: true, depth: swell, needs: beat, without_beat: energy,
         attack_ms: 60, decay_ms: 200}
    tags: {energy: [0.45, 0.95], moods: [tense, euphoric, bright], genre_fit: {trance: 0.9, house: 0.8, electronic: 0.8,
           pop: 0.7, gospel: 0.6, "*": 0.35}, valence: 0.65, organic: 0.3, density: 0.6,
           flow: {dirs: [up, outward], honours: true}}

  movers-figure8:
    label: Figure eights on the floor
    needs: [pan, tilt]
    layers:
      - {id: eight, kind: position, mode: figure8, on: floor, centre: [dance-floor, centre], size: 1.2, size_deg: 18,
         phase: bar, period_bars: [2, 1], needs: grid, spread_mode: mirror}
      - {id: beam, kind: beam, zoom: 14}
      - {id: colour, kind: colour_wheel, mode: step, step_on: bar}
    tags: {energy: [0.4, 0.85], moods: [playful, retro, bright], genre_fit: {disco: 0.9, funk: 0.85, pop: 0.8,
           latin: 0.8, afrobeat: 0.75, "*": 0.35}, valence: 0.8, organic: 0.35, density: 0.6,
           flow: {dirs: [still, left, right], honours: false}}

  movers-ballyhoo:
    label: Big ballyhoo
    needs: [pan, tilt]
    layers:
      - {id: bally, kind: position, mode: ballyhoo, on: floor, centre: centre, size: 2.5, size_deg: 45, phase: bar,
         period_bars: [2, 1], needs: grid, spread_mode: chase, spread: 0.25, order: flow}
      - {id: gobo, kind: gobo, slot: auto, change_on: bar, spin: flow, spin_scale: 0.8}
      - {id: prism, kind: prism, in: accents, spin: flow, spin_scale: 0.6}
      - {id: stab, fx: square, phase: beat, depth: 0.5, needs: beat, without_beat: energy}
    hits: {styles: [beat, full], min_strength: 0.2, depth: 0.8, decay_ms: 80, drop: {depth: 1.0, decay_ms: 400}}
    tags: {energy: [0.75, 1.0], moods: [euphoric, aggressive, fiery, glitchy], genre_fit: {techno: 0.9,
           drum-and-bass: 0.9, dubstep: 0.85, trance: 0.85, metal: 0.8, rock: 0.7, "*": 0.2}, valence: 0.6,
           organic: 0.15, density: 0.9, flow: {dirs: [cw, ccw, outward], honours: true}}

  movers-gobo-texture:
    label: Turning patterns on the walls
    needs: [pan, tilt]
    layers:
      - {id: walls, kind: position, mode: fan, centre: centre, on: wall-n, along: right, size: 2.0, size_deg: 35,
         fan: 0.8}
      - {id: gobo, kind: gobo, slot: auto, change_on: section, spin: flow, spin_scale: 0.2}
      - {id: beam, kind: beam, zoom: mid, focus: default, frost: 0.0}
      - {id: breathe, fx: sine, phase: bar, period_bars: [8, 4], depth: 0.2, needs: none}
    tags: {energy: [0.15, 0.55], moods: [mellow, elegant, dark, hypnotic], genre_fit: {jazz: 0.8, soul: 0.75,
           downtempo: 0.8, blues: 0.75, ambient: 0.6, "*": 0.35}, valence: 0.45, organic: 0.5, density: 0.3,
           flow: {dirs: [cw, ccw, still], honours: true}}

  movers-bounce-points:
    label: Hopping between places on the beat
    needs: [pan, tilt]
    layers:
      - {id: hop, kind: position, mode: bounce, points: [piano, dance-floor, mirror-ball, centre], hop: beat,
         spread_mode: chase, spread: 0.5}
      - {id: beam, kind: beam, zoom: narrow}
      - {id: pulse, fx: swell, phase: beat, depth: 0.6, needs: beat, without_beat: energy, attack_ms: 30,
         decay_ms: 220}
    tags: {energy: [0.55, 0.95], moods: [playful, bright, retro, euphoric], genre_fit: {funk: 0.9, disco: 0.85,
           pop: 0.8, latin: 0.8, hip-hop: 0.7, "*": 0.3}, valence: 0.8, organic: 0.35, density: 0.75,
           flow: {dirs: [still], honours: false}}

  movers-tilt-wave:
    label: A wave across the movers
    needs: [pan, tilt]
    layers:
      - {id: wave, kind: position, mode: tilt_wave, size_deg: 25, fx: sine, phase: bar, period_bars: [2, 1],
         needs: grid, order: flow, spread: 1.0}
      - {id: beam, kind: beam, zoom: 12}
      - {id: glow, fx: sine, phase: bar, period_bars: [2, 1], depth: 0.4, needs: grid, order: flow, spread: 1.0}
    tags: {energy: [0.35, 0.8], moods: [playful, hypnotic, liquid, geometric], genre_fit: {house: 0.8, pop: 0.8,
           electronic: 0.75, latin: 0.7, "*": 0.35}, valence: 0.7, organic: 0.35, density: 0.55,
           flow: {dirs: [left, right, cw, ccw, outward, inward], honours: true}}

  # -- every light (intensity, so lamps and movers alike) ------------------------------------------------------------
  flow-wave:
    label: A wave travelling across the room
    layers:
      - {id: wave, fx: swell, phase: bar, period_bars: [2, 1], depth: 0.6, needs: grid, without_beat: energy,
         order: flow, spread: flow.spread, attack_ms: 120, decay_ms: 700}
    tags: {energy: [0.25, 0.8], moods: [liquid, hypnotic, dreamy, playful], genre_fit: {"*": 0.6}, valence: 0.6,
           organic: 0.45, density: 0.45, flow: {dirs: [up, down, left, right, cw, ccw, outward, inward], honours: true}}

  rising-swell:
    label: Swelling upwards
    layers:
      - {id: rise, fx: saw, phase: bar, period_bars: [4, 1], depth: 0.5, needs: grid, without_beat: energy,
         order: flow, flow_dir: up, spread: 0.75}
    tags: {energy: [0.4, 0.9], moods: [tense, euphoric, bright], genre_fit: {trance: 0.85, house: 0.8, pop: 0.75,
           gospel: 0.7, "*": 0.4}, valence: 0.65, organic: 0.35, density: 0.55, flow: {dirs: [up], honours: false}}
```

### 6.3 `config/live/light_effects.yaml` (new): 10 effects

```yaml
# Light effects: overlays the director fires over a group's scene on the music's events (docs/LIGHTS.md §6.3).
#   on        drop | build | breakdown | peak | section_change | track_change | outro | every_bars
#   every_bars, chance (0..1 per trigger), length {bars|beats|seconds} | while (as long as the phase that fired it)
#   fade {in_beats, out_beats}, needs, layers (§5.2), hits, caps, tags (energy band, moods, flow; weight)
# Strobe only lives here. Each light's own strobe_hz_max (set on the Lights page) caps it; Hue never strobes.
version: 1
effects:
  strobe-burst:
    label: Strobe burst
    on: [drop]
    length: {bars: 1}
    fade: {in_beats: 0, out_beats: 0.5}
    needs: [shutter]
    layers: [{id: strobe, kind: shutter, mode: strobe, hz: [6, 10]}]
    tags: {energy: [0.7, 1.0], moods: [euphoric, aggressive, glitchy], weight: 1.0}
  drop-flash:
    label: Flash on the drop
    on: [drop]
    length: {beats: 2}
    layers: []
    hits: {styles: [], drop: {depth: 1.0, decay_ms: 600}}
    tags: {energy: [0.5, 1.0], weight: 1.0}
  downbeat-snap:
    label: Snap to a new place each bar
    on: [peak]
    length: while
    needs: [pan, tilt]
    layers: [{id: snap, kind: position, mode: bounce, points: [dance-floor, piano, mirror-ball, centre], hop: bar,
              spread_mode: unison}]
    tags: {energy: [0.65, 1.0], moods: [euphoric, playful], flow: {dirs: [still], honours: false}}
  converge-on-drop:
    label: Every beam to one place on the drop
    on: [drop]
    length: {bars: 1}
    fade: {in_beats: 0, out_beats: 1}
    needs: [pan, tilt]
    layers:
      - {id: aim, kind: position, mode: aim, centre: [mirror-ball, flow, dance-floor, centre]}
      - {id: beam, kind: beam, zoom: narrow}
    tags: {energy: [0.6, 1.0], moods: [euphoric, tense], flow: {dirs: [inward, towards, outward], honours: true}}
  gobo-shake:
    label: Shaking patterns
    on: [build, every_bars]
    every_bars: 8
    chance: 0.4
    length: {bars: 2}
    needs: [gobo]
    layers: [{id: shake, kind: gobo, slot: auto, shake: 0.7}]
    tags: {energy: [0.5, 1.0], moods: [tense, glitchy, playful]}
  prism-burst:
    label: Split beams
    on: [section_change]
    chance: 0.6
    length: {bars: 2}
    fade: {in_beats: 0, out_beats: 1}
    needs: [prism]
    layers: [{id: prism, kind: prism, in: true, spin: flow, spin_scale: 0.8}]
    tags: {energy: [0.45, 1.0], moods: [euphoric, cosmic, glitchy], flow: {dirs: [cw, ccw], honours: true}}
  colour-bump:
    label: Colour kick on the beats
    on: [section_change, every_bars]
    every_bars: 16
    chance: 0.5
    length: {bars: 1}
    layers:
      - {id: bump, kind: colour, role: highlight, depth: {fx: swell, phase: beat, between: [0.0, 0.8]}}
      - {id: wheel, kind: colour_wheel, mode: step, step_on: beat}
    tags: {energy: [0.45, 1.0], moods: [bright, playful, euphoric]}
  peak-chase:
    label: Fast chase
    on: [peak]
    length: while
    fade: {in_beats: 1, out_beats: 2}
    layers: [{id: chase, fx: square, phase: beat, subdivide: true, depth: 0.7, needs: beat, without_beat: energy,
              order: flow, spread: 1.0}]
    tags: {energy: [0.7, 1.0], moods: [euphoric, aggressive], flow: {dirs: [left, right, cw, ccw], honours: true}}
  up-wave:
    label: Light climbing upwards
    on: [build]
    length: while
    fade: {in_beats: 2, out_beats: 1}
    layers:
      - {id: climb, fx: saw, phase: beat, depth: 0.6, needs: grid, without_beat: energy, order: flow, flow_dir: up,
         spread: 1.0}
      - {id: lift, kind: position, mode: lift, lift: 2.0, flow_dir: up, depth: 0.8}
    tags: {energy: [0.4, 1.0], moods: [tense, euphoric], flow: {dirs: [up], honours: false}}
  down-wave:
    label: Light falling away
    on: [breakdown, outro]
    length: {bars: 4}
    fade: {in_beats: 1, out_beats: 4}
    layers:
      - {id: fall, fx: saw, phase: bar, depth: 0.5, needs: grid, without_beat: energy, order: flow, flow_dir: down,
         spread: 1.0}
      - {id: sink, kind: position, mode: lift, lift: 1.5, flow_dir: down, depth: 0.6}
    tags: {energy: [0.1, 0.7], moods: [dreamy, dark, calm], flow: {dirs: [down, inward], honours: false}}
```

### 6.4 `config/live/light_transitions.yaml` (new): 5 transitions

```yaml
# Light transitions (docs/LIGHTS.md §6.4). kind: cut | dark_move | crossfade | fade_dark | sweep. The director chooses
# one with the screens' own decision (the look's transition style, or the song change's), so lights and screens cross
# over together at the same moment.
version: 1
transitions:
  cut:        {label: Cut, kind: cut, tags: {transition_style: [wild], energy: [0.5, 1.0]}}
  dark-cut:   {label: Cut, moving in the dark, kind: dark_move, tags: {transition_style: [lively, wild], energy: [0.3, 1.0]}}
  glide:      {label: Glide, kind: crossfade, beats: 4, seconds: 3, dark_move: true,
               tags: {transition_style: [gentle, lively], energy: [0.0, 0.8]}}
  fade-dark:  {label: Through dark, kind: fade_dark, out_s: 2.5, in_s: 2.0, tags: {transition_style: [gentle]}}
  sweep:      {label: A sweep across the room, kind: sweep, beats: 2, wave_beats: 4, dark_move: true,
               tags: {transition_style: [lively], energy: [0.3, 0.9], flow: {dirs: [up, down, left, right, cw, ccw,
               outward, inward], honours: true}}}
```

### 6.5 Catalogue API (light_scenes.py)

```python
class Catalogue:                                  # existing; + effects, transitions, one revision over three files
    effects: dict[str, dict]; transitions: dict[str, dict]
    def pick_for(self, group: GroupInfo, direction, flow, rng, *, avoid=None) -> Optional[tuple[str, str, dict]]
    def pick_effect(self, group: GroupInfo, trigger: str, direction, flow, rng, *, running: list[str]
                    ) -> Optional[tuple[str, str]]
    def pick_transition(self, kinds: tuple[str, ...], style: Optional[str], direction, flow, rng
                        ) -> Optional[str]
    def fits(self, entry: dict, group: GroupInfo) -> bool          # needs
def group_members(groups_cfg: Mapping, rig: RigView, *, lamps=None, spatial=None) -> dict[str, GroupInfo]
class GroupInfo(TypedDict):     # contract (catalogue)
    id: str; label: str; members: list[str]; caps: list[str]     # the union of members' attrs
    follows: Optional[str]; dark_level: Any                     # "floor" | 0..1
```

`group_members` is pure and deterministic. Each lamp fixture goes to the **first** group in file order whose selector
takes it; a light no group takes is in the group `"*"` (label "Other lights"). The engine and the director both call
it, so their membership agrees.

Validation adds these problem lines:
- an unknown `kind`;
- a shutter layer in a scene;
- unknown keys;
- an effect without `on`;
- a transition with an unknown `kind`;
- `flow.dirs` words outside FLOW_DIRS.
The rest loads. When a file is missing, the parts it holds are empty, with no problem line beyond a view note.

## 7. Programs per group (builder: catalogue; director.py lighting hunks, show.yaml, contract)

### 7.1 Light groups (show.yaml `lights.groups`, §12)

Groups are selectors (kind, cap, tag, group, side, height), first match wins, in file order. `follows: <group>` runs
that group's program (the same scene, effects and transition), so the owner can split movers by side for mirroring
and still share one look. `dark_level` is where `fade_dark` goes.

### 7.2 Shapes (contract, catalogue block)

```python
LIGHT_TRANSITION_KINDS = ("cut", "dark_move", "crossfade", "fade_dark", "sweep")
LIGHT_EFFECT_TRIGGERS = ("drop", "build", "breakdown", "peak", "section_change", "track_change", "outro", "every_bars")

class LightEffectRun(TypedDict):
    id: str                       # "fx:<n>": +1 per effect fired
    name: str; label: str
    at_ms: float                  # host time; the event's line
    until_ms: Optional[float]     # None: while its phase holds (dropped from the list when it ends)
    strength: float               # 0..1
    why: str                      # "the drop", "a new section"

class LightTransitionRun(TypedDict):
    name: str; kind: str          # LIGHT_TRANSITION_KINDS
    params: dict                  # resolved: {beats, seconds, out_s, in_s, in_at_ms, wave_beats, dark_move}
    at_ms: float

class LightProgram(TypedDict):
    group: str; label: str
    members: list[str]; caps: list[str]
    scene: Optional[str]; scene_label: Optional[str]; scene_why: Optional[str]
    effects: list[LightEffectRun]
    transition: LightTransitionRun
    flow: FlowData                # the room's flow, or for a scene that doesn't honour flow, its first tagged dir with
                                  # the room's speed, spread and timing
    since_ms: float               # when the scene was picked
    at_ms: float                  # when it lands (the look change's at_ms: the screens' too)
    why: str
```

**LookData** gains `flow: FlowData` and `light_programs: dict[str, LightProgram]`. `light_scene`, `light_scene_why`
and `light_scene_drop` stay: `light_scene` is the scene of the group named by `lights.scene_group` (compat).
**DirectionData** gains `flow: FlowData`. **EventData** gains NotRequired `flow_id: int` and `sweep_beats: float` on
look_change and the track events.

### 7.3 When the director decides (director.py, lighting hunks only)

- **Scenes**, per group (in order; `follows` groups copy): picked wherever a light scene is picked today (a feel set,
  every look that lands, a song change, Next look, none set), with `Catalogue.pick_for`. Keep this look holds them, as
  today; a person's Next still picks.
- **Effects**, per group:
  - fired on the triggers: structure events (drop, build, breakdown, section_change, track_change), music.context
    phase entering peak or outro, and `every_bars` counted on the clock's bars;
  - `pick_effect`, with `chance`, at most `lights.programs.effects_max` running and at least `effect_gap_bars` since
    that group's last effect (a drop always may);
  - at_ms = the event's at_ms (structure's lines are ahead), so the effect lands on the line;
  - `while` effects end when the phase leaves. Nothing fires while neutral, House, Low, or while Keep holds
    (effects too: owner, 17 Oct).
- **Transitions**: the same decision as the screens.
  - A look change maps its `transition.style`: cut → kinds (cut, dark_move); crossfade → (crossfade, sweep), with
    beats and seconds from the look.
  - A track event maps its TrackTransition: fade-dark → fade_dark (out_s, in_s, in_at_ms from track_change);
    cut → (cut, dark_move); crossfade → (crossfade, sweep).
  - `pick_transition` among those kinds by the feel's transition_style and flow. The run's at_ms = the change's at_ms.
  - The look_change or track event gets `flow_id` and, for a sweep, `sweep_beats` = its `wave_beats`, so the screens
    sweep with it.
- **Lockstep rule**: every program change carries a future at_ms (≥ `structure.events.min_lead_s` ahead, on a line).
  It is the same at_ms as the screens' event for that change, and each output applies it at its own photon time.
- **Snapshot** `lights`: {programs (labels), flow + votes (FlowDecider.view), effects fired (last 10), mirror_ball
  (last sent, next allowed)}. Every pick, effect and flow change is logged (`light_program`, `light_effect`, `flow`)
  in director.jsonl with its why.

### 7.4 Tests (catalogue): test_light_scenes.py additions, test_flow.py, test_director.py lighting additions

- The three files load and the real catalogue compiles clean against a rig with a generic-mover-full and four Hue
  lamps.
- `needs` filters, and cap_fit/flow_fit order the picks (mover scenes win for the movers group, intensity scenes for
  lamps).
- `group_members`: first match, follows, the `"*"` group.
- Effects: triggers, chance (seeded), gaps, max, `while` ends.
- Transition mapping from look and track transitions.
- LookData.light_programs shape; the `light_scene` compat; Keep holding picks.
- Lockstep: a program's at_ms equals the look_change event's at_ms.
- room.direction.flow is published.

## 8. The page (builder: page; api.py, src/pages/Lights/*)

### 8.1 Routes (show_service api.py; through room's `/show/*` pass-through)

Every write goes through show-brain (`self._call(brain, fn)`), then `rig.reload_now()`. The jog route is direct (any
thread, no brain hop). Errors are `{error, detail, choices?, problems?}`, as today.

| route | body | answer |
|---|---|---|
| `GET /api/v1/rig` | (today) | + per row `caps`, `setup`; + `lights_ui: {send_hz, idle_s, nudge_deg, drag_deg_per_px}` from show.yaml `lights.jog` |
| `POST /api/v1/rig/fixtures/{id}/jog` | `{values?: {attr: value}, release?: bool, free?: bool}` | Rig.jog's dict (§5.8); 404 unknown_fixture |
| `POST /api/v1/rig/fixtures/{id}/setup` | any of `home`, `beam`: `"here"` \| a value \| null; `limit: {axis: pan\|tilt, end: "here"}` (the end nearer the held value moves); `limits: null`; `slots`; `max_speed`; `range`; `jog_flip`; `strobe_hz_max` | `{setup, problems}`; "here" reads Rig.holds()[id].target (409 `not_held`: "Open Move it first.") |
| `POST /api/v1/rig/fixtures/{id}/positions` | `{name, at?: "here"}` | `{positions, point: {name, at} \| null}`: a calibrated mover's new name becomes a room point via ray_hit (§3.6) |
| `DELETE /api/v1/rig/fixtures/{id}/positions/{name}` | | `{positions}` |
| `POST /api/v1/rig/fixtures/{id}/teach` | `{point: <space name>}` (uses the held pan/tilt) | `{samples: [TeachSample], fit: PoseFit}`: stores the sample, also stores `positions[point]`, solves at ≥ 3 samples, set_pose |
| `DELETE /api/v1/rig/fixtures/{id}/teach/{n}`, `DELETE .../teach` | | `{samples, fit}` |
| `GET /api/v1/rig/space` | | SpaceView + `targets` (every aimable name with its label) |
| `PUT /api/v1/rig/space/room` | `{size?: [x, y, z], front?: n\|e\|s\|w}` | SpaceView |
| `PUT /api/v1/rig/space/appliances/{id}` | `{at?, mount?, plane?}` (null clears) | the appliance; DELETE removes the placement |
| `PUT /api/v1/rig/space/points/{name}` | `{at, label?, device?}` | the point; DELETE removes it |
| `PUT /api/v1/rig/space/regions/{name}` | `{surface, centre, size, label?}` | the region; DELETE removes it |
| `GET /api/v1/rig/live` | | LiveView |

```python
class LiveFixture(TypedDict):       # contract (engine: Rig.live_view)
    at: Optional[list[float]]; dir: Optional[list[float]]     # position; beam direction (calibrated movers)
    hit: Optional[list[float]]; surface: Optional[str]        # where the beam lands
    pan: Optional[float]; tilt: Optional[float]; zoom_deg: Optional[float]
    gobo: Optional[str]; prism: bool                         # gobo slot name
    intensity: float; hex: Optional[str]; held: bool; calibrated: bool
class LiveView(TypedDict):
    t_ms: float; flow: Optional["FlowData"]
    programs: dict[str, dict]       # group -> {"label", "scene_label", "effects": [label]}
    fixtures: dict[str, LiveFixture]
```

### 8.2 The page

`/lights` keeps its list and Add a light as the primary action. A segmented control at the top, **Lights | Room map**,
is remembered in localStorage (wrapped in try/catch).

**Room map** (`RoomMap.tsx`, phone first):
- **First use** asks two things: "How big is the room?" (width east-west, length north-south, height in metres:
  three steppers) and "Which wall do people face?" (N, E, S, W chips).
- **The plan.** An SVG that fits the width, with compass letters, the front wall drawn thicker, and items:
  - screens: bars on walls;
  - lamps: dots;
  - movers: dots with their beam line from the live view;
  - named points: pins;
  - regions: soft rectangles.
- **Placing.**
  - Tap an item, then drag to place it. A bottom bar shows its name and "Height 2.40 m −/+".
  - For a mover: "Hangs from the ceiling | Stands on the floor | On a wall".
  - For a screen: "Picture size" w × h, snapped to the wall tapped.
- **Add a place:** chips "The piano", "Mirror ball", "Dance floor" and "Somewhere else…" (typed name). The new place
  drops at the centre to be dragged. Ids are the slugged label (`mirror-ball`, `dance-floor`, `piano`), so the
  catalogue's names match.
- **Side view.** A thin elevation strip under the plan (the long axis × height) for heights.
- **Live.** Each placed light's beam (from `GET /rig/live`, 4 Hz while visible) and the flow arrow:
  - straight for left/right;
  - curved for cw/ccw;
  - radial for outward/inward;
  - in the side strip for up/down;
  - labelled in words: "Flowing upwards", "Turning clockwise".

**Add a light** (LightForm.tsx), the latest ask:
1. What kind of light (as today).
2. Where it's plugged in (as today).
3. Its first channel (as today). Next **saves the light** (POST, with a provisional name = the type's label + the next
   number), so it exists and can be driven.
4. **Move it** (MoveIt.tsx, §8.3): shown for every light with an address. A light with no pan/tilt shows only its
   capability controls ("Check it lights").
5. Its name and where it is (as today; PUT). "Put it on the Room map" opens the map with this light selected.

Cancelling after step 3 leaves the light registered; the sheet offers "Remove this light". **Edit** shows the same
panels on one sheet, with Move it as a collapsible section ("Move it and set it up").

### 8.3 Move it (MoveIt.tsx, JogPad.tsx, CapControls.tsx, Preview.tsx, jog.ts)

- **On open** it holds the light (`POST jog {}`). The banner reads: "The show leaves this light alone while this is
  open." On close it releases (`{release: true}`).
- **Hand-back countdown.** From `idle_s - 15` s the banner reads "Handing it back to the show in 12 s", with a "Keep
  it" button (sends `{}`). After the server releases: "Handed back to the show. Tap to hold it again."
- **JogPad.** A square pad: Turn across, Tilt up and down.
  - Drag moves relatively: Δpx × `drag_deg_per_px[speed]`, with a "Fine | Coarse" toggle.
  - Arrow buttons nudge by `nudge_deg[speed]`.
  - Readout: "Turned 45° · Tilted −20°".
  - A "Swap left/right" and "Swap up/down" pair, stored as `jog_flip`.
- **Sending.** `jog.ts` `JogSender`: latest wins, one request in flight, at most `send_hz`. The beam follows the
  finger smoothly because the engine glides between targets.
- **CapControls**: one control per capability in `caps`, never per type.

| caps | control (words) |
|---|---|
| intensity | "Brightness" slider |
| colour mix | "Colour" swatches + picker, "White" |
| colour wheel | "Colour" chips per slot (names and colours; "Rename"), "Spin the colours" slider when `colour_spin` |
| cct | "White warmth" (warm ↔ cool) |
| gobo / gobo2 | "Pattern" chips per slot (names editable inline: "Rename"), "Spin" slider (⟲ stop ⟳), "Shake" switch |
| prism | "Split the beam" switch, "Spin" slider |
| zoom | "Beam size" (narrow ↔ wide) |
| focus | "Focus" (near ↔ far) |
| iris | "Iris" (small ↔ open) |
| frost | "Soft edge" |
| shutter | "Shutter open" switch (ON = open) |
| macros | under "More": one picker per channel, by the OFL words |

- **Store it** (buttons under the pad):
  - **"Set as home"**: setup `home: "here"`.
  - **"Set limits"**: a sub-panel; jog is sent with `free: true` while it is open. It has "Turn: stop here" (twice:
    one end, the other end), "Tilt: stop here" (twice) and "No limits". There are none by default.
  - **"Save as…"**: a name field with suggestions from the room's places.
  - **"Point it at…"**: pick a place on the map ("The piano", "Mirror ball", "Centre of the north screen", ...). The
    words say "Put the middle of the beam on it, then tap Done". After each: "Learnt 2 of 3." and then the fit's words
    ("Spot on (within 1°)."), with the samples listed and an × each.
  - **"Keep this beam as its normal look"**: setup `beam: "here"`.
- **Preview.** A small plan and side view of this light's beam (from the live view), its gobo name and zoom, and the
  current flow arrow. Uncalibrated it says: "Point it at 3 places so the map can show where it shines."

### 8.4 Tests (page): test_api.py additions, src/pages/Lights/lib.test.ts additions

- **api.py.**
  - Every new route, with a fake rig (jog direct; setup "here" from a fake hold; not_held 409; teach storing and
    solving at 3; positions creating a room point for a calibrated pose).
  - Temporary space and setup files: nothing under config/ is written by the tests.
  - The routes answer 503 without a rig.
- **lib.test.ts.**
  - `controlsFor(caps)` for generic-mover-full, an RGB par and the colour-wheel spot (the right controls, no
    hardcoded types).
  - `JogSender` latest-wins and rate limiting (fake timers).
  - The map's room ↔ SVG transforms round-trip.
  - Countdown words.

## 9. The renderer contract (for 0b; not built here)

What show_service sends, and what the renderer is asked to add. None of it is required for the lights to work.

1. **room.direction.flow** (FlowData, §4.1), published on change and every 5 s. `validate_direction` already ignores
   unknown keys. The renderer reads `flow.screens[<its screen name>]` (ScreenFlow) and the shared phase rule (§4.1),
   evaluated at its photon time with show_service's clock (`music.clock`).
2. **Binding sources** for scene and effect params:
   - `flow.angle` (degrees, the screen's own angle_deg);
   - `flow.x`, `flow.y`, `flow.inplane`, `flow.depth`, `flow.radial`, `flow.spin`;
   - `flow.speed`, `flow.coherence`;
   - `flow.wave`: the sawtooth phase 0..1 at this screen.
   Coherence is how hard to bind: a preset blends its own direction toward the flow's by coherence.
3. **Preset tags**: `tags.flow: {dirs: [...], honours: bool}` on scenes, effects and transitions, scored like the
   lights (flow_fit 1 when matching or honouring, 0.6 otherwise; tags.py may add a `flow` descriptor reading
   `direction.flow.dir`).
4. **Directional presets.**
   - Scenes and effects with a natural direction bind their params to flow: drift_clouds `wind_direction` ←
     flow.angle, copper_bars direction, curl_particles flow, particle and flow fields, flag waves.
   - Transitions with `flow: honours`: the wipe's `angle` ← the screen's angle_deg, a `clock` wipe's sense ← spin,
     an iris ← radial.
   - **Mind the convention.** Our angle is 0 = right, 90 = up as the viewer sees it. The wipe plug-in's 0° is left →
     right with image y down, so its angle = -angle_deg.
5. **Lockstep.**
   - Every look_change and track event carries `flow_id` and, for a sweep, `sweep_beats`. A screen starts its
     transition at at_ms + flow.screens[s].phase × sweep_beats beats; without sweep_beats, at at_ms as today.
   - Lights and screens use the same at_ms and the same anchor/period, each at its own photon time. So a change, a
     wave or a sweep crosses screens and lights together.
6. **Up and down are about the room.** On every screen up is up; left and right follow each screen's own plane
   (space.screen_flow), never a name. In this room both screens hang side by side on the west wall facing east
   (owner, 22 Oct), so "right" is the same way (north) on both; on screens facing each other it would flip. cw/ccw
   keep one sense on every screen (spin).

## 10. Testing without hardware

- **Encoder:** OFL documents embedded in tests (§2.8) and exact bytes; a fine-channel, wheel and rotation coverage
  table; parity with colour_mix.
- **Space:** synthetic rooms and poses with noise; round-trip IK; ray hits (§3.7).
- **Engine:** a simulated fixture (a slew-limited follower of the commanded pan/tilt after position_ms) for pre-roll
  and dark-move timing; frame-by-frame speed checks (§5.9).
- **Catalogue and flow:** seeded picks, a fake clock grid, fake structure events (§4.5, §7.4).
- **Live previz:**
  - a `virtual: true` node in fixtures.yaml keeps frames in memory: `GET /show/api/v1/rig/frame?output=<id>` shows
    each fixture's DMX bytes and attrs, and `GET /show/api/v1/rig/dmx` the decoded universes;
  - `GET /show/api/v1/rig/live` shows beams and hits on the Room map without a light plugged in.

## 11. Ownership and interfaces

### 11.1 Files (disjoint)

| builder | owns | reads (never edits) |
|---|---|---|
| **encode** | show_service/ofl.py, show_service/fixture_encode.py (new), show_service/fixtures.py, colour_mix.py (only if a hook is needed), config/room/fixtures.setup.yaml (new: header + `version: 1`, `fixtures: {}`), contract: §11.2 encode anchors, tests: test_ofl.py, test_fixture_encode.py (new), test_fixtures.py | — |
| **space** | show_service/space.py (new), config/room/space.yaml (new, §3.2), config/room/space.added.yaml (new, header + empty sections), contract: space block, tests: test_space.py (new) | contract FixtureSetup |
| **engine** | show_service/looks.py, show_service/outputs.py (jog/hold, busy, live view, lights settings, space polling hooks only), contract: frames block, tests: test_looks.py, test_looks_motion.py (new), test_outputs.py | fixture_encode, space, light_scenes.group_members / Catalogue.effects |
| **catalogue** | show_service/light_scenes.py, show_service/flow.py (new), config/live/light_scenes.yaml (append), config/live/light_effects.yaml (new), config/live/light_transitions.yaml (new), show_service/director.py (lighting hunks only), config/room/show.yaml (`lights:` block only, §12), contract: programs/flow block, tests: test_light_scenes.py, test_flow.py (new), test_director.py (lighting cases) | space (resolve, screen_flow, spatial_fn), fixtures.select |
| **page** | show_service/api.py, src/pages/Lights/* (api.ts, lib.ts, LightForm.tsx, index.tsx, new MoveIt.tsx, JogPad.tsx, CapControls.tsx, RoomMap.tsx, Preview.tsx, jog.ts), tests: test_api.py, lib.test.ts | Rig hooks, fixtures.SetupStore, space.Store/solve_pose/ray_hit |

### 11.2 contract.py anchors (Edit only; re-read first)

- **encode**:
  - `ATTRS` and the extended `CHANNEL_ATTRS` on the CHANNEL_ATTRS line;
  - `FIXTURES_SETUP_FILE` after FIXTURES_ADDED_FILE;
  - `ranges` in AttrChannel; `wheels` and `physical` in TypeInfo; `setup` in FixtureInfo; `caps` and `setup` in
    RigFixtureRow; `caps` in RigTypeRow;
  - a new block `# -- fixture capabilities (docs/LIGHTS.md §2)` (CapRange, FixtureCaps, FixtureSetup) right after
    `class RigTypeRow`.
- **space**: a new block `# -- room space (docs/LIGHTS.md §3)` at the very end of the file: FLOW_DIRS,
  FLOW_OPPOSITE, SPACE_FILE, SPACE_ADDED_FILE, MOUNTS, Surface, SpaceView, TeachSample, MoverPose, PoseFit,
  ScreenFlow.
- **engine**: `attrs` in FixtureFrame, `busy` in Frame, `LOOK_INPUTS` extended; a block `# -- motion and the hold
  (docs/LIGHTS.md §5)` (HoldView, LiveFixture, LiveView) right after `class DriverCaps`.
- **catalogue**: `flow` and `light_programs` in LookData (after light_scene_drop); `flow` in DirectionData; the
  NotRequired fields in EventData; a block `# -- light programs and flow (docs/LIGHTS.md §4, §7)` (FlowData,
  FLOW_SOURCES, LIGHT_TRANSITION_KINDS, LIGHT_EFFECT_TRIGGERS, LightEffectRun, LightTransitionRun, LightProgram,
  GroupInfo) right after `class LightSceneDrop`.

Forward references (`"FlowData"` in LiveView, `"ScreenFlow"` in FlowData, `"FixtureSetup"` across blocks) are strings,
so the blocks can land in any order.

### 11.3 The interfaces between builders

| from → to | interface |
|---|---|
| encode → engine | `fixture_encode.encode(f, t, intensity, colour, attrs)`, `caps(t, setup)`, `neutral(caps, setup)`; FixtureInfo.setup |
| encode → page | RigFixtureRow.caps / setup; `fixtures.SetupStore.patch(fid, patch)`, `fixtures.Refused` |
| encode → catalogue | `fixtures.select(..., spatial=)` with `cap:`, `side:`, `height:` |
| space → engine | `space.shared()`, `resolve`, `region_of`, `surface_of`, `surface_point`, `surface_uv`, `flow_vector`, `flow_coord`, `lateral`, `aim`, `beam_dir`, `ray_hit` |
| space → catalogue | `resolve`, `screen_flow`, `spatial_fn`, `flow_coord` |
| space → page | `space.shared()` writers, `solve_pose`, `ray_hit`, `Refused`, SpaceView |
| catalogue → engine | LookData.flow, LookData.light_programs (§7.2), `light_scenes.group_members`, `Catalogue.effects` |
| engine → page | `Rig.jog`, `Rig.holds`, `Rig.caps_of`, `Rig.reload_now`, `Rig.live_view`; frame_view attrs |
| catalogue → renderer (0b) | room.direction.flow, events' flow_id / sweep_beats (§9) |

**Degrade order** (each piece runs alone): without encode, movers send pins and centre (today); without space, no
positions (pan/tilt-space patterns, group-order waves); without catalogue programs, the `light_scene` compat; without
the engine's hold, the page's Move it says "This light can't be moved from here yet." (`{held: false, why}`).

## 12. show.yaml `lights:` (the catalogue builder adds this block verbatim; the others read it)

```yaml
# ---- Lights: groups, flow, motion, the jog and calibration (docs/LIGHTS.md). Starting points unless marked. -----------
lights:
  scene_group: lamps                        # room.look's light_scene (compat) is this group's scene
  groups:                                   # a light runs the program of the FIRST group here that selects it
    movers:
      label: Moving lights
      select: "cap:pan"
      dark_level: 0                         # fade_dark goes to this (0..1), or floor (the look's floor)
    lamps:
      label: Lamps
      select: "kind:lamp"
      dark_level: floor
    # movers-north: {label: North movers, select: "cap:pan side:north", follows: movers}   # put above movers
  programs:
    effects_max: 2                          # effects running at once per group
    effect_gap_bars: 4                      # a new effect at most this often per group (a drop always may)
  flow:
    centre: null                            # a Room map name the flow turns round / bursts from; null: the room's centre
    sources: {structure: 1.0, pitch: 0.6, listener: 0.5, moods: 0.3}
    structure: {build: up, peak: cw, drop: outward, breakdown: inward, outro: down, calm: still}
    listener: {building: up, peak: outward, releasing: down, winding_down: inward}
    moods: {euphoric: up, bright: up, cosmic: cw, hypnotic: cw, liquid: right, dreamy: still, calm: still,
            dark: down, tense: inward, aggressive: outward}
    pitch: {window_s: 4, slope: 0.03, min_conf: 0.5}   # register (A0 0 .. C8 1) per second that reads as rising
    events:
      drop: {dir: outward, bars: 1, then: down}
      build: {dir: up}
      track_change: {sweep_beats: 4}
    min_score: 0.25
    margin: 0.15
    dwell_s: 8
    still_below_energy: 0.15
    cross_beats: [16, 2]                    # the wave's crossing time at speed 0 .. 1 (powers of two)
    cross_s: [8, 1]                         # the same without a clock grid
    speed: {pace: 0.6, energy: 0.4}
    spread: look.spread
    coherence: {events: 1.0, floor: 0.3}
    symmetry: none                          # none | mirror
    mirror_ball:
      enabled: true                         # it turns the glitterball with the flow; it never switches it on
      power_member: mirror_ball.main
      power_on: spinning
      direction_target: mirror_ball_motor.direction
      speed_target: mirror_ball_motor.speed
      directions: {cw: clockwise, ccw: anticlockwise}   # cw = clockwise seen from ABOVE: check by eye, swap if wrong
      speeds: [[0.0, low], [0.4, medium], [0.75, high]]   # flow.speed from -> the remote's speed word
      min_gap_s: 120                        # §9.3: one change at most this often (the motor reverses)
    blinds:
      enabled: false                        # true: big song changes move the blinds with an up/down flow
      # targets: {<room target>: {up: <value>, down: <value>}}   # from modes.json's blinds; set them first
  motion:
    default_max_speed_deg_s: {pan: 180, tilt: 120}   # until measured (each light's own max_speed wins)
    accel_s: 0.12
    pt_speed: 1.0                           # the light's own pan/tilt speed channel (1 = fastest; the engine smooths)
    max_travel_share: 0.8                   # a beat-locked hop may spend at most this share of its period moving
    dark_move_min_deg: 30                   # a jump bigger than this travels dark when the transition says so
    settle_ms: 120
    min_size_share: 0.4                     # a pattern too fast for a light shrinks to this, then slows
    dark_max_s: 8                           # fade_dark never stays dark longer (director.track_transitions.gap's)
  jog:
    idle_s: 60                              # Move it hands the light back after this long with no moves
    smooth_ms: 80
    send_hz: 20
    nudge_deg: {fine: 1, coarse: 5}
    drag_deg_per_px: {fine: 0.1, coarse: 0.5}
  calibration:
    fit_deg: [1.5, 4.0, 8.0]                # good | ok | rough edges of the solve's rms error
    min_spread_deg: 20                      # taught points closer than this together: "point it at places further apart"
    pos_sigma_m: 0.3                        # how far the solve may move a light from where the map puts it
```

## 13. Not in this step

Matrix pixel modes, endless pan patterns beyond the jog, blade systems, fog, BlenderDMX previz, measured speed
calibration (a "time a full turn" routine), and the Hue lamps' move to the rig (they keep today's path and get flow
waves once fixtures.yaml enables them).
