# Scenes and effects together: reasoning and framework

17–18 Oct 2026, by screen-machine-2b. This is steps 1–2 of the owner's order:
1. reason it through on paper;
2. design the config and data framework;
3. implement it;
4. the owner judges the result.

A non-real-time review layer is considered only if step 4 shows a mess.

## 0. What happens today

- The scene director picks a scene. The FX director separately adds, swaps and removes up to `auto.fx.max` effects, 2 by default. Its rules:
  - at most one effect per stage;
  - effects chain in a fixed stage order: warp, feedback, glitch, stylise, colour, light, projection, motion, overlay.
- Effects sit on top of the whole picture, including scene transitions. An effect chosen during one scene carries on over the next.
- Both directors score the music only: energy, moods, genre and style. Neither looks at the other layer.
- They move independently. A scene change and an effect change can land on the same section boundary or miss each other.
- Today's per-preset tags are one model's guesses from a contact sheet, and nobody has checked them. They are not fit to judge combinations.

## 1. How the two layers interact

### 1.1 What an effect does to the picture (its role)

The stage is the wrong axis: "stylise" currently holds bloom, god rays, a kaleidoscope, a shockwave and halftone. What matters is what the effect does to the picture. There are nine roles:

| Role | Does | Examples |
|---|---|---|
| grade | changes colour and tone; shapes stay | grade, film_stock, hue_drift, split_tone_grade, palette_map |
| light | adds light: glow, rays, flares, flashes | bloom, god_rays, lens_flare, light_leaks, edge_glow, flash |
| texture | redraws the picture in a medium: dots, pixels, strokes, glyphs, CRT | halftone, dither, kuwahara, ascii_mode, crt_monitor, led_wall, pixel_sort |
| reshape | moves pixels around: warp, mirror, kaleidoscope, polar | wave_warp, twirl, kaleido_mirror, polar_wrap, droste, tile_split, liquid_warp |
| trails | smears over time | feedback_echo, afterimage_ghost, smear_drag, beat_stutter |
| corrupt | breaks the picture: blocks, tears, mosh | block_glitch, jpeg_crush, sync_fault, chroma split |
| reframe | puts the picture on another object | globe_wrap, frame_cube, cube_grid, flag_wave, tunnel_wall, mirror_ball |
| camera | moves the whole frame | shake, sway, handheld_drift, dolly_zoom, rotate_wobble |
| add | puts new things on top | rain_on_glass, snowfall, spark_overlay |

Each effect also has a **strength**: how far its output departs from its input at a normal amount. A grade is light; a datamosh is heavy.

### 1.2 What a scene can take

These are the scene properties that decide how an effect lands. They come from the picture or the plug-in, not from mood guesses.

- **busy:** how much fine detail is on screen. Sparse geometry is 0; a fractal or a particle swarm is 1.
- **motion:** how much the frame changes per second, including camera travel. Tunnels and fly-throughs are high; a slow ink bloom is low.
- **brightness:** the mean level, plus how much of the frame sits near white.
- **symmetric:** already mirrored or radial, as with mandalas, kaleidoscopic tunnels and many fractals.
- **colour source:** whether the scene is drawn in the room's palette or brings its own colours (photos, gallery art, some shaders).
- **legible:** whether the content must stay readable: the piano score, notes, photos and art, any text.
- **own traits:** whether the scene already does what an effect would do. Fluid ink already trails; a glitch shader already corrupts; a tunnel already has camera motion.

### 1.3 The combinations that matter

| Effect role | Works on | Goes wrong on | Why |
|---|---|---|---|
| grade | anything, best on self-coloured scenes (ties a photo into the room's colour) | palette-drawn scenes, mildly (it doubles the room palette); already-dark scenes with a darkening grade | Colour stacks |
| light | dark scenes with highlights: cosmic, particles, neon | bright scenes, which blow out | Light adds; level_slow already sets the overall level (the loudness-once rule) |
| texture | clear, larger forms: photos, landscapes, slow abstracts | busy scenes (halftone of a fractal is static); legible content at strong amounts | It resamples detail |
| reshape | asymmetric, organic or figurative scenes | symmetric scenes (a kaleidoscope on a mandala doubles up); busy scenes (mush); fast camera motion (fights the travel) | It re-maps space |
| trails | mid-motion scenes with distinct moving things | fast motion (smears to blur); very slow scenes (no visible effect); scenes that already trail | It mixes in past frames |
| corrupt | sparse or geometric scenes; tense or electronic music | calm organic scenes (unless tension is the point); legible content; already-glitchy scenes | It destroys structure |
| reframe | busy or textured scenes, which read as a surface | dark or sparse scenes (a black sphere on black); legible content | It shrinks the scene into an object |
| camera | stable compositions | scenes with their own camera travel (double motion, queasy) | Motion stacks |
| add | sparse scenes | busy scenes (clutter) | Detail stacks |

The general rules:
- **Load:** a busy scene plus a strong effect makes mush.
- **Redundancy:** doubling a trait the scene already has looks like a mistake.
- **Brightness and colour:** the two layers add up.
- **Legibility:** some content must survive.

Two effects follow the same rules among themselves:
- two reshapes, or reshape plus reframe, compound;
- trails plus corrupt can be a deliberate effect in techno, but is mush elsewhere.

### 1.4 How the music changes the right balance

The music decides two things. **How much the effects may take over** (a load budget) and **which roles suit**.

| Music | Scene's role | Effects' role | Load budget | Roles that suit | Typical good pair |
|---|---|---|---|---|---|
| Ambient | Carries the piece | Subtle enhancer, or none | Low (0–1 effect, soft) | grade, light, slow trails, gentle reshape | Slow organic scene + light or grade |
| Classical / solo piano | Carries; the score must stay readable | Rare, gentle | Low | grade, light; reshape only on non-legible scenes | Score + a warm grade; waterfall + glow |
| Jazz / soul / funk | Warm, organic | Texture and warmth | Medium-low | texture (film, halftone), grade, camera sway with groove | Photo or abstract + film or halftone |
| Pop / house / disco | Bright, rhythmic | Sparkle and lift | Medium–high | light, reshape (kaleidoscope), add (sparks), camera pulse | Clear bright scene + bloom/kaleido |
| Techno / electronic | Often minimal | Often the main event | High | reshape, trails, corrupt, light | **Sparse** geometric scene + heavy reshape/trails (busy + heavy = mush) |
| Rock / metal / punk | Raw | Grit and impact | Medium–high | corrupt, texture (grain), camera shake, grade (high contrast) | Fire or raw scene + grain + shake on hits |
| Hip-hop | Graphic | Graphic stylisation | Medium | texture (halftone, posterise), grade (VHS), camera (bass shake) | Bold shapes + halftone |

The load budget is the key link between music and interplay. With calm music, a busy scene leaves no room for effects. With techno, a sparse scene leaves room for a heavy one.

### 1.5 How cues change the moves

| Cue | Scene layer | Effects layer | Rule |
|---|---|---|---|
| Ordinary dwell end | New scene | Re-check the carried effects against the new scene | The carried effect must suit what it lands on; replace a poor fit during the transition |
| Section change | Usually keep | Swap one effect | Change one layer at a time, for continuity |
| Build | Keep | Raise the amount; lean towards trails, reshape and corrupt late in the build | Tension rises on the effects layer |
| Drop | May change (a big moment) | A strong effect at once, budget opened | Both layers may move together only here or at a track change |
| Breakdown | Keep, or move to calmer | Thin to 0–1, lean towards grade or light | Let the scene breathe |
| Track change | Fresh scene | Reset: choose effects for the new scene | A clean slate |
| Song end / fade to dark | — | Fade out | — |

Not changing both layers at once except at big moments is the main coordination rule. The two directors currently ignore it.

## 2. Framework: data, config and how they combine

### 2.1 Data: where each property comes from, and how much to trust it

| Property | Source | Trust |
|---|---|---|
| Effect **role** | Classified from source, one per plug-in. A factual question ("what does this do to the picture"), checked by measurement | High |
| Effect **strength** | **Measured:** render each effect over a fixed set of test scenes; mean change between input and output at amount 0.8 | High (objective) |
| Effect **adds detail** | **Measured:** change in edge density, output vs input | High |
| Scene **busy** | **Measured:** edge or high-frequency density over ~16 frames | High |
| Scene **motion** | **Measured:** mean frame-to-frame change | High |
| Scene **brightness** | **Measured:** mean level; share of the frame near white | High |
| Scene **symmetric** | **Measured:** a frame vs its mirror and rotation | Medium-high |
| Scene **colour source** | **Structural:** reads the palette or not (params / colour mapping) | High |
| Scene **legible** | **Authored:** a short list (live score, gallery, live-art, text scenes) | High |
| Scene **own traits** (trails, glitchy, camera travel) | Measured (motion, symmetry), plus a short authored list for trails/glitch | Medium |
| Retag moods / genre / harsh | LLM guess | **Low: kept only as a mild prior, no longer used for interplay** |

Measurement runs offline on GPU 1 with the existing harness (fxtest). It pauses while any screen is live, and runs once per new plug-in. Results go in one generated file, `config/live/look_stats.generated.json`, with per-preset numbers.

### 2.2 Config: one small hand-tunable table

`config/live/fx_interplay.yaml`, hot-reloaded and editable by the owner:

```yaml
# role x scene property -> a gentle multiplier on the effect's pick weight and on its amount ceiling.
# [at property 0, at property 1], linear between. 1 = no opinion.
roles:
  texture: {busy: [1.2, 0.35], legible: [1.0, 0.5]}
  reshape: {busy: [1.1, 0.5], symmetric: [1.0, 0.4], motion: [1.0, 0.7]}
  trails:  {motion: [0.6, 0.4], busy: [1.0, 0.6], own_trails: [1.0, 0.3]}      # mid motion is best: see shape
  corrupt: {legible: [1.0, 0.3], own_glitch: [1.0, 0.4]}
  reframe: {brightness: [0.5, 1.1], busy: [0.8, 1.2], legible: [1.0, 0.3]}
  camera:  {motion: [1.1, 0.4]}
  light:   {brightness: [1.2, 0.4]}
  grade:   {self_coloured: [0.9, 1.3]}
  add:     {busy: [1.3, 0.5]}
pairs:                         # effect-on-effect, same idea
  reshape+reshape: 0.5
  reshape+reframe: 0.6
  trails+corrupt: {techno: 1.0, default: 0.6}
load:                          # the music's load budget (0..1) by band row / genre; the scene's busy uses part of it
  by_band: {still: 0.2, groove: 0.35, pulse: 0.5, lively: 0.65, peak: 0.85}
  genre_bias: {techno: +0.15, ambient: -0.1, classical: -0.1}
roles_by_music:                # which roles suit the music (replaces the per-preset LLM genre guess for effects)
  ambient:   {grade: 1, light: 1, trails: 0.7, reshape: 0.6, texture: 0.4, add: 0.6, corrupt: 0.1, camera: 0.2, reframe: 0.3}
  techno:    {reshape: 1, trails: 1, corrupt: 0.9, light: 0.9, reframe: 0.7, camera: 0.6, texture: 0.5, grade: 0.5, add: 0.4}
  # ... one row per genre group (10 groups, as in the retag)
```

Every number is a soft multiplier, kept between 0.25 and 1.5. Nothing is excluded. The owner can move any value and see the effect within a second.

### 2.3 How they combine

1. **Pick weight** of an effect = its music fit (`roles_by_music[genre][role]` × today's energy and style match) × the interplay multipliers for the current scene × the pair multipliers with effects already on the bus × the owner's stars.
2. **Amount ceiling.** The same interplay multipliers, plus what's left of the load budget (budget − scene busy − the other effects' strength), cap that effect's amount macro. A texture effect on a busy scene still happens, but at 40%, not 100%. This works through amount, not exclusion.
3. **Coordination,** in a few lines of rules in the FX director:
   - On a scene change, re-score the carried effects against the incoming scene. If one now scores below half its old weight, swap or fade it during the transition.
   - Never change scene and effects on the same section boundary, except on a drop or a track change.
   - Breakdown thins to the budget; build raises amounts within it; a drop opens it.
4. **Report.** For every effect on screen, Up next and the details panel show its role, the scene's measured numbers, the multipliers and the budget. Any choice can then be explained and tuned.

### 2.4 Transitions

Transitions follow the same pattern later: the two scenes' measured properties plus the music decide gentle vs bold, and the role of the transition (dissolve, wipe, reshape, corrupt, reframe). Not in this round.

## 3. Implementation plan (step 3)

1. **Measure.** A harness pass, `tools/lookstats.py`, renders every scene preset (16 frames, 320×180) and every effect over 6 fixed test scenes, and writes `look_stats.generated.json`. It runs on GPU 1, pauses while any screen is live, and takes about 1 hour.
2. **Roles.** One role per effect plug-in, classified from source and checked against the measured change: colour change without shape change means grade, and so on. Mismatches are listed for a look.
3. **Config.** `fx_interplay.yaml`, with the starting values above, plus its loader. Hot-reloaded; problems are listed in the catalog's errors.
4. **Scoring and amounts** in the FX director. Scene-aware weight, an amount ceiling through the slot's level, and pair rules. The scene director is unchanged except for the coordination rule.
5. **Report** the reasons in Up next and the details panel.
6. **Tests** for the arithmetic and the coordination rules. Nothing more.

## 4. How the owner judges it (step 4)

A page with, for each of 6 music types (ambient, classical, jazz, pop/house, techno, rock):
- the 8 scene + effect pairs the director would now pick;
- shown as stills or short clips from the harness;
- next to what it picked before.

The owner marks each good or bad. Separately, play the CC test tracks through the Test show and watch the screens.

If it's broadly right, tune the table. If it's a mess, consider the slower review layer (never real time).
