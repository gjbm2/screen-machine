# Review: LIVE_V1 additions (cue, auto, direction, palette, inputs)

**Proposal:** `docs/av/contracts/proposals/LIVE_V1_cue_auto_inputs.md` (screen-machine-04, 14 Oct; the version edited after the owner's ~00:40 ruling).
**For:** screen-machine-04 (author, revises) and screen-machine-2f (owns LIVE_V1, room_service and the room state model; merges).
**Inputs:** four review lenses (contract safety, state-model fit, real-time robustness, owner fit/simplicity), merged here. Claims about the code were checked against `stream_service/` and `room_service/` as built. Where the lenses disagreed, §2.3 records the decision and the reason.
**Date:** 14 Oct 2026.

---

## 0. Verdict

1. **Merge after changes.** The model is right and fits the owner direction: the director names descriptors, never scenes; real-time DMX drives the scene; every scene change gets its own transition; palette is its own axis. The proposal is clean against the 00:40 no-DSP ruling. The leftovers are bars, the `energy` signal name, and AV_PROGRAMME §4.4a, which still says "beat, onset and section detection".
2. **Three blockers.** (a) Masters and tint, as specified, touch the anchor and exit-target stills, so neither cut matches. (b) A cue in `holding`, or a deferred or auto cue during exit, breaks the art↔live handshake. In the exit case, `session.held` never fires. (c) The 0.31 s `lead` is the lab figure. The production player buffers 300–600 ms, so the real lead is likely about 0.5–0.8 s, and the Maestro term has the wrong sign.
3. **Simplify for v1.** Cue takes scenes and `"auto"` only, and exit stays the only route to a still. Drop bars, `override`/`release`, `PATCH masters`, `paused`, palette tint and `follow: maestro`. Masters are derived only: pace sets the scene clock and intensity sets modulation depth, never luminance.
4. **The room side is now specified (§3, §5).** `look.*` room targets, a random director that runs in room_service, operate-open cue routes with guards, a separate show-bus token, and `auto` carried in the screens live spec. room_service never treats the director's picks as drift.
5. **The demo needs about 2 days of stream_service work** for cue, auto, direction, transitions, show-synth and bindings, then 1–2 days for the beat follower and measured lead (§4). 2f does the room_service and Room page work in parallel.

---

## 1. Verdict by section

| Section | Verdict | Reason |
|---|---|---|
| A. Model (A.1–A.3) | accept-with-changes | Matches the owner direction and the ruling. Fix the A.2 "State and priority" row (R13). A.3.3: `beat` is predicted, not raw, and one-off hits land `lead` late, about 0.5–0.8 s (R17). A.3.5: remove bars (R8). |
| 1.6 Cue | accept-with-changes | The right general verb. For v1, `to` is a scene or `"auto"`; exit stays the still route (R3). Return 409 in `holding` (R4). Exit cancels pending cues (R5). Cap concurrent transitions (R6). Specify events, errors and idempotency (R9, R10). |
| 1.7 Auto | accept-with-changes | The triggers match the ruling exactly (follow_dmx, max dwell, next). It needs: an arming state machine, dwell in seconds, a debounced and latched follow_dmx, a written weight formula, a tagged-only pool, and a pre-prepared next scene (R11). |
| 1.8 Direction | accept-with-changes | Source-agnostic and on-ruling. Replace the whole object, with neutral defaults. intensity means depth, not brightness (R2). One generic tag-matching rule plus `vocab.yaml`. Accept direction in every non-terminal state, and apply it on create without an ease (R12). |
| 1.9 Inputs | accept-with-changes | Ownership is right, and the follower is the ruling's exception. Fix: the clock (R15), staleness and heartbeat (R16), lead (R17), the follower's timeout and hysteresis (R18), minimal un-eased bindings and the `energy` rename (R19), and the token (§3 C.3). |
| 1.10 Priority stack and masters | accept-with-changes (rewrite) | `override`/`release` and `PATCH masters` add a second hand-versus-mode precedence system that room_service never clears. Replace them with a 3-layer stack and derived masters that never touch the anchor or target (R13). |
| 1.11 Palette | accept-with-changes | A lasting axis, yes. The renderer takes named presets only; drift runs in room_service so both screens agree. Pass the palette to sources rather than adding a scalar-field pass. Keep today's behaviour when no palette is set. Defer tint and `follow` (R14). |
| 1.12 Show-synth | accept (informative) | A dev tool, not a contract. Bind to 127.0.0.1 and register dev port 8711. Fault injection and offline mode are recommended (S1). |
| B. Compatibility | accept-with-changes | The claim is overstated. Palette seeding changes PATCH-scene results. Today's stream_service rejects unknown fields with 400 (`sessions.py:165`, `:341`), so rollout needs feature discovery. The fallback rules disagree with each other (R14, R19, R21). |
| C. Open points | answered | §3. |

---

## 2. Changes

### 2.1 Required before merge

Each item gives the proposal section, the problem, and the change. "Trace" points to the evidence in the code.

**R1. Cut invariant** (A.2, §1.10, §1.11; LIVE_V1 §1.5)
- *Problem:* "intensity scales the output level" and `palette_tint` "for stills" both apply to the anchor and the exit target. The entry cut then jumps, and the frame at `hold_pts` no longer matches the art stack (LIVE_V1 §2.2 rules 4 and 6). An 8 s master ease means the held frame isn't even static.
- *Change:* add to §1.5: "The `image` source (the anchor and every exit target) is always rendered at identity: no masters, palette, tint, bindings or output filters. Direction, palette and bindings apply only inside non-internal scene `SourceLayer`s, before `TransitionLayer`. The frame at `hold_pts`, and every held frame after it, equals the decoded target pixels. Any future output filter must be identity on a static frame."
- *Test:* exit with auto running and non-default direction and palette, then compare the `hold_pts` frame with the target PNG before encoding.

**R2. intensity means modulation depth** (§1.8, §1.10)
- *Problem:* the owner's standing directive is that live is full intensity. As a luminance master, `intensity: 0.6` would show a 60 % picture.
- *Change:* "`direction.intensity` (0–1, default 1) is modulation depth. It scales every binding's swing about its rest value (R19): 0 means bindings are flat, 1 means their full range. No luminance gain exists anywhere in v1." Delete "scales the output level before the optional limiter".

**R3. Cue changes scenes only** (§1.6)
- *Problem:* only room_service ever exits, and it already uses `exit`. A still form of `cue` adds four high-severity hazards:
  - `via` defaults to the director's pick, which breaks room_service's held timeout (`screens.py:1065`, sized from the session `out`);
  - `at: beat|bar` delays the out;
  - a still passed through the room API leaves a black hold that never ends;
  - the go/exit alias wording ("create's scene") drops a scene PATCHed while holding.
- *Change:*
  - `to` is a scene spec or `"auto"`. Remove the `{still}` form and the "cue to a still" column.
  - `go` and `exit` keep their routes, bodies and semantics exactly as today. go keeps 200, exit keeps 202. Each gains additive `cue_id` and `duration_s`, and room_service uses exit's `duration_s` for its held wait.
  - Delete the "Aliases" paragraph. Keep one sentence: "go is the cue that leaves holding; exit is the cue to a still. They stay separate routes."

**R4. State rules and director arming** (§1.6, §1.7)
- *Problem:* "holding: cue = go" lets the director, a "change now" tap or a mode hop start the in-transition before the page acks `live_visible`. room_service's own `go` then gets 409 (`sessions.py:156`) and aborts with a hard cut.
- *Change:*
  - Cue in `starting` or `holding` returns 409 `bad_state` ("use go"). `PATCH scene` while holding still replaces the pending scene, as today.
  - Cue in `exiting`, `held`, `ended` or `failed` returns 409 `bad_state`.
  - Cue is accepted in `entering` and `live`. In `entering` it nests over the in transition, and entering still becomes live on the in transition's `done`, including when it is superseded, as today.
  - §1.7: "The director is armed only in `live` and disarmed in every other state. follow_dmx and max-dwell triggers seen while disarmed are discarded, not queued."

**R5. Exit pre-empts every pending cue** (§1.6; render thread)
- *Problem:* if a deferred or auto `StartTransition` is applied after the out, it wraps the out as its `a`. `collapse()` then closes the out as superseded (`layers.py:148-154`), `on_transition` ignores it (`sessions.py:266`), `session.held` never fires, and room_service aborts with an unmatched cut. Separately, exit waits on the session lock while a scene is being prepared (`sessions.py:184`, `:211`).
- *Change:*
  - "Accepting `exit` atomically cancels the pending cue, manual or auto, and any in-progress director prepare, and disarms the director for the rest of the session. Each cancelled cue emits `session.cue{phase: cancelled, reason: exit}`."
  - "On the render thread, once an `out` TransitionLayer is in the tree, any later scene `StartTransition` is retired, never applied."
  - "Scene sources are prepared outside the session lock. State is re-checked under the lock before submitting, and exit never waits behind a scene prepare."

**R6. Bounded nesting** (§1.6, §1.7)
- *Problem:* nesting is unbounded, and each level renders one more 4K source (`layers.py:121-125`). The "change now" button is operate-open, and `fractal-seahorse` alone is "heavy at 4K60 on the P4000".
- *Change:*
  - "At most 2 scene transitions run at once. A scene change beyond that waits in the session's single pending-cue slot and starts when the oldest finishes; its `at_pts` reflects the wait."
  - "A newer manual cue (or PATCH scene) replaces a pending one, and the replaced cue gets `session.cue{phase: cancelled, reason: replaced}`. An auto cue never replaces a pending manual cue."
  - "The director never cues while a transition it started is still running."
  - "Exit may nest over running scene transitions and is never delayed."
  - "A PATCH scene counts as a manual cue (cause `patch`) and resets dwell."

**R7. `via` defaults and the 202 body** (§1.6)
- *Change:*
  - `via` precedence: an explicit `via`, else the director's pick when auto is enabled, else the session `in`. That last one is today's PATCH rule.
  - The response is `202 {session, state, cue_id, at_pts, duration_s}`. `at_pts` is an estimate, or null while the scene is still preparing; the pts on `session.transition{phase: started}` is authoritative.
  - `duration_s` is resolved when the transition starts and is never rescaled mid-flight.

**R8. No bars in v1** (A.3.5, §1.6 example, §1.7, §1.9)
- *Problem:* nothing supplies a downbeat, and inferring one is forbidden. Yet `snap: "bar"` and `dwell_bars` are defaults, the event example shows `at: "bar"` succeeding, and §1.6 and §1.9 give different fallbacks. Bar-length out transitions would also defeat room_service's timeout, which reads only `duration_s` (`screens.py:1303`).
- *Change:*
  - `at` is `now|beat`. `beat` falls back to `now` with `fallback: "no_beat"` when the follower is unlocked or off.
  - `auto.snap` defaults to `beat`.
  - Replace `dwell_bars` with `dwell_s: [min, max]`. With no explicit value, it is interpolated log-linearly from `direction.change_rate`: 0 → [120, 240] s, 1 → [15, 30] s. An explicit `dwell_s` wins.
  - Transition durations are seconds only and are never bindable or speed-scaled.
  - A.3.5 becomes: "Cues can start on a predicted beat. Bars arrive additively if Maestro gains a downbeat channel."

**R9. Events** (§1.6, §1.9, §1.11)
- *Problem:* room_service matches on `kind`, `phase` and `session.held` (`screens.py:647`, `:1029`). New phase values would put those consumers at risk. Scheduled, cancelled and skipped cues are invisible.
- *Change:*
  - Add a new event type, and never add phases to `session.transition`: `session.cue {cue_id, phase: scheduled|cancelled|skipped, cause, source?, reason?, scene?, transition?, at, fallback?, at_pts?}`.
  - `session.transition` gains `cue_id, cause, source?, reason?, scene, transition` on both `started` and `done`, superseded included.
  - `cause` is one of `go|exit|manual|patch|auto`. `reason` is for auto only: `dmx_change|max_dwell|next`. `source` is `panel|director|mode` and is informational, filled in by room_service.
  - Drop `requested_at`. `fallback` appears only when a fallback applied.
  - "Consumers ignore unknown fields and enum values."
  - No per-frame or per-beat events on `/api/events`. `session.inputs` at most once per 2 s, with hysteresis. `session.palette` only on an effective change, at most 1 Hz.

**R10. Idempotency and errors** (§1.6)
- *Change:*
  - `idempotency_key` is per session and kept for the session's life. A replay returns the original 202 body. The same key with a different body returns 409 `idempotency_conflict`.
  - Errors:
    - 400 `bad_request|bad_spec|unknown_preset|bad_at|bad_to`;
    - 404 `not_found`;
    - 409 `bad_state|auto_unavailable|idempotency_conflict`.

**R11. Auto, specified** (§1.7)
- *Change:* replace the fields block and the two "When the director moves" and "What it picks" lists with the following.
  - **Defaults.** An absent `auto` block means disabled. Remove `paused`, which is the same as `enabled: false`.
  - **Pool.** `pool` is one of:
    - `"all"`: presets that carry a `tags` block. Untagged presets are manual-only, so `black` and `cut` never join the mix.
    - a list of names;
    - `{"tags": {k: v}}`: scalar tags match by equality, list tags by overlap. Used for per-GPU exclusion, e.g. `{heavy: false}` on south.
    - `transitions` takes the same forms.
  - **Dwell.** Measured in seconds from the previous scene transition's `done`.
  - **follow_dmx.**
    - A change counts once the attribute has been stable for ≥ 300 ms and differs from its value at the last cue.
    - It is compared only between frames that are both fresh with `feed: live`.
    - The first such frame after create or after recovery is a baseline, not a change.
    - A change inside min dwell is latched and fires when min is reached.
  - **`cue {to: "auto"}`.** Ignores min dwell. It works whenever an auto block exists, even with `enabled: false` (a one-shot pick). With no block it returns 409 `auto_unavailable`.
  - **Weight.** `tags.weight × Π over direction descriptors (1 if matched, else 0.3) × novelty`.
    - Novelty excludes the last `avoid_repeat` scenes; if the pool is no bigger than that, it excludes only the current scene.
    - A weight is never 0.
    - An empty pool means no move, plus `session.warning`.
    - Transitions are weighted the same way, against `direction.transition_style` and `direction.energy`, never the bus signal.
  - **Prepare.** The next pick is chosen and prepared at least 2 s before min dwell, off the lock. A preset that fails to prepare is skipped and quarantined for 10 min, with `session.warning`.
  - **RNG.** Seeded, with separate streams for scene and transition picks. The effective seed is reported in GET session.

**R12. Direction, specified** (§1.8)
- *Change:*
  - **Whole object.** "create and PATCH carry the complete object, and the renderer replaces it whole." A missing field takes its neutral default, which reproduces today's behaviour: `energy 0.5, pace 0.5, intensity 1.0, change_rate 0.5, moods [], genre null, transition_style null, palette_family null`. Remove "missing fields keep their previous value".
  - **Ease.** Values on create apply at once. Later changes ease over `direction_ease_s`, default 8 s.
  - **States.** A PATCH carrying only `direction`, `palette`, `palette_transition_s`, `auto` or `beat_follow` is accepted in every state except `ended` and `failed`. It is stored and applies to scene-side rendering only. `scene` and `params` keep today's state rules. *Trace:* today any PATCH outside holding, entering or live returns 409 (`sessions.py:169`), which would turn a push during exit or entry into a failed mode step.
  - **One matching rule.** "A preset tag key is spelt exactly like the direction key it matches: `energy`, `moods`, `genre`, `transition_style`, `palette_family`. A number matches a `[lo, hi]` tag; a list matches on overlap; a key the preset doesn't tag counts as matched." Rename the example tags: `style` → `transition_style`, palette `family` → `palette_family`. `genre` is matched like `moods`.
  - **Vocab.** It lives in `config/live/vocab.yaml` (`moods, genre, transition_style, palette_family`), is hot-reloaded, served in the catalog, and read by room_service's look adapter. An unknown tag value logs a warning and is listed in the catalog's `errors`; the preset still loads.
  - **`source`.** Informational, filled in by room_service.

**R13. Parameter stack and derived masters** (§1.10; the A.2 row)
- *Problem:* layer 5 (`override` until `release`) and `PATCH masters` create a second hand-versus-mode precedence system inside the renderer. room_service never clears it, so a guest's override outlives the next mode, which contradicts ROOM_STATE_MODEL §7.3.
- *Change:* rewrite §1.10 as follows.
  - **Stack:** "plug-in default < scene preset params < session params (`PATCH params`). Session params keep today's meaning: they merge into the current scene and are cleared when the scene changes."
  - **Bindings:** "A binding is a value at whichever layer set it. `PATCH params {k: value}` on a bound parameter replaces the binding for the current scene, and `{k: null}` restores the preset's value or binding."
  - **Masters, derived only:**
    - speed = 0.5×–2×, exponential in `direction.pace`. It is applied as the scene's clock: the scene source receives `dt × speed` and a `t_local` integrated from it. It never applies to transition progress.
    - depth = `direction.intensity` (R2).
    - Both are reported read-only in `/inputs` and GET session.
  - **Delete** `override`, `release` and `PATCH masters`.
  - A.2's row becomes: "Parameter stack: default < preset < session params, written only by room_service; bindings; derived masters | on change | renderer".

**R14. Palette, v1 scope** (§1.11, B)
- *Change:*
  - **Value.** `palette` is a preset name. Drift is computed in room_service's director (§3 C.1), which sends concrete names, so both screens, and later Hue, agree. Defer `{follow: maestro}`, `{drift}` and `palette_tint`.
  - **Rendering.** "The session owns the eased palette and hands it to every source that declares `x-palette: true`, as cosine coefficients now or a LUT for stops. There is no separate scalar-field pass. A palette change crossfades over `palette_transition_s`." *Trace:* fractal_zoom colours each supersample and then averages (aa = 2), so colouring an averaged index changes the look. A field also costs an extra 4K buffer and pass per source on the P4000.
  - **Compatibility.** "Until a session `palette` is set, each scene keeps its own palette params exactly as today. Once it is set, scene-level palette params are ignored, with a log line." Remove "the first scene's palette seeds it". Keep `palette_drift` as a bindable scene param (colour-cycle rate).

**R15. Bus clock** (§1.9)
- *Problem:* "t_ms … directly comparable with the frame clock" is false. The renderer paces on `CLOCK_MONOTONIC_RAW` (`renderer.py:51-53`), and room_service uses `time.monotonic()`. The two are hundreds of thousands of seconds apart on this host.
- *Change:* "`t_ms` is CLOCK_MONOTONIC in ms, stamped by room_service (and show-synth) when the Art-Net packet arrives. The renderer never compares it directly with another clock. It maps it with `off = time.monotonic() − pace_clock()`, sampled each frame, and the follower uses `t_ms` intervals."

**R16. Bus delivery and staleness** (§1.9; to become LIVE_V1 §1.9a, 2f-owned)
- *Problem:* the "2f amendments" exist only in this proposal. With forward-on-arrival, a quiet Art-Net source makes bindings flap after 250 ms. Latest-value coalescing can swallow a 1-frame pulse.
- *Change:* 2f writes "K3 as amended" into LIVE_V1 (§5):
  - Frames are forwarded on arrival. When Art-Net is quiet, the bus repeats the latest frame at least every 100 ms, with `input.age_ms`.
  - The `renderer` role is not coalesced: a bounded queue of 64, then disconnect. Frames carry `seq`. UI roles stay latest-value.
  - Two conditions, either of which holds bindings for 250 ms and then eases signals to their idle values over 2 s:
    - link down: no frame for 500 ms, or the socket closed;
    - input stale: `input.online` false, or `feed` not `live`.
  - Recovery needs 1 s of fresh frames, then a 1 s ease back.
  - The renderer ignores the bus `mode`, `safety` and `hue` blocks.
  - Rename K3's top-level `look` to `feed` (it is unbuilt and not frozen until 16 Oct).

**R17. Lead** (§1.9 "Render-ahead", A.3.3)
- *Problem:* 0.31 s comes from bd's lab player, which ran a 67 ms buffer. The production page buffers `target_buffer_ms` = 300 ms and only jumps when it is more than 2× that for 2 s, so the real figure is likely about 0.5–0.8 s. Maestro reacts after the audio, so its latency adds to the lead; it doesn't subtract.
- *Change:*
  - "`lead_ms` = server_ms (measured per frame) + the glass client's `buffered_ms` (an EMA of the media-socket stats, which `media.py` already keeps) + 1 frame + presented→photon from `config/room/latency.json` (north uses south's value until measured, flagged) + `maestro_latency_ms` (default 0) + `trim_ms` (±100)."
  - Recompute at 1 Hz and slew at most 20 ms/s, except after an edge jump.
  - Report the breakdown in `/inputs`.
  - A.3.3: "one-off hits land about `lead` late (expected 0.5–0.8 s in production; to be measured)." Tell the owner (§5).

**R18. Beat follower** (§1.9)
- Keep the ruling's core unchanged: the median of the last 8 accepted intervals in 270–2000 ms, `beat_confidence` as the share of the last 8 edges within ±12 %, off below 0.6, and `beat_follow: extrapolate|off`.
- *Problem:* with no timeout it extrapolates forever after the pulse stops, because confidence never updates. There is no hysteresis. "beat goes to 0" when unsure contradicts the switch's own meaning of off, which is the raw edge.
- *Change:* add only the following.
  - Edge detection uses a Schmitt trigger: rise at ≥ 0.6 after ≤ 0.3, configurable. Refractory period: 0.35·P when locked, 150 ms when unlocked.
  - Confidence is measured against the grid (`last_beat + kP`), so one missed pulse doesn't zero it.
  - Lock at confidence ≥ 0.7 with at least 4 accepted edges. Unlock below 0.6, or when no edge has been accepted for max(2.5·P, 1.5 s).
  - "When unlocked or `off`, `beat` is the raw pulse-edge envelope, landing `lead` late; `beat_phase` is held and `bpm` is null; `at: beat` falls back to `now`."

**R19. Bindings, v1** (§1.9)
- *Problem:* bound values would go through `ParamEaser`, which has τ = 0.8 s (`plugins/base.py:147`) and smears beat triggers to nothing. The no-bus and stale-bus fallbacks disagree. The example binds `flash` and `hue_shift`, which no plug-in has.
- *Change:*
  - A v1 binding is `{bind, range: [lo, hi], rest?}`. Value = `rest + depth · (lo + (hi − lo)·signal − rest)`. `rest` defaults to `lo` for trigger signals (`pulse`, `beat`) and to the midpoint otherwise.
  - Bound values are written each frame and bypass the PATCH easer. Smoothing comes only from the `*_slow` signals.
  - The schema mark is `x-bindable: true` on numeric params.
  - Bindable signals are scalar 0–1. pan and tilt are mapped as `(x + 1)/2`. `rgb` is not bindable in v1.
  - When the bus is absent or stale (R16), each signal takes its idle value: triggers 0, `beat_phase` held, levels and activity 0.5. Remove "or their preset value".
  - Rename the `energy` signal to `activity_slow`.
  - Add 1–2 bindable fractal params for the demo, e.g. an exposure pulse and a colour-cycle kick.
  - Defer: binding kinds (`rate|level|trigger`), `curve`, `smooth_ms`, `amount`, and the Room page binding pickers.

**R20. Transition plug-ins prepare off the render thread** (LIVE_V1 §1.5, additive)
- *Problem:* the render thread calls `transition.setup()` while draining commands (`renderer.py:351`). The new melt, luma and glitch ports would compile NVRTC kernels there, stalling exactly when an auto transition starts.
- *Change:* "A transition compiles its kernels and loads its textures in `prepare()`, on a worker thread, before its `StartTransition` is queued. stream_service warms every registered transition at start. `setup()` on the render thread must not compile or allocate."

**R21. Compatibility and feature discovery** (B)
- *Change:* rewrite B as follows.
  1. Palette behaviour is as in R14.
  2. `GET /api/catalog` gains `api: {contract: "LIVE_V1", rev: "1.1", features: ["cue", "auto", "direction", "palette", "inputs", "bindings"]}`. room_service sends a new field only when its feature is listed.
  3. Consumers ignore unknown fields and enum values.
  4. With no bus, signals take their idle values (R19) and `at: beat` falls back to `now`.
  5. One resource-guard change is documented in the changelog: a third rapid scene change now waits instead of nesting (R6).

**R22. Session summary** (LIVE_V1 §1.2 `GET /api/sessions/{id}`)
- *Change:* add, additively: `current_scene` (on glass or incoming), `cue {pending, last}`, `auto {…effective, armed, seed, next_eligible_pts}`, `direction`, `palette`, `masters` (derived), `fixture`, `beat_follow` and `lead_ms`. room_service needs these to adopt sessions after a restart (AV_PROGRAMME §3.3).

**R23. Ruling leftovers in the proposal text**
- *Change:*
  - A.3.3: "`pulse` (raw) and `beat` (predicted by the follower)".
  - §1.7 "matching direction.transition_style and energy" becomes "`direction.energy`".
  - §1.12: "Sections reach the renderer only as effect/variant/activity values. The truth record's section boundaries are used only to score follow_dmx → cue latency."
  - §1.9 subscription: remove the `room_service.token` option (§3 C.3).
  - The AV_PROGRAMME leftovers are 2f's (§5).

### 2.2 Recommended

| # | Section | Change |
|---|---|---|
| S1 | §1.12 | Move it to the stream_service README, or to an informative LIVE_V1 appendix. Implement K3 hello/welcome. Bind 127.0.0.1. Register 8711 (dev) in the port register (screen-machine-ca). Stamp `t_ms` like room_service. Add fault flags (`--miss-pulse`, `--half-time`, `--tempo-ramp`, `--effect-noise`, `--cadence on-change`, `--stall`, `--disconnect`) and `--offline --seconds N --out bus.jsonl` at virtual time. Truth record: `{t_ms, kind: beat\|section\|effect, value}`. Renderer `--show-bus file:bus.jsonl` for replay. Validate against `schema/room-show-bus.v1.json` (2f). 1–2 styles to start. |
| S2 | §1.7 | Put the director in a GPU-free module (`stream_service/director.py`), unit-tested at virtual time against synth JSONL: the same seed and the same recording must give an identical (scene, transition, at_pts) sequence. |
| S3 | §1.9 | Follower extras: fold intervals (`k = round(I/P)`, k ≤ 4, accept if `|I − kP| ≤ 0.12P`), and smooth the phase (`beat_t ← predicted + 0.25·err`). Acceptance against the synth: lock within 8 beats; phase p95 ≤ 15 ms before lead; unlock ≤ 2 s after the pulse stops; no lock in 60 s of random edges. On glass, with screen-machine-10's `latency_cal`: p95 ≤ 40 ms. |
| S4 | §1.9 `/inputs` | Add `lead {total_ms, server_ms, buffered_ms, present_ms, photon_ms, maestro_ms, trim_ms, photon_source}`, `follower {edges_accepted, edges_rejected, locked_since, last_edge_age_ms}`, `auto {dwell_s, eligible_at_pts, pending_change, last_decision}` and `stale: null\|link\|input`. |
| S5 | §1.11 | Crossfade palettes as LUTs in OKLab. The owner format in `palettes.yaml` is hex `stops` (cosine built-ins stay in code). The catalog computes swatches: the hand-written `dusk` swatch already contains a colour that is neither a stop nor an interpolation of its stops. |
| S6 | §1.7 | Each auto decision event carries `{seed, draw_index, candidates: [[name, weight]]}` behind a debug flag. |
| S7 | gates | Before each slice ships: a 2-hour soak with auto at dwell [15, 30] s on both GPUs; late_frames ≤ 1 %, `mem_used_mb` flat, repeats ≈ 0. |
| S8 | later | A `config/live/costs.json` cost table per GPU replaces the `heavy` tag. `auto.link: "room"` if the owner wants both TVs to change together (§5). `sync_to: music\|lights` once Maestro latency is measured. `scene: "auto"` on create. |
| — | noted, not adopted | Reporting `hold_pts` 2 frames after progress 1 (encoder residue). It isn't needed while exits use only the session `out` crossfade (R3). Revisit if exit transitions diversify. |

### 2.3 Disagreements between reviewers, resolved

| Topic | Positions | Decision | Reason |
|---|---|---|---|
| Cue to a still | keep as the exit alias (proposal; AV §4.4a) / keep but guard (contract-safety, state-model) / remove (owner-fit) | Remove from v1 | Only room_service exits, and it has `exit`. Removing it closes four high-severity holes at no feature cost. It can be added back later. |
| Cue in holding | 409 (contract-safety) / replace the pending scene (owner-fit) | 409 | PATCH already replaces the pending scene; one way per job. |
| Bars | counted bars (robustness) / bar→beat fallback (contract-safety) / remove (owner-fit, state-model) | Remove | Counted bars have arbitrary phase, which reads as inferred structure. They also break room_service's timeout arithmetic. |
| Nesting | one at a time, plus 429 inside 2 s (robustness) / cap 2 (contract-safety) | Cap 2, one pending slot, director limits itself to 1, room_service limits to 1/s | Keeps today's PATCH-over-running nesting (additive) and still bounds the GPU. |
| §1.10 | room-owned slots and trims (state-model) / define lifetimes (robustness) / defer (owner-fit) | Rewrite: a 3-layer stack and derived masters (R13) | Everyone agreed on removing override and PATCH masters. Keeping params per current scene matches today's semantics. Trims come later as a room target. |
| intensity | luminance master applied per source (contract-safety) / modulation depth (robustness, owner-fit) | Depth | The owner says live is full intensity. The R1 invariant is kept anyway. |
| Speed master | multiply x-bindable rate params (proposal) / scale the scene clock (owner-fit) | Scene clock | No per-param marking; every rate the plug-in integrates scales automatically; transitions are untouched. |
| Palette drift and follow | a pure per-session function with a shared seed (state-model) / renderer `auto` (owner-fit) | room_service sends concrete names; renderer crossfades | Room-level agreement across both screens and Hue for free, and a smaller renderer. |
| beat below 0.6 | 0 (proposal) / fade to 0 (robustness) / raw edge (owner-fit) | Raw edge, plus the R18 timeout | Gives the switch one meaning of "off". A stopped pulse produces no edges, so `beat` is 0 anyway. |
| Bus clock | name CLOCK_MONOTONIC and map it (contract-safety, robustness) / stamp on arrival with pace_clock (owner-fit) | room_service stamps at Art-Net receipt; the renderer maps | Keeps WebSocket and event-loop jitter out of the follower's intervals. |
| Binding easing | normal 0.8 s ease (owner-fit) / bypass (robustness) | Bypass | The ease would erase beat effects. `*_slow` signals provide the smoothness. |
| No-bus fallback | per-signal idle (owner-fit) / per-binding rest (state-model) / session params, then preset (robustness) | Per-signal idle, with an optional per-binding `rest` | One rule, with an escape hatch. |
| Room target names | `look.*` (state-model, contract-safety, AV §4.4a) / `direction.room` (owner-fit, because of the K3 `look` clash) | `look.*`; rename K3's field to `feed` | K3 is unbuilt and unfrozen, and AV_PROGRAMME already uses `look.palette`. |
| Per-screen offsets | now (3 lenses) / later (owner-fit) | Reserve `look.trim.<screen>` (numeric fields only); not in the demo | One room, one director. The shape is fixed now so it can be added without breaking anything. |
| Where the random director runs | room_service runtime (state-model, robustness) / external tool (owner-fit) / via set_target (contract-safety) | room_service runtime overlay on `look.direction` | No drift, no log churn and no Busy refusals every 10–30 s. 04 may use a dev script that PATCHes sessions directly until room_service has it. |
| Panel direction writes | set_target with respect (contract-safety, state-model) / a soft channel (robustness) | set_target (a hand change), coalesced to ≤ 1/s by the page | It is a human-rate input. A 409 during a mode switch is fine for a slider, which retries. |
| `cue {to: auto}` with auto off | 409 (robustness) / one-shot (owner-fit) | One-shot when an auto block exists; otherwise 409 | "Hold this scene" plus "change now" is a natural pair, and there is no silent fallback to pool `all`. |
| Pool filter form | drop it (owner-fit) / keep it (state-model) | Keep a minimal filter | Needed to exclude `heavy` presets on south without listing names. |
| Weight formula | Gaussian (robustness) / product with a 0.3 floor (owner-fit) | Product with a 0.3 floor | The owner can predict it. |
| Idempotency retention | session life (contract-safety) / 10 min (robustness) | Session life | Sessions are short-lived and TTL'd. |

---

## 3. Answers to §C (2f decisions)

**C.1 Direction scope: room-level targets in room_service. The renderer sees only resolved values.**
- **The adapter.** A new in-process `look` adapter (kind `level`; it holds the value, like `sensor_mask`/MaskPusher; `read()` returns the held canonical value).
- **Its members:**
  - `look.direction`: the complete canonical object. `normalize()` fills defaults and validates against `config/live/vocab.yaml`.
  - `look.palette`: a preset name, or `{"drift": {"family"?: f, "period_s": 180}}`. With no `family`, drift follows `look.direction.palette_family`.
  - `look.director`: `"fixed"`, or `{"random": {"spread": 0.15, "every_s": [10, 30], "seed"?: n}}`, and later `{"llm": {"stale_after_s": 90}}`.
- **Policy.** `respect`, the default for `set`: a hand change records drift until the next mode request. Modes set the initial values. `house`/`normal` leaves `look.*` unmanaged, so a palette picked by hand carries across modes that don't name one; that is the "lasting axis". A mode that borrows a palette uses the existing verbs: disco `"look.palette": {"store": "pre_disco", "set": "ember"}`, normal `"look.palette": {"retrieve": "pre_disco"}`.
- **The director** runs in room_service. Its output is a runtime overlay, never `set_target`: effective = director(`look.direction`, t), plus later `look.trim.<screen>`. Palette drift is computed there too, as a concrete preset name. room_service sends the full effective `direction` and `palette` on create, and PATCHes them on change, coalesced and at most 1/s per session.
- **A 404 or 409 from a session being torn down is a no-op.** A look push is never a gated mode step and never takes part in `on_fail: abort`.
- **Later, the LLM** writes to a keyed machine channel (like §2.2b sensor ingest), accepted only while `look.director` is `llm`.
- **Scope.** Room-wide only in v1. `look.trim.<screen>` (numeric deltas, clamped 0–1) is reserved for later.

**C.2 Who calls `cue {to: "auto"}`: room_service only. The stream API is token-gated and browsers never reach it.**
- **Triggers:**
  - (a) The Room page's "change now", through new operate-open routes: `POST /api/v1/screens/{id}/cue {to: scene|"auto", via?, at?}` and `POST /api/v1/cue {screens: [ids]|"all", to, via?, at?}`.
  - (b) A live→live mode hop where the new spec has `auto.enabled`: room_service PATCHes `auto`, then cues `"auto"` with `source: mode`.
- **Not the art director.** Random and LLM speak direction only; the renderer's director cues internally.
- **Guards on the room route:**
  - 400 `bad_to` for anything that isn't a scene or `"auto"` (there is no still form);
  - 409 `not_live` unless the controller phase is `live` and no handshake is running;
  - 429 `rate_limited` above 1 cue/s per screen.
- **room_service adds** `idempotency_key` per tap and `source: panel|mode`.
- **Under LIVE_V1 §3 auth,** this is operating, so it is open.

**C.3 Bus token: a separate one.**
- **The token:** `~/.config/screen-machine/show_bus.token` (64 hex, 0600), created by room_service on first start like the others.
- **Use:** sent as K3 `hello.token` with `role: renderer`, read-only. The bus binds `127.0.0.1:8701` only and closes with 4401 on a mismatch. On 4401 the renderer re-reads the file once.
- **Flags.** stream_service gets `--show-bus-token-file`. show-synth uses the same file if it exists, otherwise `--no-auth`, which still binds to localhost only.
- **Never reuse `room_service.token`.** Under §3 it is the admin token that gates edits, and tokens aren't scoped, so "read-only reuse" isn't possible.
- **The Room page never holds a bus token.** It sees inputs through `GET /api/v1/screens/{id}/inputs`, an operate-open passthrough of §1.9's monitor, polled at ≤ 4 Hz.
- **Losing the bus never changes session state.**

**C.4 Mode presets: yes, in `config/room/modes.json`** (ROOM_STATE_MODEL §2.1; `modes.yaml` is the migration fallback only).
- **Placement:**
  - `auto` (and optionally `fixture` and `beat_follow`) goes inside each `screens.<id>` live spec;
  - `look.direction`, `look.palette` and `look.director` are room targets.
- **Under `auto.enabled`:**
  - The live spec's `scene` is the entry scene only, and its `params` apply to that scene only.
  - The screens adapter's `read()` and compare use the requested spec (entry scene plus auto). The renderer's current scene goes to status `observed.scene`; it is never drift and is never enforced.
- **Example:**
  ```json
  "live-demo": {"label": "Live demo", "extends": "live", "state": {
    "screens.north-screen": {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"},
                                      "auto": {"enabled": true, "pool": "all", "transitions": "all",
                                               "follow_dmx": ["effect", "variant"], "avoid_repeat": 2}}},
    "screens.south-screen": {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"},
                                      "auto": {"enabled": true, "pool": {"tags": {"heavy": false}}, "transitions": "all",
                                               "follow_dmx": ["effect", "variant"], "avoid_repeat": 2}}},
    "look.direction": {"energy": 0.6, "pace": 0.5, "intensity": 0.8, "change_rate": 0.5, "moods": ["bright"],
                       "genre": null, "transition_style": "lively", "palette_family": "warm"},
    "look.director": {"random": {"spread": 0.15, "every_s": [10, 30]}},
    "look.palette": {"set": {"drift": {"period_s": 180}}}}}
  ```
  A plain `set` lets the palette carry over when the room returns to `normal`, which leaves `look.*` unmanaged. To give the old palette back on exit instead, use `store` here and `retrieve` in `normal`.
- **Checked at load:** preset, palette and vocab names exist, and each screen's pool matches at least one tagged preset.
- **Auth.** Editing `modes.json`, `vocab.yaml`, tags or `palettes.yaml` writes config, so it is admin-gated. Choosing a mode, direction, palette or director, toggling auto, and "change now" are operating, so they are open.

---

## 4. Minimal slice for the owner's demo

**The demo:** art → (by hand) live → T1 → effect 1 → T2 → effect 2 → … → (by hand) art, with simulated DMX and a random or music-led middle.
- **Entering by hand** uses today's handshake: T1 is the session `in` into the entry scene.
- **The middle** is auto. It moves on follow_dmx (the synth changes effect/variant on its sections), on max dwell, or on "change now". Each move uses a weighted transition.
- **Leaving by hand** is today's exit. R5 cancels pending cues and disarms the director, the out is the crossfade, and the cut at `hold_pts` is matched.

**04 builds first (stream_service, about 2 days), in this order:**
1. `cue` (scene or `"auto"`, `at: now`) with R3–R7, R9, R10 and the R5 render-thread guard. Add the catalog `api.features` (R21) and the summary fields (R22).
2. `auto` per R11: dwell_s, next, a debounced and latched follow_dmx, the pool from tags, a seeded RNG, and a pre-prepared next scene. Tags in `scenes.yaml`/`transitions.yaml` (`heavy: true` on `fractal-seahorse`; `black` and `cut` untagged). `vocab.yaml`. The generic matching rule from R12.
3. `direction` per R12: pace sets the scene clock, intensity sets depth. Accepted on create and in every non-terminal state.
4. 3–4 transition ports alongside crossfade (melt, luma wipe, directional wipe, zoom), prepared off the render thread (R20). Variety is the visible point of the demo.
5. The show-bus client (R15, R16), pointed at show-synth on 8711: raw and `*_slow` signals, bindings per R19, 1–2 bindable fractal params, and `GET /inputs`.
6. Palette as a named preset with a crossfade, passed to sources (R14).
7. show-synth with 1–2 styles and effect/variant changes on sections.
8. The R1 cut-parity test, then an S7 soak.

**Then (about 1–2 days):** the beat follower (R18), the measured lead (R17), `at: beat` and auto `snap: beat`, then an on-glass timing check (S3).

**2f builds in parallel** (room_service and the Room page, §5): carry `auto` through, the `look` adapter with the random director, the cue routes, the screens compare rule, and a panel with direction sliders and chips, "change now", an auto toggle and a palette picker.

**Deferred:**
- bars and downbeat;
- `override`/`release` and `PATCH masters`;
- any luminance master;
- `palette_tint`, `follow: maestro`, renderer-side drift;
- per-screen trims;
- binding kinds, `curve`, `smooth_ms`, `amount`, and binding pickers with the new widgets (`multiselect`, `range`, `palette`, `binding`);
- the cost table;
- `auto.link`, `sync_to`, `scene: "auto"` on create;
- cue to a still;
- the LLM channel;
- room_service's 8701 bus and Art-Net ingest, until the MaestroDMX is configured;
- the flash limiter.

---

## 5. Merge plan

**Into LIVE_V1 (2f; rev 1.1, additive, with a changelog line):**

| Where | What |
|---|---|
| §0 | Modes row → `config/room/modes.json`. Tokens row adds `show_bus.token`. Ports: 8711 dev (show-synth). |
| §1.1 | Catalog: `api {contract, rev, features}`; `tags` on presets; `vocab`; `palettes` (computed swatches); `signals {name: {kind, idle}}`; schema marks `x-bindable` and `x-palette`. |
| §1.2 | create/PATCH fields: `auto`, `direction`, `palette`, `palette_transition_s`, `fixture`, `beat_follow`; the state rule for these fields (R12); additive `cue_id`/`duration_s` on go and exit; summary fields (R22). |
| §1.3 | `session.transition` additive fields; new `session.cue`, `session.inputs`, `session.palette`, `session.warning`; "ignore unknown fields and values"; rate caps (R9). |
| §1.5 | The cut invariant (R1) and transition prepare (R20). New plug-ins: the transition ports. |
| §1.6–§1.11 (new) | The proposal's §1.6–§1.11, revised per R2–R19: Cue, Auto, Direction, Inputs, Parameter stack and derived masters, Palette. |
| §1.9a (new, 2f) | Show bus, "K3 as amended": the schema copied in (no longer cited from the archived v1 doc), `look` → `feed`, heartbeat, renderer role not coalesced, staleness (R16), clock (R15), token (C.3). Note that v1 K3's "always runs its own output limiter" is superseded by the safety-optional ruling. |
| Appendix (informative) | show-synth (§1.12 plus S1), or move it to the stream_service README. |
| §3 | Routes: `POST /api/v1/screens/{id}/cue`, `POST /api/v1/cue`, `PUT /api/v1/targets/{target}` (a hand set, operate-open; mirrored in ROOM_STATE_MODEL §7.3), `GET /api/v1/screens/{id}/inputs`. The live spec gains `auto?`, `fixture?`, `beat_follow?`. `PATCH /live` accepts only `{scene?, params?, transition?, auto?}`; direction or palette there returns 400 `use_look_target`. Status gains `observed.scene` and `look.*`. |
| §4 | Remove "input bindings" and "Maestro/Art-Net" from out of scope. Keep "Art-Net ingest in room_service" as the next increment. |

**Into AV_PROGRAMME (2f):**
- **§4.4a "Approved shape", bullets rewritten:**
  - "The renderer subscribes; it does no DSP or inference. It smooths, and runs a metronome beat follower for latency compensation only (LIVE_V1 §1.9)."
  - "Auto advances on follow_dmx changes, max dwell or next, at `now|beat`."
  - Drop `paused` and "follow-music cueing, snapped to the beat".
  - The palette sentence becomes "the session hands its palette to indexed sources; room_service runs drift".
  - Cue becomes scenes only, with exit as the still route.
- **§4.2:** input bindings are now in scope.
- **§4.4:** the render-ahead is the measured `lead` (R17), not 0.28–0.30 s.
- **§8, owner items:**
  1. In production, one-off hits will probably land 0.5–0.8 s late rather than 0.3 s (to be measured). Is that acceptable?
  2. Should the two TVs change scene together, or independently? The default is independently.
  3. Confirm that intensity means "how hard visuals respond", not brightness.

**Into ROOM_STATE_MODEL (2f):**
- a `look` row in §5.3;
- `auto` in the screens values;
- the §5.4 read and compare rule under auto;
- the §7.3 cue and targets routes;
- the rule that director output is runtime only.

**room_service must add (2f):**
1. `normalize_desired` (`screens.py:65-104`) carries `auto`, `fixture` and `beat_follow`.
2. The `_entry` create body (`screens.py:938`) sends the effective `direction`, `palette`, `auto` and `fixture`, and re-sends them after adoption.
3. `_apply_live_change` (`screens.py:801-823`) handles hops under auto (PATCH auto, then cue auto) and auto-only changes, which today are silently dropped.
4. New fields are sent only when the catalog advertises them.
5. The held wait uses exit's `duration_s`.
6. The `look` adapter and the random director loop.
7. `show_bus.token` creation.
8. The cue routes, with guards and rate limits.
9. The inputs passthrough.
10. `observed.scene` from `session.transition`.
11. A 404/409 on a look push during teardown is a no-op.
12. `schema/room-show-bus.v1.json`, with golden frames, for show-synth's tests.
13. Later: the 8701 bus and the Art-Net ingest.

**Room page must add (2f):**
- a direction panel: sliders for energy, pace, intensity and change_rate; chips for moods, genre, transition_style and palette_family, all from the catalog `vocab`;
- "change now", per screen and for both;
- an auto toggle;
- a palette picker with swatches;
- a director selector (fixed or random);
- the current scene from `observed.scene`;
- an inputs monitor at ≤ 4 Hz.
- Later: binding editors and the new widgets.

**Other:**
- screen-machine-ca registers 8711 (dev) in the port register (`docs/PORTS.md` does not exist yet).
- 04 revises the proposal against R1–R23 and re-submits. 2f then merges the stream_service sections verbatim and writes §1.9a and §3 itself.
