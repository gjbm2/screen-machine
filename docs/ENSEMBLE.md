# The room ensemble: screens and lights playing together (design v1, 22 Oct)

The build spec for the art director's next step: every appliance is a player in one band, and the director decides how
they **interplay**, not only that they all do the same thing at once. Two builders implement it in parallel from this
page alone: **show** (show_service/ensemble.py, the catalogue, the director's ensemble hunks, the contract, the bus)
and **lights** (looks.py: gestures, roles and staggers on the rig's lights). §8 is the renderer's half, for ff (not
built here). §12 has the files each builder owns and the exact interface between them.

Every number marked *starting point* is a design default to tune by eye. Room facts are never invented: the one used
here (south is left, north is right) is the owner's own words, and the Room map's positions replace it once the room
is placed.

## 0. What the owner asked (22 Oct), and the shape of the answer

> "north- and south- screen I think mostly have the same effects and scenes at the same time, but rendering is often
> v. different ... what I think would be better is more of a conversation between room elements e.g. something might
> happen on south (left) and then on north (right) and/or echo between -- likewise the relationship between the hue
> lights, etc. that's where I think the whole system lacks coordination today."

**Today.** The renderer's room model (stream_service/rooms.py) is unison by design: a leader picks, followers mirror
the scene and the auto FX bus, and every screen cuts on the same beat. Each screen renders its own copy (its own
animation clock, seed and slow-down), so the screens drift apart by accident, not by design. show_service's lights
have a spatial phase along the flow (looks.py, flow.py, space.py), but nothing carries a musical *moment* from one
appliance to another. (Tightening true unison, with one clock, one seed and one slow-down decision for both screens,
is a separate renderer item already offered to ff. This design doesn't depend on it: every offset here is on
show_service's shared beat clock, which both screens already read.)

**The answer, in six pieces:**

1. **Players** (§2). Each screen is a player. Each light group is split into players by side (Lamps, left / Lamps,
   right). The glitterball is a centre player. Their places come from the room space, else the fixtures' zones, never
   from a name. Players on one side form a **team**, ordered left to right.
2. **Gestures** (§3). Discrete musical moments: beat and downbeat accents, phrase lines, fills, the drop's hit,
   section changes, a build's swell, and piano note bursts and chords (register and velocity). Each one is published
   once as `room.gesture` with a peak time **per player** (`parts`), on show's clock. Every consumer applies its own
   latency, so a gesture lands on the beat on each player.
3. **Interplay forms** (§4). A catalogue, `config/live/interplays.yaml`, scored on the same tag scales as the scenes:
   unison, echo, call and response, ping-pong, canon (a round), solo and accompany, contrast, register split. A form
   decides which gestures go to whom and when, each player's role, and how the next scene change is staggered.
4. **The director** (§5) picks a form at look changes, with dwell and hysteresis. It favours the piano's form while
   the piano plays. It assigns roles, routes every gesture onto the beat grid, and publishes the state on `room.look`
   and `room.direction` (`ensemble`) and the gestures on `room.gesture`.
5. **Lights** (§7) render gestures per fixture: a flash or swell, a colour pop and a mover's kick, within Hue's caps
   (no strobe, 80 ms attack). Roles scale level, pulse and motion. Today's live Hue path (the virtual Maestro) takes
   the same gestures through the director.
6. **The renderer contract** (§8): a gesture layer, roles per screen, staggered scene changes as an extension of
   rooms.py's leader and follower, and per-side contrast. Unison stays the default.

What it looks like in the room: the drop hits everywhere at once, then rings back across the room (left, right, left).
A new section opens on the south screen and the lamps around it, and the north side answers two beats later. On a
four-on-the-floor track the beat bounces wall to wall. While the piano plays, the low notes light the left side and
the high notes the right.

## 1. Rules for every builder

- Production repo, with other sessions editing other files. Never touch stream_service/ (ff's), cues.py, artnet_out.py,
  clock.py, mllink.py, autopilot.py or situations.py (3a's). No service restarts, commits, deploys, or git
  stash/checkout/reset/clean/add.
- **Shared files** (director.py, contract.py, api.py, show_service/README.md, config/room/show.yaml): use Edit only,
  re-read just before each edit, and add only your own hunks at the anchors given here.
- Owner rules:
  - a prototype that works first;
  - no gates, trips or self-off by default: the density rules in §3.5 are musical spacing in config, never a safety
    stop;
  - no auth;
  - everything in config, with no special cases: nothing branches on a device, group, mode, vibe, scene, form or
    stream name;
  - no invented config values (commented starting points only);
  - plain Room-page words for anything a person sees.
- **Degrade.** Each part lands in any order. Cross-module imports are lazy, as the director does with space.py.
  Without ensemble.py the director runs exactly as today. Without `ensemble` or `gestures` on LookData, looks.py runs
  exactly as today. Without the catalogue the built-in `unison` plays (no gestures, no staggers). Without the room
  placed or zones, every player is centre and only unison forms are eligible.
- Tests are crash-level and real, and run targeted only:
  `flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python -m pytest -q -p no:cacheprovider <files>`.
  The show_service tree may briefly fail to import autopilot while 3a's builder is mid-edit. If it does, test your
  modules directly and say so.
- Match the surrounding style: module docstrings that state the rules, page-words lines, and starting points marked
  where they are defined.
- `config/live/interplays.yaml` is not `config/live/fx_interplay.yaml` (the renderer's scene-and-effect fit). The two
  are unrelated.

## 2. Players (show: ensemble.py)

### 2.1 Who plays

`ensemble.players(rig, groups, *, cfg, space_mod=None, view=None, live_screens=None, devices=()) ->
list[contract.PlayerInfo]`. It is pure and takes no name of anything:

| player | from | id | label (page words) | members |
|---|---|---|---|---|
| a screen | every registry fixture whose type kind is `screen` (RigView `fixtures` + `types`) | its fixture id (`screen.south`) | its fixture `label` ("South Frame") | `[fixture id]` |
| lights | every light group with members (`light_scenes.group_members`, the director's `self._groups`, "*" included), split by side (§2.2) when `players.split_groups` | `<group>:left`, `<group>:right`, `<group>:all` (unplaced or unsplit members) | "<group label>, left" / ", right" / "<group label>" | the group's members on that side |
| a device | the glitterball when `lights.flow.mirror_ball.enabled`: one per `power_member` | `device:<the power_member's subsystem>` | its space point's `label` (a point with `device` equal to the subsystem), else the subsystem humanised ("Mirror ball") | `[]` |

- **`on`.** A screen is on while its renderer screen name is in `live_screens` (compose.enabled's screens: layer
  `live`). With `live_screens` None, every screen is on. Lights and devices are always on: whether a light follows the
  music is the outputs' business, as today.
- **A device** never takes gestures, staggers or colour. It takes a role (§4.3 solo, `lead_kinds`). Its one action is
  §5.7's speed step.
- **Followers.** A light group with `follows` is still its own players. It runs its leader's program, but it is in the
  room where it is.

### 2.2 Where each one is: `x` and the side

Each fixture's `x` (−1 left … +1 right) comes from the first rule that answers. The member mean gives a player's `x`.

1. **Space.** With `space_mod` and a SpaceView, `p = space_mod.position(view, fid)`, and `x = space_mod.lateral(view, p)
   / max|lateral|` over the placed fixtures. lateral is metres right of the front axis, as seen facing the room's
   front. This rule is used only when the placed players span at least `players.min_spread_m` across the room.
   Otherwise every fixture falls to rule 2, so metres and zones never mix. Example: the front is still the frame's
   default north, so both screens sit on the front axis.
2. **Zone.** The fixture's `zone` (fixtures.yaml) through `players.zones` (zone word → `left` | `right`) gives −1 or
   +1.
3. **Its screen.** A lamp with no position and no zone that carries `screen:` takes the `x` of the screen fixture with
   that renderer name. A lamp belongs with its screen.
4. **None.** `x = 0`, centre.

- **Side**: `x < −0.05` is left, `x > 0.05` is right, anything else is centre. `via` names the rule that gave `x`
  (one rule for the whole player).
- **Order**: sort by `(x, id)`. `order` is the index.
- The owner's words (22 Oct): south is on the left and north on the right. So `players.zones` ships as
  `{south: left, north: right}`. Once the Room map has the room and the front is set to the wall people face (west,
  for south to be on the left), rule 1 gives the same answer from the measured positions.

The room today (registry, no space placed): lamps:left = hue.se, hue.sw; screen.south; lamps:right = hue.ne, hue.nw;
screen.north; device:mirror_ball (centre). In order: `lamps:left, screen.south, lamps:right, screen.north`, with the
device centre.

### 2.3 Teams and the order a gesture travels

- `ensemble.teams(players, team_by, order) -> list[list[str]]`:
  - `team_by: side` makes two teams, left then right (players `on`, centre left out);
  - `team_by: player` makes one team per player, in `order`.
  - Fewer than two teams: the form is not eligible (`needs: sides`, §4.1).
- `ensemble.flow_order(players, flow, space_mod=None, view=None) -> list[str]`: the travel order. A form's `order`
  param picks it:
  - `room`: by `x` (left to right);
  - `flow`: by the room's flow.
    - With space and a placed player: ascending `space_mod.flow_coord(view, flow.dir, flow.at_xyz, centroid)`.
    - Without space: `right` and `cw` go left to right, `left` and `ccw` go right to left, and any other dir uses the
      room order.
    - So a "left" flow sends an echo from north to south.
  - `caller`: the calling team first, then by distance from it.
- Teams are ordered by their first player in the travel order. The team index (`EnsemblePart.team`) is a player's
  team's place in that order; a centre player has −1.

## 3. Gestures (show: ensemble.py; the shapes are contract's)

### 3.1 Kinds and where they come from

| kind | source | when (at_ms) | predicted | strength (× `gestures.strength.<kind>`) |
|---|---|---|---|---|
| `accent` | clock | every beat line | yes | 0.5 + 0.5 × energy |
| `downbeat` | clock | every bar line | yes | 0.5 + 0.5 × energy |
| `phrase` | clock, look | a phrase line; a look change that isn't a section | yes | 1 |
| `fill` | clock | each beat of a phrase's last bar, while energy ≥ `gestures.fill_energy` | yes | (beat in bar + 1) / beats per bar |
| `hit` | structure | `drop_armed`'s line (ahead), else a `drop` heard with no armed line within a beat (now) | armed: yes; heard: no | the event's `strength`, else 1 |
| `section` | look, structure | a look change for a section, a repeat or a song (its at_ms); `breakdown` (×0.5) | yes (breakdown: no) | 1 |
| `swell` | structure | `build`: rises from the event to the drop line (`drop_eta_beats`), else over 4 bars | no | 1 |
| `note` | piano | a burst of fewer than `piano.chord_notes` notes (§3.4) | no | (max velocity / 127) ^ `piano.vel_curve` |
| `chord` | piano | a burst with `piano.chord_notes` or more | no | the same |

- **Clock gestures** exist only when the form routes that kind (§4), the grid is `ok` and the vibe's `beat_lock` is
  on. The vibe's `accents` word (contract.ACCENT_STYLES) bounds them:
  - `none`: no clock gestures;
  - `soft`: `phrase` only;
  - `downbeat`: adds `downbeat`;
  - `beat`: adds `accent`;
  - `full`: adds `fill`.

  `accent` and `fill` also need the clock's gate at `beat`; `downbeat` and `phrase` take `dmx`.
- **Each line once.** At every tick, each line in [now + lead, now + lead + `gestures.horizon_s`) not yet scheduled
  becomes a seed. The key is `beat:<clock epoch>:<beat number>`. `lead` is the director's `_lead_s()`
  (structure.events.min_lead_s, the screens' lead).
- **Look gestures** come from `Ensemble.on_look` (§5.2). The key is `look:<look_id>`. A re-announced look keeps its key
  and moves its parts.
- **Structure gestures** come from `Ensemble.on_event`. Keys: `hit:<drop_armed id>` (a call-off re-sends it with
  `parts: []`), `hit:<drop id>`, `swell:<build id>`, `section:<breakdown id>`.
- **Nothing while** neutral (no palette resolves), House lights or Lights low: no seeds are kept.
- **Under Keep this look**, gestures go on, as music.hit flashes and the drop flash do. The form, roles and staggers
  hold.

### 3.2 Shapes

Each kind's shape (contract.GestureShape: attack, hold, decay, depth, colour depth, kick) comes from show.yaml
`ensemble.gestures.shapes.<kind>` (§10) and travels on the gesture, so every consumer agrees on the timing:

- **Lights** apply it with `contract.gesture_weight` (§6.3).
- **The renderer** treats it as the timing hint for its accent.

A part's colour is a palette role word: `gestures.colours.<kind>`, or a contrast side's (§4.3). None means the light's
own colour, brightened.

### 3.3 The shape on the bus

contract.GestureData (§6.1).

- One message per moment. `parts` carries the call and every echo, answer and canon voice, each with its own player,
  `at_ms` (when it **peaks**, as photon time on show's CLOCK_MONOTONIC), `strength` (final), `gen`, `predicted` and
  `colour`.
- **Re-sending** a message with the same `id` replaces the earlier one; `parts: []` calls it off.
- Parts of a reactive call after gen 0 are `predicted` (they are ahead when decided).

### 3.4 Piano bursts (ensemble.py `Ensemble.on_notes`)

- **Source.** The director passes `ctx.notes.raw_since(t)` tuples `(t_s, stream, channel, pitch, vel)` each tick,
  while `ctx.piano().present`.
- **A burst** opens at a note-on with `vel ≥ piano.min_vel`. It takes every note-on within `piano.burst_ms` of the
  previous one, for at most `piano.max_burst_ms`, and closes at the first tick after.
- **What it becomes.** The call's `at_ms` is the burst's first note (reactive), its `pitches` are the notes struck,
  and its `register` is (mean pitch − 21) / 87 (A0 0 … C8 1).
- **The split** (`register_split`): `piano.split` is a MIDI note, or `auto`. Auto is the median of the last
  `piano.split_notes` pitches, clamped to `piano.split_range`.
- **Why words**: "a note, low" / "a chord, high". Under 0.4 is low, over 0.6 is high, anything else is middle.

### 3.5 Keeping it musical (density, never a trip)

Applied in `Ensemble._route` per player, in time order:

- **Spacing.** A part closer than `gestures.min_gap_ms` to one already scheduled for that player is left out, unless
  it is a `hit`. The stronger shows by HTP. Hue's own cycle floor is 160 ms (caps.min_period_ms).
- **Per bar.** At most `gestures.max_per_bar` parts per player per bar (per 2 s without a grid). Hits always play.
- **Floor.** A part under `gestures.min_strength` is left out.
- **Size.** At most `gestures.max_parts` parts per gesture, earliest kept.
- Each rule is counted in the snapshot (`ensemble.counts.spaced`, `.over_bar`, `.weak`), so nothing is silent.

## 4. Interplay forms: `config/live/interplays.yaml` (show)

### 4.1 Entries

```yaml
<name>:                       # NAME_RE (light_scenes.py's)
  label: <page words>         # "Echo across the room"
  kind: <FORM_KINDS word>
  line: <page words>          # the card's line after "Playing together: ", with {order} {first} {second} {lead}
                              # {beats} {low} {high} filled in (§9)
  params: {...}               # the kind's (§4.3); a missing one takes the kind's default, a bad one is a problem line
  routes: [<GESTURE_KINDS>]   # the gestures this form plays; [] = none (today's unison)
  needs: [sides | grid | piano]   # hard: two teams; the clock's grid ok; the piano playing (PianoView.present)
  boost: {<need word>: <factor>}  # the weight x this while that holds (piano: 4 -> the piano's form leads)
  tags: {...}                 # the screens' scales, as light_scenes.yaml: energy, moods, genre_fit ("*"), valence,
                              # organic, density, weight, flow {dirs, honours}
```

**Loading.** `ensemble.Catalogue(path=contract.INTERPLAYS_FILE)` polls by mtime and size (the director every
RELOAD_S). A file that doesn't read keeps its last good entries (`error` says why). An entry that can't be used is
left out with a problem line. Tags are checked with the scenes' own rule, never a second copy:
`light_scenes.Catalogue._tags_of(raw, tagsmod, vocab, where, probs)`, with
`tagsmod = light_scenes.shared()._tags.get()` and `vocab = light_scenes.shared()._vocab(tagsmod)`.

**Scoring** is `light_scenes.shared().score({"tags": tags}, None, direction, flow)`: tags.scene_weight × flow_fit.
That is the renderer's own matching rule, with the flow's affinity. Then × each `boost` that holds. An entry whose
`needs` fail is not eligible. With no eligible entry, the built-in `unison` plays: `{kind: unison, routes: [],
line: "all at once"}`.

### 4.2 The pick (ensemble.py `Ensemble._pick`)

- **When.** At each look change the director announces (`on_look`). Also when the piano starts or stops (`on_piano`)
  and at a song change.
- **Dwell.** A new form needs the current one to have played `pick.dwell_bars` bars (no grid: `pick.dwell_s`). The
  dwell is bypassed when:
  - the current form's `needs` stop holding (the piano stopped under `piano-hands`);
  - the piano starts while an entry with `boost.piano` is eligible;
  - a song changes.
- **The draw.** A weighted random draw (the director's seeded `random.Random(ENSEMBLE_SEED)`) over the eligible
  entries. The form playing now weighs × `pick.stay` (hysteresis, as the band words), so a good fit tends to stay.
- **Held.** While Keep this look holds (and it isn't a person's own Next look), nothing changes.
- **Taking effect.** A new form takes effect at the look change's `at_ms`: it goes out ahead as `next` (§6.1).
- **Log.** Every pick goes to director.jsonl (`ensemble`) with its weights and why. The why is the pick's tag words:
  "energy 0.6, hypnotic".

### 4.3 The forms

Every form has `team_by` (side | player) and `order` (room | flow | caller). It sets three things per player:

- its **role** (ENSEMBLE_ROLES: even, lead, accompany; `show.yaml ensemble.roles` gives each role's scales);
- its **stagger**: how many beats after the room's moment a scene change lands there (each player's own, capped at
  `max_stagger_beats`);
- its **pulse**: the form's `pulse` param (default 1) × the role's `pulse` scale. It softens the scene's own unison
  beat pulse, so the form's pattern reads.

Delays in beats use show's grid. Without a grid, beats × `no_grid_beat_s`, or the param's own `_s` twin where the table
gives one.

**Rules for every form:**

- `hit`: gen 0 goes to every on player at the line (the drop is unison: SHOW_ENGINE §8.5). Echo and canon then add
  their tail.
- A look's `section`/`phrase`: each player's part is at `at + its stagger` (gen = its team index), so each player's
  pop lands with its own cut.
- `fill`: always rolls player by player along the travel order, one beat each, whatever `team_by`. The phrase gesture
  then lands as the form routes it.
- Centre players take only the parts routed to every player.

| kind | params (default) | routing | roles | stagger | pulse |
|---|---|---|---|---|---|
| `unison` | — | every routed seed: one part per on player at its at | even | 0 | 1 |
| `echo` | `delay_beats` 1, `delay_s` 0.5, `decay` 0.6, `repeats` 1, `every_bars` 2, `caller` first \| register, `order` flow | The call goes to the caller team at at. Team k (in travel order from the caller) follows at at + k·delay with decay^k. `repeats` > 1 bounces back along the order (A B A B…), up to `max_parts`. Downbeats only on bars divisible by `every_bars`. `caller: register`: piano seeds start on the low team when register < split, else the high team. | even | team k × delay_beats | 0.8 |
| `call_and_response` | `answer` replay \| own, `decay` 0.85, `half_s` 4, `every_bars` 1 | The phrase splits in halves (no grid: `half_s` blocks). The caller team plays the first half. `replay`: each call part is copied to the answer team at + half a phrase, × decay (gen 1), and live seeds in the answer half are not routed (hits and sections are, unison). `own`: the answer team plays the second half live. The caller swaps every phrase. | caller lead, answer accompany, swapped each half (as `next` on the half line) | answer team: half a phrase | 0.8 |
| `ping_pong` | `on` beat \| half_bar \| bar, `downbeat_boost` 1.5, `scene_offset_beats` 1 | The clock's accents and downbeats go to team (step mod teams), with step = the beat, half-bar or bar number. Other seeds go to the next team in turn. Downbeats are × `downbeat_boost`. | even | team k × scene_offset_beats | 0.4 |
| `canon` | `offset_beats` 2, `offset_s` 1.0, `every_bars` 1, `clock_offset` true | Every routed seed plays on team k at at + k·offset, at equal strength: a round. | even; `clock_offset_beats` = k·offset when `clock_offset` | team k × offset_beats | 0.7 |
| `solo_and_accompany` | `pass_on` section \| phrase \| look, `pass` next \| other, `accompany_beats` 8, `lead_kinds` [screen, lights] | The lead team takes every routed seed. The others take only hit, section and phrase. | the lead team lead, the others accompany. A device leads only when `device` is in `lead_kinds`. The lead passes at `pass_on` to the next team in order, as `next` at that change's at. | lead 0, the others `accompany_beats` | lead 1, the others the role's |
| `contrast` | `palette_roles` [base, highlight], `energy_split` 0.15, `swap_on` section \| look | unison, with each part's `colour` its team's palette role | even, with `palette_role` (team 0 the first, team 1 the second) and screens' `energy_bias` −/+ energy_split / 2, swapped at `swap_on` | 0 | 1 |
| `register_split` | `low` left \| right, `split` (`piano.split`), `outside` 0.25 | note and chord seeds: the pitches under the split go to the low team and the rest to the other, each part's strength from its own notes' loudest. A chord across the split plays on both. hit and section are unison. | even, with `register` [0, s] on the low team and [s, 1] on the high (s = the split as register) | 0 | 1 |

`clock_offset_beats` (canon) and `register` (register_split) are for consumers that can use them (§7.3, §8). Without
them, those forms still play through gestures and staggers.

### 4.4 The shipped catalogue (show writes this file verbatim)

```yaml
# Interplay forms: how the room's screens and lights play together (docs/ENSEMBLE.md §4; show_service/ensemble.py).
# Hot-reloaded. The art director picks one at a look change, weighted by how well its tags fit the music (the scenes'
# own scales and rule), and holds it at least show.yaml ensemble.pick.dwell_bars. Every number is a STARTING POINT,
# chosen on paper: tune by watching the room. Delete this file and the room plays as today: all at once, nothing new.
#   kind     unison | echo | call_and_response | ping_pong | canon | solo_and_accompany | contrast | register_split
#   routes   the moments it plays: accent downbeat phrase fill hit section swell note chord ([] = none)
#   params   the kind's (docs/ENSEMBLE.md §4.3); team_by side | player; order room | flow | caller
#   needs    sides (players on both sides) | grid (the beat clock is sure) | piano (the piano is playing)
#   boost    {need: factor}: the weight x factor while it holds
#   line     the Music card's words after "Playing together: " ({order} {first} {second} {lead} {beats} {low} {high})
version: 1
interplays:
  unison:
    label: All at once
    kind: unison
    line: all at once
    routes: []                                  # today's room: nothing new
    tags: {weight: 0.3}                         # the fallback: rarely drawn while another form fits
  together:
    label: Together on the big moments
    kind: unison
    line: all together on the big moments
    routes: [hit, section, phrase, chord]
    tags:
      energy: [0.6, 1.0]
      moods: [euphoric, aggressive, fiery, bright]
      genre_fit: {trance: 0.85, metal: 0.85, techno: 0.8, house: 0.8, electronic: 0.8, rock: 0.8, punk: 0.8,
                  dubstep: 0.8, drum-and-bass: 0.75, pop: 0.6, "*": 0.4}
      density: 0.8
      weight: 0.8
  echo-across:
    label: Echo across the room
    kind: echo
    line: "echo, {order}"
    params: {team_by: side, order: flow, caller: first, delay_beats: 1, delay_s: 0.5, decay: 0.6, repeats: 1,
             every_bars: 2}
    routes: [downbeat, phrase, hit, section, chord, note]
    needs: [sides]
    tags:
      energy: [0.2, 0.7]
      moods: [dreamy, hypnotic, liquid, cosmic, mellow, cool]
      genre_fit: {reggae: 0.9, downtempo: 0.9, ambient: 0.85, trance: 0.7, electronic: 0.7, house: 0.6, soul: 0.6,
                  jazz: 0.6, piano: 0.6, "*": 0.45}
      density: 0.4
      flow: {honours: true}                     # it travels with the flow, so it suits any
  ripple:
    label: Passed round the room
    kind: echo
    line: "passed {order}"
    params: {team_by: player, order: flow, caller: first, delay_beats: 0.5, delay_s: 0.25, decay: 0.8, repeats: 1,
             every_bars: 4}
    routes: [phrase, hit, section, chord]
    needs: [sides, grid]
    tags:
      energy: [0.35, 0.85]
      moods: [playful, bright, geometric, hypnotic]
      density: 0.6
      flow: {honours: true}
  call-and-answer:
    label: Call and answer
    kind: call_and_response
    line: "call and answer, {first} starts"
    params: {team_by: side, order: room, answer: replay, decay: 0.85, half_s: 4, every_bars: 1}
    routes: [downbeat, phrase, hit, chord, note]
    needs: [sides]
    tags:
      energy: [0.3, 0.8]
      moods: [playful, warm, organic, retro]
      genre_fit: {funk: 0.9, gospel: 0.9, afrobeat: 0.9, soul: 0.85, jazz: 0.85, blues: 0.85, latin: 0.8, rnb: 0.8,
                  disco: 0.75, hip-hop: 0.7, "*": 0.4}
      organic: 0.6
  back-and-forth:
    label: Back and forth on the beat
    kind: ping_pong
    line: back and forth on the beat
    params: {team_by: side, order: flow, "on": beat, downbeat_boost: 1.5, scene_offset_beats: 1}
    routes: [accent, downbeat, fill]
    needs: [sides, grid]
    tags:
      energy: [0.5, 0.95]
      moods: [playful, bright, euphoric, geometric, retro]
      genre_fit: {disco: 0.9, house: 0.85, funk: 0.8, latin: 0.8, techno: 0.75, pop: 0.75, drum-and-bass: 0.7,
                  "*": 0.35}
      density: 0.75
      flow: {dirs: [left, right, cw, ccw]}
  a-round:
    label: A round
    kind: canon
    line: "a round, {second} follows {first} by {beats}"
    params: {team_by: side, order: flow, offset_beats: 2, offset_s: 1.0, every_bars: 1, clock_offset: true}
    routes: [downbeat, phrase, section, hit, chord, note]
    needs: [sides]
    tags:
      energy: [0.25, 0.75]
      moods: [hypnotic, geometric, elegant, cosmic]
      genre_fit: {classical: 0.8, techno: 0.8, piano: 0.75, trance: 0.75, electronic: 0.75, ambient: 0.7, "*": 0.4}
      density: 0.5
      flow: {dirs: [left, right], honours: true}
  spotlight:
    label: One side leads
    kind: solo_and_accompany
    line: "{lead} leads"
    params: {team_by: side, order: room, pass_on: section, pass: next, accompany_beats: 8,
             lead_kinds: [screen, lights]}
    routes: [downbeat, phrase, section, hit, chord, note]
    needs: [sides]
    tags:
      energy: [0.15, 0.7]
      moods: [elegant, dreamy, warm, mellow, tense]
      genre_fit: {singer-songwriter: 0.9, jazz: 0.85, piano: 0.85, classical: 0.8, folk: 0.8, blues: 0.8,
                  soundtrack: 0.8, soul: 0.75, "*": 0.4}
      density: 0.35
      flow: {dirs: [still, up, down]}
  two-moods:
    label: Two moods
    kind: contrast
    line: two moods, swapping sides
    params: {team_by: side, order: room, palette_roles: [base, highlight], energy_split: 0.15, swap_on: section}
    routes: [section, phrase, hit, chord]
    needs: [sides]
    tags:
      energy: [0.2, 0.75]
      moods: [dark, tense, cosmic, cool, icy, fiery]
      genre_fit: {soundtrack: 0.85, dubstep: 0.8, downtempo: 0.8, rock: 0.7, indie: 0.7, trance: 0.7, "*": 0.4}
      valence: 0.35
      density: 0.5
      flow: {dirs: [still, inward, outward]}
  piano-hands:
    label: Low notes and high notes
    kind: register_split
    line: "piano: low notes {low}, high notes {high}"
    params: {team_by: side, order: room, low: left, outside: 0.25}   # owner, 22 Oct: low notes south (left)
    routes: [note, chord, hit, section]
    needs: [piano, sides]
    boost: {piano: 4.0}
    tags:
      genre_fit: {piano: 0.95, classical: 0.85, jazz: 0.8, "*": 0.5}
      organic: 0.8
```

## 5. The director (show: ensemble.py + director.py's ensemble hunks)

### 5.1 `show_service/ensemble.py` (new; pure; no I/O except the catalogue's file)

```python
ENSEMBLE_FALLBACK: dict[str, Any]          # §10's values: the code's fallbacks for show.yaml ensemble
LINE_PREFIX = "Playing together: "         # page words
NEEDS = ("sides", "grid", "piano")
FORM_PARAMS: dict[str, dict[str, Any]]     # kind -> param -> default (§4.3); ranges checked in Catalogue._parse
def parse_settings(raw: Any, probs: list[str]) -> dict          # show.yaml ensemble -> resolved (bad: fallback + line)
class Catalogue:
    def __init__(self, path: pathlib.Path = contract.INTERPLAYS_FILE) -> None
    def poll(self, force: bool = False) -> bool                   # True when the entries changed
    entries: dict[str, dict]; revision: str; error: Optional[str]; problems: list[str]
    def weights(self, direction: Optional[Mapping], flow: Optional[Mapping], facts: Mapping[str, bool]
                ) -> dict[str, float]                             # eligible entries only (facts: the NEEDS words)
    def view(self) -> dict
def players(rig, groups, *, cfg, space_mod=None, view=None, live_screens=None, devices=()) -> list[contract.PlayerInfo]
def teams(players: list[contract.PlayerInfo], team_by: str, order: list[str]) -> list[list[str]]
def flow_order(players: list[contract.PlayerInfo], flow: Optional[Mapping], space_mod=None, view=None) -> list[str]

@dataclass(frozen=True)
class Seed:                                # a moment before the form routes it
    key: str; kind: str; source: str; at_s: float; predicted: bool; strength: float
    dur_s: float = 0.0; register: Optional[float] = None; pitches: tuple[int, ...] = (); why: str = ""
    beat: Optional[float] = None; bar: Optional[int] = None

class Ensemble:
    def __init__(self, cfg: Optional[dict] = None, catalogue: Optional[Catalogue] = None, *,
                 rng: Optional[random.Random] = None) -> None
    def configure(self, cfg: dict) -> None
    def set_players(self, players: list[contract.PlayerInfo], now_s: float) -> bool      # True: changed (rev + 1)
    def on_look(self, now_s: float, at_s: float, *, look_id: str, reason: str, by: str, direction, flow, grid,
                held: bool, piano: bool) -> None
    def on_event(self, kind: str, ev: Mapping, at_s: float, now_s: float, grid) -> None
    def on_piano(self, present: bool, now_s: float, grid, direction, flow) -> None
    def on_notes(self, notes: Sequence[tuple], now_s: float) -> None
    def tick(self, now_s: float, grid, *, active: bool, accents: str, beat_lock: bool, energy: float,
             direction, flow) -> list[contract.GestureData]       # the new and re-sent gestures to publish
    def block(self) -> contract.EnsembleData                      # swapped by reference each tick
    def recent(self, now_s: float) -> list[contract.GestureData]  # LookData.gestures
    def lamp_parts(self) -> tuple                                 # (at_s, strength, shape, predicted, colour role,
                                                                  #  dur_s, frozenset(member ids)) for lights parts
    def part_of(self, who: str, t_s: float) -> Optional[contract.EnsemblePart]   # any thread; a player id or a member
                                                                  # fixture id; next's part once t >= its at
    catalogue: Catalogue; cfg: dict                               # the director polls the one, §7.1 reads the other
    def reset(self, now_s: float, why: str) -> None               # back to unison, nothing scheduled
    def view(self, now_s: float) -> dict                          # the snapshot (§5.6)
```

- **`grid`** is duck-typed: director.Grid's `ok, has_grid, gate, bpm, beat_s, bar_s, bpb, pb, bar_anchor`, plus
  `beat_at(t)`, `t_of_beat(k)`, `bar_at(t)` and `next_line(t, bars)`. ensemble.py never imports director.py.
- **Lines.** Beat k lands at `t_of_beat(k)`. It is a bar line when `(k − bar_anchor) % bpb == 0`, and a phrase line
  when `(k − bar_anchor) % (bpb·pb) == 0`. The half phrase is `bpb·pb / 2` beats.
- **Queues.** `on_look`, `on_event` and `on_notes` only queue seeds. `tick` routes everything queued (in at order,
  each through the form in effect at its at: current, or `next` once at ≥ next.at). It also folds `next` in once now
  ≥ its at, schedules the call-and-response turns and the solo passes as `next` (at least lead ahead, on the line),
  and returns the GestureData to publish.

### 5.2 When the director calls it (director.py, ensemble hunks only)

| hunk | where | what |
|---|---|---|
| import | top | `from . import ensemble as ensmod` and `ENSEMBLE_SEED = 2` (the form draw's own seed, beside LIGHT_FX_SEED) |
| `__init__` | after the light-programs state | `self.ensemble = ensmod.Ensemble(rng=random.Random(ENSEMBLE_SEED))`; `self._ens_note_t: Optional[float] = None`; `self._ens_piano = False`; `self._ens_players_key = None`; `self._ens_lamps: tuple = ()` (lamp_parts with colours resolved, swapped by reference); `self._ens_rev_pub = None` |
| `configure` | after `lights_cfg` | `self.ensemble.configure(ensmod.parse_settings(s.get("ensemble"), probs))` |
| `tick` | in the RELOAD_S block | `self.ensemble.catalogue.poll()` (guarded: a broken catalogue never fails the tick) |
| `tick` | right after `self._lights_tick(...)` | `self._ensemble_tick(now_s, vs, grid, room)` |
| `_on_event` | right after `self._light_event(...)` | `self._ensemble_event(kind, ev, at_ms / 1000.0, now_s, grid)` |
| `_announce` | right after `self._announce_programs(...)` | `self._ensemble_announce(ev, now_s, reason, by, at_s, grid)` |
| `_compose` | the lamp loop and the two dicts | §5.4 |
| `maestro_palette_fn` | after the drop hit | §7.1 |
| `_outputs` | the room.look and room.direction publishes | §5.5 |
| `_mirror_ball` | where the speed word is chosen | §5.7 |
| `on_mode_change` | with the other resets | `self.ensemble.reset(self._now(), "a new mode")` |
| `_build_snapshot` | a new key | `"ensemble": self.ensemble.view(now_s)` |

**`_ensemble_tick(now_s, vs, grid, room)`:**

1. **Players.** Rebuild them when the key changes: (registry revision, `self._groups_key`, space revision, live
   screens from `comp.enabled(room)[0]`, the mirror-ball config). Call `ensmod.players(self._rig_seen, self._groups,
   cfg=..., space_mod=sp, view=view, live_screens=..., devices=...)`, then `self.ensemble.set_players(...)`.
   - The devices come from `self.lights_cfg["flow"]["mirror_ball"]` when enabled: id = its `power_member`'s part
     before the dot, at = the space point whose `device` is that.
2. **Piano.** `pv = ctx.piano()`. On a change of `present`: `on_piano(...)`. While present: `on_notes(
   ctx.notes.raw_since(self._ens_note_t or now_s − 1.0), now_s)`, then move `_ens_note_t` past the newest tuple.
3. **The tick.** `active = self.pal_on and not self.house and not self.low`. Call `out = self.ensemble.tick(now_s,
   grid, active=active, accents=vs["accents"], beat_lock=bool(vs["beat_lock"]), energy=self.music["energy"],
   direction=self.direction, flow=self._flow)`.
   - Each GestureData goes to `self._publish(contract.ROOM_GESTURE, g)`.
   - With any output, every 60 s, log `ensemble_gestures` counts (never one line per gesture).
4. **Lamp parts.** `self._ens_lamps` = `lamp_parts()`, with each colour role resolved to an rgb from this look's
   palette roles (`pal.full(pal.rgb_of(look_pal roles[role].hex))`). It is swapped whole.
5. **Republish.** A new `block()["rev"]` sets `self._force_look`.

**`_ensemble_event(kind, ev, at_s, now_s, grid)`:** passes drop_armed, drop, build and breakdown to `on_event` (only
while `self.pal_on`).

**`_ensemble_announce(ev, now_s, reason, by, at_s, grid)`:** calls `self.ensemble.on_look(now_s, at_s,
look_id=ev["look_id"], reason=reason, by=by, direction=self.direction, flow=self._flow, grid=grid,
held=self.keep_until is not None and by != "human", piano=bool((ctx.piano() or {}).get("present")))`.

- `reason` maps to the seed: section, repeat or track gives `section`; drop gives none (the hit comes from the event);
  relax gives none; anything else gives `phrase`.
- A `track` reason bypasses the dwell, and also passes a solo's lead and restarts call and answer on the first team.

### 5.3 Fallbacks

- **No grid** (clock unsure, gate none): there are no clock gestures. Look, structure and piano gestures still route,
  with every delay in seconds (`delay_s`, `offset_s`, `half_s`, else beats × `no_grid_beat_s`). Turns run by time.
  `stagger_s` is from `no_grid_beat_s`.
- **Neutral.** `block()` is unison with `source: "neutral"` and `line: None`, and nothing is seeded.
- **One side only** (every player centre, or a side with nothing on): only forms without `needs: sides` are eligible.
- **A player goes off** (a screen leaves live): its parts go out anyway (harmless). Its team's next pick counts it as
  off.

### 5.4 Lamps on the director's own path (`_compose`)

The director allocates every lamp's colour (patch.yaml, else the registry's lamps), so roles reach both outputs from
here: the virtual Maestro today, and the rig's look engine through `palette_fn`.

- **Level.** In the lamp loop, after `lv_out = ... lamp_level`, and only when not House or Low:
  `lv_out = floor + (lv_out − floor) × self._ens_scale(did, "level", now_s)` when `lv_out > floor`. So accompany sits
  dimmer, never under the look's floor.
- **Contrast.** Before `_lamp_colour`: `pr = (self.ensemble.part_of(did, now_s) or {}).get("palette_role")`, and when
  set, `rc = {**rc, "palette_role": pr}`. The colour cache keys on the role's hex already. A key lamp stays white-first
  (its white_k and chroma_max still bound it).
- **The pulse.** `_outputs`' accent depth (`depth`, before `set_look_out`) is × the mean `scale.pulse` of the lights
  players: artnet_out takes one depth for every lamp.
- `_ens_scale(member, key, t_s)` is `part_of(...)["scale"][key]`, glided over `glide_ms` from `next.at_ms`, and 1.0
  without a part.
- **LookData** gains `"ensemble": self.ensemble.block()` and `"gestures": self.ensemble.recent(now_s)`.
  **DirectionData** gains `"ensemble": self.ensemble.block()`.

### 5.5 Publishing

- **room.gesture**, per GestureData, as decided. Predicted parts are at least `_lead_s()` ahead. A reactive call goes
  at once.
- **room.look** keeps its cadence, but goes out at once on a new ensemble `rev` (`_force_look`). On the bus it carries
  no `gestures`: `_outputs` publishes `{k: v for k, v in lk.items() if k != "gestures"}`. `self.look` (look_fn) keeps
  the full LookData.
- **room.direction** goes at once on a new ensemble `(id, rev)`, through a `_ens_moved(a, b)` beside `_flow_moved`.
  The renderer reads roles and `next` from it.

### 5.6 The snapshot (`director.ensemble`)

The snapshot holds:

- `{form, kind, label, line, why, source, since_s, order, roles: {player: role}, next: {at_in_s, form, roles} |
  None}`;
- `players: [{id, label, side, via, on}]`;
- `counts: {gestures, parts, by_kind, spaced, over_bar, weak}`;
- `last: [10 latest {id, kind, why, parts: n}]`;
- `weights` (the last pick), `problems`, and `catalogue: {revision, error, problems}`.

`line` is the card's words (§9).

### 5.7 The glitterball

In `_mirror_ball`, once its speed word is chosen from `flow.speed`: when `part_of("device:<subsystem>", now_s)` has role
`lead`, take the next word in `mirror_ball.speeds` (if any). The existing `min_gap_s` still bounds every change: the
motor's mechanics, not a gate. The ball never takes a gesture and is never powered.

## 6. Bus and contract (show)

### 6.1 contract.py: a new block at the very end of the file (verbatim)

```python
# -- the ensemble: the room's appliances as one band (docs/ENSEMBLE.md; the show builder's, 22 Oct) --------------------
ROOM_GESTURE = "room.gesture"      # GestureData, when decided (predicted parts >= the screens' lead ahead); want-only
WANT_ONLY_ENSEMBLE: tuple[str, ...] = (ROOM_GESTURE,)
INTERPLAYS_FILE = REPO_ROOT / "config" / "live" / "interplays.yaml"   # the forms (ensemble.py; hot-reloaded)
GESTURE_KINDS: tuple[str, ...] = ("accent", "downbeat", "phrase", "fill", "hit", "section", "swell", "note", "chord")
GESTURE_SOURCES: tuple[str, ...] = ("clock", "look", "structure", "piano")
FORM_KINDS: tuple[str, ...] = ("unison", "echo", "call_and_response", "ping_pong", "canon", "solo_and_accompany",
                               "contrast", "register_split")
ENSEMBLE_ROLES: tuple[str, ...] = ("even", "lead", "accompany")
ROLE_SCALES: tuple[str, ...] = ("level", "depth", "motion", "pulse", "fx_density", "gestures")
PLAYER_KINDS: tuple[str, ...] = ("screen", "lights", "device")
PLAYER_SIDES: tuple[str, ...] = ("left", "centre", "right")


class PlayerInfo(TypedDict):
    """One player of the room's band (ensemble.players): a screen, a light group's lights on one side, a room device."""
    id: str                        # a screen: its fixture id ("screen.south"); lights "<group>:left|right|all"; a
                                   # device "device:<room subsystem>"
    kind: str                      # PLAYER_KINDS
    label: str                     # page words: "South Frame", "Lamps, left", "Mirror ball"
    side: str                      # PLAYER_SIDES
    x: float                       # -1 (left) .. 1 (right): its place across the room
    order: int                     # 0.. by (x, id)
    via: str                       # where x came from: "space" | "zone" | "screen" | "none"
    members: list[str]             # fixture ids (a device: [])
    group: Optional[str]           # its light group (kind lights)
    screen: Optional[str]          # the renderer's screen name (kind screen)
    on: bool                       # it can show something now (a screen: live; the others: True)


class GestureShape(TypedDict):
    """How a gesture looks on a light (show.yaml ensemble.gestures.shapes.<kind>); the renderer takes its timing."""
    attack_ms: float               # up to the peak (an output's own min attack wins); a swell rises over dur_ms instead
    hold_ms: float                 # at the peak (a light's min_pulse wins)
    decay_ms: float                # then exp(-t / decay)
    depth: float                   # 0..1: how far toward full it lifts a light (HTP over the look)
    colour_depth: float            # 0..1: how far its colour moves toward the part's palette role
    kick: float                    # 0..1: a calibrated mover's lift (looks.py KICK_M x this); 0: none


class GesturePart(TypedDict):
    player: str                    # PlayerInfo.id
    at_ms: float                   # when it PEAKS on that player: photon time, host CLOCK_MONOTONIC ms (lights evaluate
                                   # at t_send + their rise, screens at now + their lead)
    strength: float                # 0..1, final: the moment's x the form's decay x the player's role ``gestures`` scale
    gen: int                       # 0 the call; 1.. its echoes, answers and canon voices
    predicted: bool                # at_ms was ahead when decided (False: a reactive call: show it as soon as possible)
    colour: Optional[str]          # PALETTE_ROLES word for this part (contrast: its side's); None: the light's own


class GestureData(TypedDict):
    """room.gesture: one musical moment and how it travels the room (docs/ENSEMBLE.md §3). A re-send with the same id
    replaces the earlier one (a moved drop line); ``parts`` [] calls it off."""
    id: str                        # "g:<key>": g:beat:<epoch>:<beat>, g:look:<look_id>, g:hit:<event id>, g:piano:<n>
    seq: int                       # +1 per message
    t_ms: float                    # when published
    kind: str                      # GESTURE_KINDS
    source: str                    # GESTURE_SOURCES
    form: str                      # the interplays.yaml entry that routed it
    strength: float                # 0..1: the moment's own, before the form
    shape: GestureShape
    dur_ms: float                  # a swell's rise (0 for the other kinds)
    register: Optional[float]      # note | chord: the mean pitch, 0 (A0) .. 1 (C8); else None
    pitches: list[int]             # note | chord: the MIDI pitches struck; else []
    beat: Optional[float]          # the clock's beat number at the call; None without a grid
    parts: list[GesturePart]       # in at_ms order
    why: str                       # page words: "the drop", "a chord, low"


class EnsemblePart(TypedDict):
    """What one player does under the form (EnsembleData.players and EnsembleNext.players values)."""
    role: str                      # ENSEMBLE_ROLES
    scale: dict[str, float]        # ROLE_SCALES -> the role's (pulse x the form's); screens never take one as a gain
    stagger_beats: float           # a scene change lands this many beats after the room's moment on this player
    stagger_s: float               # the same in seconds by show's grid now (no grid: x ensemble.no_grid_beat_s)
    palette_role: Optional[str]    # contrast: the palette role its colours lean to; else None
    energy_bias: float             # contrast: a screen's direction energy +- this; 0 otherwise
    register: Optional[list[float]]   # register_split: [lo, hi] of 0..1 register it plays; else None
    clock_offset_beats: float      # canon: its beat-bound motion runs this many beats late; else 0
    team: int                      # its team's place in the travel order (0 first); -1: centre
    kind: str                      # PLAYER_KINDS
    side: str
    x: float
    label: str
    screen: Optional[str]
    members: list[str]


class EnsembleNext(TypedDict):
    """A change already decided (a form at a look change, a lead passing, a call's turn). It applies at at_ms on each
    player at its own photon time; the director folds it in once the moment has passed."""
    at_ms: float
    form: str
    kind: str
    label: str
    line: Optional[str]
    players: dict[str, EnsemblePart]
    why: str


class EnsembleData(TypedDict):
    """room.look.ensemble and room.direction.ensemble (the same object, as ``flow``): how the room plays together."""
    id: int                        # +1 per form change
    rev: int                       # +1 per change of anything here (players, roles, next): a cache key
    form: str                      # the interplays.yaml entry ("unison": built in, without a catalogue)
    kind: str                      # FORM_KINDS
    label: str                     # page words: "Echo across the room"
    line: Optional[str]            # the card's words: "Playing together: echo, left to right"; None while neutral
    params: dict[str, Any]         # the entry's params, resolved
    order: list[str]               # player ids in the order a gesture travels (centre players left out)
    players: dict[str, EnsemblePart]
    screens: dict[str, str]        # the renderer's screen name -> player id
    since_ms: float
    at_ms: float                   # when the form now playing took effect
    glide_ms: float                # roles and scales move over this at a change (ensemble.glide_beats, else glide_s)
    next: Optional[EnsembleNext]
    source: str                    # "pick" | "piano" | "held" | "default" | "neutral"
    why: str


def gesture_weight(t_s: float, at_s: float, shape: "GestureShape", *, step: bool, min_attack_s: float = 0.0,
                   min_hold_s: float = 0.0, swell_s: float = 0.3, dur_s: float = 0.0) -> float:
    """How much of a gesture part shows at ``t_s`` (0..1; the caller multiplies by the part's strength and the shape's
    depth): the one rule for the look engine (each fixture at its own photon time) and the virtual Maestro's lamps
    (director.maestro_palette_fn). A swell (``dur_s`` > 0) rises over dur_s from at_s. ``step`` (an output that takes
    hits, or a predicted part): it rises over max(attack, min_attack) to PEAK at at_s. Otherwise (Hue, a reactive
    part): a swell up over max(attack, swell_s) from at_s (SHOW_ENGINE §5.4: Hue never gets a reactive hit). Then held
    max(hold, min_hold), then exp decay; 0 after 8 decays."""
    a = max(float(shape.get("attack_ms") or 0.0) / 1000.0, min_attack_s)
    if dur_s > 0.0:
        t0, peak = at_s, at_s + dur_s
    elif step:
        t0, peak = at_s - a, at_s
    else:
        t0, peak = at_s, at_s + max(a, swell_s)
    if t_s < t0:
        return 0.0
    if t_s < peak:
        x = (t_s - t0) / (peak - t0)
        return x * x * (3.0 - 2.0 * x)
    hold = max(float(shape.get("hold_ms") or 0.0) / 1000.0, min_hold_s)
    if t_s < peak + hold:
        return 1.0
    k = (t_s - peak - hold) / max(0.001, float(shape.get("decay_ms") or 0.0) / 1000.0)
    return 0.0 if k > 8.0 else math.exp(-k)
```

### 6.2 contract.py: fields at existing anchors (Edit only, re-read first)

- **LookData**, after `light_programs`:
  - `ensemble: "EnsembleData"`: docs/ENSEMBLE.md, the same object as room.direction's;
  - `gestures: list["GestureData"]`: the recent ones, newest last (≤ ensemble.gestures.keep), for the look engine.
    Not on the bus's room.look.
- **DirectionData**, after `flow`: `ensemble: "EnsembleData"` (docs/ENSEMBLE.md: forms, roles, staggers per screen).
- **CardData**: `look`'s comment gains `"together": str | None` (§9).

Forward references are strings, so the blocks can land in any order.

### 6.3 bus.py (show)

Line 63 imports `WANT_ONLY_ENSEMBLE` with the others. `WANT_ONLY = _WANT_ONLY_P0 + WANT_ONLY_STAGE4 + WANT_ONLY_STAGE5 +
WANT_ONLY_ENSEMBLE`. room.gesture reaches only a client whose hello `want` names it exactly. The renderer's
`MUSIC_BUS_WANT` must add `"room.gesture"` (§8.1); its `"room"` prefix doesn't match a want-only type.

## 7. Lights

### 7.1 The virtual Maestro's lamps (show: director.py; the live Hue path today)

Until the Hue cut-over (fixtures.yaml `outputs.hue.enabled`, README S5.12), Hue gets the virtual Maestro's lamp
channels, which come from `maestro_palette_fn(t)`. artnet_out calls it at the lamps' own photon time (t + hue latency).
So gestures there are the director's, on the same rule as the rig:

```python
ep = self._ens_lamps                                   # (at_s, strength, shape, predicted, rgb | None, dur_s, lamps)
vl = self.ensemble.cfg["gestures"]["virtual_lamps"]    # Hue: §5.4's attack and the drop hit's swell
for at, s, shape, pred, rgb, dur, lamps in ep:
    w = contract.gesture_weight(t_s, at, shape, step=pred, min_attack_s=vl["min_attack_ms"] / 1000.0,
                                swell_s=vl["swell_ms"] / 1000.0, dur_s=dur) * s * float(shape["depth"])
    if w <= 0.0:
        continue
    for k in lamps & out.keys():
        own = pal.full(out[k])
        tgt = own if rgb is None else pal.full(pal.mix(own, rgb, float(shape["colour_depth"])))
        out[k] = pal.mix(out[k], tgt, min(1.0, w))     # toward full value and the part's colour: HTP-like, OKLab
```

It goes after the drop hit and is guarded (any exception returns `out` as it was). The cost is a few parts × four
lamps per frame. Staggered program transitions don't apply on this path, because its palette glide is one crossfade.
Each side's section pop still lands at its own cut through its gesture part.

### 7.2 The rig's look engine (lights: looks.py)

It reads two LookData fields: `ensemble` (EnsembleData) and `gestures` (list[GestureData]). Missing, it runs as today:
bytes are identical.

- **Membership.** `_ens_map(look)`: fixture id → player id from `ensemble.players[pid].members`, cached on
  `(ensemble.id, ensemble.rev)`. The part in effect for a fixture at time `tt` is `ensemble.next.players[pid]` when
  `next` and `tt ≥ next.at_ms / 1000`, else `ensemble.players[pid]`.
- **Taking gestures.** `LookEngine.take_gesture(g: Mapping) -> bool`, any thread, under the drop lock. It stores the
  parts per fixture in a gesture ring and returns False for a malformed message. A re-sent id replaces its parts;
  `parts: []` removes them. `evaluate` calls `_take_gestures(look)` once per frame: each GestureData whose
  `(id, seq)` it hasn't seen goes to `take_gesture`. The ring keeps parts until 8 decays after their peak.
  `GESTURE_RING = 128` parts (oldest out first).
- **Per fixture, per frame** (the active branch, after the scene parts, effects and today's hits, before fade_dark,
  master and caps). Times are the fixture's own: `ti` (intensity), `tc` (colour), `tp` (position):
  - **Flash.** For each part on this fixture:
    - `w = contract.gesture_weight(ti, at, shape, step=caps_hits or part.predicted, min_attack_s=min_attack_s,
      min_hold_s=s.min_pulse_s, swell_s=HIT_SWELL_MS / 1000, dur_s=g.dur_ms / 1000) × strength`;
    - `inten = max(inten, min(1.0, level + shape.depth × w))` (HTP, as hits).
    - An output without hits (Hue) still shows predicted parts as a shaped rise ending at the beat. The rise limit
      (`min_attack_ms`) bounds it anyway.
  - **Colour pop.** With the part's `colour` role, `colour = palette_mix(colour, roles[role] at full value,
    shape.colour_depth × w(tc))`, with the roles from `look.palette.roles`.
  - **Kick.** For a calibrated mover (pose) with `shape.kick > 0`: the aim target rises by `KICK_M × kick × w(tp)`
    metres (KICK_M = 0.4, *starting point*), through the motion stage, so it is slew-limited (degrade). An
    uncalibrated light with zoom: zoom moves toward `wide` by `kick × w`. A light with neither: nothing. No guessed
    directions.
  - **Never strobe.** Gestures never set shutter. On an output with `caps.min_period_ms`, two parts on one fixture
    closer than that merge into the first (the larger strength).
  - **Not while steady.** Not under House, Low or a held frame, nor on a held, identified or not-following fixture. A
    `hit` shows over the pre-drop dip (as the drop flash); other kinds don't.
- **Roles** (`part.scale`, glided over `ensemble.glide_ms` from `next.at_ms`):
  - intensity layers with phase `beat` (scene and effect) take depth × `scale.pulse`, the others depth × `scale.motion`
    (the `w` passed to `_intensity`);
  - position and beam pattern sizes take × `scale.motion`;
  - level and palette role are not applied here: the director's colours already carry them (§5.4).
- **Stagger.** When a group's program transition `at_ms` equals `ensemble.at_ms` or `ensemble.next.at_ms` (the same
  look change: both are rounded from one at_s), each fixture's transition is evaluated at `ti − stagger_s`, with
  stagger_s = its part's `stagger_beats` × the clock's beat (no clock: its part's `stagger_s`). Through `part.w(ti −
  off, cs)` and `pf.darkness(ti − off)`, the right side crosses over later. `sweep` and `fade_dark` transitions are
  never staggered: a sweep already travels, and the room goes dark together.
- **Canon.** `clock_offset_beats` > 0 shifts that fixture's oscillator phase: `_phase` and `_cycles` evaluate at
  `tt − offset × beat_s`, so its waves run behind the other side's.
- **Stats.** `stats()["gestures"] = {taken, replaced, parts, shown, merged, by_kind}`.

### 7.3 What lights never do

- No new config file. Every number reaches looks.py on the gesture (`shape`) or on the ensemble (`scale`, `glide_ms`,
  `stagger_*`). KICK_M and GESTURE_RING are module constants marked *starting point*.
- No refusal: an unknown player, kind or role is skipped and counted.

## 8. The renderer contract (for ff; not built here)

What show_service sends, and what the renderer is asked to add. None of it is needed for the lights. With none of it
built, the screens stay in unison exactly as today. Everything below extends rooms.py's leader and follower.

1. **Subscribe.** `MUSIC_BUS_WANT` adds `"room.gesture"` (want-only). `on_room_message` gains a branch for it.
   room.direction now carries `ensemble` (EnsembleData), which `lenient_direction` ignores as an unknown key. A screen
   finds itself as `pid = ensemble["screens"][<its screen name>]`, and its part as `ensemble["players"][pid]`, or
   `ensemble["next"]["players"][pid]` from `next.at_ms` on, at its photon time.
2. **The gesture layer.** For each part of a room.gesture whose player is this screen:
   - **Timing.** Peak at `at_ms` on the glass: start the accent at `at_ms − lead − attack`. A part already inside the
     lead plays at once with its attack eased. A part more than one beat late (`predicted: false` reactive calls
     included) is not shown: it would be off the beat. That is timing, not a gate.
   - **Look.** An accent per kind, ff's choice of plug-ins. A suggested start, all of them existing effects:
     - `accent` and `downbeat`: `flash` 0.15–0.3, or a small `shake`;
     - `hit`: `shockwave`, or `flash` 0.6;
     - `section` and `phrase`: a `bloom` swell;
     - `fill`: a `trauma_shake` tap;
     - `note`: a `note_ripples`-style ripple placed by `register`;
     - `chord`: `isf_shockwave_pulse`;
     - `swell`: `bloom` rising over `dur_ms`;
     - `colour` (a palette role): a short palette pop.
   - Implement it as one "gesture" slot at the end of the FX bus (origin `gesture`). The FX director never counts or
     moves it; it is driven by an envelope from `shape`. Give it a renderer config file of ff's (for example
     config/live/gesture_accents.yaml).
   - **Strength.** `part.strength` is final: show already applied decay and role. It is never a gain on the picture
     (the screens rule): a flash is light added inside the scene, and the TVs never dim or go black from a gesture.
   - Degrade under load like any effect (degrade.py). Never refuse.
3. **Roles per screen** (`part.scale`):
   - `depth` × the screen's binding depth (direction.intensity, the music depth; never brightness);
   - `pulse` × its beat pulse depth (pulse_depth / the beat ladder's depth);
   - `motion` × its speed-bound params (the scene's pace);
   - `fx_density` × direction.fx.density for its own FX director.
   - In a room, `rooms.fx_source`: when the ensemble names a screen with role `lead`, that screen's auto FX are the
     ones the others mirror. An `accompany` screen mirrors only the first `round(n × fx_density)` of them
     (`rooms.fx_step` with the trimmed list).
   - Changes glide over `ensemble.glide_ms` from `next.at_ms`.
4. **Staggered scene changes.** The leader still picks and still prepares the pick on every screen (`_room_prepare`,
   `READY_S`). Each screen then cuts at the room's base photon time + its own stagger:
   - New in rooms.py: `stagger_s(ens: Optional[dict], screen: str, beat_s: Optional[float], at_mono: float) -> float`.
     It is the part in effect at `at_mono` (current or next); `stagger_beats × beat_s` when the renderer's clock is
     sure (`music_grid`), else its `stagger_s`; 0 without an ensemble.
   - `Session._submit`: for an auto cue (cause `auto`, the leader's own or a room follow), `photon_at += stagger_s(...)`
     for this screen. `_room_go` passes the leader's base photon time (before its own stagger) to `follow_cue`, so
     each follower adds its own.
   - Not staggered: song changes with a `transition` (fade-dark and segue go together; README S5.17), cues a person
     made (Library, a PATCH scene), and transitions with `sweep_beats`.
   - Unison stays the default: no ensemble, or all staggers 0, is today's code path.
5. **Contrast.** `palette_role` is the room palette's role this screen leans to: emphasise that role's colour in its
   palette, in whatever way the renderer's palettes allow. `energy_bias` is added to its direction energy for its own
   bindings and FX weights; the leader's picks stay the room's.
6. **Register.** `register` [lo, hi] on a screen: scenes and effects that draw notes (`needs: [notes]`,
   `note_ripples`) draw the notes inside the window at full and the others at `ensemble.params.outside` (0.25). Notes
   are never hidden.
7. **Canon's offset** (optional): `clock_offset_beats` > 0 evaluates that screen's beat-bound signals (beat_phase,
   bar_phase, the beat ladder's pulse) at photon time − offset × beat_s, so its motion is a round behind the other.
   Without it, canon still plays through gestures and staggers.
8. **The diagnostics HUD** (docs/LIVE_DIAGNOSTICS.md, e1/ff): one line, for example "Together: echo, left to right ·
   lead south".
9. **One picture by design, not by accident** (ff, 22 Oct; agreed with e9). Today each screen keeps its own scene time,
   random seed, simulation state and degrade level, so the same scene drifts apart (and the screens now sit on
   different GPUs). With this contract:
   - **Unison:** the room shares scene t0 and seed, so both screens draw the same picture.
   - **Echo and canon:** the same seed with a per-screen time offset (`clock_offset_beats` × beat_s; item 7), so a
     screen draws the same picture a round later. This replaces item 7's beat-signal-only offset where the scene
     allows it.
   - **Degrade:** one decision per room, the worse screen's, so one screen never drops effects or frame rate alone.
   - **Caveat:** a simulation that reads per-screen inputs (MIDI, the room mic) still drifts unless the input stream is
     shared, or the leader's state is mirrored to the follower. ff picks which.
   - **Photon time:** `at_ms` here is always photon time on show's clock. Each screen converts it with its own
     render→photon lead into frame ids.
   - **The gesture layer** (item 2) may be built as an overlay-stage effect, like the six overlays that listen to
     scope/fft/notes (ff's preference), instead of a bus slot. Either way the FX director never counts or moves it.
   - **Role levers** (item 3) are the four ff exposes per side: intensity/dimmer-free depth, effect count
     (fx_density), palette offset (palette_role) and scene rate (motion). The numbers per role are show.yaml
     `ensemble.roles` (§10: lead 1.0 across; accompany level 0.7, depth 0.6, motion 0.5, pulse 0.6, fx_density 0.5,
     gestures 0.5), and they arrive already applied in each part's `scale`.

## 9. The Room page (words only; the page is 09's)

One plain line on the Music card, under "Hearing": the ensemble's `line`. Examples:

| form | line |
|---|---|
| unison | Playing together: all at once |
| together | Playing together: all together on the big moments |
| echo-across | Playing together: echo, left to right |
| ripple | Playing together: passed round the room |
| call-and-answer | Playing together: call and answer, left starts |
| back-and-forth | Playing together: back and forth on the beat |
| a-round | Playing together: a round, right follows left by 2 beats |
| spotlight | Playing together: the left side leads |
| two-moods | Playing together: two moods, swapping sides |
| piano-hands | Playing together: piano: low notes left, high notes right |

**Fillers:**

- `{order}`: "left to right" or "right to left" for side teams; "round the room" for player teams on a cw/ccw flow,
  else the side words.
- `{first}` and `{second}`: the first and second team's side word.
- `{lead}`: "the left side", "the right side", or the lead player's label ("the North Frame").
- `{beats}`: "2 beats", "1 beat", "half a beat"; seconds without a grid ("a second").
- `{low}` and `{high}`: side words.

The line is None while neutral, and the page shows nothing then.

**api.py** (show, only if trivial). In `card()`, `look_out["together"] = _d(director.get("ensemble")).get("line")`.
3a is mid-edit in api.py's card: re-read first. If that hunk is moving, leave it out and say so; the snapshot carries
the line either way, and 09 can read it there.

## 10. show.yaml `ensemble:` (show adds this block verbatim, after `lights:`)

```yaml
# ---- The ensemble: how the room's screens and lights play together (docs/ENSEMBLE.md). Starting points unless marked.
ensemble:
  players:
    zones: {south: left, north: right}      # owner, 22 Oct: "south (left) ... north (right)". The Room map's positions
                                            # win once the room is placed and its front set (facing west, south is left)
    split_groups: true                      # each light group plays as one player per side (Lamps, left / Lamps, right)
    min_spread_m: 0.5                       # placed players closer than this across the room: the zones decide instead
  pick:
    dwell_bars: 16                          # a form plays at least this long before another (a song change may)
    dwell_s: 45                             # the same without a beat grid
    stay: 1.5                               # the form playing weighs this much more in the next draw (hysteresis)
  roles:                                    # screens: never a gain (depth: music depth); lights: never under the floor
    even:      {level: 1.0, depth: 1.0, motion: 1.0, pulse: 1.0, fx_density: 1.0, gestures: 1.0}
    lead:      {level: 1.0, depth: 1.0, motion: 1.0, pulse: 1.0, fx_density: 1.0, gestures: 1.0}
    accompany: {level: 0.7, depth: 0.6, motion: 0.5, pulse: 0.6, fx_density: 0.5, gestures: 0.5}
  glide_beats: 2                            # roles move over this many beats at a change
  glide_s: 1.0                              # ... without a beat grid
  no_grid_beat_s: 0.5                       # a form's beats in seconds when the clock has no grid
  max_stagger_beats: 16                     # a scene change spreads across the room over at most this long
  gestures:
    horizon_s: 0.25                         # clock lines are scheduled this window past the screens' lead (> one tick)
    min_gap_ms: 180                         # one player's parts at least this far apart (a hit always plays)
    max_per_bar: 4                          # one player's parts per bar at most (per 2 s with no grid; hits always)
    min_strength: 0.05                      # a part weaker than this is left out
    max_parts: 8                            # parts per gesture (echoes and answers included)
    keep: 32                                # recent gestures on LookData for the look engine
    fill_energy: 0.6                        # a phrase's last bar rolls across the room above this energy
    strength: {accent: 0.35, downbeat: 0.55, phrase: 0.75, fill: 0.45, hit: 1.0, section: 0.7, swell: 0.5,
               note: 0.5, chord: 0.7}
    colours: {accent: null, downbeat: accent, phrase: highlight, fill: null, hit: highlight, section: accent,
              swell: accent, note: accent, chord: highlight}   # a palette role, or null: the light's own colour
    shapes:                                 # how each kind looks on a light (the renderer takes the timing)
      accent:   {attack_ms: 0,  hold_ms: 40,  decay_ms: 160, depth: 0.25, colour_depth: 0.0, kick: 0.0}
      downbeat: {attack_ms: 0,  hold_ms: 60,  decay_ms: 220, depth: 0.35, colour_depth: 0.2, kick: 0.3}
      phrase:   {attack_ms: 40, hold_ms: 80,  decay_ms: 500, depth: 0.45, colour_depth: 0.4, kick: 0.5}
      fill:     {attack_ms: 0,  hold_ms: 30,  decay_ms: 120, depth: 0.25, colour_depth: 0.0, kick: 0.2}
      hit:      {attack_ms: 0,  hold_ms: 80,  decay_ms: 400, depth: 1.0,  colour_depth: 0.6, kick: 1.0}
      section:  {attack_ms: 80, hold_ms: 120, decay_ms: 900, depth: 0.5,  colour_depth: 0.6, kick: 0.6}
      swell:    {attack_ms: 0,  hold_ms: 0,   decay_ms: 600, depth: 0.5,  colour_depth: 0.3, kick: 0.0}  # rises over
                                                                                                         # its length
      note:     {attack_ms: 0,  hold_ms: 30,  decay_ms: 200, depth: 0.3,  colour_depth: 0.3, kick: 0.0}
      chord:    {attack_ms: 0,  hold_ms: 60,  decay_ms: 350, depth: 0.5,  colour_depth: 0.5, kick: 0.3}
    virtual_lamps: {min_attack_ms: 80, swell_ms: 300}   # the virtual Maestro's lamps are Hue: §5.4's attack, and the
                                            # drop hit's swell (director.build.hit_swell_ms) for a reactive part
  piano:
    burst_ms: 90                            # note-ons this close together make one gesture
    max_burst_ms: 250
    chord_notes: 3                          # this many notes in a burst make a chord
    min_vel: 20                             # a quieter strike makes no gesture of its own
    vel_curve: 1.5                          # strength = (velocity / 127) ^ this
    split: auto                             # register_split's line: a MIDI note, or auto (the median of the last notes)
    split_range: [48, 72]                   # auto stays within C3..C5
    split_notes: 64
```

`parse_settings` checks each key's type and range: numbers > 0 where a time, 0..1 where a share; `zones` values in
left | right; `colours` values in PALETTE_ROLES or null; every shape key present. A bad value falls back to
ENSEMBLE_FALLBACK with a problem line in `settings_problems`. With no `ensemble:` block, ENSEMBLE_FALLBACK plays with
`zones: {}`, so every player is centre and the room plays unison. The room fact lives only in show.yaml.

## 11. Tests without hardware

### 11.1 show: `show_service/tests/test_ensemble.py` (new), `test_director.py` (ensemble cases)

Use a synthetic RigView (two sm-screen fixtures zoned south and north, four sm-hue lamps zoned and with `screen:`),
`light_scenes.group_members` with `lamps: {select: "kind:lamp"}`, and `director.Grid(clock body, now)` at 120 bpm, 4/4,
8-bar phrases.

- **Players.**
  - Zones give `lamps:left, screen.south, lamps:right, screen.north` with sides and `via: zone`.
  - A space view from `space.build_view` (front west, everything placed) gives the same order with `via: space`.
  - Front north with the screens on the axis falls back to zones.
  - No zones and no space: everything centre, `teams()` has fewer than 2, and only unison forms are eligible.
  - A lamp with no zone takes its screen's side.
  - The mirror-ball device is centre, with no members.
  - `on` follows `live_screens`.
- **Catalogue.**
  - The real `config/live/interplays.yaml` loads with no problems.
  - A bad kind, an unknown param or a bad range is left out with a problem line.
  - A broken file keeps the last good entries.
  - An mtime change reloads.
  - The tags are validated by light_scenes' rule.
- **Pick.**
  - Seeded draws are reproducible.
  - `needs: piano` is eligible only with the piano.
  - With the piano present, `piano-hands` wins at least 150 of 200 seeded draws.
  - No change before `dwell_bars`, except for a song change or piano presence.
  - Keep holds the form.
  - The new form arrives as `next` at the look's at_ms and is folded in after it.
- **Routing**, one test per kind, with exact `at_ms` and strengths:
  - unison: `routes: []` publishes nothing; `together` gives a hit on four parts at one at.
  - echo: delay 1 beat, decay 0.6: left at T, right at T + 0.5 s × 0.6. A "left" flow reverses it. `caller: register`
    starts a low chord on the left.
  - call_and_response: a downbeat in the first half goes to left only, with a replay to right at + 16 beats (×0.85).
    Live seeds in the second half are not routed; a hit is. The next phrase's caller is right.
  - ping_pong: 8 beats alternate L R L R…; downbeats ×1.5; a fill rolls player by player.
  - canon: right at + 2 beats at equal strength; `clock_offset_beats` 2 on the right team.
  - solo: lead and accompany roles; a section look passes the lead to the right, as `next` at that at.
  - contrast: palette roles base/highlight by side, `energy_bias` ±0.075, swapped at a section.
  - register_split: a low chord goes left, a high one right, a two-handed one to both. An auto split from 64 notes is
    clamped.
  - No grid: delays in seconds; no clock gestures.
- **Density.** Two parts 100 ms apart on one player keep the first (`spaced` +1). A hit always plays. `max_per_bar`
  holds. A weak part is left out.
- **Piano.** Notes within burst_ms make one gesture. Three notes make a chord with its register. Velocity sets
  strength. Under `min_vel` makes nothing.
- **Clock.** Each beat is scheduled once, at least lead ahead, keyed by epoch and beat. Accents `none` gives nothing,
  and `downbeat` gives downbeats and phrases only.
- **Lifecycle.** A drop_armed line gives a predicted hit; its call-off re-sends the same id with `parts: []`; a drop
  inside the armed beat adds nothing. Neutral, House and Low seed nothing; Keep keeps gestures and holds the form.
  `reset` goes back to unison.
- **`gesture_weight`.** A step peaks at at (1.0); with an 80 ms min attack it rises from at − 0.08; a reactive swell
  takes swell_s; dur_s rises over dur. Hold, then exp decay, then 0 after 8 decays.
- **Bus.** `contract.ROOM_GESTURE in bus.WANT_ONLY`. A client with no want doesn't get it; `want: ["room.gesture"]`
  does; `want: ["room"]` doesn't.
- **Director** (test_director.py's `Rig`, with `settings["ensemble"]`, `ctx.rig` giving the zoned registry, the clock
  on and a feel set):
  - room.direction and room.look carry `ensemble`; room.look on the bus has no `gestures`, while `d.look_fn()` does;
  - under a forced `a-round` (a catalogue file in tmp_path), room.gesture messages are published with right parts at
    + 2 beats;
  - a section look's gesture has each screen's part at its stagger;
  - accompany lamps sit at floor + (level − floor) × 0.7 in `palette_fn`;
  - `maestro_palette_fn` brightens the left lamps at a left part's at and not the right ones;
  - the snapshot's `ensemble.line` reads "Playing together: …";
  - with no `ensemble:` block, nothing new is published and the colours are exactly as before.

### 11.2 lights: `show_service/tests/test_looks_gestures.py` (new)

Use one DMX RGB par on an artnet-caps output (hits) and one sm-hue lamp on a hue-caps output (min_attack 80,
min_period 160). Give them rise latencies (par 0, hue 99 ms). `look_fn` returns LookData with `ensemble` (lamps:left =
[par], lamps:right = [hue]) and `gestures`.

- **DMX.** A predicted hit part at T: the intensity at the frame with ti ≥ T is level + depth × strength, held
  min_pulse, then decays, and back to level within 8 decays.
- **Hue.**
  - A predicted part rises from T − 80 ms and peaks at T (evaluated at t_send + 99 ms), never steeper than the rise
    limit.
  - A reactive part swells over ≥ 300 ms.
  - Two parts 100 ms apart make one flash (merged).
- **Routing.** A part for lamps:right never touches the par. Unknown players are counted, never raised.
- **Once.** The same `(id, seq)` on two frames gives one flash. A re-sent id with `parts: []` removes it.
  `take_gesture` returns False on a malformed body.
- **Colour pop.** The colour moves toward the role's colour by colour_depth × w.
- **Roles.** Accompany: a beat layer's depth × 0.6, a bar layer's × 0.5. `next` at T glides over glide_ms.
- **Stagger.** A program crossfade at at_ms with the right player's stagger 4 beats at 120 bpm starts 2 s later on the
  right. A sweep isn't staggered.
- **Canon.** `clock_offset_beats` 2 shifts a beat wave by two beats on that fixture.
- **Kick.** A calibrated synthetic mover's aim rises by at most KICK_M × kick, within max_speed per frame. An
  uncalibrated mover with zoom widens; one without does nothing.
- **Steady.** House, Low and held: no gesture. Under the dip: a hit shows, an accent doesn't.
- **Parity.** No `ensemble` and no `gestures`: frames byte-identical to the same look without them (today's tests
  unchanged).

## 12. Ownership and interfaces

### 12.1 Files (disjoint)

| builder | owns | reads (never edits) |
|---|---|---|
| **show** | show_service/ensemble.py (new); config/live/interplays.yaml (new, §4.4); director.py (ensemble hunks only, §5.2); contract.py (the §6.1 block at the end, the §6.2 fields); bus.py (the WANT_ONLY line and its import); config/room/show.yaml (the `ensemble:` block only, §10); api.py (the one card line, only if trivial, §9); README.md (an "S5.31 The ensemble" entry); tests: test_ensemble.py (new), test_director.py (ensemble cases) | light_scenes (shared(), score, _tags_of, group_members), space (position, lateral, flow_coord), compose.enabled, flow.speed_word |
| **lights** | show_service/looks.py; outputs.py only if a hook proves needed (none is expected: everything arrives on LookData); tests: test_looks_gestures.py (new) | contract (GestureData, EnsembleData, EnsemblePart, gesture_weight) |

### 12.2 The interface between them

| from → to | interface |
|---|---|
| show → lights | LookData `ensemble` (EnsembleData: players, members, scale, stagger_beats/stagger_s, clock_offset_beats, glide_ms, next) and LookData `gestures` (list[GestureData], newest last, each id re-sent whole), through `look_fn` as today |
| show → lights | `contract.gesture_weight` (§6.1, verbatim): the one envelope rule for both paths |
| show → lights | the director's lamp colours already carry the role level and contrast's palette role (§5.4): looks.py doesn't apply them again |
| show → renderer (ff) | room.gesture (want-only), room.direction.ensemble (§8) |
| show → page (09) | the snapshot's `director.ensemble.line`; CardData `look.together` |

**Landing order.** Show lands the contract block first, verbatim, so the lights builder can import it. Until it lands,
the lights builder reads the dicts by key and calls `getattr(contract, "gesture_weight", None)`; without it there are
no gestures, and nothing fails.

**Degrade order.**
- Without ensemble.py the director is as today.
- Without the catalogue, built-in unison plays (nothing new).
- Without zones or space, everything is centre and unison.
- Without looks.py's half, gestures still show on today's Hue path (§7.1) and the screens (once ff builds §8).
- Without ff's half, the screens stay in unison as today.

## 13. Not in this step

- One shared clock, seed and slow-down for both screens (ff's separate item).
- Per-lamp players (each Hue as its own voice; `split_groups: member` later if the room asks for it).
- Learning forms from the owner's taps.
- The listener choosing forms by name (it guides by moods and trajectory only, as for scenes).
- Blinds as players.
- A Room-page control for the form (the line is read-only; Next look still moves everything on).
