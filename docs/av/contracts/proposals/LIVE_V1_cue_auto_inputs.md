# Proposal: LIVE_V1 additions — cue, auto-director, direction, palette, inputs

**Status:** revision 2 (applies review R1-R23; folds in addendum A1-A5); for merge into LIVE_V1 rev 1.1 by screen-machine-2f.
**Author:** screen-machine-04 (renderer owner). **Reviewer and merger:** screen-machine-2f, which owns LIVE_V1, room_service and the room state model.
**Date:** 14 Oct 2026 (rev 1 and rev 2). **Review:** `docs/av/reviews/LIVE_V1_cue_auto_inputs_review.md` (merge after changes).
**Scope:** additive within v1. `go`, `exit`, the existing events and the media socket keep their routes, bodies and semantics as today's stream_service implements them; they only gain fields. (Today's stream_service also accepts `exit` in `held`, which LIVE_V1's text doesn't list; this proposal writes it down, add §1.2 and B.7.) `PATCH` keeps its route and bodies and accepts the new field-only bodies in more states (add §1.2). Two behaviours change:
- **PATCH scene becomes a manual cue** in `entering` and `live`. It prepares outside the session lock and still returns 200 once its scene is prepared. A third rapid scene change now waits in the pending slot instead of nesting, and while it waits a newer cue can replace it or exit can cancel it (add §1.2, §1.6, B.5).
- **One validation is added:** every out transition must resolve to `crossfade` with `duration_s` ≥ 0.5 s (add §1.5, B.6). A `cut` out, or a shorter crossfade out, returns 400 `bad_spec`. v1 offers both, but no mode uses either (`modes.yaml` uses `crossfade-3s`), and room_service exits only through the mode's `out`. It is a deviation from R3 and R21.5, for 2f to accept (changelog, "§2.2 noted" row).

Owner direction (13–14 Oct) is in §A. The stream_service text is §1: first the additions to existing LIVE_V1 sections (§1.1, §1.2, §1.3, §1.5), then the new §1.6–§1.11, written to slot in after LIVE_V1 §1.5. §C records 2f's decisions. §D lists what the owner still has to answer. show-synth (Appendix S) and the dev console conventions (Appendix V) are informative.

---

## Changelog (rev 1 → rev 2)

| R | Change | Where |
|---|---|---|
| R1 | Cut invariant: the anchor and every exit target render at identity. R1's "tint" is written "(and any future tint)", because `palette_tint` is removed for v1 | add §1.5, A.3.6 |
| R2 | `intensity` is modulation depth; no luminance master or output-level gain. Deviation for 2f to accept: R2's "No luminance gain exists anywhere in v1" becomes "No luminance master or output-level gain exists in v1". *Why:* R19 asks for an exposure pulse, which is a scene-internal luminance effect (fractal_zoom's `exposure`; warp_stack's `gain` is the same kind). R1 keeps the anchor and exit targets at identity, and live's base level stays at full. D.3 tells the owner that beat-driven exposure lifts exist | §1.8, §1.9, §1.10, D.3 |
| R3 | Cue takes a scene or `"auto"` only; go and exit unchanged, plus `cue_id` and `duration_s`; aliases paragraph removed | §1.6, add §1.2 |
| R4 | Cue is 409 outside `entering`/`live`; the director is armed only in `live` | §1.6, §1.7 |
| R5 | Exit cancels the pending cue and any director prepare, and disarms the director; render-thread guard; prepare off the lock | §1.6, §1.7 |
| R6 | At most 2 transitions at once, one pending slot, replacement rules; PATCH scene is a manual cue. Rank is applied as written: rank follows `cause`, so every auto cue, `cue {to: "auto"}` included, ranks below manual and PATCH cues and never replaces one. Deviation for 2f to accept: the `in` counts toward the cap of 2, so in `entering` one scene cue nests over the `in` and a second waits (R6 caps scene transitions only, which would allow two). *Why:* the `in` renders a 4K source like any scene transition, and R6's cap exists to bound live 4K sources on the GPU (`fractal-seahorse` is heavy on the P4000). The WIP already counts it | §1.6, §1.7, add §1.2, add §1.3 |
| R7 | `via` precedence; 202 body with `duration_s`; `duration_s` is resolved when the transition starts and never rescaled mid-flight; `at_pts` is an estimate | §1.6, add §1.2 |
| R8 | No bars: `at: now\|beat`, `snap: beat`, `dwell_s` derived from `change_rate`; durations in seconds only | A.3.5, §1.6, §1.7, §1.9 |
| R9 | New `session.cue`; additive `session.transition` fields; no new phases; rate caps. Two deviations from the R5, R6 and R9 wording, for 2f to accept: `reason` stays the auto trigger on every event, and the cancel or skip reason goes in a separate field `why` (`exit`, `replaced`, and the new `auto_changed`, `ended`, `prepare_failed`, `empty_pool`, `no_candidate`, `manual_pending`) instead of `reason: exit\|replaced`; `source: director` is set by the renderer on its own director's moves, never by room_service. *Why:* `reason` keeps one meaning (the auto trigger) on every phase, so a cancelled or skipped auto cue keeps it, and the new end causes don't overload R9's auto-only enum; room_service never sees the renderer's own director moves before their events, so only the renderer can set `source: director` | add §1.3, §1.6 |
| R10 | Idempotency per session, kept for its life; error codes | §1.6 |
| R11 | Auto fully specified: defaults, pool, dwell, follow_dmx, weight, prepare, RNG. Two deviations from R11 wording, for 2f to accept: (1) R11 measures dwell "from the previous scene transition's `done`"; an `auto` PATCH that turns the director on (`enabled` false → true, or a new block with `enabled: true`) also restarts dwell from that PATCH. *Why:* a max dwell that expired while auto was off would otherwise fire the moment it is re-enabled. (2) R11 says "an empty pool means no move, plus `session.warning`" and weights transitions "the same way"; an empty transitions pool instead falls back to the session `in`, so the scene move still happens, and `session.warning{code: "empty_pool", pool: "transitions"}` names the pool. *Why:* R7's `via` precedence already ends at the session `in`, and an empty transitions pool should not block a scene change | §1.7, add §1.3 |
| R12 | Direction: whole object, neutral defaults, ease, states, one matching rule, `vocab.yaml`. Today's `transitions.yaml` drops its `cut` tags and renames `style` to `transition_style` before ship | §1.8, add §1.1, add §1.2 |
| R13 | 3-layer stack; derived masters (speed = scene clock, depth); override, release and PATCH masters removed | §1.10, A.2, A.3.4, add §1.5 |
| R14 | Palette: names only, handed to `x-palette` sources (`fractal_zoom` and `warp_stack`), no field pass; compatibility rule; drift is computed in room_service's director, as concrete preset names. `palette_period` (index scaling, not colour) and `warp_stack`'s colour-cycle rate `drift_speed` (the counterpart of `palette_drift`, which R14 keeps) stay scene params (deviation from R14 wording, for 2f to accept) | §1.11, B.1, add §1.5, §C.1 |
| R15 | Bus clock: CLOCK_MONOTONIC stamped at receipt, mapped each frame | §1.9 |
| R16 | Bus heartbeat, renderer role not coalesced, staleness, `look` → `feed` (2f writes LIVE_V1 §1.9a) | §1.9 |
| R17 | Measured `lead_ms` with a breakdown; the Maestro term adds. The WIP's status (addendum A3) is noted. `maestro_latency_ms` and `trim_ms` are stream_service CLI settings only in v1; no API sets them. The lead block's photon fields are fixed as `photon_source` (a screen id, or `default`) plus a boolean `photon_measured`, and the lead block gains `measured`. This changes addendum A3's `photon_source: "south (unmeasured north)"`. *Why:* consumers read an id and a flag instead of parsing a note, and the WIP already uses `measured` for the server and buffer terms. The WIP's `"latency.json:south (no north measurement)"` and its `frame_ms` (for S4's `present_ms`) are aligned before ship | §1.9, A.3.3, D.1 |
| R18 | Follower: Schmitt edges, grid confidence, lock and unlock, timeout, raw edge when unlocked or off. Addendum A1 refines two details (renderer-internal, no contract change; deviation from R18 wording, for 2f to accept): the Schmitt thresholds are relative to a decaying peak, and P is the mean of the intervals within ±12 % of their median rather than the plain median. *Why:* absolute 0.6/0.3 thresholds missed the synth's snares, so pop locked at half tempo; the plain median of 25 ms-quantised Art-Net intervals read 126 bpm for a 124 bpm song | §1.9 |
| R19 | Bindings `{bind, range, rest}`, depth formula, no easing, idle values, `energy` → `activity_slow`; the `rgb_slow` signal is removed (`rgb` is monitor-only); bindable demo params `zoom_rate`, `palette_drift`, `exposure` (R19's exposure pulse) and `palette_shift` (R19's colour-cycle kick). These are the names the WIP's `fractal_zoom` and `scenes.yaml` already use; rev 1 called them `exposure_kick` and `cycle_kick`. The §2.3 no-bus decision ("per-signal idle, with an optional per-binding `rest`") is read as: with the bus absent or stale, a binding with an explicit `rest` eases to `rest` on the staleness schedule; otherwise it follows its signal's eased idle value | §1.9, add §1.1, B.4 |
| R20 | Transitions prepare off the render thread. Addendum A2: warm-up runs at each GPU's first session, not at service start (deviation from R20 wording, for 2f to accept; it keeps a CUDA context off the Windows desktop GPU) | add §1.5 |
| R21 | Compatibility rewritten; catalog `api.features` | B, add §1.1 |
| R22 | Session summary fields | add §1.2 |
| R23 | Ruling leftovers: A.3.3 wording, `direction.energy`, synth sections, no `room_service.token` | A.3.3, §1.7, §1.9, Appendix S |
| §2.3, §3 | Review decisions adopted | §C, §1.6–§1.11 |
| S1, S2, S3, S4 (lead, stale only), S5, S7 | Recommended items adopted as notes: synth fault flags and replay; GPU-free director; follower acceptance; `/inputs` lead and `stale` (S4's `follower` and `auto` blocks are not adopted); computed swatches, OKLab; a 2-hour soak before each slice ships. S1 as built differs in three places: the truth record is `{t_ms, event: beat\|section\|control, …}` (S1's `kind: beat\|section\|effect`; the section line carries the effect); all four styles exist (S1: "1–2 styles to start"); and tempo faults are driven at runtime through `/v1/control` (`ramp`, `bpm`, `pulses: false`) instead of the `--tempo-ramp` and `--half-time` flags | Appendix S, §1.7, §1.9, §1.11, add §1.5 |
| Owner request, 14 Oct: vary the bpm in the demo | show-synth's runtime control (`GET/POST /v1/control`: bpm step, ramp, song, breakdown, next section), stream_service's dev proxy `GET/POST /dev/synth`, and the /dev console's Music panel are written down. All three exist in the WIP. Appendix V's steps start show-synth first: without it, `/dev/synth` returns 502 `synth_unreachable`. Deviation for 2f to accept (§C.3): show-synth's `/v1/control` takes no token, even when `show_bus.token` exists. *Why:* it is dev-only and bound to 127.0.0.1. It changes only the simulated music, never session or room state. The /dev console reaches it only through stream_service's Bearer-gated `/dev/synth`, so no browser holds the bus token. room_service's 8701 bus has no control API | Appendix S, Appendix V, §1.9 |
| S6 | Decision debug payload: not adopted; deferred | — |
| §2.2 noted | Deviation for 2f to accept (R3 "exit ... exactly as today"; R21.5 "one resource-guard change"): every out transition (the session `out` on create, and any `transition` on `exit`) must resolve to the `crossfade` plug-in with `duration_s` ≥ 0.5 s, in any spec form; otherwise 400 `bad_spec`. A `cut` out and a shorter crossfade out (`duration_s` 0 included) are rejected. *Why:* the review's noted item (report `hold_pts` 2 frames after progress 1, for encoder residue) is unneeded only "while exits use only the session `out` crossfade". A cut, or a crossfade of zero or near-zero length, is the worst case for residue: the `hold_pts` frame is then the first target frame after a full-frame change, so the encoded and decoded frame the page cuts on has not yet converged to the still. That breaks the owner rule that the stream opens on and converges to the still, and pre-encode parity can never catch it. Keeping every exit a real crossfade keeps the review's premise, so `hold_pts` stays exact at progress 1 and LIVE_V1 §1.3's definition is unchanged. v1 offers `cut` and short crossfades as outs, but no mode uses them (`modes.yaml` uses `crossfade-3s`). v1 therefore also requires every easing to map raw progress 0 → 0 and ≥ 1 → 1 exactly, and every transition plug-in to be bit-exact at eased 0 and 1. Four tests replace the single cut-parity test: an endpoint test for every registered transition plug-in; pre-encode exit parity and post-decode exit parity (mean \|diff\| ≤ 1/255 at the `hold_pts` frame; the addendum's end-to-end check measured 0.55/255) for `crossfade` at 0.5 s and 3 s; and entry parity for every transition allowed as `in`. The session `out` is checked on create, never deferred to exit time, and an explicit `transition` on `exit` on that request. room_service also checks each live spec's `transitions.out` when it loads modes (proposed by 04; 2f to confirm) | add §1.5, add §1.2, B.6, §C.4 |
| New plug-ins | Registers the source `warp_stack` (`x-palette`, `drift_speed`, `render_scale`), which §1.11 and B.1 already rely on. Lists `burn`, `glitch` and `pixelate`, which the tree's registry already discovers and tags, as rev 1.1 transitions beside `melt`, `luma`, `wipe` and `zoom`. Example preset names now match `transitions.yaml` | add §1.5 |
| Addendum | The 14 Oct addendum (`LIVE_V1_cue_auto_inputs_addendum.md`, A1-A5) is folded in: A1 in §1.9 Beat follower, A2 in add §1.5 Transition prepare, A3 in §1.9 Render-ahead, A4 in add §1.1, A5 as Appendix V (informative). A1 and A2 are flagged in the R18 and R20 rows, and A3's `photon_source` format change in the R17 row. A4's "Effects: amount" line is recorded as future: rev 1.1 has no effect plug-in kind (only sources and transitions), so the hint is added when one exists. The addendum file is marked as folded in, kept for history and not for merge | §1.9, add §1.5, add §1.1, Appendix V |
| Exit in `held` | Today's stream_service (and the WIP) accepts `exit` in `held`; LIVE_V1 §1.2's text lists only entering, live and exiting. It is written down here so the text matches the service (2f to add it to LIVE_V1 §1.2). It is not a new behaviour, so R3 ("exactly as today") holds | add §1.2, B.7 |
| 04 clarifications beyond the review | For 2f to approve explicitly: `palette` can be changed but not cleared, and `palette: null` on PATCH returns 400 (add §1.2, §1.11); retrieving an unset stored palette leaves `look.palette` unset (§C.1, proposed); direction `note`, and direction `source` values `random` and `llm` (§1.8); unknown direction keys are ignored and logged once per key (§1.8); an unknown vocab value is kept and matches nothing (§1.8); a direction key that is null or an empty list counts as matched (§1.8); a scalar string tag matches by equality, a list tag by membership (§1.8); vocab: moods gain `calm`, `hypnotic` and `playful` and lose `cool`, `palette_family` is `warm\|cool\|vivid\|neutral` (the families in `palettes.py`), and presets and palettes tagged with the mood `cool` are retagged before ship (add §1.1, §1.8); list and string direction keys apply at the next pick (§1.8); among tagged presets, one without the key matches a `false` pool filter, untagged presets are never in an auto pool, and a name list may hold only tagged presets (§1.7); `tags.weight` must be > 0, and a preset with `weight` ≤ 0 is listed in the catalog `errors` and treated as untagged (§1.7); a newer auto cue replaces a pending auto cue (§1.6); a director move that finds a manual cue pending is dropped silently, and a `cue {to: "auto"}` that finds one is skipped with `why: manual_pending` (§1.6); a cue is pending from acceptance until the render thread applies its StartTransition, so a beat-timed one stays pending while it waits (§1.6); a scene StartTransition retired by the render-thread guard ends as `cancelled, why: exit`, and every accepted cue ends in exactly one of started, cancelled or skipped (§1.6, add §1.3); a follower unlock during a beat wait starts the cue at once, and `session.transition{started}` then carries `fallback: "no_beat"` (§1.6, add §1.3); a waiting cue starts on any running scene transition's `done`, superseded included (§1.6); `at: beat` is lead-compensated (§1.6); `cue {to: "auto"}` takes `at` from the request, and `snap` applies only to director moves (§1.7); the `via` choice, a director transition pick included, is made when a cue is accepted, while `duration_s` keeps R7's timing (§1.6); a PATCH carrying `scene` returns the cue fields (add §1.2); PATCH scene prepares outside the lock and returns 200 once prepared; a prepare failure returns today's error with no `session.cue`, and exit during its prepare returns 409 `bad_state` with `cancelled, why: exit` (add §1.2, B.5); PATCH scene uses its own `transition`, else the session `in`, never the director's pick (add §1.2); the summary's `scene` and `scene_params` follow every scene transition (add §1.2); a mixed PATCH follows today's rules (add §1.2); an `auto` PATCH discards the prepared pick and cancels a pending director move with `why: auto_changed` (§1.7, add §1.3); an `auto` PATCH re-takes the follow_dmx baseline and discards any latched change (§1.7); `empty_pool` refers to the pool before novelty, and when novelty alone leaves no candidate the director stays without a warning and `cue {to: "auto"}` is skipped with `why: no_candidate` (§1.7); the follow_dmx baseline is re-taken on arming, and a derived `dwell_s` is evaluated continuously (§1.7); `session.warning` rate cap, and an empty pool is re-checked only on change (add §1.3); error codes for the new fields: an unknown palette returns 400 `unknown_preset`, a binding on a param not marked `x-bindable` or to an unknown or non-bindable signal returns 400 `bad_spec`, `dwell_s` with min ≤ 0 or min > max returns 400 `bad_spec`, and `direction: null` on PATCH returns 400 `bad_request` (add §1.2, §1.7, §1.9, §1.11); transition params are never bindable (§1.9); binding output is clamped to the schema range (§1.9); a binding with an explicit `rest` eases to it on the staleness schedule (§1.9); the bus `feed` values `live\|hold\|safe\|black`, defined here for 2f's K3 text (§1.9); the summary also appears in `GET /api/sessions` and `GET /api/state` (add §1.2); the demo param names and ranges `exposure` and `palette_shift` (§1.9); `warp_stack` also declares `x-palette` (§1.11); the cosine palettes stay in `stream_service/palettes.py`, and a `palettes.yaml` entry with a built-in's name may carry only `label` and `tags` (`stops` there is a catalog error, and the entry is ignored) (§1.11) | as listed |
| 04 clarifications from the 14 Oct gap check | For 2f to approve explicitly: `superseded` on `session.transition{done}` is defined (today's behaviour, now written down), and `session.held` and `hold_pts` come only from the non-superseded `done` of the most recent exit's out transition (add §1.3); a pending cue ends as `cancelled, why: ended` when the session is deleted, times out or fails (add §1.3); only cues of cause `manual`, `patch` or `auto` emit `scheduled`, and go and exit emit only `session.transition` (add §1.3); pending-slot replacement is by acceptance order, a PATCH scene is accepted when its prepare completes and always has `at: "now"`, and a PATCH scene received in `holding` whose prepare finishes after go is accepted becomes a cue (add §1.2, §1.6); exact transition ends are stated on raw progress for the easings and on eased 0 and 1 for the plug-ins, and the parity tests compare with the decoded, letterboxed pixels or the solid rgb (add §1.5); while a session palette is set, the Room page hides the `palette` param of `x-palette` sources and shows the session palette (add §1.1); a `cue {to: "auto"}` skipped at acceptance returns `at_pts: null, duration_s: null` and draws no pick (§1.6); quarantine removes a preset from auto pools only, only an auto pick's prepare failure quarantines, a failure of the director's ahead-of-time pick emits only `session.warning`, and a manual or `to: "auto"` cue whose prepare fails ends as `skipped, why: prepare_failed` plus `session.warning` (§1.7, add §1.3); `beat_follow: "off"` during a beat wait starts the cue at once, like an unlock (§1.6); a bare `look.*` entry value is shorthand for `{"set": value}`, as ROOM_STATE_MODEL §2.4 already defines (§C.1); the Room page also does not show a param as basic while it is bound (add §1.1); the render-thread exit guard is a latch set when an `out` StartTransition is applied, and it stays set through `held` and any re-exit (§1.6); an `in` done that arrives after exit has been accepted never changes state or arms the director (§1.6, add §1.3); no `session.transition` follows `session.ended` or `session.error` (add §1.3); a `cue {to: "auto"}` without `via` takes the director's prepared pick when one is ready (§1.6, §1.7); the follow_dmx baseline is the value when the last scene transition started or the baseline was last re-taken, and a starting scene transition clears a latched change (§1.7); `cue.last` counts only cues of cause `manual`, `patch` or `auto`, and `started_pts` is defined (add §1.2); an `auto.pool` or `auto.transitions` name list naming an unknown preset returns 400 `unknown_preset` (add §1.2, §1.7) | as listed |

**Removed for v1:** cue to a still; bars, downbeat, `dwell_bars`, `at: "bar"`, `snap: "bar"`; `override` and `release`; `PATCH masters`; `auto.paused`; `palette_tint`; `{"follow": "maestro"}` and renderer-side `{"drift"}`; any luminance master; binding kinds, `curve`, `smooth_ms`, `amount`; the `rgb_slow` signal; "the first scene's palette seeds it"; reuse of `room_service.token`.

---

## A. The model

### A.1 Owner direction

1. **Art → live happens once, by hand, and so does live → art.** In between, every scene change gets its own transition, chosen per change.
2. **Two control layers at two speeds:**

   | Layer | Rate | Speaks in | Today | Later |
   |---|---|---|---|---|
   | **Art director** | every ~10–30 s | descriptors: genre, moods, energy, pace, intensity, change rate, transition style, palette family | the panel, or room_service's random director (§C.1) | an LLM listening to rolling music clips, through a keyed room_service channel |
   | **Real time** | every frame | beats, colour, activity, pan/tilt/zoom, effect/variant | show-synth (Appendix S) | Maestro over Art-Net → room_service → show bus |

3. **Palette is its own lasting axis.** It is separate from the effect (scene) and from modulation. It carries across scene changes and moves on its own clock.
4. **Variety of transition types.** Crossfade is the default. v1 adds melt, luma wipe, directional wipe and zoom; burn, glitch, pixelate and more follow (gl-transitions ports, MIT). *(Note: burn, glitch and pixelate are already registered and are listed in rev 1.1; add §1.5.)*
5. **Inside a scene, live DMX drives it in real time**, including beat effects.
6. **Respond to DMX and obey the director; do no DSP of our own** (owner, 14 Oct ~00:40).
   - There is no audio analysis and no inferred musical structure: no section, bar or downbeat detection, no mood or genre inference.
   - Maestro provides the real-time musical intelligence. The director (human, random, later an LLM) provides the art direction.
   - The one exception is a minimal **beat follower**, needed only for latency compensation (§1.9). It is switchable; when unsure it unlocks and `beat` falls back to the raw pulse edge.
   - Whether audio-aware AI later outperforms DMX is an open question. The bus and the `direction` interface are source-agnostic, so either can be swapped without downstream changes.

### A.2 Layers

| Layer | Rate | Owner | In this proposal |
|---|---|---|---|
| Clocks: the frame clock, plus a beat follower used only for latency compensation | per frame | renderer | §1.9 |
| Signals: raw values plus smoothing only (no inference) | per frame | room_service decodes and stamps; the renderer smooths | §1.9 |
| Room state: `look.direction`, `look.palette`, `look.director`; modes; the random director | on change | room_service | §C.1, §C.4 |
| Parameter stack: default < preset < session params, written only by room_service; bindings; derived masters | on change | renderer | §1.10 |
| Sequencing / director: what next, via what, when | occasional | renderer `auto`, steered by `direction` | §1.7, §1.8 |
| Render graph: sources (with the session palette) → transitions; the anchor and exit targets at identity | per frame | renderer | add §1.5, §1.11 |

### A.3 Rules

1. **The art director names descriptors, never scenes.**
   - Scene and transition presets carry `tags`. Untagged presets are manual-only.
   - The renderer's director maps the current `direction` to weighted picks (§1.7).
   - New scenes join the mix through their tags alone.
2. **Bind rates, not positions.**
   - Level signals and the director set *speeds*: zoom rate, colour-cycle rate, rotation rate. Triggers drive decaying kicks (A.3.3).
   - The plug-in integrates them, so nothing jumps.
   - The derived `speed` master (from `direction.pace`) scales the scene clock (§1.10).
3. **Separate triggers from levels.**
   - Beat effects use `pulse` (raw) and `beat` (predicted by the follower).
   - Rates and levels inside a scene (e.g. zoom rate, colour-cycle rate) bind to smoothed signals: `activity_slow`, `level_slow`. Pace and depth are derived masters from `direction` (§1.10), never from signals.
   - Steady beats land on time through the follower. One-off hits land about `lead` late (expected 0.5–0.8 s in production; to be measured, §D.1), so they drive slow-decaying effects, not sharp flashes.
4. **One parameter stack**: default < preset < session params, written only by room_service (§1.10). There is no override layer.
5. **Cues can start on a predicted beat.** Bars arrive additively if Maestro gains a downbeat channel.
6. **The cut is sacred.** The anchor and every exit target render at identity (add §1.5).

---

## 1.1–1.5 Additions to existing LIVE_V1 sections

### Add to §1.1 Catalog

`GET /api/catalog` gains, additively:

```json
{
  "api": {"contract": "LIVE_V1", "rev": "1.1",
          "features": ["cue", "auto", "direction", "palette", "inputs", "bindings"]},
  "vocab": {"moods": ["mellow", "warm", "bright", "euphoric", "tense", "dreamy", "dark", "calm", "hypnotic", "playful"],
            "genre": ["jazz", "pop", "techno", "ambient", "house"],
            "transition_style": ["gentle", "lively", "wild"],
            "palette_family": ["warm", "cool", "vivid", "neutral"]},
  "palettes": {"ember": {"label": "Ember", "kind": "cosine", "…": "§1.11"}},
  "signals": {"pulse": {"kind": "trigger", "idle": 0}, "activity_slow": {"kind": "level", "idle": 0.5},
              "beat_phase": {"kind": "phase", "idle": "hold"}, "…": "§1.9"},
  "errors": ["scenes.yaml: example-preset: unknown moods value 'brooding' (preset loaded)"]
}
```

- **`api.features`**: room_service sends a new field only when its feature is listed (B.2).
- **Presets gain `tags`** (§1.8), e.g. `"fractal-dendrite": {"label": "…", "source": "fractal_zoom", "params": {…}, "tags": {"energy": [0.3, 0.9], "moods": ["bright", "euphoric", "tense"], "weight": 1.0}}`.
- **`vocab`** comes from `config/live/vocab.yaml` (§1.8), **`palettes`** from §1.11, **`signals`** (`{name: {kind, idle}}`, bindable signals only) from §1.9.
- **`errors`** lists config problems as strings: invalid presets, unknown tag values.
- **Schema marks:** `"x-bindable": true` on a numeric property (§1.9); `"x-palette": true` at the top level of a source schema (§1.11).
- **UI hint `basic`** (2f's request; addendum A4). `"x-ui": {"basic": true}` on a property marks one of the 1–3 headline params per plug-in that the simplified Room page shows:
  - `fractal_zoom`: `zoom_rate` and `palette`;
  - `warp_stack`: `palette`, `drift_speed` and `fb_amount` ("Trails").
  - Params meant to be driven by realtime signals (`warp_stack`'s `energy` macro, `fractal_zoom`'s `exposure`, `warp_stack`'s `gain`) never carry `x-ui.basic`. The Room page also does not show a param as basic while it is bound.
  - While the session has a `palette` (§1.11), the Room page hides the `palette` param of `x-palette` sources and shows the session palette (`look.palette`) in its place, because the source ignores its own `palette` param then.
  - Addendum A4 also named `amount` for effects. Rev 1.1 has no effect plug-in kind (only sources and transitions), so that hint is added when one exists.

### Add to §1.2 Sessions

**New create and PATCH fields:**

| Field | create | PATCH | Absent on create means | Spec |
|---|---|---|---|---|
| `auto` | yes | yes; whole object, `null` removes it | disabled | §1.7 |
| `direction` | yes | yes; whole object; `direction: null` returns 400 `bad_request` | neutral defaults (today's behaviour) | §1.8 |
| `palette` | yes | yes; `palette: null` returns 400 `bad_request` | scenes keep their own palette params | §1.11 |
| `palette_transition_s` | yes | yes | 4.0 | §1.11 |
| `beat_follow` | yes | yes | `"extrapolate"` | §1.9 |
| `fixture` | yes | no | derived from `screen` | §1.9 |
| a binding in place of a param value | in the scene spec's `params` | in `params` | — | §1.9 |

On PATCH an absent field is unchanged; `auto: null` removes auto.

**Errors for the new fields** (on create and PATCH):
- an unknown palette name: 400 `unknown_preset` (§1.11);
- `direction: null` or `palette: null` on PATCH: 400 `bad_request` (to reset direction, send the neutral object);
- `dwell_s` with min ≤ 0 or min > max: 400 `bad_spec` (§1.7);
- a binding on a param not marked `x-bindable`, or to an unknown or non-bindable signal (`effect`, `variant`, `rgb`, `bpm`, `beat_confidence`): 400 `bad_spec` (§1.9);
- an `auto.pool` or `auto.transitions` name list naming an untagged preset: 400 `bad_spec`; naming an unknown preset: 400 `unknown_preset` (§1.7).

**State rule:** a PATCH carrying only `direction`, `palette`, `palette_transition_s`, `auto` or `beat_follow` is accepted in every state except `ended` and `failed`. It is stored and applies to scene-side rendering only. `scene` and `params` keep today's state rules; a PATCH that mixes them with the fields above follows today's rules for the whole body.

**PATCH scene** in `entering` or `live` counts as a manual cue (cause `patch`). It resets dwell and obeys the nesting cap and pending slot (§1.6).
- **Prepare first.** PATCH scene gets its `cue_id` on receipt and prepares the scene outside the session lock. It is accepted as a cue only once the scene is prepared, so it enters the pending slot only then. Replacement in the slot is by acceptance order (§1.6 Bounded nesting), so its `cue_id` can be lower than that of a cue it replaces.
- **Timing.** A PATCH scene always has `at: "now"`.
- **Response.** Once the scene is prepared, under the lock, the cue emits `session.cue{phase: scheduled}` and is submitted, or waits for the cap; either way it is the session's pending cue until the render thread applies it (§1.6). PATCH then returns 200 with the summary plus the cue fields: `{…, cue_id, at_pts, duration_s}`. `at_pts` may be null while the cue waits.
- **Prepare failure.** PATCH returns today's error and emits no `session.cue`.
- **Exit during the prepare.** If exit is accepted while the scene is preparing, PATCH returns 409 `bad_state` and emits `session.cue{phase: cancelled, why: exit}`. This is its only `session.cue` event.
- **While it waits.** After the 200, a PATCH scene waiting in the pending slot can still be replaced by a newer manual cue or PATCH scene, or cancelled by exit. It then emits `session.cue{phase: cancelled}` (`why: replaced` or `exit`) and never starts.
- **Transition.** Its transition is the PATCH `transition`, else the session `in`, as today; the director's pick never applies to PATCH.
- **In `holding`** PATCH scene still replaces the entry scene, as today. It is not a cue there and returns no cue fields. A PATCH scene received in `holding` whose prepare finishes after go is accepted becomes a cue (cause `patch`), like one received in `entering`, and returns the cue fields.

**go and exit** keep their routes, bodies and semantics exactly, as today's stream_service implements them, except that the out transition is validated (add §1.5, B.6). "go is the cue that leaves holding; exit is the cue to a still. They stay separate routes." Their responses gain `cue_id` and `duration_s`:

```json
{"session": "s-3f9a12c4", "state": "entering", "cue_id": "c-0001", "duration_s": 3.0}
{"session": "s-3f9a12c4", "state": "exiting", "cue_id": "c-0010", "duration_s": 3.0}
```

- go keeps `200`; exit keeps `202`.
- go and exit start their transition at once, so their `duration_s` is final. room_service sizes its held wait from exit's `duration_s`.
- **Exit in `held`** (today's behaviour; LIVE_V1 §1.2's text lists only entering, live and exiting, so 2f adds it there). exit is also accepted in `held`. It moves to `exiting`, runs the out transition (a `crossfade` of at least 0.5 s, add §1.5) from the held target to the new target, clears `hold_pts`, and emits a new `session.held` with a new `hold_pts`. room_service's `live.leave` must use the latest `hold_pts`.

**Summary** (`GET /api/sessions/{id}`, and each entry in `GET /api/sessions` and `GET /api/state`) gains:

```json
"current_scene": "fractal-dendrite",
"cue": {"pending": {"cue_id": "c-0009", "cause": "manual", "source": "panel", "scene": "fractal-rabbit",
                    "transition": "luma-3s", "at": "beat", "at_pts": null},
        "last": {"cue_id": "c-0007", "cause": "auto", "source": "director", "reason": "dmx_change",
                 "scene": "fractal-dendrite", "transition": "melt-2s", "started_pts": 812.35}},
"auto": {"enabled": true, "pool": "all", "transitions": "all", "dwell_s": [42.4, 84.9], "snap": "beat",
         "follow_dmx": ["effect", "variant"], "avoid_repeat": 2,
         "armed": true, "seed": 1849203, "next_eligible_pts": 856.8},
"direction": {"energy": 0.6, "pace": 0.5, "intensity": 0.8, "change_rate": 0.5, "moods": ["bright"],
              "genre": null, "transition_style": "lively", "palette_family": "warm",
              "source": "random", "note": null},
"palette": "ember",
"masters": {"speed": 1.0, "depth": 0.8},
"fixture": "screen.north",
"beat_follow": "extrapolate",
"lead_ms": 572
```

- `current_scene` always equals `scene`. In `entering` and `live` that is the scene on glass, or the incoming one while a scene transition runs. Before go it is the entry scene, which is not yet on glass; after exit it is the last scene.
- `scene` and `scene_params` keep their meaning and always describe the scene that PATCH params edits. They update whenever a scene transition starts, whatever its cause (and, in `holding`, when PATCH scene replaces the entry scene). `current_scene` is the field room_service reads when it adopts a session.
- `cue.pending` is null when the slot is empty. `cue.last` is the last cue of cause `manual`, `patch` or `auto` that started (go and exit never replace it), or null before one starts; `started_pts` is the pts of its `session.transition{started}`.
- `auto` is the effective block (defaults filled, `dwell_s` resolved) plus `armed`, the effective `seed` and `next_eligible_pts` (null when disarmed). It is null when there is no auto block.
- `masters` is derived and read-only (§1.10).
- room_service needs these fields to adopt sessions after a restart (AV_PROGRAMME §3.3).

### Add to §1.3 Events

**`session.transition`** gains `cue_id`, `cause`, `source?`, `reason?`, `scene` and `transition` on both `started` and `done`, superseded included, `fallback?` on `started`, and `superseded?` (boolean, only on `done`). `kind` and `phase` keep their values; there are no new phases.

- **Superseded** (today's behaviour, written down): "Transitions nest; a newer transition takes the running TransitionLayer as its `a`. When a transition reaches progress 1, the tree collapses it to its `b` and discards everything in its `a`. A discarded transition that hadn't finished reports `done` with `superseded: true` at the discarding frame, with that frame's pts. Every `started` gets exactly one `done`, unless the session ends or fails first: no `session.transition` follows `session.ended` or `session.error`."
- `session.held` and `hold_pts` come only from the non-superseded `done` of the most recent exit's out transition, and the session moves to `held` only on that `done`.

```json
{"type": "session.transition", "session": "s-…", "screen": "north-screen", "kind": "scene", "phase": "started",
 "pts": 812.35, "cue_id": "c-0007", "cause": "auto", "source": "director", "reason": "dmx_change",
 "scene": "fractal-dendrite", "transition": "melt-2s"}
```

- `cause` is `go`, `exit`, `manual`, `patch` or `auto`.
  - `cause: auto` covers director moves (`reason: dmx_change|max_dwell`, `source: director`) and `cue {to: "auto"}` (`reason: next`, `source: panel|mode`).
  - `cause: manual` is a `POST /cue` to a named scene; `cause: patch` is a PATCH scene.
  - For the pending slot, rank follows `cause` (R6): `manual` and `patch` cues rank as manual, and every `auto` cue, director move or `cue {to: "auto"}`, ranks as auto (§1.6).
- `fallback` appears on `started` only when the cue's `at: "beat"` ran as `now` (`"no_beat"`), including when the follower unlocked, or `beat_follow` was set to `off`, while the cue waited for its beat (§1.6).
- On `session.transition`, `reason` is for auto cues only: `dmx_change`, `max_dwell` or `next` (a `cue {to: "auto"}`). It has the same meaning on `session.cue`.
- `source` is informational. It is `panel` or `mode` when room_service sends the cue: room_service sets it on the cue body and the renderer echoes it. The renderer sets `director` on its own director's moves (`reason` `dmx_change` or `max_dwell`). room_service's art director never cues (§C.2).
- `scene` is null on `kind: "out"` (the target is a still). `transition` is the preset name, or the plug-in name for an inline spec.

**`session.cue`** (new) covers cues that can wait, be cancelled or be skipped: cause `manual`, `patch` or `auto`. go and exit start at once and appear only on `session.transition`.

```json
{"type": "session.cue", "session": "s-…", "screen": "north-screen", "cue_id": "c-0007", "phase": "scheduled",
 "cause": "auto", "source": "director", "reason": "dmx_change", "scene": "fractal-dendrite", "transition": "melt-2s",
 "at": "now", "fallback": "no_beat", "at_pts": 812.35}
{"type": "session.cue", "session": "s-…", "screen": "north-screen", "cue_id": "c-0009", "phase": "cancelled",
 "cause": "manual", "source": "panel", "why": "exit", "scene": "fractal-rabbit", "transition": "luma-3s",
 "at": "beat"}
```

Fields: `{cue_id, phase, cause, source?, reason?, why?, scene?, transition?, at, fallback?, at_pts?}`.

| `phase` | When | `reason` (auto cues only) | `why` |
|---|---|---|---|
| `scheduled` | the cue is accepted; `at_pts` is an estimate or null | `dmx_change`, `max_dwell`, `next` | — |
| `cancelled` | exit (§1.6, including a scene StartTransition retired by the render-thread guard), a newer cue took the pending slot, an `auto` PATCH cancelled a pending director move (§1.7), or the session ended (DELETE, TTL or failure) while the cue was pending: emitted before `session.ended` on DELETE or TTL, and right after `session.error` on failure | as on `scheduled` | `exit`, `replaced`, `auto_changed`, `ended` |
| `skipped` | the scene failed to prepare (§1.7 Prepare); the auto pool was empty, or held only the current scene (§1.7); or a `cue {to: "auto"}` found a manual cue pending (§1.6) | as on `scheduled` | `prepare_failed`, `empty_pool`, `no_candidate`, `manual_pending` |

- `reason` is always the auto trigger, on every phase, so a cancelled or skipped auto cue keeps it. `why` appears only on `cancelled` and `skipped`, for every cause, and says why the cue ended without starting.
- **Lifecycle.** Every accepted cue of cause `manual`, `patch` or `auto` emits `scheduled` and then ends in exactly one of: started (`session.transition{phase: "started"}` with the same `cue_id`), `cancelled` or `skipped`. go and exit emit only `session.transition`. A PATCH scene is accepted only once its scene is prepared (add §1.2): a prepare failure is reported by PATCH's error alone, and exit during its prepare emits only `cancelled`.
- `at` is the effective value. `fallback` appears only when one applied: `"no_beat"` means `at: "beat"` was asked for and ran as `now`. There is no `requested_at`.

**`session.warning`** (new):

```json
{"type": "session.warning", "session": "s-…", "screen": "south-screen", "code": "prepare_failed",
 "preset": "fractal-seahorse", "detail": "quarantined for 600 s: …"}
```

`code` is `prepare_failed` (with `preset`) or `empty_pool` (with `pool`: `"scenes"` or `"transitions"`).

**`session.inputs`** and **`session.palette`** (new) are in §1.9 and §1.11.

**Rules:**
- Consumers ignore unknown fields and enum values.
- No per-frame or per-beat events on `/api/events`.
- `session.inputs` at most once per 2 s, with hysteresis. `session.palette` only on an effective change, at most 1 Hz.
- `session.warning` fires once per distinct condition (code + preset or pool), at most once per 60 s per code. An empty pool is re-checked only when `direction`, `auto` or the catalog changes, or a quarantine ends.

### Add to §1.5 Renderer plug-ins

- **Cut invariant.** "The `image` source, which renders the anchor and every exit target (both `png_b64` and `rgb` stills, never `solid`), is always rendered at identity: no masters, palette, bindings or output filters (and any future tint). Direction, palette and bindings apply only inside non-internal scene `SourceLayer`s, before `TransitionLayer`. The frame at `hold_pts`, and every held frame after it, equals the decoded, letterboxed target pixels (or the solid rgb). Any future output filter must be identity on a static frame."
- **Exact transition ends.** "Every easing maps raw 0→0 and raw ≥1→1 exactly. The TransitionLayer renders `a` alone at raw progress ≤ 0 and `b` alone from raw progress 1 (the `hold_pts` frame). Every plug-in is also bit-exact at eased 0 and 1. In v1 every out transition (the session `out` on create, and any `transition` on `exit`) must resolve to the `crossfade` plug-in with `duration_s` ≥ 0.5 s, in any spec form (a preset name, `{"preset": …, "params": {…}}` or `{"transition": …, "params": {…}}`); otherwise 400 `bad_spec`. A `cut` out, and a crossfade out shorter than 0.5 s, are rejected." The session `out` is checked on create, never deferred to exit time: a 400 on exit would leave room_service only `live.abort`, an unmatched hard cut to art. An explicit `transition` on `exit` is checked on that request.
  - *Why a crossfade:* after a cut, or a zero-length crossfade, the `hold_pts` frame is the first target frame after a full-frame change. Once encoded and decoded it has not yet converged to the still, so the page would cut on a frame that doesn't match the art. A crossfade converges to the still before `hold_pts`, so `hold_pts` stays exact at progress 1 (LIVE_V1 §1.3's definition), without the review's noted 2-frame delay.
  - v1 offers `cut` and short crossfades as outs, but no mode uses them (`modes.yaml` uses `crossfade-3s`). Only out transitions are restricted: `cut` and the other transitions (New plug-ins, below) may still be used for the session `in` and for scene changes.
- **Tests** (R1, and the review's §2.2 noted item):
  - *Endpoint test,* for every registered transition plug-in: with random `a` and `b`, eased progress 0 must output `a` bit-identical, and eased progress 1 must output `b` bit-identical. Every easing must map raw 0 to 0 and raw ≥ 1 to 1 exactly.
  - *Exit parity,* for `crossfade`, the only out transition, at 0.5 s (the minimum) and 3 s: exit with auto running and a non-default direction and palette, then compare the `hold_pts` frame, and every held frame after it, with the decoded, letterboxed target pixels (or the solid `rgb`) before encoding.
  - *Post-decode exit parity,* for the same exits, on both GPUs: decode the encoded stream at the `hold_pts` frame and require mean |diff| ≤ 1/255 against the decoded, letterboxed target (or the solid `rgb`). Pre-encode parity can't see encoder residue; this test can. The addendum's end-to-end check measured 0.55/255.
  - *Entry parity,* for every transition allowed as `in`: create with a non-default direction and palette, auto enabled and a bound param. Every `holding` frame, and the first `in` frame (progress 0), must equal the decoded, letterboxed anchor pixels (or the solid `rgb`) before encoding.
- **Transition prepare.** "A transition compiles its kernels and loads its textures in `prepare()`, on a worker thread, before its `StartTransition` is queued. `setup()` on the render thread must not compile or allocate."
  - **Warm-up timing** (addendum A2; deviation from R20's "warms every registered transition at start", for 2f to accept): stream_service warms every registered transition at each GPU's first session, not at service start.
  - *Why:* warming at start would create a CUDA context on the RTX 4060 even when no session uses it. That GPU is the Windows desktop GPU, with about 1.5 GB free.
  - The first session on a GPU therefore pays the compile while it is `starting`, before `session.ready`. It never pays it during a live transition.
- **Scene clock.** A scene source receives `dt × speed` and a `t_local` integrated from it (§1.10). Transition progress always runs on real time.
- **Palette.** A source whose schema has `"x-palette": true` receives the session palette (§1.11). `image` never does.
- **New plug-ins** (rev 1.1):
  - source `warp_stack` (`x-palette`; colour-cycle rate `drift_speed`; `render_scale`);
  - transitions `melt`, `luma` (luma wipe), `wipe` (shapes `iris`, `clock` and `linear`) and `zoom`, ported from gl-transitions (MIT), alongside `cut` and `crossfade`;
  - transitions `burn`, `glitch` and `pixelate`, already registered. A.1.4 has them following later, but the registry auto-discovers every module in `plugins/transitions`, and `transitions.yaml` already tags them into the `all` pool. Rev 1.1 therefore lists them, and the endpoint test covers them.
  - Each transition takes the common `duration_s` and `easing`. Example presets, as in `transitions.yaml`: `melt-2s`, `luma-3s`, `wipe-iris`, `wipe-clock`, `wipe-soft`, `zoom-punch`, `burn-2s`, `glitch`, `pixelate-1.5s`.
- **Gates** (S7, recommended): before each slice ships, a 2-hour soak with auto at `dwell_s: [15, 30]` on both GPUs; late_frames ≤ 1 %, `mem_used_mb` flat, repeats ≈ 0.

---

## 1.6 Cue: the general scene-change verb

`POST /api/sessions/{id}/cue`

```json
{"to": "fractal-rabbit",
 "via": "luma-3s",
 "at": "beat",
 "source": "panel",
 "idempotency_key": "north-1697240000-7"}
```

**Fields:**
- `to` is one of:
  - a scene spec, exactly as on create/PATCH: a preset name, `{"preset": …, "params": {…}}` or `{"source": …, "params": {…}}`;
  - `"auto"`: the director picks now. This is the panel's "change now" button.
  - There is no still form. Exit is the only route to a still.
- `via` (optional) is a transition spec. Precedence: an explicit `via`, else the director's pick when auto is enabled, else the session `in` (today's PATCH rule). A one-shot `to: "auto"` counts as enabled for its own pick.
- `at` (optional) is `"now"` (the default) or `"beat"`, the follower's next predicted beat. `beat` starts the transition on the first rendered frame whose t_render + lead ≥ the next predicted beat, so the change lands on the beat on glass; `at_pts` is that frame's pts. When the follower is unlocked or off, `beat` runs as `now` and events carry `fallback: "no_beat"`. If the follower unlocks, or `beat_follow` is set to `off`, while a cue waits for its beat, the cue starts at once, and its `session.transition{phase: "started"}` carries `fallback: "no_beat"`.
- `source` (optional) is `panel` or `mode`: informational, set by room_service on the cue body, echoed on events. The renderer sets `director` on its own director's moves (`reason` `dmx_change` or `max_dwell`); room_service's art director never cues (§C.2).
- `idempotency_key` (optional) is per session and kept for the session's life. A replay returns the original 202 body. The same key with a different body returns 409 `idempotency_conflict`.

**Response:** `202`

```json
{"session": "s-3f9a12c4", "state": "live", "cue_id": "c-0009", "at_pts": null, "duration_s": 3.0}
```

- In this example `fractal-rabbit` is still preparing, so `at_pts` is null; the cue then waits for the next predicted beat. It is the summary's pending cue (add §1.2), and exit cancels it (§1.6 Exit pre-empts).
- `at_pts` is an estimate, or null while the scene is still preparing. The `pts` on `session.transition{phase: "started"}` is authoritative.
- The `via` choice is made when the cue is accepted; a director transition pick is drawn then, except when a `to: "auto"` takes the prepared pick, which uses the transition drawn with it (§1.7 Prepare).
- A `to: "auto"` that is skipped at acceptance (`manual_pending`, `empty_pool` or `no_candidate`) returns 202 with `at_pts: null, duration_s: null`, emits `scheduled` then `skipped`, and draws no scene or transition pick, so the seeded sequence (§1.7 RNG) is unaffected.
- "`duration_s` is resolved when the transition starts and is never rescaled mid-flight" (R7). The 202 `duration_s` is the value the chosen `via` resolves to at acceptance. For a cue that waits, it differs only if that transition preset is reloaded before the cue starts. go and exit start at once, so theirs is final (add §1.2).
- Transition durations are seconds only, never bindable or speed-scaled.

**From** is whatever is on screen when the cue starts, including a running transition, which nests (bounded below).

**State rules:**

| State | `cue` |
|---|---|
| `starting`, `holding` | 409 `bad_state` ("use go"). `PATCH scene` while holding still replaces the entry scene, as today. |
| `entering` | Accepted. It nests over the `in` transition. Entering still becomes live on the `in` transition's `done`, including when it is superseded, as today, but only while the session is still `entering`. An `in` done that arrives after exit has been accepted never changes state and never arms the director. |
| `live` | Accepted. A scene change; the state stays `live`. |
| `exiting`, `held`, `ended`, `failed` | 409 `bad_state`. |

**Exit pre-empts every pending cue:**
- "Accepting `exit` atomically cancels the pending cue, manual or auto, and any in-progress director prepare, and disarms the director for the rest of the session. Each cancelled cue emits `session.cue{phase: cancelled, why: exit}`."
- "On the render thread, once an `out` StartTransition has been applied, any later scene `StartTransition` is retired, never applied, for the rest of the session. This is a latch: it stays set after the out collapses at progress 1, through `held` and any re-exit."
- "Scene sources are prepared outside the session lock. State is re-checked under the lock before submitting, and exit never waits behind a scene prepare."
- A scene StartTransition retired by the render-thread guard ends as `session.cue{phase: cancelled, why: exit}` with its `cue_id`. Each cue emits this once, whether exit's cancel or the guard ends it.
- A PATCH scene still preparing when exit is accepted ends the same way, and PATCH returns 409 `bad_state` (add §1.2).

**Bounded nesting:**
- A cue is pending from acceptance until the render thread applies its StartTransition: while it is preparing, waiting for the cap, or waiting for its beat. A beat-timed StartTransition may be queued ahead with its start condition; it stays pending, and shows as the summary's `cue.pending`, until the render thread applies it. A session has one pending slot, and the replacement rules below apply to it.
- Every accepted cue ends in exactly one of started, cancelled or skipped (add §1.3).
- At most 2 scene transitions run at once (the `in` counts; a deviation from R6 for 2f to accept, changelog R6 row). A scene change beyond that waits in the pending slot. A waiting cue starts when any running scene transition reaches `done` (superseded included); its `at_pts` reflects the wait.
- **Rank follows `cause`** (R6, add §1.3). `manual` and `patch` cues rank as manual. Every `auto` cue ranks as auto: director moves (`dmx_change`, `max_dwell`) and `cue {to: "auto"}` (`next`).
- A newer manual cue (or PATCH scene) replaces any pending cue, and the replaced cue gets `session.cue{phase: cancelled, why: replaced}`.
- Replacement is by acceptance order. A PATCH scene is accepted when its prepare completes, so it replaces whatever is pending then, even a cue with a higher `cue_id`. A PATCH scene always has `at: "now"`. A PATCH scene received in `holding` whose prepare finishes after go is accepted becomes a cue (cause `patch`), like one received in `entering` (add §1.2).
- An auto cue never replaces a pending manual cue:
  - a `cue {to: "auto"}` that finds one is accepted (202) and ends at once as `session.cue{phase: skipped, why: manual_pending}`;
  - a director move that finds one is dropped silently and re-evaluated after that cue's transition is done.
- A newer auto cue replaces a pending auto cue (`why: replaced`).
- The director never cues while a transition it started is still running.
- Exit may nest over running scene transitions and is never delayed.
- A PATCH scene counts as a manual cue (cause `patch`) and resets dwell.
- room_service also limits cues to 1/s per screen (§C.2).

**Errors:**
- 400 `bad_request`, `bad_spec`, `unknown_preset`, `bad_at`, `bad_to`;
- 404 `not_found`;
- 409 `bad_state`, `auto_unavailable` (`to: "auto"` with no auto block), `idempotency_conflict`.

**Events:** `session.cue{phase: "scheduled"}`, then `session.transition` (add §1.3).

## 1.7 Auto: the renderer's director

Session field `auto`, on create and PATCH. The whole object is replaced; missing fields take the defaults shown. An absent block (or `null`) means disabled.

```json
"auto": {"enabled": true,
         "pool": "all",
         "transitions": "all",
         "dwell_s": null,
         "snap": "beat",
         "follow_dmx": ["effect", "variant"],
         "avoid_repeat": 2,
         "seed": null}
```

**Fields:**
- `enabled: false` keeps the block, so a one-shot `cue {to: "auto"}` still works, but the director never moves on its own.
- `pool` is one of:
  - `"all"`: presets that carry a `tags` block. Untagged presets are manual-only, so `black` and `cut` never join the mix.
  - a list of preset names;
  - `{"tags": {k: v}}`: scalar tags match by equality, list tags by overlap. Used for per-GPU exclusion, e.g. `{"tags": {"heavy": false}}` on south.
  - Tag filters consider only presets that carry a `tags` block; untagged presets are never in an auto pool. Among tagged presets, one without the key matches a `false` filter and nothing else. A name list may contain only tagged presets (400 `bad_spec` otherwise).
  - `transitions` takes the same forms.
- `dwell_s` is `[min, max]` in seconds. `null` derives it from `direction.change_rate`, log-linearly: `[120, 240] × 8^(−change_rate)`, so 0 → [120, 240], 0.5 → [42, 85], 1 → [15, 30]. An explicit value wins; it needs 0 < min ≤ max, otherwise 400 `bad_spec`. A derived `dwell_s` is evaluated continuously from the eased `change_rate`; GET session reports the current window.
- `snap` is `now` or `beat` (default `beat`). Director moves start at once (`now`) or at the next predicted beat (`beat`, timed as `at: "beat"` in §1.6). `beat` falls back to `now` with `fallback: "no_beat"`. `cue {to: "auto"}` takes `at` from the request (default `now`); `snap` applies only to director moves.
- `follow_dmx` lists the discrete fixture attributes (`effect`, `variant`) whose change counts as Maestro suggesting a shift. `[]` turns it off. This is an explicit value change on the bus, not inference. Whether Maestro changes these on musical sections, and how often, is a commissioning test item.
- `avoid_repeat` is the novelty window (below).
- `seed: null` means a random seed. The effective seed is reported in GET session.

**Arming:** "The director is armed only in `live` and disarmed in every other state. follow_dmx and max-dwell triggers seen while disarmed are discarded, not queued." After `exit` it stays disarmed for the rest of the session (§1.6).

**Dwell** is measured in seconds from the `done` of the transition that brought the current scene in (the `in` for the entry scene). An `auto` PATCH that turns the director on (`enabled` false → true, or a new block with `enabled: true`) restarts dwell from that PATCH, so a max dwell that expired while it was off doesn't fire at once. Other `auto` PATCHes leave dwell running.

**When the director moves:** it is armed and enabled, no transition it started is still running, dwell ≥ min, **and** either a latched follow_dmx change (`reason: dmx_change`) or dwell ≥ max (`max_dwell`).

**`cue {to: "auto"}`** (`reason: next`) ignores min dwell and follows the cue state rules (§1.6). It works whenever an auto block exists, even with `enabled: false` (a one-shot pick). With no block it returns 409 `auto_unavailable`.

**follow_dmx:**
- A change counts once the attribute has been stable for ≥ 300 ms and differs from the baseline: the attribute's value when the last scene transition (of any cause) started, or when the baseline was last re-taken (below), whichever is later. A scene transition that starts also clears any latched change.
- It is compared only between frames that are both fresh with `feed: "live"`.
- The first such frame after create or after recovery is a baseline, not a change.
- The baseline is also re-taken when the director arms (`entering` → `live`), so a change seen while disarmed never fires later.
- An `auto` PATCH re-takes the baseline and discards any latched change, so a change seen while auto was disabled (armed, but `enabled: false`) never fires when it is re-enabled.
- A change inside min dwell is latched and fires when min is reached.

**Pick:**
- Scene weight = `tags.weight × Π over direction keys (1 if matched, else 0.3) × novelty`, using the one matching rule (§1.8).
- `tags.weight` must be > 0 (default 1.0), so a non-excluded preset's weight is never 0. A preset whose `tags.weight` is ≤ 0 is listed in the catalog `errors` and treated as untagged, so it stays manual-only.
- Novelty excludes the last `avoid_repeat` scenes; if the pool is no bigger than that, it excludes only the current scene.
- `empty_pool` refers to the pool before novelty. An empty pool means no move, plus `session.warning{code: "empty_pool", pool: "scenes"}` (rate-capped, add §1.3); a `cue {to: "auto"}` is then `skipped` with `why: empty_pool`.
- If novelty alone leaves no candidate (the pool holds only the current scene), the director stays on the current scene without a warning, and a `cue {to: "auto"}` is `skipped` with `why: no_candidate`.
- If the transitions pool is empty, the transition falls back to the session `in`, with `session.warning{code: "empty_pool", pool: "transitions"}`.
- Transitions are weighted the same way. Their tags normally name `transition_style` and `energy`, matched against `direction.transition_style` and `direction.energy`, never a bus signal (e.g. `activity`, `activity_slow`).

**Prepare:** the next pick is chosen and prepared at least 2 s before min dwell, off the lock. A preset that fails to prepare is skipped and quarantined for 10 min, with `session.warning{code: "prepare_failed"}`.
- A `cue {to: "auto"}` without `via` takes the director's prepared pick (scene and transition) if one is ready and its scene is not the current scene, and the director then prepares a new one. Otherwise it draws and prepares a fresh pick. With `via`, it draws only the scene.
- Quarantine removes the preset from auto pools only; manual cues and PATCH still try it. Only an auto pick's failure (the director's own, or a `cue {to: "auto"}`'s) quarantines the preset.
- A failure of the director's ahead-of-time pick emits only `session.warning` (no cue exists yet), and the director picks again.
- A manual or `to: "auto"` cue whose prepare fails ends as `skipped, why: prepare_failed`, and also emits `session.warning{code: "prepare_failed"}`. A PATCH scene's prepare failure is reported by PATCH's error alone (add §1.2).

The pick is made against the direction at pick time; an `auto` PATCH discards an unused prepared pick. An `auto` PATCH also cancels a pending director move with `session.cue{phase: cancelled, why: auto_changed}`; a pending manual cue or `cue {to: "auto"}` is unaffected.

**RNG:** seeded, with separate streams for scene and transition picks. The effective seed is reported in GET session.

**Implementation note (recommended):** the director lives in a GPU-free module, `stream_service/director.py`, unit-tested at virtual time against show-synth JSONL. The same seed and the same recording must give an identical (scene, transition, at_pts) sequence.

**Events:** every auto cue emits `session.cue`, then `session.transition` if it starts, with `cause: "auto"` and its `reason`. Director moves also carry `source: "director"`.

## 1.8 Direction: the art-director input

Session field `direction`, on create and PATCH. It is room-level state in room_service (`look.direction`, §C.1), set by modes, the panel and the random director (later an LLM), and pushed by room_service to every live session. The renderer sees only the resolved object.

**Neutral defaults** (they reproduce today's behaviour):

```json
"direction": {"energy": 0.5, "pace": 0.5, "intensity": 1.0, "change_rate": 0.5,
              "moods": [], "genre": null, "transition_style": null, "palette_family": null,
              "source": null, "note": null}
```

**Example:**

```json
"direction": {"energy": 0.3, "pace": 0.25, "intensity": 0.6, "change_rate": 0.2,
              "moods": ["mellow", "warm"], "genre": "jazz",
              "transition_style": "gentle", "palette_family": "warm",
              "source": "panel", "note": "late-evening jazz, mellow"}
```

**Fields:**
- **Whole object.** "create and PATCH carry the complete object, and the renderer replaces it whole." A missing field takes its neutral default. `direction: null` on PATCH returns 400 `bad_request`; to reset, send the neutral object.
- Numbers are 0–1, clamped. Unknown direction keys are ignored and logged once per key, so room_service can add a descriptor without breaking pushes.
- `moods` is a list; `genre`, `transition_style` and `palette_family` are a string or null. Values come from `vocab`; an unknown value is kept, matches nothing, and is logged.
- `source` is `panel`, `mode`, `random` or `llm`: informational, filled in by room_service. `note` is free text for the panel.

**Effects:**

| Key | Effect |
|---|---|
| `energy`, `moods`, `genre`, `transition_style`, `palette_family` | tag matching for scene and transition picks (§1.7) |
| `pace` | the `speed` master: `2^(2·pace − 1)`, so 0 → 0.5×, 0.5 → 1×, 1 → 2×; applied as the scene clock (§1.10) |
| `intensity` | "`direction.intensity` (0–1, default 1) is modulation depth. It scales every binding's swing about its rest value (§1.9): 0 means bindings are flat, 1 means their full range." No luminance master or output-level gain exists in v1. Scene-internal effect params such as fractal_zoom's `exposure` (and warp_stack's `gain`) are scene effects, never applied to stills (add §1.5); this narrows R2's wording (changelog R2 row). |
| `change_rate` | the default `dwell_s` (§1.7) |

The renderer uses `palette_family` only for tag matching. Drift within a family runs in room_service (§1.11, §C.1).

**Ease:** values on create apply at once. Later numeric changes ease over `direction_ease_s` (a stream_service setting, default 8 s). List and string keys take effect at the next pick.

**States:** accepted in every state except `ended` and `failed`; stored, and applied to scene-side rendering only (add §1.2).

**One matching rule:** "A preset tag key is spelt exactly like the direction key it matches: `energy`, `moods`, `genre`, `transition_style`, `palette_family`. A number matches a `[lo, hi]` tag; a list matches on overlap; a key the preset doesn't tag counts as matched."
- A scalar string tag matches by equality; a list tag by membership.
- `genre`, `transition_style` and `palette_family` are matched like `moods`: the direction's string matches a list tag that contains it, or a scalar tag equal to it.
- A direction key that is null or an empty list counts as matched.
- `weight` and other tag keys (e.g. `heavy`) are not direction keys. `weight` scales the pick (§1.7); the others serve pool filters.

**Tags on presets** (`config/live/scenes.yaml`, `transitions.yaml`):

```yaml
fractal-dendrite:
  label: Fractal zoom (dendrite, neon)
  source: fractal_zoom
  params: {preset: dendrite, palette: neon, palette_period: 8, zoom_rate: 0.3}
  tags: {energy: [0.3, 0.9], moods: [bright, euphoric, tense], weight: 1.0}

fractal-seahorse:
  label: Fractal zoom (seahorse, mono; heavy at 4K60 on the P4000)
  source: fractal_zoom
  params: {preset: seahorse, palette: mono, palette_period: 10, zoom_rate: 0.2, iterations: 160}
  tags: {energy: [0.0, 0.5], moods: [dreamy, mellow], heavy: true, weight: 0.7}

melt-2s:
  label: Melt 2 s
  transition: melt
  params: {duration_s: 2.0}
  tags: {transition_style: [lively, wild], energy: [0.4, 1.0], weight: 1.0}
```

`black` and `cut` stay untagged. Today's `transitions.yaml` still tags `cut` and spells the key `style`; before ship it drops the `cut` tags and renames `style` to `transition_style` (R12).

**Vocab:** `config/live/vocab.yaml`

```yaml
moods: [mellow, warm, bright, euphoric, tense, dreamy, dark, calm, hypnotic, playful]
genre: [jazz, pop, techno, ambient, house]
transition_style: [gentle, lively, wild]
palette_family: [warm, cool, vivid, neutral]
```

- `palette_family` uses the families in `stream_service/palettes.py`, so no family shares a name with a palette (`neon`, `mono`).
- `moods` adds `calm`, `hypnotic` and `playful`, which today's `scenes.yaml` and `palettes.py` tags use. `cool` is a palette family, not a mood: presets and palettes tagged with the mood `cool` are retagged before ship.
- Hot-reloaded, served in the catalog as `vocab`, and read by room_service's look adapter.
- An unknown tag value logs a warning and is listed in the catalog's `errors`; the preset still loads.

## 1.9 Inputs: show bus, signals, beat follower and bindings

**Subscription:**
- `--show-bus ws://127.0.0.1:8701/v1/show --show-bus-token-file ~/.config/screen-machine/show_bus.token` (once room_service serves it).
- In dev: `--show-bus ws://127.0.0.1:8711/v1/show --show-bus-token-file ~/.config/screen-machine/show_bus.token` (show-synth, Appendix S). Omit `--show-bus-token-file` when show-synth runs without a token (`--no-auth`, or no `show_bus.token` file).
- The token goes in K3 `hello{v, role: "renderer", client_id, token}`; the role is read-only. A mismatch closes with 4401, and the renderer then re-reads the token file once.
- **The renderer never parses Art-Net.** room_service owns the Art-Net ingest and the fixture patch.
- Losing the bus never changes session state.

**Bus delivery: K3 as amended.** 2f writes it into LIVE_V1 §1.9a; the renderer side is restated here.
- Frames are forwarded on arrival. When Art-Net is quiet, the bus repeats the latest frame at least every 100 ms, with `input.age_ms`.
- The `renderer` role is not coalesced: a bounded queue of 64, then disconnect. Frames carry `seq`. UI roles stay latest-value.
- K3's top-level `look` is renamed `feed`. Its values `live`, `hold`, `safe` and `black` are defined here for 2f's K3 text.
- The renderer reads `seq`, `t_ms`, `feed`, `input`, `global.activity` and its own fixture. It ignores the `mode`, `safety` and `hue` blocks.

```json
{"v": 1, "type": "show.state", "seq": 118233, "t_ms": 81234567.8, "feed": "live",
 "input": {"kind": "artnet", "online": true, "age_ms": 23, "pps": 40.1},
 "global": {"activity": 0.42},
 "fixtures": {"screen.north": {"rgb": [0.25, 0.45, 0.9], "level": 0.9, "pulse": 0.4, "pan": 0.2, "tilt": -0.1,
                               "zoom": 0.55, "effect": 2, "variant": 0}}}
```

**Clock:** "`t_ms` is CLOCK_MONOTONIC in ms, stamped by room_service when the Art-Net packet arrives (show-synth: when it produces the frame). The renderer never compares it directly with another clock. It maps it with `off = time.monotonic() − pace_clock()`, sampled each frame, and the follower uses `t_ms` intervals."

**Staleness.** Two conditions, either of which holds bindings for 250 ms and then eases signals to their idle values over 2 s:
- link down: no frame for 500 ms, or the socket closed;
- input stale: `input.online` false, or `feed` not `live`.

Recovery needs 1 s of fresh frames, then a 1 s ease back.

**The session's fixture:**
- `"fixture": "screen.north"` on create.
- The default is derived from `screen`: `north-screen` → `screen.north`, `south-screen` → `screen.south`, `devtest` → `screen.north`.

**Signals** (all scalar 0–1 unless noted):

| Signal | Kind | Idle | Bindable | From |
|---|---|---|---|---|
| `pulse` | trigger | 0 | yes | fixture attribute |
| `level`, `zoom` | level | 0.5 | yes | fixture attributes |
| `pan`, `tilt` | level, mapped `(x + 1)/2` from −1…1 | 0.5 | yes | fixture attributes |
| `activity` | level | 0.5 | yes | bus `global.activity` |
| `level_slow`, `activity_slow` | level; one-pole smoothing, τ ≈ 2 s | 0.5 | yes | renderer: smoothing only |
| `beat` | trigger: 1 at each predicted beat, then decays | 0 | yes | beat follower; the raw pulse-edge envelope when unlocked or off |
| `beat_phase` | phase: 0–1 ramp | hold | yes | beat follower |
| `effect`, `variant` | discrete (DMX value) | — | no; follow_dmx only | fixture attributes |
| `rgb` | colour | — | no; monitor only | fixture attribute |
| `bpm`, `beat_confidence` | report | — | no; monitor only | beat follower |

The catalog's `signals` block lists the bindable ones:

```json
"signals": {"pulse": {"kind": "trigger", "idle": 0}, "beat": {"kind": "trigger", "idle": 0},
            "beat_phase": {"kind": "phase", "idle": "hold"},
            "level": {"kind": "level", "idle": 0.5}, "zoom": {"kind": "level", "idle": 0.5},
            "pan": {"kind": "level", "idle": 0.5}, "tilt": {"kind": "level", "idle": 0.5},
            "activity": {"kind": "level", "idle": 0.5},
            "level_slow": {"kind": "level", "idle": 0.5}, "activity_slow": {"kind": "level", "idle": 0.5}}
```

**The renderer does no inference beyond the beat follower** (owner ruling A.1.6): no onset detection beyond the follower's edge detector, and no section, bar or downbeat inference.

**Beat follower:** a metronome follower, not a beat tracker.
- **Edges:** rising edges of `pulse` (or `level`, configurable) through a Schmitt trigger. Refractory period: 0.35·P when locked, 150 ms when unlocked.
  - The thresholds are relative to a decaying peak, not absolute (addendum A1): rise at ≥ 0.6 × the recent pulse peak, re-arm below 0.3 × the peak. The peak decays with a time constant of about 2 s. The ratios are configurable.
  - *Why:* with absolute thresholds of 0.6 / 0.3, the synth's snares (≈ 0.4–0.5 × the accent) were missed, so pop locked at half tempo. Maestro's pulse depth will vary by show and fixture, so absolute levels are fragile.
- **Period** P: take the last 8 accepted inter-beat intervals, accepting only intervals within 270–2000 ms (30–220 bpm), measured on `t_ms`. P is the mean of those within ±12 % of their median (addendum A1).
  - *Why:* bus frames arrive at Art-Net cadence (~40 Hz, 25 ms quantisation), and the plain median of quantised intervals read 126 bpm for a 124 bpm song. Averaging the inliers removes that bias. Robustness to outliers is the same, because the median still selects the inliers.
- **Next beat** = last beat + P.
- **`beat_confidence`** = the share of the last 8 edges within ±12 % of the grid (`last_beat + kP`), so one missed pulse doesn't zero it.
- **Lock** at confidence ≥ 0.7 with at least 4 accepted edges. **Unlock** below 0.6, or when no edge has been accepted for max(2.5·P, 1.5 s).
- "When unlocked or `off`, `beat` is the raw pulse-edge envelope, landing `lead` late; `beat_phase` is held and `bpm` is null; `at: beat` falls back to `now`."
- Session field `"beat_follow": "extrapolate" | "off"`, default `extrapolate`.
- *Recommended:* fold intervals (`k = round(I/P)`, k ≤ 4, accept if `|I − kP| ≤ 0.12P`) and smooth the phase (`beat_t ← predicted + 0.25·err`). Acceptance against the synth: lock within 8 beats; phase p95 ≤ 15 ms before lead; unlock ≤ 2 s after the pulse stops; no lock in 60 s of random edges. On glass, with `latency_cal`: p95 ≤ 40 ms.

**Render-ahead:**
- "`lead_ms` = server_ms (measured per frame) + the glass client's `buffered_ms` (an EMA of the media-socket stats, which `media.py` already keeps) + 1 frame + presented→photon from `config/room/latency.json` (north uses south's value until measured, flagged) + `maestro_latency_ms` (default 0) + `trim_ms` (±100)."
- Maestro reacts after the audio, so its latency adds to the lead. `maestro_latency_ms` and `trim_ms` are stream_service settings (`--maestro-latency-ms`, `--lead-trim-ms`), service-wide. In v1 they are CLI-only: no API sets them, and per-screen trims are deferred (B, Not in v1).
- Recomputed at 1 Hz; it slews at most 20 ms/s, except after the glass client's catch-up jump (it skips ahead when buffered > 2 × `target_buffer_ms` for 2 s), when it snaps to the new value.
- The follower's `beat` and `beat_phase` are evaluated at `t_render + lead`, so steady beats land on time.
- Raw levels and one-off hits can't be predicted. They land about `lead` late (expected 0.5–0.8 s in production; to be measured, §D.1), so bind them to slow-decaying effects.
- The breakdown is reported in `/inputs`.
- **Photon source.** `photon_source` is the screen id whose measurement supplies `photon_ms` (e.g. `south`), or `default` when `config/room/latency.json` is missing or unusable; `photon_ms` is then the built-in default (178.7 ms, south's 13 Oct value). `photon_measured` is `false` when the value is borrowed from another screen or is the default. North uses south's value, `photon_measured: false`, until north is measured. `measured` is `false` while `server_ms` and `buffered_ms` are still the initial estimates (3 frames and the profile's `target_buffer_ms`).
- *Status in the WIP* (addendum A3): the lead is computed and slewed as specified, from server_ms + the client's `buffered_ms` EMA + 1 frame + presented→photon from `config/room/latency.json`. The WIP fixes `trim_ms` and `maestro_latency_ms` at 0; the `--lead-trim-ms` and `--maestro-latency-ms` settings are not built yet. The WIP still writes `photon_source` as `"latency.json:south (no north measurement)"` (or `"default"`), has no `photon_measured`, and names `present_ms` `frame_ms`; it is aligned with this text before ship.

**Bindings:** in scene presets' and scene specs' `params` and in `PATCH params`, a numeric parameter marked `"x-bindable": true` can take a binding instead of a value. Transition params are never bindable. For example:

```json
"params": {
  "zoom_rate":     {"bind": "activity_slow", "range": [0.1, 0.5]},
  "exposure":      {"bind": "beat", "range": [1.0, 1.3]},
  "palette_drift": {"bind": "level_slow", "range": [0.0, 0.2], "rest": 0.02}
}
```

- A v1 binding is `{bind, range: [lo, hi], rest?}`.
- A binding on a param not marked `x-bindable`, or to an unknown or non-bindable signal (`effect`, `variant`, `rgb`, `bpm`, `beat_confidence`), returns 400 `bad_spec` on create, PATCH or cue. In a preset file it is a catalog error, and the preset is listed in `errors`.
- Value = `rest + depth · (lo + (hi − lo)·signal − rest)`, where depth = `direction.intensity` (§1.8). `rest` defaults to `lo` for trigger signals (`pulse`, `beat`) and to the midpoint otherwise. The result is clamped to the parameter's schema range.
- Bound values are written each frame and bypass the PATCH easer. Smoothing comes only from the `*_slow` signals.
- When the bus is absent or stale, each signal eases to its idle value (table above) on the staleness schedule. A binding with an explicit `rest` eases to `rest` on the same schedule (a 250 ms hold, then 2 s), and eases back over 1 s on recovery; otherwise it follows its signal's eased idle value. With the default rests this also yields `rest` for trigger and level signals; a `beat_phase` binding holds its last value. With no bus from session start, bindings start at these values.
- A binding is a value at whichever layer set it (§1.10).
- **Demo params** (`fractal_zoom`): `zoom_rate`, `palette_drift` (colour-cycle rate), and two effect params, all marked `"x-bindable": true`. These are the names and ranges the WIP's `fractal_zoom` and `config/live/scenes.yaml` use (rev 1 called them `exposure_kick` and `cycle_kick`):
  - `exposure` (number, 0–3×, default 1.0 = identity): R19's exposure pulse, a multiplier inside the fractal's colouring. The demo binds it `{"bind": "beat", "range": [1.0, 1.3]}`, so it rests at 1.0 and lifts on each beat; `intensity` 0 keeps it at 1.0. It is a scene effect, not a master, and is never applied to stills.
  - `palette_shift` (number, 0–1, default 0, in palette cycles): R19's colour-cycle kick, a phase offset added to the `palette_drift` phase. The demo binds it `{"bind": "beat", "range": [0.0, 0.06]}`.
  - `warp_stack` marks its own rate and level params bindable (e.g. `drift_speed`, `fb_amount`, `fb_zoom`, its `energy` macro, and `gain`, a 0–3× scene-internal level like `exposure`).

**Inputs monitor:** `GET /api/sessions/{id}/inputs` →

```json
{"online": true, "stale": null, "age_ms": 18, "feed": "live", "fixture": "screen.north",
 "values": {"rgb": [0.25, 0.45, 0.9], "level": 0.9, "pulse": 0.4, "pan": 0.2, "tilt": -0.1, "zoom": 0.55,
            "effect": 2, "variant": 0, "activity": 0.42},
 "derived": {"level_slow": 0.71, "activity_slow": 0.44, "beat": 0.12, "beat_phase": 0.37,
             "bpm": 123.8, "beat_confidence": 0.91, "beat_locked": true, "beat_follow": "extrapolate"},
 "bindings": [{"param": "zoom_rate", "bind": "activity_slow", "range": [0.1, 0.5], "rest": 0.3, "value": 0.28}],
 "masters": {"speed": 1.0, "depth": 0.8},
 "lead": {"total_ms": 572, "server_ms": 35, "buffered_ms": 340, "present_ms": 17, "photon_ms": 180,
          "maestro_ms": 0, "trim_ms": 0, "photon_source": "south", "photon_measured": false, "measured": true}}
```

- `values` are raw bus values (pan and tilt −1…1). `stale` is null, `link` or `input`.
- `photon_source` is the screen id whose measurement supplies `photon_ms`, or `default` (Render-ahead, above). On north it reads `south`, with `photon_measured: false`, until north is measured. `measured` is `false` while `server_ms` and `buffered_ms` are still estimates.
- room_service passes this through at `GET /api/v1/screens/{id}/inputs` (§C.3).

**Event:**

```json
{"type": "session.inputs", "session": "s-…", "screen": "north-screen", "online": true, "stale": null, "beat_locked": true}
```

It fires when the link goes up or down, the input goes stale or recovers, or the follower locks or unlocks; at most once per 2 s, with hysteresis.

## 1.10 Parameter stack and derived masters

**Stack:** "plug-in default < scene preset params < session params (`PATCH params`). Session params keep today's meaning: they merge into the current scene and are cleared when the scene changes." Only room_service writes session params.

**Bindings:** "A binding is a value at whichever layer set it. `PATCH params {k: value}` on a bound parameter replaces the binding for the current scene, and `{k: null}` restores the preset's value or binding."

**Masters, derived only:**
- **speed** = 0.5×–2×, exponential in `direction.pace` (§1.8). It is applied as the scene's clock: the scene source receives `dt × speed` and a `t_local` integrated from it. It never applies to transition progress.
- **depth** = `direction.intensity` (§1.8).
- Both are reported read-only in `/inputs` and GET session.
- There is no override layer, no `release`, no `PATCH masters` and no luminance master.

## 1.11 Palette: a session-level axis

**On create and PATCH:**

```json
"palette": "ember",
"palette_transition_s": 4.0
```

- `palette` is a palette preset name; an unknown name returns 400 `unknown_preset`. Once set, it can be changed but not cleared in v1: `palette: null` on PATCH returns 400 `bad_request` (add §1.2).
- Drift is computed in room_service's director (§C.1), which sends concrete names, so both screens, and later Hue, agree.
- `palette_transition_s` defaults to 4.0.

**Rendering:** "The session owns the eased palette and hands it to every source that declares `x-palette: true`, as cosine coefficients now or a LUT for stops. There is no separate scalar-field pass. A palette change crossfades over `palette_transition_s`."
- In v1 `fractal_zoom` and `warp_stack` declare `x-palette`; both already use the shared cosine palettes. `image` never receives a palette (add §1.5).
- During a crossfade the source receives a LUT, blended in OKLab (recommended).

**Compatibility:** "Until a session `palette` is set, each scene keeps its own palette params exactly as today." Once it is set, the `palette` param of every `x-palette` source is ignored, with a log line. `palette_period` (index scaling, not colour) and the colour-cycle rate (`palette_drift` on `fractal_zoom`, `drift_speed` on `warp_stack`) stay scene params.

**Palette presets:**
- The cosine palettes in `stream_service/palettes.py` stay in code: `ember`, `gold`, `dusk`, `ice`, `ocean`, `aurora`, `neon`, `rainbow`, `acid` and `mono`.
- `config/live/palettes.yaml` (hot-reloaded) holds the owner format, hex `stops`, plus labels and tags for any palette.
- A `palettes.yaml` entry with a built-in's name may carry only `label` and `tags`. `stops` on a built-in name is a catalog error (listed in `errors`), and that entry is ignored.

```yaml
sunset:
  label: Sunset
  stops: [[0.0, "#0b0b2e"], [0.4, "#6a2c70"], [0.7, "#f08a5d"], [1.0, "#f9ed69"]]
  tags: {palette_family: warm, moods: [dreamy]}

ember:            # built-in: label and tags only
  label: Ember
  tags: {palette_family: warm, moods: [mellow, warm]}
```

**Catalog:**

```json
"palettes": {"ember": {"label": "Ember", "kind": "cosine",
                       "cosine": {"a": [0.5, 0.28, 0.12], "b": [0.5, 0.3, 0.14], "c": [1, 1, 1], "d": [0, 0.06, 0.14]},
                       "swatch": ["#ff8f35", "#a7430b", "#180000", "#181e1c", "#a77840"],
                       "tags": {"palette_family": "warm", "moods": ["mellow", "warm"]}},
             "sunset": {"label": "Sunset", "kind": "stops",
                       "stops": [[0.0, "#0b0b2e"], [0.4, "#6a2c70"], [0.7, "#f08a5d"], [1.0, "#f9ed69"]],
                       "swatch": ["#0b0b2e", "#452056", "#944d70", "#f39b5f", "#f9ed69"],
                       "tags": {"palette_family": "warm", "moods": ["dreamy"]}}}
```

- `swatch` is computed, never hand-written: 5 samples at t = k/4 for stops (interpolated in OKLab) and t = k/5 for cosine palettes, which are cyclic.
- Palette tags use the direction key names (`palette_family`, `moods`). room_service's drift reads `palette_family`.

**Event:** `session.palette` fires on an effective change only, at most 1 Hz:

```json
{"type": "session.palette", "session": "s-…", "screen": "north-screen", "palette": "ember",
 "swatch": ["#ff8f35", "#a7430b", "#180000", "#181e1c", "#a77840"]}
```

---

## B. Compatibility

1. Palette behaviour is as in §1.11: until a session `palette` is set, scenes keep their own palette params exactly as today. Once it is set, every `x-palette` source (`fractal_zoom`, `warp_stack`) uses it instead of its `palette` param.
2. `GET /api/catalog` gains `api: {contract: "LIVE_V1", rev: "1.1", features: ["cue", "auto", "direction", "palette", "inputs", "bindings"]}`. room_service sends a new field only when its feature is listed. (Today's stream_service rejects unknown fields with 400.)
3. Consumers ignore unknown fields and enum values.
4. With no bus, signals take their idle values (§1.9), bindings with an explicit `rest` ease to it, and `at: beat` falls back to `now`.
5. One resource-guard change, documented in the LIVE_V1 changelog: a third rapid scene change now waits instead of nesting (§1.6). PATCH scene in `entering` or `live` is now a manual cue (add §1.2). It prepares outside the lock and returns 200 once its scene is prepared, as today. A PATCH scene that waits in the pending slot can be replaced by a newer cue or PATCH, or cancelled by exit; it then emits `session.cue{phase: cancelled}` and never starts.
6. New validation: out transitions must resolve to `crossfade` with `duration_s` ≥ 0.5 s (add §1.5); a `cut` out or a shorter crossfade out returns 400 `bad_spec`. It is checked on create (the session `out`) and on exit's own `transition`. v1 offers both rejected forms, but no mode uses them (`modes.yaml` uses `crossfade-3s`), and room_service exits only through the mode's `out`. room_service also checks it when it loads modes (§C.4). It is a deviation from R3 and R21.5, for 2f to accept (changelog, "§2.2 noted" row).
7. Exit in `held` is today's behaviour, written down (add §1.2): it re-runs the out transition to the new target and emits a new `session.held` with a new `hold_pts`. LIVE_V1 §1.2's text gains `held` in exit's allowed states.

**Not in v1 (deferred):** cue to a still; bars and downbeat; `override`/`release` and `PATCH masters`; any luminance master; `palette_tint`, `follow: maestro`, renderer-side drift; per-screen trims; binding kinds, `curve`, `smooth_ms`, `amount` and the Room page binding pickers; a per-GPU cost table (replaces `heavy`); `auto.link`, `sync_to`, `scene: "auto"` on create; the LLM channel; the flash limiter; room_service's 8701 show bus and Art-Net ingest (2f; until the MaestroDMX is configured; the demo uses show-synth on 8711); the new Room page widgets (`multiselect`, `range`, `palette`, `binding`); S6 decision debug payloads.

## C. Decided

2f's answers (review §3), in brief. 2f writes the room_service, ROOM_STATE_MODEL and LIVE_V1 §0, §1.9a, §3 and §4 text, and fixes the AV_PROGRAMME leftovers (§4.4a, §4.2, §4.4; review §5).

**C.1 Direction scope: room-level `look.*` targets in room_service.** The renderer sees only resolved values.
- A new in-process `look` adapter holds three targets. The adapter is kind `level` and holds the value (like `sensor_mask`/MaskPusher); `read()` returns the held canonical value.
  - `look.direction`: the complete canonical object; `normalize()` fills defaults and validates against `config/live/vocab.yaml`;
  - `look.palette`: a preset name, or `{"drift": {"family"?: f, "period_s": 180}}`; with no `family`, drift follows `look.direction.palette_family`;
  - `look.director`: `"fixed"`, or `{"random": {"spread": 0.15, "every_s": [10, 30], "seed"?: n}}`; later `{"llm": {"stale_after_s": 90}}`.
  - Entry forms follow ROOM_STATE_MODEL §2.4: a bare value (any JSON value without the verb keys `store`, `set` or `retrieve`) is shorthand for `{"set": value}`, and an object is an operator entry only through those keys. So `"look.palette": {"drift": {…}}` and `"look.palette": {"set": {"drift": {…}}}` mean the same.
- Policy: `respect`, the default for `set`: a hand change records drift until the next mode request. Modes set the initial values. `house`/`normal` leaves `look.*` unmanaged, so a hand-picked palette carries across modes (the lasting axis). A mode that borrows a palette uses `store`/`set`/`retrieve`, e.g. disco `"look.palette": {"store": "pre_disco", "set": "ember"}`, normal `"look.palette": {"retrieve": "pre_disco"}`.
- Retrieving an unset stored palette leaves `look.palette` unset; running sessions keep their current palette until they end, and new sessions use scene palettes (proposed by 04; the renderer cannot clear a session palette, §1.11).
- The random director runs in room_service as a runtime overlay, never `set_target`: effective direction = director(`look.direction`, t), plus later `look.trim.<screen>`. Palette drift is computed there too, as concrete preset names.
- room_service sends the full effective `direction` and `palette` on create, and PATCHes them on change, coalesced to at most 1/s per session.
- A 404 or 409 from a session being torn down is a no-op. A look push is never a gated mode step and never takes part in `on_fail: abort`.
- Later, the LLM writes to a keyed machine channel (like §2.2b sensor ingest), accepted only while `look.director` is `llm`.
- Room-wide only in v1. `look.trim.<screen>` (numeric deltas, clamped 0–1) is reserved.

**C.2 Only room_service calls `cue`.** The stream API is token-gated; browsers never reach it.
- Triggers: the Room page's "change now" through operate-open `POST /api/v1/screens/{id}/cue {to: scene|"auto", via?, at?}` and `POST /api/v1/cue {screens: [ids]|"all", to, via?, at?}`; and a live→live mode hop whose new spec has `auto.enabled` (PATCH `auto`, then cue `"auto"` with `source: mode`).
- The art director (random, later LLM) speaks direction only; the renderer's director cues internally.
- Room route guards: 400 `bad_to` for anything but a scene or `"auto"`; 409 `not_live` unless the controller phase is `live` with no handshake running; 429 `rate_limited` above 1 cue/s per screen.
- room_service adds an `idempotency_key` per tap and `source: panel|mode`.

**C.3 A separate show-bus token.**
- `~/.config/screen-machine/show_bus.token` (64 hex, 0600), created by room_service on first start.
- Sent as K3 `hello.token` with `role: renderer`, read-only. The bus binds `127.0.0.1:8701` only and closes with 4401 on a mismatch.
- stream_service takes `--show-bus-token-file`. show-synth uses the same file if it exists, otherwise `--no-auth` (still localhost only).
- `room_service.token` is never reused: it is the admin token, and tokens aren't scoped.
- The Room page never holds a bus token. It reads `GET /api/v1/screens/{id}/inputs`, an operate-open passthrough of §1.9's monitor, at ≤ 4 Hz.

**C.4 Modes in `config/room/modes.json`** (`modes.yaml` is the migration fallback only).
- `auto` (and optionally `fixture` and `beat_follow`) goes inside each `screens.<id>` live spec. `look.direction`, `look.palette` and `look.director` are room targets.
- Under `auto.enabled`, the live spec's `scene` is the entry scene only, and its `params` apply to that scene only. The screens adapter's `read()` and compare use the requested spec (entry scene plus auto); the renderer's current scene goes to status `observed.scene` and is never drift and never enforced.
- Example (south is the same with `"pool": {"tags": {"heavy": false}}`):

  ```json
  "live-demo": {"label": "Live demo", "extends": "live", "state": {
    "screens.north-screen": {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"},
                                      "auto": {"enabled": true, "pool": "all", "transitions": "all",
                                               "follow_dmx": ["effect", "variant"], "avoid_repeat": 2}}},
    "look.direction": {"energy": 0.6, "pace": 0.5, "intensity": 0.8, "change_rate": 0.5, "moods": ["bright"],
                       "genre": null, "transition_style": "lively", "palette_family": "warm"},
    "look.director": {"random": {"spread": 0.15, "every_s": [10, 30]}},
    "look.palette": {"set": {"drift": {"period_s": 180}}}}}
  ```

  A plain `set` lets the palette carry over when the room returns to `normal`, which leaves `look.*` unmanaged; to give the old palette back on exit, use `store` here and `retrieve` in `normal`.

- Checked at load: preset, palette and vocab names exist, and each screen's pool matches at least one tagged preset.
- Also checked at load: each live spec's `transitions.out` resolves to `crossfade` with `duration_s` ≥ 0.5 s (add §1.5), so a bad out fails when modes load, not at the cut (proposed by 04; 2f to confirm).
- Auth: editing `modes.json`, `vocab.yaml`, tags or `palettes.yaml` is admin-gated; choosing a mode, direction, palette or director, toggling auto and "change now" are operating, so open.

**Also decided (review §2.3):**
- Panel direction writes are `set_target` hand changes, coalesced to ≤ 1/s by the page. A 409 during a mode switch is fine; the slider retries.
- Until room_service has the random director, 04 may use a dev script that PATCHes sessions directly.
- `look.*` keeps its name; K3's field becomes `feed`.
- Both TVs change independently by default (§D.2).

## D. Owner open items

1. **Late one-off hits.** In production, one-off hits (raw pulses, effect changes, an unlocked beat) will probably land 0.5–0.8 s late rather than 0.3 s, because the player buffers 300–600 ms (to be measured). Steady beats are compensated. Is that acceptable?
2. **Both TVs together or independently?** Each screen's director picks on its own. Default: independent. Changing together would be `auto.link: "room"` later.
3. **What intensity means.** Intensity is how hard the visuals respond to the music (modulation depth), not brightness. Live stays at full brightness; `intensity` only sets how far bindings swing. Scenes can still lift their own exposure on the beat (the demo's fractals pulse `exposure` from 1.0× to about 1.3×), and `intensity` 0 makes that flat too. Please confirm.

---

## Appendix S. show-synth (informative)

A development stand-in for Maestro and room_service's bus. It is a dev tool, not a contract; it may move to the stream_service README.

`python -m stream_service.tools.show_synth --port 8711 --song pop --bpm 124 --seed 7 [--record truth.jsonl]`

- It publishes K3-as-amended frames (LIVE_V1 §1.9a) on `ws://127.0.0.1:8711/v1/show` at Art-Net cadence (40 Hz, jittered ±3 ms). It binds 127.0.0.1 only. Port 8711 is dev only, registered by screen-machine-ca; 8701 stays room_service's.
- It uses `~/.config/screen-machine/show_bus.token` if present, otherwise `--no-auth`.
- A hello whose token doesn't match closes with 4401, as on room_service's bus (§C.3), so the renderer's re-read applies in dev too. The WIP's 4403 is aligned before ship.
- It stamps `t_ms` like room_service: CLOCK_MONOTONIC ms at the moment each frame is produced.
- **Song structure:** a list of sections, e.g. intro 8 bars, verse 16, chorus 16, break 8, drop 16, outro 8.
  - Each section has an energy level, a colour set and an effect/variant.
  - `pulse` and `level` hit on beats, with section-dependent accents (kick on 1 and 3, a fill every 8 bars).
  - `activity` follows the section energy, with noise.
  - Sections reach the renderer only as effect/variant/activity values. The truth record's section boundaries are used only to score follow_dmx → cue latency.
  - Bars exist only inside the synth's song model. No bar or downbeat position is published on the bus. The truth record's section lines and the control API's status carry the synth's bar count for scoring and display only; the renderer never sees them.
- **Styles:** `--song pop|techno|jazz|ambient`, all four built (start tempos 124, 132, 96 and 70 bpm; each has its own kick and snare pattern, accent depth, pulse decay and section plan). `--bpm` (40–200) overrides the start tempo; the tempo can then be changed while it runs (Runtime control, below).
- **Truth record:** `--record truth.jsonl` writes one JSON line per event, `{t_ms, event: beat|section|control, …}`, so the follower's lock, timing and re-lock can be scored:
  - `{t_ms, event: "beat", beat}` at each beat's exact time, tempo changes included (a beat's time is the inverse of the integrated tempo);
  - `{t_ms, event: "section", song, section, section_index, section_start_bar, bar, beat}` at each section start;
  - `{t_ms, event: "control", …}` for each applied control change, with the fields that were applied (e.g. `bpm`, `ramp`, `song` and `at_bar`, `pulses`, `next_section`).
- **Runtime control (dev only).** This is how the tempo is varied while the demo runs. It is served on the bus port.
  - `GET http://127.0.0.1:8711/v1/control` returns `{song, bpm, target_bpm, ramp, pulses, section, section_index, bar, bar_in_section, section_bars, beat, pending_song, songs, bpm_range}`. `bpm` is the tempo now (mid-ramp included) and `target_bpm` the tempo after any ramp. `ramp` is `{from_bpm, to_bpm, over_s, remaining_s}` or null, and `pending_song` is `{song, at_bar}` or null.
  - `POST /v1/control` (`Content-Type: application/json`) accepts any of the following keys in one object, and returns the same status:
    - `{"bpm": n}`, 40–200: a tempo step now. The beat phase stays continuous (no jump).
    - `{"ramp": {"to_bpm": n, "over_s": s}}`, `to_bpm` 40–200 and 0 < `over_s` ≤ 600: a linear ramp from the current tempo. It can't be combined with `bpm`.
    - `{"song": "pop|techno|jazz|ambient"}`: switches style at the next bar, where the new song's section plan starts. The tempo is kept unless `bpm` is also given.
    - `{"pulses": false}`: a breakdown. `pulse` stops and `level` stops hitting, so the follower should unlock; `{"pulses": true}` brings the beats back, and it should re-lock.
    - `{"next_section": true}`: jumps to the next section now (usually a new effect, so follow_dmx sees a change).
  - Errors: 400 `bad_request` (an unknown key, a value out of range, or `bpm` with `ramp`), 400 `bad_json`, 415 `unsupported_media_type`. A rejected body applies nothing.
  - Auth: none beyond the 127.0.0.1 bind (deviation from §C.3 for 2f to accept; see changelog); room_service's bus on 8701 has no control API.
  - Each applied change is written to the truth record as `event: "control"`.
  - From a shell: `curl -s -X POST -H 'Content-Type: application/json' -d '{"bpm": 140}' http://127.0.0.1:8711/v1/control`. The /dev console reaches it through stream_service's `/dev/synth` (Appendix V).
  - It covers fault cases at runtime: `ramp` covers `--tempo-ramp`; a `bpm` step to half the tempo stands in for `--half-time`; and `pulses: false` covers a long run of missed pulses, the unlock case of `--miss-pulse`.

**Recommended:**
- K3 `hello`/`welcome`.
- Fault flags: `--miss-pulse` (single dropped pulses), `--effect-noise`, `--cadence on-change`, `--stall`, `--disconnect`. For tempo ramps and half time, use `/v1/control` `ramp` and `bpm` (above) instead of `--tempo-ramp` and `--half-time`.
- Offline mode: `--offline --seconds N --out bus.jsonl`, generated at virtual time.
- Renderer replay: `--show-bus file:bus.jsonl`.
- Validate frames against `schema/room-show-bus.v1.json` (2f, with golden frames).

The soak gate (S7) is in add §1.5 Gates.

---

## Appendix V. Dev console (informative)

Conventions of the /dev console in the WIP implementation (`~/dev/sm-renderer-wip`, which the demo on :8092 runs; addendum A5). They are dev and ops aids, not contract, and room_service depends on none of them.

- **Screen ids.** The /dev demo creates sessions with screen id `dev-<screen>` and an explicit `fixture`. It avoids the real screen ids because a room_service pointed at the same instance ends any session for a real screen id that it didn't create.
- **`GET /dev/art/{screen}.png?w=&h=`** (dev only; screens `north-screen`, `south-screen` and `devtest`) returns the screen's current art from `--art-dir`, letterboxed. The demo uses it as the anchor and as the exit target.
- **`GET /api/diag`** (authenticated) returns host load, per-GPU utilisation, encoder utilisation and memory, and per-session timings over a 2 s window with a bottleneck verdict.
- **`GET/POST /dev/synth`** (authenticated with the Bearer token, like `/api/*`; dev only): a proxy to show-synth's control API (Appendix S, Runtime control), at the URL derived from `--show-bus` (`ws://H:P/v1/show` → `http://H:P/v1/control`; `wss` → `https`). The token is checked here and never forwarded; the synth's status code and JSON pass through. It returns 404 `not_found` when stream_service runs without `--show-bus`, and 502 `synth_unreachable` (no answer within 3 s) or `not_synth` (an answer that isn't JSON) when no synth answers. A POST body must be a JSON object (otherwise 400 `bad_request`). The console's Music panel uses it to set the bpm, ramp, song and breakdown.
- **Music panel** ("Music (simulated Maestro)"), polled at 1 Hz through `/dev/synth`:
  - a BPM slider (60–180) and number box (40–200) with Set, and quick chips 90, 110, 124, 128, 140 and 174;
  - "Ramp to N bpm over S s";
  - a Song select (pop, techno, jazz, ambient), a "Breakdown (stop beats)" checkbox and Next section;
  - the synth's state (tempo, ramp, song, section, beats on or off) and the follower's bpm, lock, confidence and difference from the synth;
  - a re-lock watch, which times how long the follower takes after each change to unlock (breakdown) or to lock on tempo (within 1.5 % of the synth for 3 s).
- **To vary the bpm in the demo:** start show-synth: `python -m stream_service.tools.show_synth --port 8711 --song pop` (Appendix S), from `~/dev/sm-renderer-wip`. Then open the /dev console on :8092 (started with `--show-bus ws://127.0.0.1:8711/v1/show`), enter the API token, start the live demo, and use the Music panel. If the panel reports 502 `synth_unreachable`, show-synth isn't running on the port that `--show-bus` names. Without the console: `curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"ramp": {"to_bpm": 90, "over_s": 16}}' http://127.0.0.1:8092/dev/synth`.
