# Plug-in conventions (scenes, transitions, effects)

**Where things are (14 Oct, overrides any older path below):**
- Plug-ins live directly in the deployment: `stream_service/plugins/sources/<id>.py`, `stream_service/plugins/transitions/<id>.py`, `stream_service/plugins/effects/<id>.py`, each with an optional `<id>.presets.yaml` next to it. The registry discovers them automatically; /dev lists everything registered.
- Shared toolkits: `stream_service/plugins/_lib/` (cuda.py HELPERS, palettes.py, params.py, effect_base.py, upsample.py, sdf3d.py, frame3d.py, splat3d.py). Import as `from stream_service.plugins._lib.cuda import HELPERS, launch_2d` (old docs say `smfx._lib`; same modules).
- Test harness: `cd /home/gjbm2/dev/screen-machine && nice -n 15 ~/.venvs/sm-stream/bin/python -m stream_service.tools.fxtest stream_service.plugins.<kind>.<id>:<Class> --preset '<json>' --name <id> [--bench] [--video 8]`. Output (sheet.png, video.mp4, summary.json) goes to `~/.cache/sm-fxtest/out/<name>/`.
- Keep it simple: build it, render it once, look at the sheet, fix what looks wrong, done. No review process.


**Read this whole file before writing a plug-in.** It defines the interfaces, the parameter tiers, the licence rules, the performance budgets and how to test.

## 0. What this is for

This is a live-music visual system. One GPU renderer per TV renders scenes and composites the transitions between them, at 4K60. The output goes to two Samsung TVs as an HEVC stream.

There are two control layers:
- **Art director** (every 10–30 s; human or random now, an LLM later) picks scenes, transitions and **regional** settings: style, density, symmetry, palette family, pace.
- **Real time** (every frame) comes from MaestroDMX (an autonomous lighting controller listening to the music) via a show bus.
  - Signals: `energy` (smoothed musical activity 0–1), `beat`/`pulse` (a decaying 0–1 envelope on each beat), `rgb` (Maestro's colour), and pan/tilt/zoom.
  - These drive **realtime** parameters.

**We do no audio DSP.** Plug-ins just respond to their parameters, which the binding layer sets from these signals.

Owner taste:
- Visuals must feel alive for live music. Static looks are a failure.
- Many looks need warp layering and colour drift.
- We want plenty of 3D.
- Transitions can be ambitious.

## 1. Layout

```
stream_service/plugins/_lib/      shared helpers (read-only for you; the 3D toolkit agent owns sdf3d.py)
stream_service/plugins/sources/<name>.py        one Source subclass per file
stream_service/plugins/transitions/<name>.py    one Transition subclass per file
stream_service/plugins/effects/<name>.py        one Effect subclass per file
stream_service/plugins/<kind>/<name>.presets.yaml   1–3 presets for your plug-in (see §6)
~/.cache/sm-fxtest/out/<name>/                   harness output (sheet.png, summary.json, video.mp4)
```

- Class attribute `name` is unique snake_case and equals the file stem.
- Each plug-in also has `label` (a short title) and `description` (one sentence).
- Imports:
  - `from stream_service.plugins.base import Source, Transition`;
  - `from stream_service.gpu import raw_module`;
  - `from stream_service.plugins._lib.cuda import HELPERS, launch_2d`;
  - `from stream_service.plugins._lib.params import ...`;
  - `from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs`;
  - `from stream_service.plugins._lib.upsample import internal_size, present`;
  - `from stream_service.plugins._lib.effect_base import Effect`;
  - 3D: `from stream_service.plugins._lib.sdf3d import SDF3D, ...` (see that file once it exists).
- **Reference implementations** to copy the style from:
  - `stream_service/plugins/sources/warp_stack.py`: a layered scene with the standard macros;
  - `stream_service/plugins/transitions/{melt,burn,zoom,luma,wipe,glitch,pixelate}.py`;
  - `stream_service/plugins/effects/chroma.py`.

## 2. Interfaces (GPU, CuPy, CUDA)

**Frames:** CuPy `uint8` arrays of shape (H, W, 4), sRGB-encoded RGBA. Alpha is ignored; write 255. Blend in sRGB space like CSS, unless you have a reason not to.

**Source:**
- `setup(ctx, params)`: call `super().setup(ctx, params)` first, then allocate everything.
- `render(t_local, dt, out)`: call `p = self.step_params(dt)` once per frame to get the eased params, then draw into `out` (the output size).
- `close()`.
- `ctx.width`, `ctx.height`, `ctx.fps`, `ctx.cp` (cupy) and `ctx.new_frame()` are available.
- `dt` is 1/fps normally, 0 on the first frame, and a multiple of 1/fps after a skip.

**Transition:**
- `render(progress, a, b, out)`, where progress is already eased 0→1.
- `duration_s` and `easing` are common params, added for you.
- **Endpoints must be pure:** progress 0 shows exactly `a`, progress 1 exactly `b`. The harness checks this.
- `a` and `b` are both live, moving scenes, or stills. No timing other than `progress` (× `self.duration_s` if you need seconds).

**Effect:** `render(t_local, dt, src, out)`, with `p = self.step_params(dt)`. At `amount = 0` the output must be (near) identical to `src`.

**GPU rules:**
- Write kernels as CUDA C in a `raw_module(SRC)` string (compiled once per process) and launch them with `launch_2d`. The current CUDA stream is already set; never synchronise.
- **No per-frame allocation.** Allocate buffers in setup. Small param uploads per frame (`arr.set(np_array)`) are fine.
- **No host round trips** in render: no `.get()`, `cp.asnumpy`, `.item()` or `synchronize`.
- **No `cupy.random`.** curand is missing in this venv. Generate randomness on the host with numpy in setup, or hash in-kernel (`hash1`).
- fp32 only. fp64 runs at 1/32–1/64 rate on these cards.

## 2b. Listening: the piano's notes and the room's sound (owner, 15 Oct)

Maestro is coming out. show_service (docs/SHOW_ENGINE.md) now hears the room mic and takes the Steinway's MIDI, so every note is exact: pitch, velocity, pedals. Scenes and effects can read it directly.

- **Declare it:** a class attribute `listens = ("notes",)`, or any of `"notes"`, `"spectrum"`, `"chroma"`, `"scope"`, `"fft"` (the fast feed, below). The catalog shows it. The director prefers listening scenes while the piano plays (piano mode pools them).
- **Read `self.sound`** in `render`. The session refreshes it once per frame, before any plug-in renders, and both scenes of a transition see the same frame.
  - `self.sound.present`: the piano is playing, or played in the last few seconds.
  - `self.sound.notes_gpu`: cupy float32 `[128, 4]`, one row per MIDI pitch (piano keys are 21–108). Columns, from `stream_service.sound_view`:
    - `LEVEL`: how loud the note still sounds, 0–1. It includes the piano's decay, the damper and sostenuto holds, and the undamped top keys.
    - `VEL`: strike velocity, 0–1.
    - `AGE_ON`: seconds since the strike (`inf` = never).
    - `AGE_OFF`: seconds since key-up; −1 while the key is held.
  - `self.sound.frame`: host-side `SoundFrame`, with the same `notes` plus:
    - `onsets` `[(pitch, vel, age_s)]`: new strikes this frame, each exactly once. Use it to spawn ripples or particles.
    - `releases`;
    - `pedals` (damper, soft, sostenuto);
    - `chroma[12]`, `key`, `mode`, `tension`, `register`, `spread`;
    - `spectrum[32]` and `history[256, 32]` with `history_row` (smoothed, τ ≥ 300 ms);
    - `t_ms`: the photon time, the frame's clock in ms.
  - `self.sound.spectrum_gpu`: `[32]`.
- **`self.sound` may be `None`**, or never `present`: look alive anyway. Idle motion, drift and breathing must not depend on notes.
- **Ages are at photon time.** The TV shows a frame ~0.3 s after it is rendered, so what you draw for a note already matches what the ear hears at that moment. The strike itself always lands late, so:
  - **bloom in, ease out;** no hard flash on an onset;
  - scrolling forms (a note rising from the keyboard) hide the lag well;
  - kicks go to rates and amplitudes, never positions (as for `pulse`).
- **Read-only, no allocation.** Pass `notes_gpu` straight to your kernel. Keep host-side note history in arrays you preallocate in `setup`.
- **Velocities are already capped** for the v1 adsilent's occasional hot notes (show_rt), so don't filter them again.
- **Crash check:** fxtest gives listening plug-ins a test piano on its own clock. Use `--piano chords|arpeggios|scale|sweep|repeats|sostenuto|medley`, or `--piano file.mid`.
- **The fast feed: `"scope"` and `"fft"`** (18 Oct; `stream_service/scope_feed.py`). show_service's raw waveform and 48-band FFT at up to 60 frames/s, for oscilloscope and spectrum overlays. Refreshed with the rest of `self.sound`, once per frame:
  - `"scope"`: `wave_gpu` cupy float32 `[1024]` (21 ms at 48 kHz, oldest first): DC removed, slow AGC (a quiet room still shows a trace), soft clip, so about −1..1 (never beyond ±1.2). `wave_trigger` (int): the latest rising zero crossing in the first half; draw `wave[trigger : trigger + 512]` for a steady trace. Host copy: `wave`.
  - `"fft"`: `fft_gpu` float32 `[48]`, 0–1, log bands 40 Hz–16 kHz (−80 dBFS → 0, −12 dBFS → 1), instant attack, release τ 0.12 s. `fft_peak_gpu` `[48]`: peak hold 0.4 s, then falls 1.5/s. `fft_history_gpu` `[128, 48]`: a ring, one row per 1/30 s of real time; `fft_row` is the newest row (row `(fft_row - k) % 128` is k rows back). `level` (0–1): fast overall loudness. Host copies: `fft`, `fft_peak`, `fft_history`.
  - `scope_live`: the fast feed is fresh (under 0.5 s old). Without it, `fft` follows the smoothed `spectrum` (so FFT effects still move on any setup) and `wave` eases to zeros over 0.3 s.
  - **Lag:** this is the newest sound there is, not photon-aligned like the notes, so it trails the ear by the render lead (~0.2–0.3 s). Fast-attack visuals (bars, traces, flashes that decay) hide it best.
  - **All zeros is normal** (no mic, a quiet room). Look fine with no audio: an effect passes the scene through untouched.
  - The GPU arrays may be `None` if the session could not allocate them: check before launching. Pass them and `fft_row` / `wave_trigger` straight to your kernel.

## 3. Parameters: tiers and the standard macro interface

**Every parameter declares `x-tier`** using the helpers in `stream_service/plugins/_lib/params.py` (`num`, `enum`, `flag`):

| Tier | Meaning |
|---|---|
| `realtime` | May change every frame from DMX. It must never cause a jump, flicker or discontinuity, so smooth it in-kernel if needed. Also mark `x-bindable`: `level` (set from a 0–1 signal), `rate` (a speed you integrate, so position never jumps) or `trigger` (expects a fast 0–1 envelope, e.g. the beat). |
| `regional` | Art-director knobs that change every 10–30 s, eased over a second or so. Style, density, symmetry, scale, complexity, camera distance, palette. **Prefer continuous morph parameters over enums**, and the image must look fine mid-ease. |
| `structural` | Set by presets only: seed, mode enums whose change is a discontinuity. |
| `quality` | `render_scale`. |

**Every scene must include `**standard_scene_params(...)`.** Give each macro real, coherent meaning for your scene:

- **`energy`** (0–1, level): the "how much is going on" macro, the most important knob.
  - It must coherently drive several internal things at once: motion speed, density/complexity, amplitude/displacement, contrast/brightness range, camera motion.
  - At 0 the scene is calm and sparse **but still alive**. At 1 it is busy and intense.
  - It must look good at every value.
- **`pulse`** (0–1, trigger): the beat response. A decaying envelope arrives on every beat.
  - Make it visible and musical but tasteful: a brightness flash, scale/zoom kick, displacement kick, feedback kick, ring/particle spawn, colour punch.
  - **Kicks add to rates or amplitudes, never to positions.** Nothing may jump when the pulse ends.
- **`speed`** (rate multiplier): multiplies every motion rate. Integrate phases on the host: `self.phase += rate * p["speed"] * dt`.
- **`intensity`** (0–2, level): output gain.
- **`palette`, `palette_period`, `drift_speed`**:
  - colour through the shared cosine palette (`PALETTE_CUDA`, `pal(index, P)`, with `palette_coeffs(name)`) whenever the look suits a palette: index = your scalar field × period + the drift phase;
  - integrate drift: `self.drift += p["drift_speed"] * p["speed"] * dt`;
  - this keeps colour a separate axis that a later shared palette pass and Maestro's colour can take over;
  - for inherently multi-colour scenes, pass `palette=None` and still expose a hue/tint shift.
- **`render_scale`** (default 0.5 for anything heavy): render into an internal buffer of `internal_size(ctx, s)` and finish with `present(ctx, buf, out)`.
- **`seed`**.

**Scene-specific params:** add 3–8 regional ones (the interesting style axes) and at most 4 extra realtime ones.
- Every numeric param must visibly change the image across its range. The harness flags dead knobs.
- Defaults must look great with no signals (energy 0.5, pulse 0).

**Basic params (Room page):** mark 1–3 headline params per plug-in with `basic=True` (`num(..., basic=True)` or `enum(..., basic=True)`, which sets `"x-ui": {"basic": true}`). The simple Room page shows only these; everything else sits under "Adjust".
- `palette` (scenes) and `amount` (effects) are already basic.
- Add one or two of the most expressive *regional* knobs per scene, e.g. symmetry, density or "trails".
- Never mark realtime-bound macros (energy, pulse) basic.

**Transitions:** style params are `regional` or `structural`. No realtime params needed.

**Effects:** include `**standard_effect_params()` (`amount`: realtime level; `pulse`: trigger), plus regional style params.

## 4. Performance budgets

Measured by `--bench` on the Quadro P4000 at 3840×2160. The bench is exclusive on the GPU.

| Kind | Budget |
|---|---|
| Scene, at its default `render_scale` | ≤ 6.0 ms |
| Transition | ≤ 3.0 ms |
| Effect | ≤ 2.0 ms |

- During a transition, two scenes plus the transition must fit in 16.7 ms, with the encoder alongside.
- Heavy work, 3D raymarching especially, renders at `render_scale` 0.5 (1080p internal), or 0.35–0.5 for very heavy fractals.
- Use early-outs, bounded step counts and cheap normals/AO.
- If you can't meet the budget, lower the default quality, then document it.

## 5. Licences and attribution (strict)

Every plug-in file starts with a docstring containing:

```
Source: <URL(s)>            (or "Original")
Author: <name(s)>
License: <SPDX id>          (e.g. MIT, CC0-1.0, CC-BY-NC-SA-3.0)
license_class: permissive | attribution | noncommercial
Ported from GLSL by smfx (ideas/changes: ...)   (if ported)
```

| Class | Licences | Rule |
|---|---|---|
| permissive | MIT, BSD-2/3-Clause, Apache-2.0, ISC, Zlib, CC0, Unlicense, public domain | Allowed. |
| attribution | CC-BY, CC-BY-SA | Allowed, with full attribution. |
| noncommercial | CC-BY-NC, CC-BY-NC-SA. **Shadertoy's default** when a shader states no licence is CC-BY-NC-SA-3.0. | Allowed, flagged `license_class: noncommercial`. This is a private home installation that is never distributed. |
| forbidden | GPL, AGPL, LGPL; code with no licence and no default (e.g. glslsandbox, random gists) | **Do not copy or port.** You may implement the same *idea* from scratch, and label it "Original (inspired by <URL>)". |

**Record the exact licence you saw**, quoting where you saw it. If unsure, treat it as noncommercial or reimplement.

## 6. Presets file

`stream_service/plugins/<kind>/<name>.presets.yaml` holds 1–3 presets showing your plug-in's range.

```yaml
<preset_name>:                 # unique, kebab-case, prefixed by plug-in name
  label: Human title
  source: <name>               # or `transition:` / `effect:`
  params: {...}                # only what differs from defaults
  bindings:                    # scenes and effects: standard first; others optional
    energy: {bind: energy}
    pulse: {kick: beat, amount: 1.0}
  tags:
    energy: [0.2, 0.9]         # the music energy range this suits
    moods: [hypnotic, cosmic]  # from the vocabulary below
    style: [lively]            # transitions only: gentle | lively | wild
    weight: 1.0
```

**Mood vocabulary:** calm, mellow, dreamy, warm, dark, bright, euphoric, tense, aggressive, playful, hypnotic, cosmic, organic, geometric, retro, glitchy, elegant, liquid, fiery, icy.

## 7. Testing: crash check only (owner ruling, 14 Oct)

Individual plug-ins only need a basic "does it crash" check. The owner reviews looks himself and rips out what he doesn't like.
Run once: `cd /home/gjbm2/dev/screen-machine && nice -n 15 ~/.venvs/sm-stream/bin/python -m stream_service.tools.fxtest stream_service.plugins.<kind>.<id>:<Class> --name <id>`
Done when it prints `"ok": true` and the sheet (`~/.cache/sm-fxtest/out/<id>/sheet.png`) is not black or blank. No bench, video, knob-sweep or review iterations required. Budgets in section 4 are guidance: aim for them, don't chase them.

## 8. Rules

- Write only your own plug-in files and `~/.cache/sm-fxtest/out/<name>/`. **Never edit `stream_service/plugins/_lib/*`** (only the 3D-toolkit agent edits `sdf3d.py`) or other agents' plug-ins.
- Write only your own plug-in files (and presets). Engine files (stream_service/*.py, plugins/base.py, plugins/__init__.py) belong to the engine agent.
- No git operations. Don't start servers. Don't kill processes you didn't start. Never touch the TVs.
- Keep CUDA honest: bound every loop, no recursion, and guard against NaN (normalise with an epsilon).

## 9. Batch 2 additions (14 Oct, 01:45) — binding for new plug-ins

**Effects layer.** The engine now runs effects ON TOP of a scene (scene-attached) and on a master FX bus over the whole programme (after transitions), so an effect must look right on ANY input: generative scenes, transitions mid-way and the art still. Rules:
- Class attribute `stage` (one of `warp`, `feedback`, `glitch`, `stylise`, `colour`, `light`, `projection`, `motion`, `overlay`). The engine orders a stack by stage in that order (warp first, overlay last); e.g. a cube projection then a camera shake then a strobe.
- `amount = 0` → output == src (within 1/255). Amount fades are how the engine brings effects in and out, so 0→1 must be a smooth, good-looking morph (for projections: flat full-frame → full 3D look; for motion: still → full shake).
- `pulse` is a beat trigger (attack fast, decay 100–400 ms); never a jump cut of geometry. Shake/vibrate/strobe effects are the exception that may move positions, but keep them musical (decaying, bounded, no frame-to-frame white noise jitter unless that is the explicit look).
- Effects with history (feedback, trails, time-slice) allocate their buffers in `setup()`, are robust to `dt = 0` and to a sudden content change.
- Budget ≤ 2 ms at 4K (P4000); projections may use `internal_size`/`present` at 0.5 scale if they also budget the upsample.

**Toolkits (read-only for everyone except their author):** `stream_service/plugins/_lib/sdf3d.py` (raymarching/SDF), `stream_service/plugins/_lib/frame3d.py` (map the input frame onto 3D shapes: plane, box, sphere, cylinder, water heightfield; for projection effects and 3D transitions), `stream_service/plugins/_lib/splat3d.py` (3D points/lines/ribbons/particles by projection + additive splatting; for particle and wireframe scenes).

**Tags (presets file, all kinds).** In addition to §6:
```yaml
  tags:
    energy: [0.2, 0.9]
    moods: [hypnotic, cosmic]          # §6 vocabulary
    style: lively                      # gentle | lively | wild   (all kinds)
    family: <one>                      # see below
    dims: 2d                           # 2d | 3d
    technique: [raymarch, fractal]     # 1-3 of: raymarch, sdf, fractal, ifs, particles, points, wireframe, simulation, agents, feedback, noise, warp, tiling, analytic, volumetric, heightfield, projection, displacement, kaleido, glitch, colour, light, optical, retro, cellular, field
    weight: 1.0
```
**Families.** Scenes: `abstract`, `fractal`, `cosmic`, `tunnel`, `landscape`, `water`, `fire`, `organic`, `geometric`, `architecture`, `particles`, `light`, `retro`, `glitch`. Transitions: `blend`, `wipe`, `warp`, `3d`, `fragment`, `glitch`, `light`, `organic`. Effects: the `stage` value.
