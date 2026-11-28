# PC22 config upgrade: finsbury against the code (28 Nov 2026)

Written for a session on PC22 bringing `deployments/finsbury/` up to what the code now expects. It replaces
`docs/PICKUP_FROM_LAPTOP.md` as the list of what is still to do; that file still explains each feature.

**Where PC22 stands.** Its code is current: PC22 merged the laptop's work on 28 Nov 09:18 (`e8f06c05`). Its config
is not. finsbury's files were last brought up on 27 Nov 07:45 (`5ed4786c` the GigBARs, `1a1bcde1` Karaoke), and the
laptop's rig gained line-ups, strobes as four lights, bars as one thing, the karaoke scene tag and sound calibration
after that. Nothing fails: `python -m smconfig check` says `ok` for finsbury and the rig reports no problems. The
new features just have nothing to work with, so they do nothing or the wrong thing.

**How this was checked** (from the laptop, 28 Nov about 10:00): finsbury's files in git at `a8361670`, the laptop's
files beside them, and PC22's own read-only answers at `https://screen.finsburybankforsavings.com`
(`/rs/show/api/v1/rig`, `.../rig/space`, `.../lights/library`, `/api/sound/outputs`, `/api/karaoke/now`). The
config blocks in §1 and §2 were parsed with show_service's own parser against finsbury's fixtures: no problems.

## Before you start

- **Commit PC22's own app-written files first.** `git status deployments/finsbury`. The running room has things git
  does not: a `dance-floor` region, a keep-out round the mirror ball, a planned screen `sm-screen-1`. They are in
  PC22's `lights/space.added.yaml`, written by the Room editor. Commit them before editing anything, so no pull or
  hand edit loses them.
- Tell the owning session before editing its file (docs/AV_PROGRAMME.md §11; `fixtures.yaml` is c8's).
- After each edit: `python -m smconfig check`. show_service re-reads `show.yaml`, `fixtures.yaml` and the space
  files by itself; room_service re-reads `modes.json` on change. Nothing here needs a Flask restart.
- Check each step at `GET /rs/show/api/v1/rig` (`problems` empty) and in Light setup › Room, with Lights › Mode
  set to the mode you changed.

## What is already right (leave it)

`deployment.yaml` (every capability the code knows, `karaoke` on, `phone_tunnel` off, the karaoke venv and JS
runtime), `publish-destinations.json` (all three live screens have `live.auto`; the `library` bucket is there),
`rooms.json`, `room/room.yaml` (`tunnel_host_suffixes` is only for the phones' tunnel, which PC22 does not use),
`services/*.env`, the Karaoke mode itself, the GigBAR patch on the two virtual nodes, the Spectre card for the
virtual node.

## 1. Light groups and line-ups: `show.yaml` (the big one)

**Now.** finsbury's `show.yaml` adds only `remote-lights`. So the shared `lamps` group (`kind:lamp`) holds 17
lights: the four pars, four derbies, two lasers, two strobes, **the hazer** and the four Hue lamps. There are no
line-ups. The effect:
- the lasers, derbies and strobes play lamp scenes; the scenes written for them (`tags.parts`) are never drawn;
- every part plays in every section, so a verse looks like a last chorus;
- nothing is ever featured, so the room-light rule (LIGHTS.md §7.5) never brings the bright lights down for the
  laser or the derbies;
- a look group selects the hazer.

**Fix.** Add the laptop's groups and line-ups to `lights:` in `deployments/finsbury/show.yaml`, beside
`remote-lights`. Copy them from `deployments/laptop/show.yaml` as they stand:

```yaml
lights:
  groups:
    lamps: {label: Washes, select: "kind:lamp !tag:derby !tag:strobe !tag:laser !tag:haze !tag:house", carries_beat: 2}
    lasers: {label: Lasers, select: "tag:laser", dark_level: 0, scenes_for: [laser], repeat_weight: 1.0,
             reads_under_lux: 3, lands_on: [ceiling]}
    derbies: {label: Derbies, select: "tag:derby", dark_level: floor, scenes_for: [derby], carries_beat: 3,
              reads_under_lux: 20}
    flashes: {label: Strobe zones, select: "tag:strobe", dark_level: 0, scenes_for: [flash], repeat_weight: 1.0}
  lineups:
    # the laptop's thirteen, unchanged
```

**The Hue lamps are part of the washes** (owner, 28 Nov: "the four in the room are designed to be part of the
washes"). With this block finsbury's `lamps` is the four pars and the four Hue lamps, one wash group of eight: they
take the same scene, play together and rest together. That is the only difference from the laptop, where `lamps` is
the pars alone (its label there is "Pars"; "Washes" fits this room). finsbury's `around` order in `fixtures.yaml`
already walks the Hue lamps and the bars' pars as one ring. Do not give the Hue lamps a group of their own.

What follows from it: a line-up that does not play the washes rests the Hue lamps with the pars, dark or at a low
glow in the look's colour (`rest-dark`, `rest-glow`). Five of the thirteen do: the heads alone, derbies and heads,
heads and the laser, derbies and the laser, heads and derbies with the laser. Until the real bars are in the room
the Hue lamps are its only real wash, so in those looks the room is lit by the screens alone. The glitterball and
pinspots (`remote-lights`) are in no line-up, so they carry on as now.

**Then set this room's light.** `lights.room_light.other_lux` is 1.0 on PC22, the laptop room's projected picture.
Two TVs leave more. Set finsbury's own in its `show.yaml` (`lights: {room_light: {other_lux: <lux>}}`, and
`reflect` for its walls: see the comment in `config/room/show.yaml`). The `reads_under_lux` figures are the
laptop's starting points too.

## 2. The strobes as four lights each: `lights/fixtures.yaml`

**Now.** Each bar's strobes are one fixture (`gigbar-a.strobe`, type `gigbar-flash`). The scenes that run the
strobe LEDs left to right, right to left and ping-pong need each LED as its own light (`76fdd08a`, 27 Nov).

**Fix**, copying the laptop's lines (`deployments/laptop/lights/fixtures.yaml`):
- under `types`, replace `gigbar-flash` with `gigbar-strobe-led` and `gigbar-strobe-led-s`;
- give `gigbar-derby` `streaks: 0.6` (a derby shows one or two colours of streak, not a mix);
- replace `gigbar-a.strobe` with `gigbar-a.strobe1`…`strobe4` at 27, 28, 29, 30, and `gigbar-b.strobe` with
  `gigbar-b.strobe1`…`strobe4` at 127, 128, 129, 130 (LED 4 is the `-s` type).

Nothing else in the address plan moves. The old `gigbar-a.strobe` and `gigbar-b.strobe` places in
`space.added.yaml` then name nothing; §3 places the new ones.

## 3. The bars as bars, and the room's own measurements: the space files

**Now.** `bars` and `layouts` are empty on PC22. Every part has its own `at` in `space.added.yaml`, copied from the
laptop's provisional room, with hand-set head poses.

**Fix** (PICKUP_FROM_LAPTOP §4a has the steps; LIGHTS.md §3.6b the editor):
- copy the `layouts: gigbar-move` block from `deployments/laptop/lights/space.yaml` into finsbury's `space.yaml`;
- in Light setup › Room, add each bar as a bar and take the per-part places out (a part with its own `at` keeps it
  and ignores the bar). Keep the ids `gigbar-a` and `gigbar-b`, so the parts are `gigbar-a.par1` and so on.

**Check these are measured, not defaults:**
- `room.size` is `[5.0, 10.0, 5.0]`, the laptop's provisional floor plan;
- both screens' pictures are `size: [2.0, 1.0]` (a 65-inch Frame is about 1.43 × 0.80 m);
- `room.haze_half_life_s` is unset, so the haze model runs on the shared 150 s. Time it once a hazer is in the room.

**Places the karaoke scenes aim at.** `singer` is missing: add a place called `singer` where the singer stands
(the scenes fall back to `dance-floor`, then the room's centre). `dance-floor` and `mirror-ball` are there.

**Keep-outs.** The laptop keeps the moving heads off its screen (a keep-out with `groups: [movers]`). PC22 has one
round the mirror ball and none over the two TVs.

**When the real bars arrive** (not before): the node's `nodes` entry and `dmx-a.node` (GIGBAR_RIG §6), each
light's lean and each head's home on the Lights page (`fixtures.setup.yaml` is empty on PC22), and "Point it at…"
for the heads' real poses.

## 4. The Karaoke mode: `room/modes.json`

**Now.** finsbury's `karaoke` mode has no `director.light_scenes`. Party and Disco have theirs. Without it:
- Karaoke's own scenes for waiting and for a song's lead-in (the singer's spot: `stage-waits`, `next-up`,
  `next-up-rise`) are not chosen first;
- the glitterball and pinspots draw from every appliance scene, Disco's `disco-colour` and `disco-wild` included,
  not just the three tagged for Karaoke.

**Fix.** Add to the `karaoke` mode's `state`, as the laptop's `singalong` has:

```json
"director.light_scenes": {"set": {"scene_tags": ["modes:singalong"]}, "restore": true}
```

**Still open from PICKUP §2:** no `transition_s`. With `"transition_s": 3` at the top of the file, every device
eases over the same time on a mode change.

## 5. Sound and timing: Settings › Sound & timing

**Now.** No sound output on PC22 has been measured. Karaoke plays on `sonos:drawing-room`, which runs on the
default 500 ms; the three screens have no figure and use each browser's own. `latency.json` has no `sound` entry
and no `sources`.

**Fix**, in the room, from Settings › Sound & timing (docs/SOUND.md; it needs the room mic and quiet):
- Calibrate `sonos:drawing-room`, and each screen that will ever play karaoke sound;
- "the sound coming in" (how late the beat clock puts a sound);
- when the real bars are up, each light's timing by camera (docs/calibration/README.md).

The results are written to `deployments/finsbury/calibration/latency.json`: commit it.

## 6. On the machine, not in git

- **Virtual node.** `tools/virtual_node/` changed on 27 Nov (the laser, the bar's layout). Restart Spectre's
  `screen-machine-virtual-node` card: start and restart re-copy the files.
- **Karaoke venv.** Duets (28 Nov) run `tools/karaoke/voices.py` in it. Check
  `~/.venvs/sm-karaoke/bin/python -c "import librosa, onnxruntime, torchaudio"`. The speaker model (26 MB) is
  fetched on first use.
- **Frontend.** If the pull has not been deployed: `git status src/`, then `scripts/deploy-frontend`.
- `python -m smconfig.secrets check`.

## Done when

- `GET /rs/show/api/v1/lights/library` lists the groups `movers`, `lamps` (8: the four pars and the four Hue
  lamps), `lasers`, `derbies`, `flashes` (8) and thirteen line-ups;
- `GET /rs/show/api/v1/rig/space` has two `bars` and the `gigbar-move` layout, and no problems;
- Light setup › Room, Lights › Mode "Room as in Karaoke": the singer's spot while nothing plays, and line-ups
  changing with the sections of a song;
- `GET /api/sound/outputs` shows a measured delay for the karaoke output;
- `python -m smconfig check` is `ok`, and `git status deployments/finsbury` is clean.
