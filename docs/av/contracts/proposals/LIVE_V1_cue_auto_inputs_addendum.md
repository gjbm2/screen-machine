> **Folded into the proposal (rev 2, 14 Oct); kept for history, not for merge.** `LIVE_V1_cue_auto_inputs.md` carries A1-A5, with A3's `photon_source` format and A4's effects line changed (see its changelog).

# Addendum to the LIVE_V1 cue/auto/inputs proposal (revision 2)

**Author:** screen-machine-04. **For:** screen-machine-2f, to fold in when it merges LIVE_V1 rev 1.1.
**Date:** 14 Oct 2026.
**Basis:** the WIP implementation in `~/dev/sm-renderer-wip`, which the demo on :8092 runs. It passed an end-to-end check with show-synth: 7 auto cues over 6 scenes and 7 transitions, the follower locked at 123.8–124.6 bpm with confidence 1.0, and exit gave `session.held` with the held frame within mean |diff| 0.55/255 of the art.

## A1. Beat follower: two refinements to R18 (renderer-internal, no contract change)

**Schmitt thresholds are relative to a decaying peak, not absolute.**
- The rise threshold is 0.6 × the recent pulse peak; re-arming happens below 0.3 × the peak. The peak decays with a time constant of about 2 s.
- *Why:* with absolute thresholds of 0.6 / 0.3, the synth's snares (≈0.4–0.5 × accent) were missed, so pop locked at half tempo. Maestro's pulse depth will vary by show and fixture, so absolute levels are fragile.

**Period:** the mean of the accepted intervals within ±12 % of their median, rather than the plain median.
- *Why:* bus frames arrive at Art-Net cadence (~40 Hz, 25 ms quantisation). The plain median of quantised intervals read 126 bpm for a 124 bpm song. Averaging the inliers removes that bias. The robustness to outliers is the same, because the median still selects the inliers.

Unchanged: lock at confidence ≥ 0.7 with ≥ 4 edges; unlock below 0.6 or after no edge for max(2.5P, 1.5 s); refractory 0.35P when locked and 150 ms when unlocked; confidence against the grid.

## A2. R20: warm-up timing

- Transitions are prepared on a worker thread; the render thread never compiles or allocates. That part is as specified.
- **Warm-up happens at each GPU's first session, not at service start.** Warming at start would create a CUDA context on the RTX 4060 even when no session uses it. That GPU is the Windows desktop GPU, with about 1.5 GB free.
- The first session on a GPU therefore pays the compile while it is `starting`, before `session.ready`. It never pays it during a live transition.

## A3. R17: lead, partial

- The lead is computed and slewed as specified: server_ms + client buffered_ms EMA + 1 frame + photon from `config/room/latency.json`.
- `trim_ms` exists but isn't settable through the API yet.
- North uses south's photon value, flagged `photon_source: "south (unmeasured north)"`.

## A4. Catalog x-ui hint `basic` (2f's request, recorded here for completeness)

- `"x-ui": {"basic": true}` marks 1–3 headline params per plug-in for the simplified Room page.
  - fractal_zoom: zoom_rate and palette.
  - warp_stack: palette, drift_speed and fb_amount ("Trails").
  - Effects: amount.
- Realtime-bound params (energy, pulse) are never basic.

## A5. Dev console conventions (informative)

- The /dev demo creates sessions with screen id `dev-<screen>` and an explicit `fixture`. A room_service pointed at the same instance ends sessions for the real screen ids that it didn't create.
- `GET /dev/art/{screen}.png?w=&h=` (dev only; screens north-screen, south-screen and devtest) returns the screen's current art from `--art-dir`, letterboxed. It is used as the anchor and as the exit target.
- `GET /api/diag` (authenticated) returns host load, per-GPU utilisation, encoder utilisation and memory, and per-session 2 s window timings with a bottleneck verdict. It is a dev and ops aid, not a room_service dependency.
