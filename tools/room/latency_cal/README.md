# Latency calibration: screen → light sensor

A repeatable routine that measures how long a change on a kiosk screen takes to reach the room's light sensor. From that it computes the effective render → photon latency, which the renderer uses as its render-ahead for beat-phased visuals.

Run it again after anything in the chain changes: TV firmware or picture settings, kiosk compositor or Chrome, stream profile, encoder settings or network path.

## What it measures

```
 WSL (calibrate.py)                 kiosk box (one clock: CLOCK_REALTIME)                TV + room
 t_cmd ──CDP over ssh, wired──▶ page rAF frame: overlay white/black ──HDMI──▶ panel ──light──▶ Yocto-Light-V5
                                 (raf1, Date.now domain)                                       │ USB, 100 reports/s
                                                                kiosk_sampler.py ◀─────────────┘ (t_ns on receipt)
```

- **Command → apply:** WSL command to the page's rAF frame. This is the network plus CDP, corrected by a measured WSL↔box clock offset.
- **Apply → photon:** the page frame to the 10/50/90 % crossings of the lux step. Both ends are timed on the box's own clock, so the network doesn't enter this number.
  - **The sensor:** the Yocto-Light-V5 makes a new measurement only every 100 ms, whatever the report rate.
    - That holds on firmware 70007 (updated on 13 Oct), checked with `--sensor-modes`.
    - Only measureType 0 (100 ms) and 5 (~720 ms) are accepted; the documented 20 Hz mode is not reachable through the API. The 100/s reports are collapsed back into those measurement events, and crossings are interpolated between them.
  - **Bias:** each measurement integrates roughly the preceding period, so half a period is subtracted. A simulation of 60 pulses recovered a planted latency to within about 4 ms (standard error about 1.3 ms).
  - **Check:** the result reports `steps_with_intermediate`. A value below 0.8 means integration is shorter than the period, and the result may read low by up to half the difference.
  - **Spread:** per-pulse crossings vary with the sensor's phase, so the routine uses the mean of phase-randomised pulses.
- **Presented → photon:** apply → photon minus apply → presented. Presented is measured with Element Timing (`renderTime` of a tagged span added in the same frame); on 13 Oct apply → presented was 35 ms, about two frames. If Element Timing is unavailable, the routine falls back to one frame and says so in `presented_source`. A video frame's "presented" time (rVFC) sits at the same point.
- **Effective render → photon:** stream capture → presented (bd's lab: rVFC `expectedDisplayTime` on the server clock) plus presented → photon. Pass the stream figure with `--stream-to-presented-ms`.

## Prerequisites

- An **agreed owner window**: the run takes over one TV for about 35 s with slow white/black changes, at least 1 s apart (photosafe). Agree a quiet window with screen-machine-bd as well.
- WSL: the repo `.venv` (it has `websockets`); key-based SSH to `gjbm2@192.168.1.10`, the box's wired interface.
- The box: `~/.venv` with `yoctopuce` (the light relay's venv).
- **Sensor access, with no sudo (`--sensor relay`, the default).** The box runs the calibration-capable relay, `lightsense.py` (version `calib-3`; repo copy in this directory). The script writes `~/latency_cal/capture.json` on the box and sends the relay `SIGUSR1`. The relay runs as the same user, so no sudo is needed.
  - The relay keeps running. It logs 100 timestamped reports a second to the requested file, and keeps sending the pre-capture lux value, so auto-brightness holds steady and flashes can't feed back through the screens' masks.
  - The capture ends on `SIGUSR2`, at its deadline, or when the relay exits. The sensor's report frequency is then restored.
  - The relay's normal behaviour is unchanged: 2 Hz `{"sensor_name","lux"}` to the same URL.
  - **Room ingest (calib-3, LIVE_V1 §2.2b):** an optional second sink to room_service. It is off unless `~/.config/light-relay/room.json` exists on the box (mode 0600, `{"url": "ws://<room host>:<port>/v1/ingest/lux", "key": "…"}`).
    - The file is read at start and on `kill -HUP`, so changing the port or key needs no sudo.
    - It sends every new measurement (~10 Hz, from the sensor's value callback), plus a 1 Hz heartbeat while the light is steady.
    - After a step of more than 0.75 EV it re-reads the sensor once, about 120 ms later. That gives room_service's two-sample filter its confirming reading promptly.
    - During a capture it sends the held value with `"held": true`, so room_service doesn't react to the flashes.
    - The legacy send is unaffected.
    - **Hardware checks after the first deploy with the sink on** (it has only been tested against a fake sensor so far):
      - the value-callback rate is about 10 Hz in changing light;
      - the legacy send stays at 2 Hz. The sink's 5 ms `HandleEvents` pump shares its event loop, so a USB stall would delay both;
      - a calibration run's report-arrival jitter (`report_rate` in `meta.json`/`result.json`) matches a run with the sink off. During a capture, both pumps call `HandleEvents`.
- **Installing or updating the relay** (owner; the one step that needs sudo):
  ```bash
  scp tools/room/latency_cal/lightsense.py gjbm2@192.168.1.10:lightsense.py
  ssh -t gjbm2@192.168.1.10 sudo systemctl restart light-relay
  ```
  The 2025 original is kept as `~/lightsense.py.backup-20261013-pre-calib`. To roll back, copy it over `~/lightsense.py` and restart.
- **Fallback, `--sensor exclusive`:** stops the relay and samples with `kiosk_sampler.py`, which has exclusive USB access (`--relay tty|sudo-n|none`). `--probe` uses this path.

## Run

```bash
# 1. Optional: the sensor's achievable report rates (needs the relay stopped; no screen)
.venv/bin/python tools/room/latency_cal/calibrate.py --probe

# 2. Calibrate one screen (the sensor sees the south TV best)
.venv/bin/python tools/room/latency_cal/calibrate.py --screen south --yes \
    --stream-to-presented-ms 130     # bd lab p50 at 4K60 under sway, 13 Oct

# 2b. Live path, end to end: renderer → stream → TV → photon (needs stream_service with cal_flash)
.venv/bin/python tools/room/latency_cal/calibrate.py --screen south --yes --path live
#     room_service puts the screen live on the cal-flash scene, and each flip is scheduled on the renderer's
#     clock (at_ns), so no page overlay is involved. The result goes to outputs.tv-<screen>.live in
#     latency.json; screens.<id> (the TV's own share) is left alone. The screen goes back to art at the end.

# 3. Re-run the analysis on a saved run
.venv/bin/python tools/room/latency_cal/calibrate.py --reanalyse tools/room/latency_cal/runs/<run>
```

Options:
- `--pulses 20`: the number of changes; the whole run takes about 35 s. On 13 Oct, 10 changes matched 60 to within 1 ms, and 20 gave a standard error of about 4 ms.
- `--interval 1.2 --jitter 0.2`: seconds between changes. The photosafe floor is 1 s; the TV settles in about 0.4 s.
- `--freq 100/s`: the timed-report rate.
- `--sensor relay|exclusive`: how the sensor is read (see Prerequisites).
- `--early-check 5`: aborts if the sensor saw none of the first steps.

Avoid the page's timed reloads (local hours 2, 6, 10, 14, 18 and 22, until RP P5 replaces them). A reload removes the overlay mid-run, and the run then aborts cleanly.

## Outputs

- `runs/<UTC stamp>-<screen>/`:
  - `samples.jsonl`: the box sampler's header, every report, and the footer, which says whether the sensor's settings were restored;
  - `pulses.json`: the page log, with the WSL command times;
  - `meta.json`: ping, CDP RTT, clock offset and drift;
  - `result.json`: the summary plus one row per pulse.
- `config/room/latency.json`: the latest result per screen. **The renderer reads `screens.<id>.effective.render_to_photon_ms`** (and the rise/fall breakdown) as its render-ahead.

### `config/room/latency.json` schema (version 2)

| Key | Written by | What it holds |
|---|---|---|
| `screens.<id>` | this routine | The full result. stream_service reads `screens.<id>.effective.presented_to_photon_ms`. |
| `outputs.<output>.<attribute>` | this routine (`tv-<screen>.brightness`); the photodiode runner (lamps, DMX, TVs) | One entry per output and attribute. |
| `sources.<stream>` | the photodiode/mic runner | One entry per audio stream. |

**`outputs.<output>.<attribute>` fields:**
- `{rise,fall}.{t10,t50,t90}`: ms from `zero` to 10/50/90 % of the light step, with `n` steps per edge;
- `zero`: what time 0 is. For TVs it is the frame's presented time; another writer names its own, e.g. "DMX frame sent";
- `trim_ms`: a hand-set trim. Re-runs keep it;
- `se_ms`, `method`, `measured_at`, `run_dir`.

**`sources.<stream>`:**
- `acoustic_offset_ms`, signed: positive means the sound reaches the listener later than the stream's timestamps say;
- `method`, `measured_at`.

**Writers:**
- Each writer reads the file, changes only its own keys and replaces the file atomically (write `.tmp`, then rename).
- Readers ignore keys they don't know.

## Safety and cleanup

On every exit path (error, Ctrl-C, SIGTERM):
- the overlay is removed;
- the sampler is stopped, and it restores the sensor's original settings (measure type, advertising mode, report frequency). They are never written to flash;
- the relay is restarted if this script stopped it.

The sampler has its own deadline, so it restores the sensor even if WSL dies.

Recovery:
- **If the overlay can't be removed:** kill that screen's Chrome, and `kiosk-wayland.sh` respawns it on its own URL in about 10 s. The script prints the exact command.
- **If the sensor was left modified:** run `ssh gjbm2@192.168.1.10 '~/.venv/bin/python ~/latency_cal/kiosk_sampler.py restore'` with the relay stopped.

## Later

- **A stream-path driver.** bd's lab could gain a full-frame flash source (`--source flash`), so the routine measures render → photon directly instead of composing two measurements.
- **Relay v2** (RP P4b, `deploy/sensors/light-relay/`) should expose this fast, timestamped mode itself, so calibration no longer needs the relay stopped.

## Hue lamps (`hue_cal.py`, 18 Oct)

The same measurement for the four Hue lamps, through the production path: Art-Net universe 0 into room_service's receiver (127.0.0.1:6454) → room_service's HueOutput (25 Hz, xy + brightness) → Hue Bridge Pro → Zigbee → lamp → the Yocto sensor. Time zero is the moment the first ArtDmx frame carrying a new level leaves WSL (`zero: "Art-Net frame sent (WSL)"`).

```bash
.venv/bin/python tools/room/latency_cal/hue_cal.py --dry-run             # daylight check: no lamp is touched
.venv/bin/python tools/room/latency_cal/hue_cal.py --yes --each          # dark room: which lamps the sensor sees, with SNR
.venv/bin/python tools/room/latency_cal/hue_cal.py --yes                 # all four together, 20 flips (~45 s)
.venv/bin/python tools/room/latency_cal/hue_cal.py --yes --lamps hue.se,hue.sw
.venv/bin/python tools/room/latency_cal/hue_cal.py --reanalyse tools/room/latency_cal/runs/<run> --update-latest
```

What a real run touches, and puts back:
- **Preflight:** it refuses if a screen is live, because its frames zero the screens' channels. It also refuses if the Hue output has been handed to show_service (`hue.streamer: show`) or taken over by another app.
- **Art-Net:** it sends 40 Hz frames from 127.0.0.2, so show_service's virtual Maestro sees a foreign sender and yields. When the run stops sending, room's input goes stale and show_service resumes by itself.
- **Room targets:** each lamp that isn't already on `dmx` is set to `dmx` by hand, then put back to exactly its earlier value. This happens on every exit path, including Ctrl-C and SIGTERM.
- **Flashes:** white and dark at least 1 s apart (photosafe).
- **Sensor:** it uses the light relay's capture with no sudo, as for the screens.
- **Clock:** the WSL↔box wall-clock offset comes from about 200 UDP echoes to a throwaway responder started on the box over SSH. It is the median of the minimum-RTT probes' midpoints, and is taken before and after the run. On 18 Oct it was −49.6 ± 0.9 ms (the box runs behind WSL).
- **Results:**
  - a run directory with `samples.jsonl`, `pulses.json`, `meta.json` and `result.json`;
  - `config/room/latency.json` gets `outputs.hue.intensity` (all four lamps together) or `outputs.<lamp>.intensity` (a subset).
  - The show's Hue lead (show_service artnet_out `hue_latency_ms`) is set from these entries.
