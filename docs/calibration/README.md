# Calibrations

The owner's rule (20 Oct 2026): the room, the PA, the mics, the screens and the lights can't be controlled, but they
can be measured. Each calibration is an occasional run per room and device; its result goes into a file the live
stack reads, so compensation follows the measurement. Rerun one when its hardware, placement or settings change.

| Calibration | What it measures | Tool | Result read live |
|---|---|---|---|
| Screen latency | a screen change → light at the room's sensor (render-ahead for beat-phased visuals) | `tools/room/latency_cal/` (Yocto light sensor via the light relay) | `config/room/latency.json` `screens.<screen>` |
| Room audio | what the PA, the room and a mic do to music before the beat clock hears it; a room model for offline tests | `tools/room/audio_cal/` (record the mic, fetch or own the original, analyse) | `config/room/audio_cal.json` `streams.<stream>.summary.music_lufs` → show_service's loudness gate offset |
| Lighting output latency (planned) | a lighting trigger → light from a fixture through Art-Net → node → DMX/D-Fi, with a camera and a screen-flash reference | to build: [the owner's brief](lighting-latency-and-portable-subnet-brief.md), chapter 1 | `config/room/latency.json` `outputs.<latency_key>` (show_service `artnet_lan.output_lead` already leads each Art-Net output by it) |

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
