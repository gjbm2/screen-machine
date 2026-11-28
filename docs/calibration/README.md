# Calibrations

The owner's rule (20 Oct 2026): the room, the PA, the mics, the screens and the lights can't be controlled, but they
can be measured. Each calibration is an occasional run per room and device; its result goes into a file the live
stack reads, so compensation follows the measurement. Rerun one when its hardware, placement or settings change.

| Calibration | What it measures | Tool | Result read live |
|---|---|---|---|
| Screen latency | a screen change → light at the room's sensor (render-ahead for beat-phased visuals) | `tools/room/latency_cal/` (Yocto light sensor via the light relay) | `deployments/finsbury/calibration/latency.json` `screens.<screen>` |
| Room audio | what the PA, the room and a mic do to music before the beat clock hears it; a room model for offline tests | `tools/room/audio_cal/` (record the mic, fetch or own the original, analyse) | `deployments/finsbury/calibration/audio_cal.json` `streams.<stream>.summary.music_lufs` → show_service's loudness gate offset |
| Lighting output latency | the frame sent → light from a fixture (Art-Net → node → DMX), with this device's camera and a screen-flash reference ([the owner's brief](lighting-latency-and-portable-subnet-brief.md), chapter 1) | the app: Settings › Sound & timing › Lights › Calibrate (`src/components/sound/CameraCalibration.tsx`; show_service's probe `POST /api/v1/rig/fixtures/{id}/probe` and `GET /api/v1/clock`) | `latency.json` `outputs.<fixture>.intensity` {rise, fall t50}, for the light and the others of its model on its output; show_service evaluates each light that far ahead, and an output with no lead of its own sends its beat frames their median early (`artnet_lan.output_lead`) |
| Screen picture delay (camera) | a frame presented on the screen's page → its light, against this device's own flash | the same Calibrate, choosing the screen: its display page flashes on `PUT /api/sound/flash` | `latency.json` `screens.<screen>.effective.presented_to_photon_ms` (+ `trim_ms`), stream_service's render-ahead |
| Sound in (the beat clock's own lateness) | a sound leaving this device's speaker (its audio output timestamps, on show_service's clock) → the time the beat clock gives it (levels `k`) | Settings › Sound & timing › Sound coming in › Calibrate (`POST /api/sound/calibrate {output: "in"}`) | `latency.json` `sources.<stream>.acoustic_offset_ms` (moved by minus what the chirps were off by), show_service's receiver |

Related, later: the owner's portable rig (a Windows laptop driving Art-Net lighting only, no screens) needs its own
local Ethernet lighting subnet (the brief's chapter 2).

Notes for the lighting calibration and the portable rig, from how this repo works today:

- **Clocks.** show_service runs in WSL on CLOCK_MONOTONIC; a Windows camera stamps QPC. Map one to the other as the
  PC22 audio agent does (its heartbeats carry QPC capture times that show_service fits).
- **The trigger boundary.** Lighting frames are scheduled by show_service's Art-Net sender (`show_service/artnet_lan.py`,
  phase-locked to the beat, led by each output's latency); inject the test pulse there so it rides the production
  cadence and queues.
- **Hardware not here yet.** No Art-Net node is installed (show_service's LAN sender has a virtual node); the brief's
  chain is Art-Net node (e.g. a DMX-AN2) → DMX → D-Fi Hub → fixtures. The screen-reference loop can be built and
  tested before it arrives.
- **Windows networking on PC22** goes through Spectre (CLAUDE.md): its `lan-wan` API makes firewall rules; never
  hand-rolled netsh. A laptop without Spectre needs an equivalent, scripted provisioning.
- **WSL and Art-Net discovery.** WSL2's default NAT lets show_service send unicast Art-Net out but not hear ArtPoll
  replies or broadcasts on the lighting subnet; on a laptop, WSL's mirrored networking mode or running show_service
  natively on Windows has to be proven with the real node (the brief's 2.3 asks for exactly that check).

## Sound outputs

Every sound output's delay (screens' own pages, Sonos zones) is measured from the app: Settings › Sound & timing, or
`POST /api/sound/calibrate`. It is stored in `latency.json` `outputs[<output id>].sound`. See [../SOUND.md](../SOUND.md).
