# Sound: where it plays and how late it is heard

One vocabulary of sound outputs, one store of their delays, one calibration, for every part of the app (owner, 27 Nov:
"control of output device (for sound) isn't standard across karaoke mode vs other modes ... generalise this and ensure
it's properly engineered and exposed in a consistent standard way ... latency calibration itself should also be
generalised across the app and sensibly exposed").

## Outputs (`smsound/outputs.py`)

| id | what plays it | who can use it |
|---|---|---|
| `screen:<screen id>` | that screen's own display page (Web Audio in its browser: the TV, HDMI, Bluetooth or the computer's speakers) | karaoke |
| `pc22` | the Windows host's speakers (show_sender's Windows synth, or piano_render's WASAPI player); only with `host.windows_python` | the piano and Music |
| `sonos:<zone>` | a zone of `deployments/<id>/room/sonos.json` (with an `ip`), through room_service; only with the `sonos` capability | karaoke, the piano and Music |
| `device:<id>` | a phone or tablet playing it itself (show_service piano_live; dynamic) | the piano and Music |
| `none` | nothing | everyone |

Every list of outputs is built from `smsound.outputs` as rows of one shape: `{id, kind, kind_label, label, room, hint,
calibrate, latency}`. A consumer says which kinds it can play to (`routes/sound/api.py` `CONSUMERS`). The ones it can't
are still listed, marked `available: false` with `why`. The pickers:

- **Karaoke's Sound** (`/karaoke` › Settings) uses `components/sound/SoundOutputSelect.tsx` over `GET
  /api/karaoke/outputs`, which is `GET /api/sound/outputs?for=karaoke`. The choice is `settings.output`. Unset, it means
  the karaoke screen's own page; the old word `"screen"` means the same.
- **The piano's and Music's Play on** (`src/pages/Room/PlayOn.tsx`) uses show_service's `GET /api/v1/test` and `GET
  /api/v1/player` `choices.outputs`. Those answers are now standard rows with their latency (`smsound.outputs.standard`).
  Which outputs the piano offers is still show_service's (`sources.py`: Sonos only with a rendered sound).
- **Settings › Sound & timing** lists them all.

## Latency (`smsound/latency.py`)

**One rule:** milliseconds, and + means the room hears (or sees) it that much after the moment it was scheduled. The
consumer starts that much earlier, or shows that much later.

**One store:** the deployment's `calibration/latency.json` (schema v2: `screens`, `outputs`, `sources`). Sound outputs
sit beside the lights and screens:

```json
"outputs": {
  "screen:vj":    {"sound": {"delay_ms": 134, "trim_ms": 10, "method": "chirp", "measured_at": "...", "spread_ms": 3}},
  "sonos:living": {"sound": {"delay_ms": 480, "method": "chirp", ...}},
  "hue":          {"intensity": {...}}
}
```

**The effective delay** is `delay_ms + trim_ms`. If nothing is measured, the kind's default applies (Sonos: 500 ms,
measured by hand on 21 Oct, or the zone's older `start_ms`). Failing that it is `null`, and the player uses its own
figure: a browser's reported output latency.

**What each output's delay means:**
- **A screen:** from the display page's schedule (the server's clock) until the room hears it.
- **Sonos:** from room_service's play request until the room hears the file's start. This is piano_render's
  `start_ms`, which now comes from here (`piano_render/sonos.py start_ms_for`). While it plays, the Sonos's own
  position (RelTime) still corrects the clock.
- **`pc22`:** measured live by the piano while it plays (`piano_render/calibrate.py`, its own state file).
- **A device:** measured by its own browser (`src/pages/Room/deviceLag.ts`).

**Who applies it:**
- **Karaoke:** `/api/karaoke/now` `settings.audio_delay_ms` is the output's effective delay. It is 0 on a Sonos, whose
  clock sonos_out.py anchors to what the room hears, and 0 with no sound. The display page's lyrics, stream_service's
  lyric feed and show_service's karaoke feed all read it, so they agree whichever output plays.
- **The piano on Sonos:** `start_ms_for`.

## Calibration (`smsound/chirps.py`, `routes/sound/calibrate.py`)

`POST /api/sound/calibrate {output}` measures a screen or a Sonos:

- **Ten coded chirps** (rising and falling sweeps over different bands) at uneven times, after a quiet noise bed that
  wakes the speaker. Each chirp is found on its own in the room mic's signal (show_service `WS /v1/levels?wave=2048`).
  The first three are dropped while the speaker settles. The median of the rest must agree within ±25 ms, or nothing
  changes.
- **A screen** plays them from its display page (`DisplayPage/SoundCalibration.tsx`, polling `GET /api/sound/calibrate`)
  at their server times.
- **A Sonos** plays them as a file through `piano_render.sonos.SonosOutput`, exactly as a piece or a song. The zone's
  delay becomes its current one plus however far the chirps were off.
- **While it runs:** show_service turns the room mic on (`show_service/sound_cal_feed.py`, a mic want), and karaoke
  pauses its song (a `before` hook).
- **The result** goes into latency.json; the trim is kept.

**Where it's exposed:**
- `GET /api/sound/outputs[?for=]`, `GET /api/sound/latency` (every latency the deployment knows: sound outputs,
  screens' picture-to-light, lights, sound sources), `PUT /api/sound/outputs/<id>/latency {trim_ms | delay_ms |
  forget}`, and `POST`/`GET /api/sound/calibrate`. Writes need an admin where sign-in is on (`config/auth/gates.yaml`).
- **Settings › Sound & timing:** each output with its delay, Calibrate and trim, plus the other timing read-only, each
  with what it measures and the tool that measures it.
- **Karaoke › Settings:** the same control for the karaoke's output. `POST /api/karaoke/calibrate` measures the
  karaoke's output.

**Still measured elsewhere** (read-only here):
- Screens' picture-to-light and the lights' response: `tools/room/latency_cal` (calibrate.py, hue_cal.py) with the
  light sensor.
- The piano's live sink delay: `piano_render/calibrate.py`.
- Room audio levels: `tools/room/audio_cal`.
