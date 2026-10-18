# Show engine: our own ears, art director and lights (design v1.1)

**For:** the owner, and the sessions that own the pieces it touches:
- 04: renderer;
- 2f: room_service core, the Room page, AV_PROGRAMME and modes;
- 68: DMX, Hue and the show bus;
- aa: room adapters;
- ca: ports, ctl, Spectre and the Windows pieces;
- 10: latency calibration.

**Maintainer:** screen-machine-c8. Send changes to c8.

**Date:** Thu 15 Oct 2026. **Status:** design, researched and adversarially reviewed. P0 is ready to build (§13).

**Scope:** 2f has amended AV_PROGRAMME to match (15 Oct): §4.4a inputs come from show_service; §6.3 Maestro is optional; §11 gives c8 its ownership row and rule 3 now says "through room_service or show_service". PORTS.md changes go through ca. The renderer still does no audio analysis itself; it consumes ours.

**How it was made:**
- **Research:** eleven tracks on 15 Oct.
  - real-time DSP and beat tracking;
  - local music ML;
  - lighting engines and fixtures;
  - automated direction;
  - OpenAI audio;
  - audio transport;
  - latency, plus a skeptic pass on it;
  - reuse of existing projects;
  - two code maps;
  - a probe of this machine.
- **Review:** four lenses checked the result: real-time engineering, codebase fit, show quality, and completeness. v1.1 folds in their findings.
- **Evidence:** numbers marked *measured* were taken on PC22 under render load. Model names and prices are marked *(retrieved 15 Oct; re-verify at build)*.

---

## 0. Summary

1. **Day one is a "virtual Maestro".**
   - A new `show_service` listens to the room's audio, tracks the beat, and sends Art-Net to room_service's existing receiver in exactly Maestro's channel layout.
   - The TVs and the Hue lamps then follow the *real* music the same day.
   - No other session's code changes, and Flask doesn't restart.
   - Everything after that is upgrades.
2. **show_service runs as two processes**, in the same exception class as the renderer: a real-time worker with no front door of its own.
   - `show_rt` handles audio in, DSP, the beat clock and the light outputs.
   - `show_brain` handles the director, the LLM, the API, the console and the slow ML worker.
   - Redeploying the brain never drops the beat lock or the lights.
   - Neither runs inside Flask. *Measured:* a full garbage collection took 92 ms, and a busy thread added 6 ms of GIL delay.
3. **Audio and piano MIDI arrive as VBAN over UDP, started by the receiver.**
   - show_rt asks each sender to stream, and WSL's NAT lets the replies in, as it already does for Hue.
   - So there is no Windows relay, no inbound firewall rule and no relay card.
   - **The room kit is bought (owner, 15 Oct):**
     - an Audio-Technica ATR4697-USB boundary mic by the Steinway (the room mic carries the PA music too; there is no line feed);
     - the Steinway's adsilent system, which sends **exact USB MIDI**: notes, velocities, pedals.
   - Both plug into **the kiosk mini-PC**, which runs a sender as its own unit beside the light relay.
   - Until the kit arrives, PC22's own playback (WASAPI loopback) or its USB mic stands in.
   - *Measured:* Windows→WSL is under 1 ms. The input chain is about 5 ms, as the owner estimated; the kiosk's powerline link adds jitter, which only delays what we know.
4. **Beats land by prediction, not reaction.**
   - We know about an onset 12–25 ms after it sounds. Each output adds its own delay: DMX 15–45 ms, Hue ~50–150 ms (to be measured), the live TV stream **0.55–0.85 s** (the renderer's own live lead).
   - So everything periodic comes from a **beat clock**, Ableton Link-style: tempo, a phase anchor on the host clock, bar and phrase position, and a calibrated confidence.
   - Each output reads the clock at `now + its own latency` and aims slightly *early*. People tolerate light before sound far better than after it.
   - Only DMX reacts to unpredictable hits. Screens never get transients.
5. **Targets, measured with a photodiode on the audio interface:** 95 % of beat flashes within 25 ms of the sound at the listener on DMX, and within 40 ms on Hue and the TVs. This holds for music with a steady beat.
   - Rubato piano gets **no beat lock at all**. A wrong beat is worse than none.
   - The room then follows dynamics, onsets and harmony instead. It degrades; it never refuses.
6. **When the piano plays, the notes are exact.**
   - Harmony, chords, key, dynamics and "the piano is playing" come straight from MIDI, with no guessing.
   - Note-ons reach the lights in a few milliseconds, with no FFT window in the way.
   - The mic adds the room: loudness, ring, voices.

   **Three tiers of understanding, all on CPU.** The GPUs stay with the renderer.
   - **DSP** at 100 Hz: bands, onsets, loudness, the clock.
   - **Local ML** every 1–10 s, ~0.3 core: genre family, mood words, arousal, instruments, music vs talk, key, sections, build/drop.
   - **An OpenAI listener** about every 20 s: genre in words, section function, "building to a climax", track changes.
   - **The LLM arms; the DSP fires.** Verdicts are 12–20 s old and only set expectations; timing always comes from the DSP.
7. **Modes are the vibes.** Each existing mode (Normal, Piano, Lights only, Ambient, Party, Disco) gets a `show.vibe`.
   - One room-level **art director** composites, in order: vibe, music-auto, LLM context, the owner's nudges, then momentary actions.
   - It drives the screens (direction words, palette, clock, cue hints), the Hue lamps and future DMX alike.
   - Looks change on phrase lines and section boundaries, chorus looks come back, and the peaks are saved for real drops.
8. **The screens stay full intensity, always.**
   - Level, dimming, "lights low" and pre-drop dips apply to lamps and DMX only.
   - Screens express "calmer" or "darker" only through scenes, palette and words.
9. **The lamps get roles.**
   - The lamps *above* the screens extend the screen's colours.
   - The lamps *opposite* are the room's key light: warm white or a faint tint at dinner, full colour only in Party and Disco.
   - A palette can come **from the art on glass**.
   - Every show starts and ends with choreography, not a snap.
10. **Auto** (owner, 16 Oct): with a toggle on, a supervisor above the director switches *modes* by itself (the piano starts → Piano).
    - Each mode carries its own "switch to me when…" settings as plain config.
    - It confirms before acting and leaves slowly.
    - When it acts, it applies the mode minus that mode's `skip` list (blinds, ball, TV power). Piano fades the music out and back.
    - A person's choice always wins; every switch is announced with Undo.
    - Sonos now-playing (track, artist, source) comes from local polling (§8.9).
11. **The Room page gets one small Music card:**
    - "Hearing: soul · relaxed";
    - Calmer / Wilder;
    - a palette row with "From the art";
    - Next look, Keep this look, **House lights**;
    - active chips with Back to auto;
    - a "More" unroll with lamp brightness and "Describe it".

    Setup lives on a separate show console, never on /room.
12. **Fixtures:** one registry for screens, Hue lamps, DMX and LED strips, with DMX types from Open Fixture Library.
    - Our own Art-Net sender: unicast, frames phase-locked to the beat grid.
    - The first DMX buy should be 1–2 **pinspots on the mirror ball**.

---

## 1. What changes, what stays, and the rules we keep

| Area | Today | After |
|---|---|---|
| Music source | MaestroDMX over Art-Net. **Not arriving today:** the Windows relay was never started (no `artnet-relay.ps1`, no card, no firewall rule; room shows 0 packets). | Live audio into show_service. Maestro is optional, as the lowest layer. |
| Beat | The renderer's BeatFollower on Maestro's DIMMER pulse | P0: the same BeatFollower, fed by pulses we place exactly on the predicted beat. P4: the renderer reads our clock directly. |
| Music state, direction | `music_direction.py` reads ACTIVITY, pulse and GOBO, and trims the panel around it | P0: the same code on our virtual channels. P2: the room director's output replaces the renderer's *music proposal* only (§8.7). |
| Show bus | room_service :8701 (inside Flask) | P0–P3: unchanged. P4: show_service serves `127.0.0.1:8701` after room sets `show_bus_port: 0`. The renderer reconnects by itself with no restart. K3 frames are kept; v2 types are added (§11). |
| Hue lamps | room_service streams Maestro's colours at 25 Hz | P0: unchanged, now fed by us. P5: the streamer moves into show_rt behind a `hue.streamer: room\|show` switch (68). |
| DMX fixtures | none | Fixture registry and our own Art-Net out (P5, when a node is bought) |
| Room page | Lamp rows "Follow Maestro", test show, per-screen energy and colours | A room-level Music card (§10). While the show drives, per-screen energy and colours move to it. |
| OpenAI | `routes/openai.py`, gpt-4o, a new client per call | A small shared `utils/llm/` layer used by the listener. **gpt-4o reportedly shuts down on 14 Jan 2027**, so the existing callers need a new default (§7.6). |

**Rules this design keeps** (CLAUDE.md, owner memory, AV_PROGRAMME):
- **Live = full intensity, with no mask in the renderer.** Show levels and dips never reach a screen as a gain (§8.3).
- **One front door.**
  - show_service binds `127.0.0.1:8703` only.
  - Pages reach it through `:8700/show/*` (a pass-through like `/renderer/*`), which also works as `https://…/rs/show/*`.
  - Pages use relative URLs and derive ws or wss from `location`.
- **No auth** on any of it.
- **Degrade, never refuse.** No disabled controls; no stop gates; every failure falls back to a quieter behaviour.
- **Safety is optional and off by default** (AV_PROGRAMME §0.6). The flash and strobe caps are settings, default off. Hue's update rate is a device limit, not a cap.
- **No hardcoded IPs.** Senders, nodes and lamps are named in config (DNS name, MAC, short name or bridge id). Broadcast addresses are derived from the interface.
- **GPU jobs: none.** show_service sets `CUDA_VISIBLE_DEVICES=` (empty). All ML is CPU, ONNX where possible.
- **Heavy CPU work is niced and serialised.** show_rt runs at nice 0. The slow worker, builds and benchmarks run at nice 19 or SCHED_IDLE.
- **Art↔live stays a hard cut between matching frames.** The director sends a screen no cues, cuts or accents while it is entering or leaving live (§8.6).
- **Every long-running piece gets a Spectre card**, and every port is registered in PORTS.md before code (ca).

---

## 2. Shape

```
 room mic + piano MIDI ─► sender (PC22 now; kiosk unit later) ◄── SUBSCRIBE 1 s ┐
                      └────── VBAN/UDP (2.7 ms packets) ──► WSL (NAT reply path) ┘
 ┌─ show_rt (nice 0; numpy only; no asyncio on the hot path) ────────────────────────────────┐
 │ HOT THREAD: recv → sample-clock map → per-packet IIR hits → fast DSP → activation → CLOCK   │
 │             └ ring buffer (shared memory, 120 s) ─────────────────────────┐                │
 │ OUTPUT THREAD: look evaluation per output at now + L(output, attribute)    │                │
 │   P0: ArtDmx → 127.0.0.1:6454 (room's receiver, Maestro's layout)          │                │
 │   P5: ArtDmx → LAN nodes (beat-phased frame grid) · Hue DTLS 50 Hz (moved from room)        │
 └──────────── latest-value IPC (clock+features 100 Hz ▲ ; look+events+nudges 10 Hz ▼) ───────┘
 ┌─ show_brain (asyncio) ─────────────────────────────▼───────────────────────────────────────┐
 │ slow worker (child, own venv, nice 19, ≤2 threads, time-boxed): Beat This! · EffNet+heads ·  │
 │   CED · key · sections · build/drop                                                          │
 │ LLM listener (~20 s) · ART DIRECTOR (10 Hz) · show bus v2 (P4) · API + console · state file  │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
      ▲ :8700/show/* pass-through (room_service, 2f)        │ (P0–P3 via room's :8701; P4 own bus)
   Room page Music card · show console                       ▼ stream_service (renderer)
```

| Port | What | Bind | Exposure |
|---|---|---|---|
| 8703/tcp | show_brain API, console, events WS | `127.0.0.1` | Only through `:8700/show/*` |
| 6980/udp | VBAN audio. The receiver starts each stream, so replies come back through WSL's NAT. | WSL socket | None needed. A Windows relay is a fallback for push-only third-party senders. |
| 6454/udp | P0: ArtDmx to `127.0.0.1:6454` (room's receiver). P5: unicast to LAN nodes, and ArtPollReply back. | | P5 replies come back through NAT, or through the relay that ca builds anyway |
| 8701 | Show bus: room's until P4, then show_brain's | `127.0.0.1` | Localhost only |

**Why two processes.** Everything with timing lives in show_rt: ingest, the clock, the hit path and the output frames. It avoids importing openai, pydantic or aiohttp, and keeps few live objects.
- It runs `gc.disable()` with explicit gen-0 collections between hops, `gc.freeze()` after warm-up, `sys.setswitchinterval(0.001)` and BLAS at 1 thread.
- It adds 1e-9 dither so filter states never go denormal (*measured* 4.2× slower in digital silence).
- show_brain does everything that allocates, waits or changes often.
- IPC is latest-value only: a shared-memory seqlock, or localhost UDP.
- If the brain restarts, show_rt holds the last look and keeps the clock and the lights.

**Startup.** It always runs and starts at boot, unlike the on-demand renderer. It uses `scripts/ctl show`, with `deploy/sm-show.env` and no hold line. Both processes report on one Spectre card.

**Time.** Everything uses CLOCK_MONOTONIC. No slewing NTP daemon runs in WSL: chrony's default slew is 83 333 ppm, and the old chrony fight cost 2 %.

---

## 3. Hearing: audio in

### 3.1 The wire: VBAN, started by the receiver

VBAN is VB-Audio's free UDP audio protocol (spec rev 13). It has a 28-byte header and plain PCM, and states its own rate, channels and bit depth.
- **A stream name inside every packet**, so identity survives any NAT.
- **A TEXT sub-protocol on the same port** for the sender's heartbeat.
- **Fallback senders and listeners exist:** PipeWire `module-vban-send`/`recv` and `vban_emitter`. VB-Audio's Windows apps are not installed; only VB-CABLE is.

**How the stream starts.**
- show_rt sends `SUBSCRIBE {stream}` from its receive socket to each configured sender every second.
  - The packet is a VBAN TEXT packet with stream name `sub` and body `{"v":1,"subscribe":["room-mic","room-piano"],"from":"show_rt"}`.
  - It goes to the sender's UDP 6980, at an address from config (a DNS name, never a literal IP).
  - Optional `"devices": {stream: name}` (16 Oct, the Room page's Mic choice) asks the sender to capture that stream from that device. A sender that can't find the name, or gets no hint, keeps its default.
  - Optional `"off": [streams]` (16 Oct, the mic on demand): keep these streams' capture devices **closed**. The sender still sends each one's heartbeat every 500 ms, with `state: "off"`, `frame`/`n`/`t_cap_ns` null and its `devices`, so the Room page can still list the mics. Body: `{"v":1,"subscribe":["room-piano"],"off":["room-mic"],"devices"?:{...},"from":"show_rt"}`.
  - A stream in neither list, or no SUBSCRIBE for 5 s: the sender stops sending it and closes its device. An empty `"subscribe": []` is still a SUBSCRIBE: a sender whose only stream is off keeps getting them, and keeps its off heartbeats.
- The sender streams audio, MIDI and heartbeats to the source address of the latest SUBSCRIBE. It stops 5 s after the last one.
- Through WSL's NAT that is exactly the Hue/DTLS pattern that already works.
- It needs no relay, no inbound rule and no card. Recovery after a WSL restart takes about 1 s.
- Push-only senders (PipeWire) need the Windows relay instead.

| Field | Value |
|---|---|
| Audio | 48 kHz, 128 frames per packet (2.7 ms) for 1–2 channels. For 4 channels: INT16 at 128 frames, or INT24 at ≤96 frames. The payload limit is 1436 B. |
| Sample index | The running sum of frames per packet, checked for continuity against the packet counter. A new timeline segment starts on any discontinuity, a change of frame size or session, or an idle/device switch. |
| Stream names | `room-mic` (ATR4697 on the kiosk), `room-piano` (MIDI, §3.5), `pc22-loop`, `pc22-mic`, `test-piano`, `phone-test`; `room-line` reserved if a line feed ever exists. Unknown names are accepted at the lowest priority and shown in health, not dropped. |
| Heartbeat | Every 500 ms, VBAN TEXT (UTF-8 JSON): `{v, sess, frame, n, t_cap_ns, in_lat_ms, dev, state: ok\|no_device\|idle\|xrun\|off, xruns, peak_dbfs[], rms_dbfs[], clips}` |

**Heartbeat fields** (clarified 16 Oct; the reference is `show_service/vban.py`, which senders can import as-is):
- It is a TEXT packet carrying **the audio stream's own name**. A sender with two streams sends two heartbeats.
- `state: "off"` (16 Oct, the mic on demand): the SUBSCRIBE's `off` keeps this stream's device closed. The receiver reads the stream as "off", never live.
- `devices` (optional, 16 Oct): the capture devices the sender could use for this stream, which feed the Room page's Mic list. There are at most 32 names of at most 128 characters each. `vban.make_heartbeat` drops names from the end until the packet fits 1436 B.
- `sess`: new on every sender restart or device re-open, and after more than 250 ms of lost capture.
- `frame`: the VBAN counter of the audio packet whose first sample is `n`.
- `n`: that sample's index, counting frames since `sess` began, including the packets skipped over an overflow.
- `t_cap_ns`: the sender's clock (below) when sample `n` reached the ADC.
- `frame`, `n` and `t_cap_ns` are null before any audio.
- `peak_dbfs` and `rms_dbfs` are per channel over the last 500 ms, floored at −120 (strict JSON has no −inf).
- `xruns` (input overflows) and `clips` count from the start of `sess`.
- TEXT headers: bit-rate index 0, channel 0, format UTF-8 (`0x10`).
- The heartbeat's nuFrame is its own TEXT counter. The audio packets' counter must be continuous.

**Sender clock** (clarified 16 Oct). Heartbeat `t_cap_ns` and MIDI `t_ns` use one clock per sender process, in integer ns:
- On Linux (the kiosk), use `time.monotonic_ns()`.
- On Windows with Python 3.12 (the PC22 agent, the test piano), use `time.perf_counter_ns()`. That is QueryPerformanceCounter: system-wide, with 100 ns ticks, and the clock PortAudio's WASAPI `time_info` uses. Never use `time.monotonic_ns()` there. In 3.12 it is GetTickCount64, with 15.6 ms steps (measured on PC22), which would scatter notes up to 15.6 ms early and bias the capture map by as much.
- From PortAudio, take only the latency difference, never its absolute time: `t_cap_ns = now_ns − (time_info.currentTime − time_info.inputBufferAdcTime)·1e9`, with `now_ns` read on entry to the callback. Without an ADC time, use `now_ns − in_lat`.

**Overflows** (clarified 16 Oct). The sender keeps `n` and nuFrame locked to its ADC clock. On an input overflow (ALSA or WASAPI), it advances the counter and `n` over the lost packets and sends nothing for them. The receiver zero-fills them as a gap, so its timeline stays on time. If more than 250 ms is lost, the sender starts a new `sess` instead. A sender must never lose capture time while it keeps counting. The receiver can only see that through the heartbeat, and then only after it has happened.

### 3.2 Senders

The sender is one Python script (~150 lines) with two capture back ends. It captures, packs, meters, listens for SUBSCRIBE and retries. It does no DSP.

**PC22 (dev, today):**
- Python 3.12 on Windows with PyAudioWPatch. It stamps with `time.perf_counter_ns()`, never `time.monotonic_ns()` (§3.1, Sender clock).
- It captures the speakers' WASAPI **loopback** (what the PC plays) and/or a mic. The Insta360 Link is live at about −36 dBFS ambient; the Samson Meteor is unplugged.
- **Keeping loopback alive:** WASAPI sends nothing while nothing plays. The agent therefore plays a silent render stream, so the device clock and the sample index never stop.
- It re-opens the device when the default device changes.
- It gets a Spectre card from `scripts/windows/audio-agent/*.ps1` (ca).
- **Built, 16 Oct (d4):** `tools/show_sender/pc22_agent.py` sends a PC22 mic as stream `pc22-mic` on UDP 6984: the first input matching `--mic-match`, else the Windows default. It uses the Dell U3224KB monitor's mic, because the Insta360 Link heard everything 134–148 ms later (d4, three runs). It runs as a Windows scheduled task under the Spectre card `screen-machine-audio-agent`, and show.yaml's `senders.pc22` subscribes it. Mic only for now; loopback is not built. Windows' microphone privacy setting must allow desktop apps, or the agent sends silence.
- WSLg's mic is dead on this box (RDP source returns silence, then unloads), so we don't use it.

**The kiosk mini-PC (owner, 15 Oct):** `screen-machine-drawingroom`, a Beelink N95 on Ubuntu 24.04.
- The mic and the piano's USB MIDI plug in here. The owner chose this box, overriding the earlier "kiosk stays dumb" and "not on powerline" advice.
- **The unit:** the same script with sounddevice/ALSA (128-frame period, the device chosen by card name) plus the ALSA MIDI reader (§3.5).
  - It runs as **its own systemd unit** (`show-sender.service`), separate from `light-relay.service`, so an audio or MIDI fault never takes the gallery light mask down.
  - `Restart=always`, `Nice=-5`, packets marked DSCP EF.
  - A Spectre card like kiosk-light-relay's, and a PORTS.md entry.
- **LIVE, 18 Oct** (the owner: plug the MIDI cable or a mic in and it Just Works). The kiosk has no passwordless sudo, so it runs as a **systemd `--user` unit** (`show-sender.user.service`; `gjbm2` lingers, so it starts at boot without a login):
  - `arecord` is unpacked from Ubuntu's alsa-utils .deb into `~/show-sender/bin`; nothing is installed system-wide.
  - The devices come from the seat ACL of the kiosk's own session, which also covers USB devices plugged in later; while that session restarts, the sender retries every 2 s.
  - `tools/show_sender/deploy.sh` does all this, and still installs the system unit wherever sudo works. `kiosk-ctl.sh` handles both kinds of unit.
  - `senders.kiosk` is on in show.yaml; the Room page lists it as "Drawing room".
  - The Mic choice is the Drawing room mic. Until one is plugged in, mic.py's fallback lets PC22's mic listen after `FALLBACK_S`.
  - The piano: any USB MIDI card, looked for every 2 s and re-found after a power cycle.
  - Not done (they need sudo): `Nice=-5` and DSCP EF.
- **Built by:** screen-machine-98, against the wire spec in §3.1 and §3.5.
- **Powerline:** the kiosk is on powerline (Wi-Fi as fallback). Sample-index stamping turns its jitter into delay only, but expect the 40 ms gap clamp to fire at times. Health shows it.

**Zero-code fallback for a mic:** ffmpeg (on both OSes) sending RTP L16. The probe proved this into WSL at a steady 10 ms. The receiver can learn RTP later; it isn't in P0.

### 3.3 Receiver and timeline

**No playout buffer.** We analyse; nothing plays the audio out.
- Each packet's audio is stamped by **sample index**.
- Sample index maps to CLOCK_MONOTONIC along the lower envelope of arrival times: a running minimum over about 2 s, then a line over about 60 s.
- Skew is clamped to ±300 ppm, with an alarm beyond. That tracks USB clocks (≤±100 ppm, or 0.36 s an hour untracked) and doubles as a watchdog on our own clock.
- Network jitter delays what we know, but never moves when things happened.

**Gaps.**
- Wait for a missing packet for at most `clamp(3 × jitter, 4, 40 ms)`.
- Then zero-fill and flag the gap, so onset detection resets instead of firing on the resumed audio.

**Acoustic offset per source.**
- `latency.json` (v2, 10's schema) stores `sources.<stream>.acoustic_offset_ms` for every stream. It is signed: **+ means the sound reaches the listener later than the stream's timestamps**. It is applied at ingest, so the clock's anchors are *acoustic* times.
- Loopback is captured before the playback chain (10 ms over HDMI, up to seconds through Sonos), so its offset is positive. The room mic is within a few ms of 0, depending on where the listener sits (~3 ms per metre).
- When the offset is positive, hits are scheduled ahead instead of sent at once. This is free listen-ahead (§5.6).
- On a source switch, the clock carries over and the new offset applies, so the phase doesn't jump.

**Ring buffer.** 120 s per stream in shared memory, with a header holding the write head; readers copy and retry if lapped. It feeds:
- the slow worker;
- the LLM clips;
- a "what it heard" download, served on `127.0.0.1` only and never through the pass-through, because guest Wi-Fi reaches `:8700`.

**Health per stream (2 Hz).**
- Network: loss, late packets, jitter, hold, drift, last-packet age.
- Audio: peak and RMS, clipping, silence, noise floor.
- Sender: device, state, xruns.
- States: streaming, quiet, stale, down, no_device.
- The card shows one line: `room-mic · 48k/1ch · −24 dBFS · 0.3 % loss · 4 ms jitter`.

**Source choice.** By priority: `room-mic` > `pc22-loop` > `pc22-mic` (and `room-line` first if one ever exists). Switch on health; fall back to the next live source, never to nothing. MIDI is not a fallback for audio: it is an extra, exact layer whenever the piano plays (§3.5).

### 3.4 Hardware (bought, owner 15 Oct)

**Room mic: Audio-Technica ATR4697-USB.**
- An omni boundary mic on the chest-height countertop by the Steinway, 50 Hz–15 kHz, USB-C.
- It hears the PA music and the piano alike.
- Its maximum SPL and any onboard processing are unpublished. 98 tests it for overload and pumping when it is plugged in; the fallback is a Samson Go Mic.

**Piano: the Steinway's adsilent v1 silent system.**
- USB MIDI from a Mini-B port, class-compliant per its guide. Channel 1 is the keys; channel 2 is playback of its own recordings.
- MIDI flows with the mute rail off while the box is on, so it works during normal acoustic playing.

**No line feed, interface or mixer.** The Sonos Connect is in another room. With no line feed:
- the mic carries everything;
- the LLM's privacy gate (§7.5) matters;
- there is no listen-ahead from the music chain.

**Sender box:** the kiosk (§3.2).

**Calibration kit** (a few pounds, §5.5): a USB stereo input adapter with a small electret mic and a BPW34 photodiode, so light and sound share one sample clock.

### 3.5 Piano MIDI

**The sender** (on the kiosk, 98) reads the adsilent's ALSA port. It stamps each event on the kiosk's CLOCK_MONOTONIC (`time.monotonic_ns()`), the **same timebase as the audio heartbeat's `t_cap_ns`** (one clock per process, §3.1), so the audio stream's clock map places notes and sound on one timeline. With no audio stream, MIDI gets its own lower-envelope map.

**The wire:** VBAN TEXT packets (UTF-8 JSON) on the same socket, stream name `room-piano`, sent within 2 ms of an event:

```
{"v":1,"seq":1234,"ev":[[t_ns, status, d1, d2], …],          // this batch
 "prev":[[seq-1, [[t_ns, …], …]], [seq-2, …]]}               // the last two batches again: one lost packet heals at once
{"v":1,"seq":1240,"state":{"keys":[…88 velocities or 0…],"cc64":0-127,"cc66":…,"cc67":…},"t_ns":…}   // every 500 ms
```

**Losses.** Gaps in `seq` are filled from `prev`. The periodic state heals anything else, so a dropped note-off can never leave a stuck note for more than half a second.

**Wire details** (clarified 16 Oct; the reference is `show_service/vban.py`):
- `seq` counts event batches, +1 each, from 1 in each sender session.
- A batch holds at most 32 events. A bigger burst becomes more batches, so every packet fits in 1436 B.
- `prev` is newest first. The sender drops `prev` entries, oldest first, if a packet would not fit.
- A state packet's `seq` is the last batch sent before it. A state does not use up a `seq`.
- `keys[0]` is A0 (MIDI 21). `keys` lists the keys physically down, with their strike velocities.
- **`"sess"` is mandatory** from our senders (the kiosk unit and the test piano), on batches and states alike. There is one `sess` per stream (its device session): new on a sender start or a MIDI device re-open, and independent of the audio stream's heartbeat `sess`, so an audio hiccup never restarts the note table (agreed with 98, 16 Oct). Receivers still accept MIDI without it, from third parties.
- **The receiver's rule** (`vban.midi_seq_verdict`). The wire has restarted when `sess` changes, or when a batch or state has a `seq` more than 8 behind the last (a reorder window wider than `prev`). Otherwise a batch with `seq` ≤ the last is a duplicate, and a state with `seq` < the last is stale.

**The unit** survives the piano box being switched off and on, re-opening the port by name. Every live session is also saved as a `.mid` file on PC22.

**In show_rt:** a **note table** of 128 slots: amplitude, `vel_on`, `t_on`, released, `t_off`, `vel_off`.
- Amplitude = velocity × a per-pitch decay model. The damper (CC64, proportional) holds notes; a key-up without the damper damps fast.
- Plus the pedals.
- **Hot-note cap.** The adsilent v1 sometimes reports absurd velocities on fast same-key repeats (about 1 in 5–10 on some keys). The velocity jump on a fast repeat of the same key is limited.

**What the notes give:**
- **Exact harmony.** Chroma, chord, key and register come from the sounding notes, including pedal-held ones. While the piano plays, they replace the audio estimates.
- **Presence.** "The piano is playing" is MIDI activity, not a guess.
- **Hits.** A note-on reaches show_rt in about 1–2 ms over USB plus the network, with no FFT window. Lamps and DMX can react to the piano directly.
- **Voices.** Knowing which notes sound lets the mic's spectrum discount the piano and find the voice and the room.
- **Rubato.** Onsets are exact, so the clock's activation gets clean input. Piano music often stays unlocked anyway (§5.3), which is fine.

**Test piano.**
- Replays MIDI files as stream `test-piano` (lowest priority, marked test) into the same receiver. Built by 98; runs on PC22, so it stamps with `time.perf_counter_ns()` (§3.1, Sender clock) and sends its own `sess`.
- Material:
  - the owner's saved sessions;
  - MAESTRO (real Disklavier performances with pedals; CC BY-NC-SA, fine here);
  - synthetic checks (all 88 keys, chords, a pedal wash).
- **End-to-end hardware check:** play back one of the adsilent's own recordings from its phone app; it comes out of the real USB port on channel 2.

---

## 4. Listening: real-time features (no AI)

All features are stamped in acoustic time. They are normalised to 0..1 by rolling percentiles (p05/p95 over 60 s and 10 min), and then **gated by absolute loudness** (§6.3), so quiet background music never reads as "wild".

**Hit path, per packet (2.7 ms)** in show_rt's hot thread:
- IIR band filters and envelope followers (`sosfilt` with state, ~20 µs);
- an adaptive threshold with hysteresis and a refractory period.

Group delays are about 3 ms on bass, 10 ms in the kick band, and ~0 ms on highs. A DMX hit packet is sent from the same thread.

**Fast lane, hop 256 at 48 kHz (5.3 ms):**
- SuperFlux onset strength on a 512-point log-mel;
- six band levels (sub, bass, low-mid, mid, high-mid, air), each with level, hits and presence (Synesthesia-style names);
- a 32-band log spectrum, published smoothed (§9.5);
- peak, RMS, and momentary and short-term LUFS (`pyebur128`);
- centroid ("brightness") and flatness.

**Rhythm lane:** audio resampled to 44.1 kHz (soxr) for madmom's online beat activation at 100 fps (§5.3). madmom was trained at 44.1 kHz; at 48 kHz its frames are 9 % off.

**Medium lane, 10 Hz:**
- a causal harmonic/percussive split (median filter): onsets from the percussive part, chroma from the harmonic part;
- harmonic change rate;
- onset density;
- percussive ratio;
- pulse clarity;
- swing ratio, once locked;
- syncopation.

**Rejected:** stem separation (Demucs-style), for GPU cost and latency.

**AGC:** only the tracker lane gets a slow, bounded pre-gain: aim for −20 LUFS, up τ 10 s, down 3 s, +36 dB maximum. It freezes during speech or the gate. Templates are WLED's PI controller and LedFx's ExpFilter.

**Cost:** *measured* 0.2–0.45 ms per hop for the fast lane, plus madmom (next section).

---

## 5. Timing: landing on the beat everywhere

### 5.1 Budget (wired)

| Stage | Typical | Worst |
|---|---|---|
| Mic/ADC + 128-frame period + packet + LAN + NAT + WSL wake-up | 4–6 ms | ~15 ms (powerline adds 5–150) |
| Until an onset is visible (IIR per packet / 512-pt window) | 3–11 ms | 21 ms |
| **Audio → hit known in show_rt** | **8–20 ms** | ~40 ms |
| Art-Net → node → DMX line → LED | 2–25 ms (on-receipt node, short frames) / 12–45 ms (free-running node) | ~70 ms (fixture smoothing: more) |
| Hue: send + bridge + Zigbee + lamp fade | ~50–150 ms *(estimate: measure)* | — |
| TV live stream: the renderer's live lead (server + client buffer EMA + frame + photon) | **0.55–0.85 s** | moves with buffer jumps (after a powerline stall, seconds) |

The 309 ms quoted before was a composed estimate: the lab's stream leg (130 ms) plus the measured TV leg, presented→photon (179 ms rise). The TV leg holds for any path. The production buffer is bigger than the lab's, so the live lead is larger. `stream_service/sessions.py` tracks it (`_update_lead`). Note that south brightens about 75 ms slower than it darkens (rise t50 179 ms, fall 104 ms; probably local dimming), so per-edge timing matters.

**What this means:**
- Reactive hits are only usable on DMX, and only with a node that outputs on receipt and short frames.
- Everything periodic is predicted.
- Lateness is what people notice: they detect light lagging sound at about 45 ms, but tolerate light leading by 100–200 ms.

### 5.2 Targets (on music with a steady beat; photodiode-measured, §5.5)

| Output | Aim (mean) | p95 \|light − sound at listener\| |
|---|---|---|
| DMX | ~10 ms early | ≤ 25 ms |
| Hue | ~15–20 ms early | ≤ 40 ms |
| TVs | ~15 ms early | ≤ 40 ms (LIVE_V1's own glass target), outside buffer upsets |

Precision is spent on **beats and bars** (≤ 4 Hz). People can't judge sync on faster repetitive pulses, so 8th and 16th notes free-run at the clock's tempo. The north TV is not yet measured; it borrows south's latency, so north needs calibrating before P4 claims the two screens are in phase.

### 5.3 The beat clock

**Published state** (on every re-anchor, plus a 10 Hz heartbeat):

```
{bpm, anchor_ms (CLOCK_MONOTONIC of beat k, acoustic), beat_at_anchor: k, beats_per_bar, bar_anchor_beat,
 phrase_bars, conf, bar_conf, phrase_conf, state: searching|locked|holding|free}
```

Consumers compute `beat(t) = k + (t − anchor)·bpm/60` at their own `now + lead`. We **share a clock, not triggers** (Ableton Link's model).

**Estimator and published clock are separate.**
- The estimator may jump: re-lock, octave change.
- The *published* phase is slewed only while confidence is above the beat-lock gate. Below the gate nothing is beat-locked anyway, so jumps are published as they are.
- Slew is applied as a temporary tempo change, never as an anchor step, so consumers extrapolate smoothly.

**Inputs:**
1. **Beat activation:** madmom's online LSTM at 44.1 kHz and 100 fps. It is built from git main, pinned, and kept as a local wheel. *Measured* 1.4 ms median and 2.9 ms p99 per frame (~14 % of a core).
   - Fallback: aubio-ledfx tempo at 0.04 ms per hop. It is weaker (57 % F1), but runs anywhere.
2. **Tempo and stability:** a real-time PLP tempogram (Meier, Chiu & Müller, TISMIR 2024, MIT) over ~6 s of the activation.
   - It gives tempo and octave candidates and a stability value.
   - We reimplement it with a sliding DFT, because the reference code is too slow.
   - A running tempo prior of ±40 % around the last 30–60 s.
   - The published 74.7 % F1 exists *only* with madmom's activation.
3. **Phase observations:** activation peaks, refined by the kick-band IIR hits (known group delay). This also self-calibrates madmom's lag, which varies with timbre.
4. **Integer corrections only, from Beat This!** (MIT), every 5–10 s in the slow worker, on the interior of a 30 s window:
   - which beat is the one;
   - the bar length;
   - the tempo octave.

   They are carried to now by beat count, applied at a bar line, and never used as a phase observation. Its frames are 20 ms coarse and seconds old.
5. **Humans** (show console only): Tap, Half/Double, "Now is the one". These are high-confidence observations, not overrides.

**Update.**
- An alpha-beta PLL with β = α²/(2−α), α ≈ 0.15–0.25 (critically damped, a 4–10 beat time constant).
- The period comes from a least-squares fit over the last ≥16 beats, not from the PLP bin: a half-bin error drifts 33 ms in 16 beats.
- Errors beyond ±20 % of a period are outliers, unless 3 in a row agree, which triggers a re-lock.
- Octave changes need 8 beats of evidence, or a section boundary.
- `min_bpm` is about 45; madmom's default of 55 causes octave errors on slow music.

**Holding.**
- Through breakdowns, the tempo, phase and bar/phrase hypothesis are **held indefinitely**. Only the confidence decays, and it is used only for gating.
- When percussion returns, the held grid is tested first: 2–4 consistent beats re-lock it and keep the phrase count. A cold search starts only if it fails.
- So a drop after a 32-bar breakdown lands on the grid.

**Confidence.**
- It is a *calibrated* probability that the next beat lands within ±X ms. A logistic fit over PLP stability, hit rate, madmom/PLP agreement and the last Beat This! vote, fitted on recordings (§13).
- Hysteresis: beat-lock on at 0.7, off at 0.6; DMX-only on at 0.45, off at 0.35. Minimum dwell 2 bars.
- Beat-locked depth fades over a bar; it never switches.
- From cold, the clock locks in about 3–8 s. Accuracy is scored by beat continuity (L-correct) and a tight window, not by ±70 ms F1.

### 5.4 Rules per output

Each output evaluates its look at **`now + L(output, attribute)`**:
- one latency per attribute kind: intensity uses the *rise* edge, while colour and mechanical position have their own;
- the *fall* latency only sets a minimum pulse width (≥ L_rise − L_fall + the visible width).

| Output | Gets | Never gets |
|---|---|---|
| **DMX** | Beat-locked effects; **hits** (per packet, from show_rt); recurring hits *scheduled* from the bar pattern (§9.5); piano note-ons straight from MIDI | — |
| **Hue** | Gentle beat pulses (attack ≥ 80 ms, so the bridge's tick is invisible); smoothed features; washes from palette roles. Send continuously at 50 Hz, not only on change, so a lost packet heals. | Reactive hits (they land 60–170 ms late). An *unforeseen* drop gets a slow swell (attack ≥ 300 ms); only a *predicted* drop gets a timed hit. |
| **Screens** | The clock, evaluated by the renderer at its own live lead; features smoothed (spectrum32: rise τ 35 ms, fall 300 ms; owner, 17 Oct: sound to glass under 0.5 s); cue hints and drop warnings at least **lead + 250 ms ahead (≈1 s)**; `drop_armed` ≥ 2 beats early | Transients of any kind. A raw spectrum or onset would land ~0.6 s late. No gain or level, ever. |

**DMX frame grid.**
- Frames are **phase-locked to the beat**: `tick = beat_period / m`, with m the largest multiple of 4 that keeps the rate at ≤ 44 Hz (120 bpm → 40 Hz; 128 bpm → 42.7 Hz).
- Re-phase on each clock update, so a frame leaves exactly at `t_beat − L_dmx` and on every 16th.
- A hit pulls the next frame forward but never closer than 22.7 ms to the last one: Art-Net 4's 44 Hz maximum, and nodes do drop excess frames.
- Frames run only up to the highest patched channel (even length), with a per-node `full_512` override.
- Prefer, and measure, nodes that output on receipt: σ ≈ 1 ms, against 6.5 ms free-running.

**Confidence policy.**
- **Locked:** beat-locked everywhere.
- **Middle:** beat-locked on DMX only, at reduced depth; screens and Hue follow bar phase softly.
- **Low, or a vibe with `beat_lock: off`:** nothing beat-locked. DMX follows onsets; Hue and screens follow energy, register and harmony.

**Unpredictable big moments.** DMX hits at once and Hue swells. The screens cut at the first downbeat at least lead + preparation away. The renderer pre-prepares the peak scene once `build_progress` passes 0.5. When the build detector *predicts* the drop on the 8/16-bar grid (§6.2), every output lands it together.

### 5.5 Calibration: a photodiode on the kiosk

**The method.** A calibration-only kit for a few pounds: a cheap **USB stereo input adapter** on the kiosk, with a small electret mic on one channel and a BPW34 photodiode (or a small solar cell) on the other.
- Light and sound share **one sample clock**, so light-versus-sound is measured directly (screen-machine-10's suggestion). There is no clock sync and no USB buffer latency to calibrate.
- The ATR4697 can't take a second channel. Its own offset is measured once against the kit's mic, by cross-correlation.
- PC22's mic input can stand in for dev.
- Each edge is timed to sub-millisecond precision within its stream, so about 20 pulses give an SE around 1 ms after the clock map. Smooth by 2 ms against mains flicker and LED PWM.
- No network clocks enter the result.

**Per output.**
- Record t10, t50 and t90 for rise and fall on every Hue lamp, DMX fixture and TV.
- Align to perceived onset (~t20–t30), not t50.
- Also: is Hue tick-quantised or forwarded on arrival? Run the histogram at 50 Hz against 25 Hz phase-locked. Set the stream proxy to the lamp nearest the bridge.

**End to end.** Play a click track through the room speakers, with the mic and the photodiode on the same interface. This sets each source's `acoustic_offset_ms`.

**TVs.**
- Keep screen-machine-10's Yocto tool as a TV cross-check. Measure **north** with the photodiode: the Yocto can't see north well from where it sits, and moving it disturbs the mask. Re-measure south with the photodiode too; the means should agree within about 10 ms.
- Add a *live-path* run: a renderer-stamped full-frame flash scene measured to photon. 10 offers a `--path live` mode in calibrate.py and, with 04's OK, the flash scene itself.
- Local dimming off on the TVs might shrink both edges. That is the owner's manual choice only: software never writes TV picture settings.

**Ground truth for tuning the tracker.** Cross-correlate the source file with the mic recording, which is sub-ms. Beat This! labels are only good to about ±10 ms.

Results go in `config/room/latency.json` v2 (hot-reloaded; schema in `tools/room/latency_cal/README.md`):
- **`outputs.<output>.<attribute>`** = `{rise: {t10, t50, t90, n}, fall, trim_ms, zero, se_ms, method, measured_at, run_dir}`. `zero` for DMX is "frame sent"; `trim_ms` survives re-runs.
- **`sources.<stream>`** = `{acoustic_offset_ms, method, measured_at}`.

Writer rules: read the file, change only our own keys, write `.tmp` and rename. Readers ignore unknown keys. Re-run after any change to TVs, kiosk, encoder, Hue firmware, node, fixture, mic or interface.

**Piano notes** reach show_rt about 1–2 ms after the key, plus the powerline. Expect about 5–20 ms, with spikes. That is the fastest input we have:
- Lamps and DMX can answer individual notes. Hue answers gently: it can't flash, and its latency is unmeasured.
- Screens never flash on notes. They *bloom and sustain*: each note is aged against the renderer's expected photon time, so its brightness on glass follows the decay the ear hears. Scrolling and waterfall forms hide the remaining lag.

### 5.6 Listen-ahead

Any source we tap *before* a playback delay gives look-ahead equal to that delay:
- PC22 loopback ahead of its speakers;
- possibly Sonos line-in. Its delay settings are *unverified*; reports range from a 75 ms minimum to about 3 s on a Port. Measure it by cross-correlation per session before relying on it.

With look-ahead at least as long as an output's latency, even unpredictable hits land on time there, including the TV at ≥ 0.6 s. It doesn't apply to a live piano. With the room mic as the only music source (§3.4) it applies only to PC22 playback during dev.

---

## 6. Understanding: local ML and structure (the 1–10 s tier)

The slow worker is a child process of show_brain with **its own venv**: essentia, or ONNX exports; torch only if Beat This! can't be exported. It runs on CPU at nice 19 with ≤ 2 threads, and skips a cycle rather than queueing.

**v1 models** (*measured* on PC22 under load):

| Every | Model | Gives | Cost |
|---|---|---|---|
| 1 s | **Discogs-EffNet** (ONNX) + heads | 15 parent genres (from 400 styles); 1280-d embedding (for sections); heads: moods, instruments, danceability, voice/instrumental, engagement, bright/dark, acoustic/electronic, arousal | 6–9 ms + <1 ms per head |
| 1 s | **CED-mini** (AudioSet, Apache-2.0) | music / speech / conversation / singing / crowd / applause / silence: **the gate for everything**, and the room's "talking" state | 22 ms |
| 2 s | Essentia HPCP + Key over 20 s (from MIDI chroma while the piano plays) | key, mode, key strength; plus **tension** (dissonance and harmonic distance from the key) and **chord_change**, published for the renderer | few ms |
| 5–10 s | Beat This! | integer corrections to the clock (§5.3) | 0.8–3 s |

**Reliable on live audio:**
- music vs talk;
- genre family;
- acoustic vs electronic;
- vocal vs instrumental;
- danceability;
- arousal;
- engagement;
- bright vs dark;
- main instruments;
- key, on tonal music.

**Unreliable:**
- fine styles;
- valence;
- specific moods;
- chords;
- anything with people talking over the music.

So:
- smooth everything over 10–60 s with hysteresis and minimum dwell;
- freeze genre and mood while music probability is under 0.5;
- scale confidence down while speech is above 0.3.

**Genre words** (17 Oct: 04's 31 vocab.yaml genres; 2b's audit). EffNet's 400 fine styles map onto the genre words through show.yaml `genre_map`: `styles` gives a style its own word (Electronic---Deep House → house, Rock---Math Rock → indie), and `parents` gives each family's default for the rest (Jazz → jazz, Electronic → electronic; null = no word, as for Non-Music). The 15-parent table alone left the genre null on almost every publish (the parent max crowned Rock for jazz; Electronic waited for the listener), while the top fine style was right almost every second.
- Each tags line's top 5 styles (`ml.tasks.tags.styles_top`), weighted by activation², give word shares; a style with no word counts against the leader.
- The shares are smoothed (`tau_s` 30 s), frozen and speech-scaled as above; the leader changes with the same margin and dwell.
- **conf = the leader's share.** It is calibrated: one family dominating reads 0.5–0.95, a mixed track about 0.3. The director takes the word at `director.genre_min_conf` 0.4.
- room.direction's **`genres`** carries the top 3 shares (summing to 1) even when no single word clears the bar, for the renderer's genre_fit tags.
- Measured on the 9 test tracks: the word now reads ~90% of the time on 7 of them (before: never); the two soul tracks split blues / funk / reggae.

room.direction also carries **`valence`** (0 sad … 1 happy: show.yaml `structure.valence`'s weights over the mood head's top labels), **`organic`** (the acoustic head) and **`hold`** (a person holds the look; the renderer's scene and FX directors change nothing while it is true, owner 17 Oct). Extending vocab.yaml is 04's call.

### 6.1 Sections

**Boundaries.** A causal half-checkerboard novelty runs on the 1 Hz embeddings: `1 − cos(mean(last 4 s), mean(the 15 s before))`. A boundary is confirmed after 3–5 s and snapped to the downbeat.

**Memory.**
- Each section's centroid is remembered per track, so a returning section is a repeat ("A again").
- The most-repeated high-energy cluster is the **probable chorus**.
- For a known repeat, its length is predicted from the first time through, so the next change can land on the predicted phrase line, not 3–5 s late.

**Track changes.** A gap, plus a novelty spike, plus a jump in tempo, key or genre, resets the memory. If the source exposes now-playing metadata (Sonos), that is the truth.

### 6.2 Build, drop, breakdown (DSP only)

Trajectories are slopes, not textures. CLAP-style taggers are near chance on order in time. The detectors are ported from FastLED's audio suite (MIT) to numpy.

| State | Rule |
|---|---|
| **Build** | Over 8–16 s: loudness, centroid and onset density rising, while 20–120 Hz is held or falling. Output `build_progress` 0–1. |
| **Drop** | Within one beat after a build: the bass band jumps more than ~6 dB, with a loudness jump and a novelty spike. |
| **Breakdown** | Bass and percussion collapse while harmonic content stays. |
| **Predicted drop** | While a build holds, `drop_eta` is the next 8- or 16-bar line from the held phrase count, weighted by `phrase_conf`. |

Jazz and classical have no builds or drops in this sense. The detectors fall back to energy and novelty phases there, without forcing labels.

### 6.3 How loud is the room?

Normalised features would stretch quiet 8 pm background music to full scale. So:
- `energy = normalised × g(room_loudness)`, where `g` stays low below the mode's expected level (e.g. Party around −20 LUFS, Dinner around −35).
- Room loudness comes from the mic's calibrated LUFS (the room mic is the source), cross-checked with the Sonos volume once aa's Sonos adapter exists (it's a placeholder today).

### 6.4 Later, only if an evening's logs show a gap

- CLAP or MuQ-MuLan open-vocabulary axes;
- MusiCNN arousal/valence;
- silero-VAD (CED covers speech);
- MAEST for finer genre.

---

## 7. The LLM listener

### 7.1 Role: guide the director, arm the cues, never fire *(owner, 18 Oct; README S5.22)*

**Inputs.** The listener only *describes*. It never sees the visual direction or the nudges. Its verdicts are about 13 s old when they arrive (a 10 s clip, then ~3 s for the call).

**It guides the art director** (§8.2). The owner: the AI "can characterise 'this is soft jazz', which otherwise the system generally won't know, and that should certainly help the a.d. determine wildness, intensity, style etc. of approach". So, while its beliefs are confident (≥ 0.6, fresh or holding):
- its **genre** leads room.direction's `genre` and `genres` and the band base's `genre_energy` term; the local ML's genre is the fallback;
- its **approach.intensity** (how wild and intense the visuals should be) *is* the band base's genre term, the style's base wildness, slewed ≤ 0.1 a second;
- its **approach.transitions** sets `transition_style` and its **approach.change** sets `change_rate` (show.yaml `director.listener_change`: slow 0.15, medium 0.45, fast 0.75), but only where no person and no feel sets them: **human > feel > listener > bands**;
- its **moods** replace the band moods, as before; its **trajectory** still nudges the energy.

**It arms cues that a real-time component fires** (cues.py, the cue engine, 10 Hz). The listener's `ending` picks the standing cue for the song's end; its `coming` forecasts are armed with their windows and fired on the beat clock when the DSP sees their signs (§8.5 "Cues"). The listener never fires anything itself.

**Confidence comes from agreement over time,** not from the model's self-reported confidence.
- Votes decay with half-lives: genre 120 s; mood, and the approach's words and intensity, 45 s. One read is half confident; two agreeing reads are confident.
- A genre word changes only when a new leader wins two reads in a row by a margin, or at once on a DSP track change.
- `character` and `coming` are the newest clip's (each window on the acoustic clock, from the clip's end; a passed window is dropped). `ending` is the latest word that said something, held 60 s.

**Expiry:** hold 60 s, then fade to neutral over 60 s.

**What it's asked** (§7.3's output).
- Ask: what the music is in a line (`character`), genre and style, mood words, the recommended approach, trajectory, how the piece will end, what is coming (with its window, the signs that will announce it, and how big a moment it deserves), track change.
- **Not any more** (18 Oct): instrumentation and vocals (the local ML worker gives them free), section function, same-track and evidence (nothing used them). Output tokens dominate the cost, so the answer is compact.
- **Never:** BPM, key, beats or energy numbers. Audio LLMs score near zero on those, and the DSP does them. Never the track's identity or lyrics either: those can trigger refusals.

### 7.2 Model and cost *(owner, 17 Oct; checked against Google's and OpenAI's own pages that day)*

**Primary, the owner's choice (17 Oct): Gemini Flash** through Gemini's OpenAI-compatible endpoint, on the same client (`gemini-3.8-flash` today; `GEMINI_API_KEY`, paid tier, whose data isn't used to improve Google's products).
- Gemini 3.x Flash scores 70.7–71.7 on MMAU-Pro music, against 67.3 for gpt-audio.
- Verified 17 Oct: the endpoint takes `input_audio` parts and tools, and `models.list()` lists the ids (ai.google.dev/gemini-api/docs/openai); 3.8 Flash takes audio in (its model page). With the owner's key, one text-only call with the real tool and prompt answered in 4 s for $0.0014.
- **Cost:** $0.75 per 1M input tokens, audio included, and $3.75 out including thinking, through 31 Dec 2026 (both double on 1 Jan 2027; ai.google.dev/gemini-api/docs/pricing). Audio is 32 tokens/s.
- **Measured 18 Oct** (8 real calls with the new answer on test-music clips, README S5.22): 2.1–2.7k prompt tokens (audio included) and 150–215 out a call, **$0.0022–0.0028 a call (mean $0.00256)**, 2.9–3.6 s. At the new cadence: **$0.62 an hour steady** (every 15 s), $1.85 an hour while everything develops (every 5 s); the card's estimate assumes half the time fast until an hour has measured it: **about $1.23 an hour, $4.92 an evening** (it was $2.90 at a 24 s clip every 20 s).
- Request: `reasoning_effort: low` (3.x can't turn thinking off), the OpenAI-only `store`/`modalities` left out, tool schemas in OpenAPI's dialect (`nullable`).

**The fallback: OpenAI `gpt-audio-1.5`** through Chat Completions with `input_audio`. Without the Gemini key it is the one used, as before.
- **Cost:** audio ≈ 10 tokens/s × 24 s × $32/M, plus about 1.6k text tokens in and 200 out at its text rates: about $0.014 a call, **about $10 an evening**.
- gpt-audio and gpt-audio-mini shut down on 20 Jan 2027 (gpt-audio-1.5 doesn't), so gpt-audio is no longer listed.

**The cost is visible** (README S5.20): `GET /api/v1/listening` carries `estimate` {per call, per hour, per evening (show.yaml `listener.evening_h`, 4), cadence, model, provider, basis} and the card's `listening` block `spend_today_usd`, `est_evening_usd` and `provider` ("Google" / "OpenAI"), even while the switch is off. A clip's cost is the ledger's recent mean for the model that would be used, else llm.yaml's `typical` usage × its prices. The ledger computes real cost from the usage tokens and the prices in `config/llm.yaml`.

**For information:** a one-evening comparison on the owner's own music can be logged once the listener runs. It changes nothing unless the owner says so.

**Model ids live only in `config/llm.yaml`.** The layer reads `models.list()` at startup (the listener's warm-up) and hourly. An explicit id that is listed is used as written; else, for an entry with `discover`, the newest listed id matching its pattern (the newest plain Flash); else it is skipped, logged once, and the task falls down its list. A provider without its key is skipped. A model that rejects the forced tool is skipped from then on.

**No local audio-LLM:** they need ~20 GB of RAM or a free GPU, and we have neither.

### 7.3 The call

**Cadence** (owner, 18 Oct: "24 s every 20 s is too long a round trip"; show.yaml `listener`).
- A **10 s clip every 15 s** while the music is steady.
- **Every 5 s while something develops:** structure says build or outro; a forecast window is open or opens within 20 s; a predicted song end is within 30 s; a section boundary came in the last 10 s.
- Up to **2 calls in flight**, starts at least **4 s** apart; plus one call 8 s after a DSP section boundary (at least half the interval after the last start, one per interval).
- No relaxed back-off any more (it was 45 s after 3 agreeing verdicts).
- Skip while CED says silence or talk.
- A daily budget slows the cadence; it never stops the listener.

**Limits.** Timeout 12 s, no retries: the next window is the retry.

**Clip.** Mono, 24 kHz, normalised to −16 LUFS, MP3 64 kbps, encoded in memory (~80 KB for 10 s). The true LUFS goes in the text. A room clip is the longest music-only run in those 10 s, at least 6 s.

**Message.**
- A static, cacheable system prompt with the field definitions, the vocab words and these rules: trust the measured numbers; "unknown" is valid; ignore speech content.
- The user content holds:
  - a 120 s DSP timeline at 2 s resolution (<600 tokens);
  - the segment map;
  - what structure knows now: its phase, how far into the section, a known repeat's length, the probable chorus, an armed song end (README S5.22);
  - the previous verdict marked "hypothesis, may be wrong", left out every 5th call;
  - the audio.

**Output** (one forced tool; the words are plain strings, cleaned to these lists after the call, so one stray word never fails an answer nor sends the clip again to the fallback; README S5.20):

```
content: music|speech|mixed|silence|noise
character: ≤12 free words ("soft late-night jazz trio")
genre {label (vocab), detail (free text), confidence}
mood {words ≤3 (vocab), confidence}
approach {intensity 0..1, change: slow|medium|fast, transitions: gentle|lively|wild, confidence}
trajectory {state: building|peak|sustaining|releasing|steady|winding_down, confidence}
ending: cold|fade|segue|unknown
coming [≤2] {kind: chorus|drop|climax|breakdown|key_lift|band_in|vocal_in|stop|outro|end|new_section,
             eta_min_s, eta_max_s (from the clip's end), signs ⊆ [louder, low_in, busier, stop_return,
             timbre_change, key_change, drums_in, drums_out, vocal_in, vocal_out, phrase_line],
             size: small|medium|big, confidence}
track_change
why: ≤12 words
```

`GET /api/v1/listening` and the card's listening block also carry `character`, `approach` {intensity, change, transitions, conf} and `armed`, the cue sheet: [{kind, words ("Big change at the chorus, likely in 10-25 s", "Ready: slow fade if the music fades"), window_s, size}] (README S5.22).

`genre.detail` values ("bossa nova", "Afro house") are logged to grow the vocabulary.

### 7.4 "Describe it"

A text box under More on the Music card, e.g. "smoky jazz club".
- **What it can set:** only what the card itself can set, within the current mode:
  - Calmer or Wilder steps;
  - a palette from the library (or one shifted in OKLCh);
  - moods from vocab;
  - Keep this look.
- **Results:** shown as undoable chips.
- **If the text implies another mode** ("make it a rave"), the reply is a one-tap **"Switch to Party?"** tile. It never switches silently.
- **While waiting:** "Working on it…", then the change applies at the next bar.
- **If the LLM fails:** a local keyword match against the vocab and palette names. It degrades; it never refuses.

### 7.5 Privacy

The room mic is the only music source (owner, 15 Oct), so clips would carry guests' conversation to a cloud API every 20 s.
- Clips go only while CED says the audio is music-dominant and speech is below about 0.3. The clip is cut to the music-dominant seconds.
- A vibe can turn the listener off (`listener: off`). For example, while the piano plays MIDI already gives the harmony and the dynamics.
- **Owner, 17 Oct: nothing goes to any provider unless Auto is on AND the room's mic is on**, for cost and because it is intrusive. Unknown counts as off, and the gate runs again right before each upload, so a clip cut before Auto or the mic went off is never sent. The ring doesn't even record for the listener while the gate is shut. Config: show.yaml `listener.requires_auto` / `requires_mic` (both true).
- **The gate, in full** (README S5.20): the AI listening switch ("AI listening"), Auto on, the mic on, the feel not saying `listener: off`, sound coming in, substantive music (below), the loudness floor, a usable model. **The mode's show.main `listening` no longer gates** (owner, 17 Oct); it stays in the API for readers.
- **No substantive audio, no upload:** a clip needs the music/speech check (music-dominant, speech under ~0.3, over a continuous run) and the loudness floor (`listener.quiet_gate`) over the last 10 s. Without the classifier running, nothing goes.
- On the Music card (e1, 17 Oct, one listening block): the **Room mic** switch with its input picker, then **AI listening** (the switch, called "Identify the music" until 18 Oct: off stops all uploads), then **What it hears**. The menu has no switches. Describe it, the DSP and the local ML carry on whatever these say.
- To date (17 Oct) one clip has ever been sent ($0.006). On 18 Oct, 8 clips of the test-music files (never room audio) validated the new answer (README S5.22).
- Requests use `store: false` on OpenAI (Gemini's endpoint has no such field), and never a free tier.
- The camera on PC22 (Insta360) is **out of scope**: no room stills go to any model.

### 7.6 The `utils/llm/` layer, and gpt-4o

**What it is.** A small layer: a provider registry, cached sync and async clients, a capability table, and `call_json(task, …)` with validation, a circuit breaker, fallback, a JSONL cost ledger and deduplicated `llm.*` alerts.
- As built (README S3.12, S5.20): providers carry `label`, `omit` and `schema` (`openapi`); model entries `discover` and `typical`; `probe(task)` reads `models.list()` ahead of the first call; `estimate(task, audio_s=)` prices one call (ledger, else the price table; price rows may be globs); `lenient=True` keeps an answer whose only schema problems are soft. Alerts are log lines (logged once), not `llm.*` alerts.
- It targets the **openai 1.x API**. Flask pins `openai<2`, with 1.77 installed, which supports `input_audio` and tools.
- So the same code serves Flask and show_service, and show_service pins 1.77 too, until both move together.

**The gpt-4o deadline.** `routes/openai.py` defaults to `gpt-4o`, which reportedly shuts down on **14 Jan 2027**. Its four callers (scheduler image reasoner, refiner, translate, Alexa) need a new default before then.
- This is a one-line change plus a Flask restart, for whoever owns `routes/`.
- `openai_prompt()` can later sit on `utils/llm` as a thin shim.

---

## 8. The art director

### 8.1 Modes are the vibes

Each mode in modes.json gets a `show` entry, applied by a new `show` room adapter. c8 writes it to aa's contract (`roomstate/contract.py`; ROOM_STATE_MODEL §5).
- **Shape:** subsystem `show`, member `main`, kind `level`; change time ~2 s, poll every 5–10 s, read timeout 2 s.
  - `normalize` checks `{vibe: str, listening: bool}` offline and returns it in canonical form.
  - `value_schema` gives the mode editor its form.
- **Reading:** a GET of the active vibe, in exactly the normalised shape. If show_service is unreachable, the read raises, the belief goes stale and the subsystem shows `available: false`.
- **Applying:** a PUT to `/show/api/v1/vibe`, then a read-back.
  - It returns `Outcome("ok")`, or `Outcome("failed", "show_service unreachable")` with retry allowed.
  - Unless a rule lists `show` under `abort_on`, a failed row only makes the transition `partial`, so a mode never blocks.
  - Reconcile retries with backoff, and health recovery re-applies when show_service is back.
- **modes.json:**
  - Modes are flat. A mode that uses the show has `"show.main": {"set": {"vibe": "…", "listening": true}, "restore": true, "policy": "ignore"}`. A mode that doesn't mention `show.main` leaves it alone. `"show.mic": {"set": true, "restore": true}` makes the mode follow the music, so the mic listens while it is on, and a restore puts back what was there before.
  - With `ignore`, the vibe applies on arrival and retries until it lands. After that it's the user's, so nudges never show as drift and are never undone.
- It is registered in `build_adapters()` and tested at "doesn't crash" level with a fake HTTP server.
- **Going live** needs the P2 Flask restart. Any modes.json that names `show` must wait for that restart: modes.json hot-reloads, the code doesn't.

`config/room/vibes.yaml` holds the vibes as hot-reloaded data:

Today's modes and their proposed vibes (examples, all config):

| Mode | Vibe | Screens | Lamps |
|---|---|---|---|
| Normal (gallery) | `home` | art | house (the show doesn't touch them) |
| Piano (a live mode since 16 Oct: TVs on HDMI, mask suspended; **blinds and lamps untouched**, because people play in the daytime: `blinds: null` and `hue: null`, live since 16 Oct) | `piano` | live, `scene_tags: [piano]` (piano-waterfall-classic, -ember, -harmony) | untouched (`hue: null`) |
| Lights only | `dinner` | art | above-lamps take the art's colours; key lamps warm; slow |
| Ambient | `chill` | live (effects on the art) | slow washes from the art palette |
| Party / Live test | `party` | live | full colour, accents, drops |
| Disco | `disco` | live | as Party, hotter |
| Off | — | standby | house |

**Vibe fields:**
- energy range and expected loudness;
- follow amount;
- change rate and dwell;
- palettes (ranked; `art` allowed);
- `key_cct` and `lamp_chroma_max` for the key lamps;
- accent policy: none, soft, downbeat, beat or full;
- strobe allowed;
- peak tokens per hour;
- whether the LLM listens;
- a one-paragraph persona for the LLM.

**Modes are plain config** (owner, 16 Oct). They are non-hierarchical for now. A hierarchy may come later, but only if the Room page shows it plainly; never as hidden structure in the file. Each mode carries every setting it makes, and users can add, duplicate and delete modes (2f and aa are flattening modes.json). So:
- **Strobe and accent limits** are explicit fields on each mode's `show` entry (`strobe: false`, `accents: soft`). They are never inferred from a mode "class". Rule of thumb for the owner: allow strobes only in a mode that also lowers the blinds.
- A mode whose lamps move faster than slow washes should suspend the sensor mask in its own settings, as Lights only does today.
- Each mode's existing `screens.live.direction` (Party energy 0.8 and so on) moves into its vibe (2f).
- Vibes are a convenience, so several modes can share one. A mode may also carry the vibe's fields inline.

**No special cases** (owner, 16 Oct): every behaviour is a vibe or mode setting, and any mode can use any of them. The behaviours earlier drafts tied to Piano are general fields:

| Field | Values | What it does |
|---|---|---|
| `beat_lock` | on / off | Whether anything locks to the beat (off = follow dynamics, register and harmony only) |
| `change_looks` | `phrase` / `between_pieces` / `time` | When the director may change looks. `between_pieces` = no notes for 2 s or more with the damper up, or applause. |
| `follow` | list of `dynamics`, `register`, `harmony`, `energy`, `onsets` | What steers parameters within a look |
| `relax_on_talk` | true / false | Whether talking relaxes the room to the base look |
| `listener` | on / off | Whether the LLM listener runs |
| `scene_tags` (on the mode) | tags | The pool of scenes; scenes declare `needs` (`notes`, `spectrum`), and the director only picks scenes whose needs are present |
| `strobe`, `accents`, `peak_tokens_per_hour` | | As in §8.2–8.5 |

**Example: today's Piano mode** is configured with `beat_lock: off`, `change_looks: between_pieces`, `follow: [dynamics, register, harmony]`, `relax_on_talk: false`, `listener: off`, `scene_tags: [piano]`, `hue: null` and `blinds: null`.

The same fields serve any other mode. A "quiet jazz" mode could use them; so could a party where someone sits down at the piano and the director favours note scenes, because scenes whose `needs: [notes]` are met become eligible.

### 8.2 Layers (composited every 100 ms, lowest first)

| Layer | What |
|---|---|
| **L0 Vibe** (from the mode) | Bounds and policies |
| **L1 Music-auto** | DSP and local ML through the band table, generalised from `music_direction.py`; gated by room loudness |
| **L2 LLM context** | Words need two agreeing reads; numbers slewed ≤ 0.1 per update; arms cues |
| **L3 Owner and guests** | Calmer/Wilder steps, palette pick, Keep this look, House lights, Lights low |
| **L4 Momentary** | Next look |
| **L5 Caps** | Optional settings, **default off**: flash rate, red-flash, strobe |

**Numbers:**

```
f = clamp_to_vibe(vibe.centre + follow·conf·liveness·(music − neutral) + c_llm·llm_bias + steps·step_size)
```

**Words**, highest priority first: human pick, then LLM (two agreeing reads at ≥ 0.6), then music bands, then vibe defaults. The genre: the LLM's, then the local ML's, then the feel's. `transition_style` and `change_rate`: a person's or the feel's own, then the LLM's approach, then the band row's (README S5.22).

### 8.3 Output: one look, allocated per output

**`direction`.** The renderer's vocabulary, plus `arousal`, `tension`, `section`, `density` and `groove`. P1 needs 04's `validate_direction` fix (ignore unknown keys) first.

**`look`** (fixture-agnostic):
- a palette with roles (base, accent, highlight);
- level and background floor;
- key CCT and chroma limit;
- saturation and contrast;
- motion speed and amount;
- accent density and style;
- spatial spread;
- change rate and transition style.

**`events`** (stamped in time): `section_change`, `phrase`, `build`, `drop_armed`, `drop`, `accent`, `cut`.

**The screens rule.** `level`, the floor, Lights low, House lights, dips and blackouts apply to **Hue and DMX only**. Screens receive direction, clock, palette and events. "Calmer" or "darker" on a screen means a different scene, palette or word, never a gain or a mask. The TVs never go black from a show event.

### 8.4 Nudges: simple, visible, no silent drift

**Relative step buttons.** Calmer and Wilder move one step within the mode's bounds. The server holds the value and pushes it to every phone, so two phones never fight over a stale slider.

**Lifetimes.**
- Steps and palette picks **last until the mode changes or Back to auto**. There is no silent decay.
- **Keep this look:** a fixed 15-minute countdown, shown on its chip.
- **House lights:** a toggle, instant and not quantised. Lamps (and DMX) go to the room's restored house state, or warm white at 80 %. Mode and screens are untouched; tap again to resume.
- **Lights low:** a latched chip. Lamps go to 10 % over 2 s.
- **Next look:** fires on the next bar (at most about 4 s), and shows "Changing…" at once. Phrase quantisation is for the director's own changes, not for a human tap.

**Mode changes clear every nudge.**

**Logging.** Every nudge is logged with the music context at that moment. That log can later teach the director the owner's taste.

### 8.5 When looks change (restraint, written down)

**The director's own changes.**
- A **confirmed section boundary** changes the look on the next downbeat; min_bars doesn't apply.
- For a *known repeat*, the change lands on the predicted phrase line.
- Without a section change, `min_bars` (32 / 16 / 8 at change rate 0 / 0.5 / 1) throttles changes, which happen only on 8-bar phrase lines.
- If a change would land more than 1 bar into a section, **don't change the look**. Express the section within the look instead (level, accent density, spread), which responds within a beat.

**Holds are the default; transitions are spent on sections.**
- After 64 bars without change, add a *variation*, not a new look.
- Inside a look, vary every 4 bars, with a fill on a phrase's last bar.

**Section memory.** A returning chorus gets its look back.

**Song change.** Allows a palette change from the mode's list, and clears the section memory.

**Song ends** (owner, 17 Oct: the art director sees the end of a song coming and crosses to the next one naturally, with a fitting transition). As built in show_service/README.md S5.17. **Live end to end, 17 Oct:** show_service 22fd99b0, plus 04's renderer half 044823cd (prepares on the arm, fades to the black scene and back, cut or crossfade on the downbeat, a big change makes a fresh pick and a small one a variant of the same plug-in, a held look is left alone, both screens cut together).
- **Seeing the end coming.** While Sonos plays a song with a known length, structure works out when it ends from Sonos's position and length (they are read every 2 s, in whole seconds, so it keeps narrowing the estimate). About 20 s before the end it says so (`track_end_armed`), updates it if the end moves, and calls it off on a pause, a skip or a seek. Radio and streams have no length, so there is nothing to see coming.
- **Hearing the end.** Without Sonos (or between Sonos's reads), a silence of 3 s after music is a song's end, dated from the start of the silence (`track_end`). A shorter rest is part of the song. A silence while Sonos says the song still has a long way to go is a break inside it, not the end. Talk alone never ends a song.
- **The new song.** The track change comes as before (Sonos's next track, or the sound's own evidence). Its time is the new song's first beat when the clock has one, else its first sound; if Sonos changes track during silence, structure waits for the new song's first sound.
- **Choosing the transition.** The director picks one for each of these moments, and it travels on the event itself so the screens play it at that change:
  - **a gap between the songs** (the room goes silent at the end, or the song is fading out): the screens fade to dark as the old song ends and fade in with the new one. Each half lasts 1.5–4 s, and they never stay dark more than 8 s;
  - **one song running straight into the next:** a cut on the new song's first downbeat when the beat is locked, else a 2 s crossfade;
  - **the feel adjusts it:** a gentle feel never cuts (it crossfades, and everything is slower), and a wild one cuts even without the beat (and is quicker). With no feel set, the screens still follow songs by the plain rule;
  - **how big a change:** genre, key, tempo, overall energy and (from Sonos) the artist are compared. A big change brings a new scene family and a new palette; a small one keeps the family and the palette. Until the new song has been heard, the change counts as big.
- **The sound comes back with no new song** (the same song after a long rest): the screens come back too.
- **A person's hold wins.** While someone holds the look (Keep this look, or their own change is about to land), the events say "held", there is no transition, and the director's own look stays put.
- **Lamps.** At a fade-through-dark end the lamps sink to the feel's floor (never off), then the new song's look lands on the first bar line the screens' lead allows, crossing over the same way. This happens at every song change, whatever the feel's change rule.

**Cues: the listener arms, the cue engine fires** (owner, 18 Oct; cues.py, 10 Hz on show-brain; README S5.22). Everything goes out as a room.event the renderer already knows, so 04's side needs no change.
- **Standing cues**, armed while music plays, with or without the listener:
  - **an abrupt stop**: the fast level falls 20 dB under the 2 s before within 0.3 s, ends near the stream's floor, after music. The screens go dark at once (`track_end` detail `stop`, fade-dark, out 0.2 s, in 1 s); if the sound comes back before structure's own end takes over, the same id comes again with `sound_back`. structure's gap-based `track_end` (same id) may follow; the renderer, already dark, ignores the second;
  - **a fade-out**: structure's outro phase. The screens fade to dark over the time the music takes to reach the floor at its present slope (3–12 s), and come back if it comes back up;
  - the listener's **ending** picks: cold arms the stop only; fade arms the fade (the stop stays armed: a fade never trips it); segue arms neither, so the track_change's crossfade carries it; unknown, or no listener, arms both.
- **Forecast cues** from the listener's `coming`: one armed per kind (a newer forecast replaces it; its window's end disarms it). Confident (≥ 0.6) with a phrase line of the beat clock inside the window and an early sign (a build, the loudness rising, one of its own signs): it fires AT that line. Otherwise it fires on the next downbeat after one of its signs is seen inside the window (signs from the DSP, structure and the local ML: louder, low_in, busier, stop_return, timbre_change, key_change, drums_in/out, vocal_in/out, phrase_line). Big is a `drop`, medium a `section_change` (detail `forecast: <kind>`, id `cue:<kind>:<n>`); small is logged only. At most one fire a bar; nothing fires while a person holds the look.
- Every arm, fire, hold, miss and each window's outcome (what structure itself heard in it) is logged (`cues.jsonl`, and the snapshot's ring), to measure the hit rate. The armed sheet is on the card (§7.3).

**Builds and drops.**
- During a build, every 2–4 bars: step the accent subdivision (½ → ¼ → ⅛), raise saturation, and narrow toward one tension colour.
- Pre-drop: lamps dip to 20–30 %. Screens never dip.
- Drop: a hard look cut and a unison 1-beat hit on lamps and DMX.
- Only a drop that follows a build spends a **peak token**, from an hourly budget per mode (Party 4–6 an hour).
- Breakdown: raise the floor, slow the motion, cut saturation 20 %.

**When unsure** (low beat confidence): dwell by time (45–180 s), change at energy-novelty points with 2–4 s crossfades.

**Silence or talk:** relax to the vibe's base look, never black (unless the vibe sets `relax_on_talk: false`). No audio: hold for 10 s, then drift to the base look over 20 s.

### 8.6 Starting and ending a show

**Entry** (a mode that sets lamps to follow the show):
- Lamps fade from house to the first look over about one phrase (8–10 s).
- The first look's palette is **from the art** where the vibe allows.
- No beat accents until the clock is locked *and* one phrase has passed.

**Exit.**
- The mode's transition first asks show_service to release Hue (`POST /show/api/v1/hue/release`) and waits for the `released` reply.
- Only then does the lamps' generic put-back (`restore`) bring them back, over 5–10 s.
- DMX nodes fall to their failsafe house scene.

**Around the screens' cuts.** While any screen is entering or leaving live (opening on, or converging to, its still), the director sends that screen no cue hints, cuts or accents.

### 8.7 Who wins: the director vs today's controls

- The bus `room.direction` (P4), or our virtual channels (P0), feed the renderer's **music proposal** slot, the one `MusicDirection` fills today. `effective_direction()` stays.
- When the show starts driving, the per-session panel trim is reset to neutral.
- While the show drives, the screen card hides its energy chips and Colours row; those become the room-level Calmer/Wilder and palette on the Music card. The screen card keeps Scene, Next (this screen) and Effects.
- Per-lamp House/Follow stays as a per-lamp override, and the director respects it.
- Palette has **one writer**: the director sets the room's `look.palette` target through room_service, not past it.

### 8.8 Colour

**Palette choice.** Palettes come from three places:
- the mode's ranked list;
- **the art**: 3–5 OKLCh clusters from the on-glass still that room_service already captures, mapped to base, accent and highlight roles;
- the human.

**What music changes.** Only saturation, level, contrast and which role is emphasised, all through arousal and energy, which are reliable.
- Palettes change at a confident song change, or on a tap.
- Valence and key never choose a hue: in the ICLR 2026 study, emotion → hue rules scored 2.67/7 against 4.51 for humans.

**Lamp roles** (from patch.yaml's `placement`):
- **above** a screen: extends it (that screen's art colours; motion when the mode allows it);
- **opposite**: the key light. Warm white (2400–3000 K) or chroma ≤ ~0.05 in Home, Dinner, Chill and Piano. Full colour only in Party and Disco. Never green or cyan.

**Interpolation.** OKLCh (coloraide), so screens, Hue gamuts and DMX emitters agree.

---

### 8.9 Auto: the room picks its own mode (the supervisor above the director)

The **director** decides how a mode looks. **Auto** decides *which mode* the room is in. With Auto on, it watches the streams and switches modes itself; for example, the piano starts, so Piano.

Research (16 Oct): Home Assistant, Lutron, Hue, ecobee, Nest field studies, Sonos TV Autoplay, and HCI work on automation surprise and mixed-initiative design all agree:
- confirm before entering, and leave much more slowly than you enter;
- a manual choice holds until the activity ends;
- Undo means "wrong, don't do that again";
- say what changed and why, every time;
- act only when sure, otherwise offer one tap;
- never let automation start music, change volume, move blinds or spin the ball.

**Where it lives.** A slow rule engine (about 1 Hz) in show_brain, next to the audio, MIDI and listening data. It acts only through room_service's public mode call:
- `PUT /api/v1/mode {mode, source: "autopilot", reason, expect, skip}`;
- `source` already works today;
- `reason`, `expect` and `skip` are small additions for aa and 2f (below).

Rules are hot-reloaded config, so tuning them never needs a Flask restart. The internal name is `autopilot`, because "auto" already means the renderer picking scenes. The page label is **Auto**.

**All config, per mode** (owner: modes are plain config). Each mode can carry its own `autopilot` entry, so adding, duplicating or deleting a mode carries its triggers with it. There is no piano→Piano mapping in code:

```json
"piano": {
  "label": "Piano",
  "autopilot": {
    "enter_when": {"piano_playing": {"for_s": 10}},
    "from": ["gallery", "lights-only"],
    "leave_when": {"piano_idle": {"for_s": 30}},
    "leave_to": "previous",
    "skip": ["drawing-room:blinds", "mirror_ball", {"drawing-room:tv": {"from": "standby"}}]
  },
  "state": {
    "sonos.drawing-room.volume":   {"set": 0, "restore": true},
    "sonos.drawing-room.playback": {"set": "paused", "restore": true},
    …
  }
}
```

**The owner's flow** (16 Oct):
1. Auto is on. The pianist starts.
2. After about 10 s of playing, Auto switches to Piano, and Sonos **fades out** from wherever it was and pauses.
3. When the pianist finishes, either someone picks Normal, or Auto notices the piano has been idle and goes back to the previous mode.
4. Normal brings the music back and **fades the volume up** to where it was.

How the pieces make it work. This is all generic mode machinery, nothing piano-specific:
- **"Put back afterwards"** (`{"set": v, "restore": true[, "max_age_s": n]}`) is one option that any mode can set on any row (aa: done 16 Oct, live at the next Flask restart; 2f: a switch per row in the editor). Chains work too: Piano → Party (which doesn't touch Sonos) restores the music when Piano is left.
  - On entry, the mode stores the current value, then sets its own.
  - On leaving, whatever mode comes next, each such row gets its stored value back, unless the next mode sets that row itself, or the stored value is older than its max age (about 3 h).
- **Fades** are the Sonos adapter's general behaviour for every volume and pause/play change (aa: done):
  - volume ramps over 4 s by default (`ramp_s` in sonos.json; a mode's `opts.over_s` wins), and a newer change carries on from the current level;
  - pause fades out first;
  - starting music fades up from 0.
  - It uses the room's own volume, so a grouped room elsewhere isn't faded along with it.
- **Auto** only picks modes. Restoring is the mode system's job, so Auto never starts music the mode system wasn't putting back.

**Idle time before leaving.** Owner, 17 Oct: **30 s** (2 minutes was too long), in Piano's own `leave_when` (`piano_idle` for_s). **Minimum stay** in a mode before Auto switches again: **60 s** (modes.json top-level `autopilot.min_dwell_s`; 5 minutes was longer than a track). Switching Auto on waives the stay in the current mode. Both are config.

**Manual choice wins.** If someone wants to play along *with* the music, they pick Normal by hand and Auto holds.

**Situations** are the vocabulary of what show_service can sense. It is fixed and documented, but which mode uses which situation is config:

| Situation | Built from |
|---|---|
| `piano_playing` / `piano_idle` | MIDI channel 1 from the live `room-piano` stream: ≥ 12 note-ons in 10 s, spanning ≥ 6 s, ≥ 4 distinct pitches. When the mic is up, it must also hear the piano, so silent headphone practice doesn't switch (configurable). Ignores channel 2 (the adsilent replaying itself), `test-piano`, `phone-test` and the `pc22-*` streams. |
| `music_audible` | CED music ≥ 0.5 on the room mic for ≥ 5 s; or Sonos PLAYING, not muted, volume > 0, *and* confirmed by the mic |
| `sonos_playing`, `genre_in`, `energy_above` | Sonos now-playing (below), the listener and local ML, and gated room loudness. `genre_in` takes the AI listener's word when the ML leads with a family that has no word, or isn't sure (as the director does; 17 Oct) |
| `mood_in` | The music feels like one of these (vocab moods): the AI listener's moods or the ML's mood tags, confident (≥ 0.6). "the music feels calm or dreamy" (17 Oct; README S5.20) |
| `building` | structure's section phase is a build, or the AI listener's trajectory says building. "the music is building" |
| `track_changed` | A new song started within `within_s` (30 s): music.context's track change, from Sonos or the sound. "a new song started in the last 30 s" |
| `talking`, `quiet` | CED speech with no music; silence for N minutes |
| `between` | Time of day |
| `light_below` / `light_above` | The room's light sensor (Flask's `/api/lightsensor/lightsense`, the `living-room` sensor today): the median lux over the last minute, so someone walking past doesn't flip it, with 15 % hysteresis. Labels: "the room is darker than 50 lux", "the room is brighter than 200 lux" |

Situations combine with `all`, `any` and `not`, each with its own `for_s` confirmation time.

**Triggers are per-mode config** (owner, 17 Oct; README S5.19). What starts a mode in Auto is that mode's own `autopilot` block, built from these words (MIDI, the mic, Sonos, the listener, the clock, the light). No code names a mode or a device.

- **The empty shapes** (one reading, in Auto and in the check):
  - no `autopilot` key: the mode starts only when someone picks it;
  - `enter_when` (or `otherwise_suggest_when`) absent, `null` or `{}`: never by itself, with no warning;
  - `leave_when` absent: Auto never leaves it by itself;
  - `from` absent or `[]`: from any mode;
  - `leave_to` absent: `"previous"`.
- **The light** (show.yaml `situations.light`):
  - `url`: Flask's own route;
  - `sensor`: `living-room`, the one a rule reads when it names none;
  - `poll_s` 10, `smooth_s` 60, `hysteresis` 0.15 (starting points).
  - The poll is async with a 2 s budget, so it never blocks the brain. A failed poll keeps the last value.
  - The words read unknown with no good read for 3 polls, or while the sensor is missing or silent.
  - A word's params are `lux` (required), `sensor` and `for_s`. For example, `{"light_below": {"lux": 50, "for_s": 300}}`.
- **The editor's routes** (the Room page's Auto editor, 2f). Both use the parser Auto evaluates with, and neither saves anything.
  - `GET /api/v1/autopilot/rules` gives every mode of the room's config with:
    - sentences (`enter`, `suggest`, `leave`, `summary`);
    - `from`, `leave_to`, `problems`, `available`, `live`, `nodes` and `now`;
    - `auto`: what Auto itself makes of each rule now (holds, held_s, needs_s, blocked).
  - `POST /api/v1/autopilot/check {"mode", "autopilot", "modes"?}` answers the same keys plus `ok` and `unknown_words`:
    - `from` and `leave_to` may name the room's modes, the unsaved `modes`, and (for `leave_to`) `"previous"`;
    - a mode in its own `from` is a warn.
  - Everything is keyed by the node's path in the block: `enter_when`, `enter_when.any[1]`, `leave_when.all[0].not`, a word of a several-word object `enter_when.piano_playing`, `from` and `leave_to`.
  - Problems are `{path, text, level: error|warn}`:
    - errors: the rule never holds, or the room would refuse it;
    - a word that can't be sensed now is a warn ("The piano isn't connected yet"), never an error.
  - `live` is each node's raw truth now (for_s left out; null = can't tell).
  - Sentences read naturally: "both: … and …", "any of: … or …", "not …", ", for at least 2 min".
  - Every param schema in `GET /api/v1/situations` carries the room's x-ui hints: label, unit, unset_label, required, slider `ends`, and `enum` + `enum_labels` for the light sensors.

**Global settings** go in one top-level `autopilot` block in modes.json:
- on/off (default **off** until the owner turns it on);
- quiet hours (default 23:00–08:00: Auto may only leave a mode it entered, never enter one);
- minimum dwell (5 min);
- flap guard: at most 4 automatic switches in 30 min, then Auto pauses for 30 min and says so;
- a 30-minute backoff after a failed, partial or rolled-back switch;
- how long a manual choice holds (below);
- chip timings.

aa adds the new mode key and the top-level key to the model's allowed set.

**Confidence tiers.**
- **Act** when the `enter_when` evidence holds.
- **Offer a one-tap tile** when only `otherwise_suggest_when` holds (e.g. for Party or Disco).
- **Do nothing** otherwise.
- When signals conflict, priority is piano MIDI (exact) > Sonos metadata > mic classifiers > the LLM.

Whether a mode can be entered automatically, suggested, or neither is entirely its own config (`enter_when`, `otherwise_suggest_when`, or nothing). No mode is special in code.

**Side effects.** When Auto enters a mode, the targets in that mode's `skip` list are left alone. Each mode's list is its own config; the usual entries are blinds, the ball and TV power-on. Taking the screens live (TVs to HDMI) is allowed. **Music is per mode:** Piano fades the music out and back (the owner's flow, above). Auto never *starts* music that wasn't already playing.
- This needs `skip` on `PUT /mode` (aa: done). Picked by a person, the same mode applies everything as usual.

**People always win.**
- **A person picks a mode while Auto is on:** Auto *holds*. It shows "Auto paused · you chose Disco · Resume" until the activity ends (no piano and no audible music for 10 min), capped at 2 h, or until Resume. Picking the mode Auto would have chosen is not an override.
- **Undo, or a manual change within 60 s of an Auto switch:** treated as a correction. That trigger→mode pair is suppressed for the rest of the episode (or 4 h), and logged with the evidence for tuning.
- **Nudges inside a mode** (Calmer, Wilder, palette, Keep this look) are not overrides. They stop Auto from *leaving* a mode it entered, because someone is engaged.
- **Never undo a person's changes:**
  - Auto acts only when no transition is running (`mode.transition` and `mode.target` both null), and never re-requests the current mode (that would undo hand changes).
  - It sends `expect: {current, last_transition_id}`. room_service answers 409 `stale` if the room changed meanwhile, so an Auto request can never cancel a tap that landed while it was deciding. People never send `expect`, so nobody is gated.

**Never react to the room's own actions.**
- Sonos and mic-loudness changes are ignored for about 60 s after room_service sent anything to Sonos, or ran a transition that touched it. This uses a per-member `sent_age_s` in status (aa).
- "Sonos stopped" or "quiet" is never the exit test for a mode that pauses Sonos.

**Leaving.** Exit windows are 5–10× longer than entry windows; pianists pause between pieces. A mode Auto entered goes back to the mode that was active before Auto acted (`leave_to: previous`), never to a fixed mode.

**Say it, every time.**
- Each Auto switch shows a chip on /room for 30–60 s: **"Auto → Piano · the piano is playing · Undo"**.
- A standing Auto line under the mode tiles shows the state and the reason: Watching / Holding (you chose Party) / Paused (quiet hours) / Paused (too many changes).
- If show_service is down, the line reads "Auto · not listening". The switch is never disabled.
- Every decision is logged with its evidence on the show console.

**Sonos now-playing** (aa's Sonos adapter, which is a placeholder today). SoCo 0.32 over local UPnP on :1400, outbound only, so it works from WSL.
- **Polling:** poll the group *coordinator* every 2 s while playing and every 10 s when idle; mute and volume every 10 s; topology every 60 s. A 3 s timeout, off the event loop.
- **Addresses:** a seed player address comes from `.env`; the rest from the topology. Events and SSDP discovery don't cross WSL's NAT, so we don't use them.
- **What comes through:**
  - Title, artist, album, art, duration and position for Sonos-app services and Spotify Connect, including the Spotify track id.
  - AirPlay is partial.
  - Line-in and TV give nothing.
- **Published:** a read-only `now_playing` member: state, source, title, artist, album, track id, duration, position, mute, group. It gives exact track boundaries for the director (§6.1).
- **Owner confirmed, 16 Oct:** the system is **S2**, **UPnP is on** (so SoCo works; aiosonos over outbound wss :1443 is a fallback), and the room is the Sonos room **"Drawing Room"**: one **Sonos Amp (192.168.1.17, S2 software 97.2)** driving four passive speakers. Found by SSDP from PC22's Windows side and reachable from WSL on :1400. modes.json's `sonos.lounge.*` members should map to it (aa). The seed goes in `.env`; the group coordinator is always resolved from the topology, because the room may be grouped. The house has 9 Sonos players in all.
- **Genre for a new track:** one text-only LLM call (about £0.0002), cross-checked against MusicBrainz and cached. It feeds the director as a prior; the room's audio wins if they disagree for 30 s. The AI listening switch covers this too. Spotify's genre API is deprecated and isn't used.

**v1 scope** (P3c, after P3b):
- the piano rule only;
- music and Sonos rules once CED (P1) and aa's Sonos adapter exist;
- every other mode as its own config says (none have triggers until the owner adds them).

### 8.10 Composition and balance

The owner's rulings (17 Oct): **one composer, many renderers.** Modes only switch appliances on. The director decides how much each appliance that is on responds to the music, for an acceptable overall tonal balance and compositional equilibrium. **Volume is never intensity.** Session 75 wrote the design, and its spine is:
- **Which appliances are on is read, never decided.** A screen counts while it is live. A Hue area counts while room believes it is exactly `dmx`; `taken_over` (another app holds it) never counts. No code knows a device or a mode by name.
- **How much is one formula per appliance and dimension.** The dimensions are picture, lamp level, pace, beat pulse, music depth and how often looks change. Loudness enters once, as a partial, floored share:
  - it is scaled by **the song's own dynamic range**, so a compressed song or an AGC'd input doesn't pump;
  - **tension and builds** lift the picture, so a quiet but tense build keeps it up;
  - **a dark scene isn't dimmed further** (its APL, once the renderer reports it);
  - **speech is never loud**, and the piano's velocity is the loudness while it plays;
  - silence calms, and missing audio is unknown, not quiet.
- **Each feel sets only the limits:** how much it follows (`follow`), each dimension's quiet end (`q`), floors and bases. The number in the room is the director's call, moment by moment. The picture never goes below 0.6.
- **Lamps by role:** the lamps above a screen move with it (weight 1.0). The lamps opposite are the slower partner (0.6).
- **One tempo fold:** stream_service's `music_response.json` range [70, 180). A half-time beat lock no longer reads as slow.

Built in steps. **Step 2 (17 Oct)** is the composition core (show_service/compose.py, pure) in two modes, chosen in show.yaml `director.compose.mode`:
- **observe** (the default): the plan is published on `room.look` as `compose`, for the DSP monitor. Nothing else changes.
- **drive:** room.direction carries the screens' music depth (`intensity`), beat depth (`pulse_depth`) and the feel's peak words (`peak`). The tempo is folded. The lamps take the plan on their own beat ladder (beat; else the clock's dmx beats at `looks.middle_depth`; else a steady level from the plan instead of the raw energy).

Later steps: the picture on the screens' INTENSITY channel with APL headroom (3), one clock for changes (4), figure and ground (5). As built: show_service/README.md S5.18.

## 9. Outputs

### 9.1 Look engine (v1: intensity and colour only)

Per output and tick, in show_rt's output thread:
1. Take a snapshot at `now + L(output, attribute)`.
2. Effects produce **assignments**.
3. Resolve them by layer priority: intensity highest-takes-precedence (HTP), colour latest-takes-precedence (LTP).
4. Apply master and caps (lamps and DMX only).
5. Encode.

**v1 effects:**
- oscillators (sine, saw, square, triangle) on beat, bar or phrase phase;
- phase spread across an ordered group;
- accents;
- crossfades timed in beats.

Groups are by tag. This follows Afterglow's assigner model (EPL) and LX's tag groups, as references.

**Later, with the first moving fixture:** pan/tilt, aim at room points, spatial waves.

### 9.2 Fixtures: `config/room/fixtures.yaml` (hot-reloaded)

v1 is hand-edited. An OFL import script and a Rig page come when the first node arrives.

```yaml
outputs:  {screens: {driver: showbus}, hue: {driver: hue}, dmx-a: {driver: artnet, node: node-1, port_address: 1}}
nodes:    {node-1: {match: {short_name: "eDMX1"}, full_512: false, failsafe: scene}}
types:    {sm-screen: {source: builtin}, sm-hue: {source: builtin},
           pinspot: {source: ofl, file: config/room/fixture-types/ofl/<maker>/<model>.json, mode: "4ch"}}
fixtures:
  screen.north: {type: sm-screen, output: screens, screen: north-screen, zone: north}
  hue.ne:       {type: sm-hue, output: hue, light: 9e4f…, zone: north, placement: above}
  hue.nw:       {type: sm-hue, output: hue, light: b952…, zone: north, placement: opposite, role: key}
  spot.ball1:   {type: pinspot, output: dmx-a, address: 1, role: ball-spot, tags: [ball]}
```

**Ids and settings.** The existing ids are kept, because rooms.json, the Hue adapter, the renderer and the Room page use them. The Hue block (bridge, area, gamma, `writes_enabled`) moves from patch.yaml unchanged.

**OFL types (MIT).** Channels we don't drive are pinned to OFL's `defaultValue`. Today's patch would leave a real fixture's shutter at 0, which is dark.

**Colour to emitters:**
- RGB maps straight across;
- RGBW takes the shared white;
- RGBA and RGBAW use non-negative least squares on the emitter chromaticities;
- colour wheels take the nearest slot;
- then per-fixture white-balance gains and a dimmer curve.

### 9.3 Art-Net out (P5, when a node is bought)

We write our own sender of about 200 lines. The ArtDmx builder is *copied* into show_service with the sequence fixed (1–255; `seq & 0xFF` would hit 0, which means "off"). It is not imported from room_service.

This is shared plumbing, so it gets **real unit tests**: packet bytes, sequence wrap, keepalive and the frame grid.

**Rules:**
- Unicast only; it works from WSL through NAT today.
- Port-Address ≥ 1.
- Frames on the beat-phased grid (§5.4), ≤ 44 Hz.
- A resend at least every second when nothing changes.

**Discovery and health.**
- A unicast ArtPoll every 3 s to each known node. The reply comes back through NAT, or through ca's relay.
- Nodes are matched by MAC or short name.
- Read their RefreshRate, merge flags and failsafe support.

**No ArtSync with one universe.** It is broadcast-only, which WSL can't send.

**Failsafe.** Each node stores a warm house scene (ArtAddress `AcFailRecord`, then `AcFailScene`), so a crash leaves a pleasant room.

**First hardware:**
- a wired node (DMXking eDMX1 MAX, or ENTTEC ODE Mk3 for 2 universes and PoE), measured for on-receipt output;
- **1–2 LED pinspots aimed at the mirror ball** (`role: ball-spot`). The ball barely sparkles in four diffuse lamps. The director uses them as the peak device: low in verses, on at choruses and drops, off in breakdowns.

**The ball itself.** Its power stays with the modes: an IR toggle, never sent by the director. In Party and Disco only, the director may ask room_service for `mirror_ball_motor.speed` at section lines, at most once every 2 minutes.

**Previz.** A virtual node in the console, or BlenderDMX on another LAN PC.

### 9.4 Hue

**P0–P4: unchanged.** room_service streams what our virtual Art-Net says. The Hue channels are written at `t − L_hue`.

room's receiver is last-writer-wins per universe. If Maestro is ever plugged back in, it uses universe 1 ("Offset Universes"). Otherwise show_service pauses its virtual Maestro while room's `/api/v1/dmx` reports a foreign sender, as fake_artnet yields.

**P5: the streamer moves to show_rt**, in steps with 68:
1. The Hue adapter gets its own REST client, so house control, readback and identify no longer depend on the streamer.
2. A hot-reloaded switch `hue.streamer: room|show`. With `show`, the adapter asks show_service to stream, reads its state, and treats show_service being down as `unconfirmed`. The lamps stay as they are and the mode never fails.
   - **The switch is exclusive.** There is one streamer per area, so room's HueOutput must be idle before show_rt starts the area. Otherwise the two get 403s from each other.
   - **Takeover detection.** Under `show`, the adapter treats show_rt's auth id as "ours".
3. **Release handshake.** Lamps ignore REST while streaming. Before the adapter writes a stored state (a put-back, or a house state), it calls `POST /show/api/v1/hue/release`. show_rt stops the area and replies once the bridge reports it inactive, plus about 1 s for the bridge's post-entertainment write. Only then does the adapter re-assert the state. This replaces what `HueOutput.set_wanted` does in-process today.
4. show_rt copies room's never-fight takeover rule.
5. Rollback: flip the switch back.

**Streaming changes:**
- 50 Hz continuous;
- the lamps' measured lead;
- beat pulses with attack ≥ 80 ms;
- washes and base colour from the lamp roles;
- no hits.

### 9.5 Screens (the renderer, 04)

**P0: nothing changes in the renderer.** Our virtual screen fixtures carry Maestro's channels:
- **DIMMER `pulse`:** an edge exactly on each predicted acoustic beat. The BeatFollower then leads by the renderer's own lead (`maestro_ms` is already 0).
- **ACTIVITY:** gated energy.
- **GOBO:** a step on each section change.
- **RGB:** the palette.
- **PAN, TILT, ZOOM:** slow audio stand-ins: brightness, bass and onset density, smoothed.

So the 834 generated mappings, including the ~1,800 references to pan/tilt/zoom/gobo, keep working.

**P4: the native clock.** The renderer reads `music.clock` into its Snapshot (anchor, period, confidence), with bar and phrase phase evaluated at its live lead. Both screens then share one phase. Bar and phrase snaps are added, and cue hints become a third `due()` reason. If show_service is down there is no bus. Signals then ease to idle and direction falls back to the panel and mode; it never "falls back to Maestro".

**Audio textures without lag.**
- Keep `runtime.py`'s clock-synthesised kick bump, evaluated at now + lead.
- Add a **bar pattern**: onset strength for 6 bands × 16 positions per bar, averaged over the last 4–8 bars, with a confidence, published at 1–2 Hz.
- The renderer turns it into a *predicted* spectrum and waveform at its own lead.
- The real spectrum comes only heavily smoothed, for shape and colour.
- Shaders that read raw transients are tagged `reactive` and kept out of auto pools, unless listen-ahead covers the lead.
- The same bar pattern lets DMX *schedule* recurring off-beat hits instead of reacting to them.

**Piano scenes** (proposed by 98 to 04; scenes tagged `modes: [piano]`, declaring `needs: [notes]` or `[spectrum]`). From the bus (§11.1) the renderer builds a per-frame view:
- a GPU note table (128×4) with ages evaluated at photon time;
- the onsets list, the pedals and chroma;
- the smoothed spectrum with a 256-row history ring for waterfalls.

It derives bind signals for every scene: `note_on`, `note_rate`, `velocity`, `register`, `spread`, `pedal`. `tension` and `chord_change` come from show_service.

Real spectrum data also lets the ISF importer bring in the "Audio Visualizer" category and audio/audioFFT inputs it skips today (`plugins/_lib/glsl/importer.py:354`, `:361`). Raw-transient shaders stay tagged `reactive`.

**Small fixes this needs (04):**
- ~~`validate_direction` must ignore unknown keys (P1)~~: done by 04 on 15 Oct; live at the next renderer restart;
- a genre and palette-family tagging pass, because no preset carries those tags today;
- mark onset-type signals as not bindable on screens;
- publish which vocab words the library can deliver, so the LLM is never steered toward words no scene carries.

---

## 10. The human interface

### 10.1 /room: one Music card per room with an audio source (2f)

It sits directly under the mode tiles. Phone first, light and warm, primary actions up front:

1. **"Hearing: soul · relaxed"** with a level bar. No bpm; genre only when confident. Otherwise `Quiet`, `Talking`, or `No sound coming in`.
2. **Calmer** · **Wilder**: two big step buttons.
3. **Palette:** Auto · **From the art** · 5 swatches from the mode's list.
4. **Next look** · **Keep this look** · **House lights**.
5. **Active chips**, each with an ×, plus **Back to auto**. Shown only when there are any.
6. **More** (an unroll): Lights low (the lamp-brightness control) · Describe it.

**Listening** is one block on the Music card (e1, 17 Oct): the **Room mic** switch with its input picker, **Identify the music** (the AI listening switch, `api.showListening`), then **What it hears** (the catch-all "Listening" reads "Not sure yet" there). The menu has no switches. Nothing is ever disabled. When the beat is unsure the card says "following loosely". The lamp rows read "Follow the music".

The test show becomes two test *sources* in the console: PC22 playback, and the **test piano** (§3.5). These let the room be exercised from another room.

**Later:** Punch, once a DMX fixture exists.

### 10.2 The show console (`:8700/show/`, also `/rs/show/`; never /room)

For the owner and builders. It has:
- audio health and meters, with source choice;
- the live clock and features;
- Tap, Half/Double and "Now is the one";
- the LLM verdict log;
- the director's decision log: every look change with its reason;
- vibes (read-only; they are YAML);
- fixtures and node health;
- the latency trims and the photodiode calibration runner.

---

## 11. Interfaces

### 11.1 Show bus

**P0–P3.** Room's `:8701` K3 bus, unchanged, fed by our virtual Art-Net.

**P4.** show_brain serves `127.0.0.1:8701/v1/show` after room sets `show_bus_port: 0`. The renderer reconnects by itself, with no restart.

**Compatibility rules** (sent to 2f as a LIVE_V1 1.2 proposal; 2f owns the contract):
- **K3 shape kept exactly:** `show.state` always carries `fixtures.screen.north/south` with the K3 fields, plus `global.activity`.
- `input = {kind: "show", online: <show_service alive>}`.
- `feed` is `live` while show_service runs, **whatever the audio is doing**. Audio health goes under a new key `audio`. Otherwise a quiet room would idle the screens.
- welcome `schema: 2` still serves v1 clients exactly as today.
- `hello` may carry `want: [types]`. The renderer's `show.state` is coalesced once it reads `music.clock`.

**Extra types:**

| Type | Rate | Body |
|---|---|---|
| `music.clock` | on re-anchor + 10 Hz | §5.3 |
| `music.features` | 50 Hz | band level/hits/presence, onset, LUFS, brightness, energy (gated), drive, punch, smoothed `spectrum32` (spec below) |
| `music.pattern` | 1–2 Hz | the bar pattern (6 × 16) with confidence |
| `music.context` | 1 Hz | genre (with conf), moods, instruments, vocal, dance, arousal, engagement, key/mode, talking, section `{id, letter, repeat_of, phase, build_progress, drop_eta_beats, bars_in_section}`, track `{id, changed}` |
| `music.insight` | on arrival | the LLM verdict, with window and segment ids |
| `music.hit` | per hit | sent only to clients that `want` it (lights, never the renderer) |
| `music.note` | immediately | `{seq, t_ms, on, pitch, vel, source: live\|test, sess}`. `vel` is the strike velocity on note-on (already hot-note capped) and the release velocity on note-off (0 if the device sends none). |
| `music.pedal` | immediately | `{seq, t_ms, pedal: damper\|soft\|sostenuto, value 0–1}`. The damper is proportional (CC64/127). |
| `music.notes` | 2–10 Hz | `{seq, t_ms, keys: [[pitch, vel, t_on_ms], …], pedals: {damper, soft, sostenuto}, present, source, sess}`: the keys held at `t_ms`. `seq` is the last event it reflects. Consumers use it to heal lost events. |
| `music.harmony` | 10 Hz | `{chroma12 (from MIDI while the piano plays, else audio), key, mode, tension, chord_change, register, spread}`. The director uses it. The renderer derives its own from the notes while the piano plays, and prefers this for audio-only music. `global.music.chroma12` on `show.state` carries the audio chroma too. |
| `room.direction`, `room.look` | on change | §8.3 |
| `room.event` | at least lead + 250 ms ahead | `{kind, id, at_ms, …}`; song-end kinds and fields below |
| `room.why` | 1 Hz, want-only | the room's reasons in words for the renderer's diagnostics panel (docs/LIVE_DIAGNOSTICS.md v2; e1 / 04, 17 Oct), about 300 B: `{t_ms, auto: {on, line, last}, listening: {on, effective, why}, mic: {on, why}, look: {why, age_s}}`. `auto.last` is the last switch with its reason ("Piano -> Gallery: the piano has stopped"); `look.why` is the last look change's reason ("a new section", "the drop", "someone pressed Next look"); each text is at most 80 characters. Only a client whose hello `want` names it gets it. |

**`room.event` and song ends** (17 Oct; the shape agreed with 04; README S5.17):
- **Every** room.event has a stable `id`, from both publishers. A re-send (a moved drop line, a re-armed end, a call-off, a `sound_back`) keeps the same id and replaces the earlier one. The ids are:
  - `<kind>:<track id>` for the three track events;
  - `section_change:<track id>:<section id>` and `drop:<track id>:<section id>`;
  - `<kind>:<track id>:<n>` (counted within the track) for build, breakdown and drop_armed;
  - `look_change:<look_id>` from the director.
- `track_end_armed` `{at_ms: the predicted end, eta_s, via: "sonos", conf, track_id: the ending song's}`. If it is called off, the same id comes again with `eta_s: null, detail: "disarmed"` and no transition.
- `track_end` `{at_ms, via: "sonos"|"dsp", track_id: the ending song's}`:
  - `at_ms` is the armed end for `sonos`, and the start of the gap for `dsp`;
  - `detail: "sound_back"` (the same id, `at_ms` = the first sound): the sound returned with no new song.
- `track_change` `{at_ms, via, track_id: the new song's, prev_track_id}`. `at_ms` is the new song's first beat while the clock's gate is dmx or better, else its first sound.
- All three carry `transition: {style: "fade-dark"|"crossfade"|"cut", out_s, in_s, seconds, on: "downbeat"|"now", change: "big"|"small", dark_max_s}`:
  - `fade-dark`: `out_s` applies on track_end (old song → dark), `in_s` on track_change (dark → new song). Both are on every event; `seconds` is null.
  - `crossfade`: `seconds`.
  - `cut`: `seconds: 0`, `on: "downbeat"`.
  - `dark_max_s` is set only for fade-dark.
  - While a person holds the look the events carry `held: true` and no `transition`.

**`spectrum32`, fixed** (agreed with b8; `runtime.py` `SPEC_LO_HZ`/`SPEC_HI_HZ`):
- **Bands:** 32 bands log-spaced from **30 Hz to 16 kHz**. Band k spans `30·(16000/30)^(k/32)` to `30·(16000/30)^((k+1)/32)` Hz, edges 30, 37, 44, 54, 66, 80, 97, 118 … 8881, 10806, 13149, 16000. The centre is the geometric mean of the two edges.
- **Values, 0–1, shape-preserving:**
  - each band's power is taken in dB;
  - one *shared* reference is used for all bands: the rolling p95 of the loudest band over 60 s;
  - `value = clamp((dB − (ref − 60)) / 60)`, i.e. a 60 dB window below the reference.
  - Bass therefore still reads higher than air, as shaders expect; there is no per-band normalisation.
- **Then:** multiplied by its own fast gate and smoothed with a rise τ of 35 ms and a fall τ of 300 ms. It goes to screens only.
- **Latency** (owner, 17 Oct: sound to glass under 0.5 s; screen-machine-73's chain budget leaves the show side ≤ 80 ms):
  - the FFT is 4096 points over a Hann of the newest 2048 samples, zero-padded, so the window's centre is 21 ms back, not 43, and the bass bands interpolate rather than stair-step;
  - the fast gate is `smoothstep(bottom, bottom + 6 dB, the window's own level)`, with `bottom` the gate's (`ref − 15`); `g`'s 3 s short-term loudness held the bars up 1.3–3.3 s after the music stopped and opened them about 1 s late;
  - music.features publishes at **50 Hz** (it was 25).
  - Measured offline: from silence to half the bars 40 ms (was ~1 s); a tone in playing music 37 ms; the bars fall to half 240 ms after the music stops; kicks don't pump the treble.
- The mic's own range is about 50 Hz–15 kHz, so the outermost bands read low.

Every time is CLOCK_MONOTONIC in ms, as today, and is the time of the **sound** (capture time through the clock map), not arrival. For notes and pedals, `seq` is one increasing space per `sess`. These piano shapes are agreed with 98 and 04; 98 writes the renderer's consumer (`stream_service/music_view.py`) and the TestPiano that emits them. Kiosk Chrome is on the box clock (−57 ms ± 2 ms), so it would keep an offset from a minimum-RTT ping filter if it ever needs beat-timed DOM changes.

### 11.2 show API (v1, through `:8700/show/`; relative paths only)

```
GET    /show/api/v1/state                      what it hears, vibe, steps, palette, chips, health
WS     /show/v1/events                         state diffs at 2 Hz + chip changes
POST   /show/api/v1/step      {dir: calmer|wilder}
PUT    /show/api/v1/palette   {name: auto|art|<palette>}
POST   /show/api/v1/action    {do: next|keep|house|low|tap|half|double|one}
DELETE /show/api/v1/nudge/{id|all}             × on a chip / Back to auto
PUT    /show/api/v1/vibe      {name}            (the show adapter; modes only)
POST   /show/api/v1/describe  {text}            → chips, or a "switch to <mode>?" suggestion
PUT    /show/api/v1/listening {on}              GET: the listener's view, incl. provider, est_evening_usd and estimate
                                                {per_call_usd, per_hour_usd, per_evening_usd, evening_h, cadence_s, model,
                                                provider, basis: ledger|price table} (README S5.20), shown while off too
POST   /show/api/v1/hue/release                 → {released: true} (exit choreography)
PUT    /show/api/v1/autopilot {on}              Auto on/off (persisted); GET state shows {on, state: watching|holding|paused, why, last}
POST   /show/api/v1/autopilot/undo              undo the last Auto switch (a correction)
POST   /show/api/v1/autopilot/resume            end a hold or pause
GET    /show/healthz
```

**The card's `listening` block** (`GET /show/api/v1/card`, contract.CardData; README S5.20): `{on, effective, why, spend_today_usd, est_evening_usd, provider}`. `provider` is "Google" or "OpenAI" (the model that would be used now); the price shows before the switch is on.

**What survives a restart:** vibe, steps, palette pick, chips (with absolute expiry) and listening. They live in `~/.local/state/screen-machine/show/state.json`, written atomically. The clock, section memory and LLM beliefs deliberately start cold.

### 11.3 Config files (all hot-reloaded)

| File | Owner (proposed) | Contents |
|---|---|---|
| `config/room/vibes.yaml` | c8 | Vibes (§8.1) |
| `config/room/fixtures.yaml`, `config/room/fixture-types/` | c8 (patch.yaml stays 68's until P5) | §9.2 |
| `config/room/latency.json` v2 `outputs`, `sources` | 10 (schema, done 15 Oct), c8 (writes its own keys from the photodiode runner) | §5.5 |
| `config/llm.yaml` | c8 | task → provider/model list, prices, budgets |
| `config/room/modes.json` per-mode `autopilot` + top-level `autopilot` | 2f (file), aa (schema), c8 (semantics) | §8.9 |
| `deploy/sm-show.env` | ca (wiring), c8 (flags) | Sender names, venv paths, `CUDA_VISIBLE_DEVICES=` |

---

## 12. Reuse vs build

| Component | Reuse | Build |
|---|---|---|
| Senders | PyAudioWPatch (Windows loopback/mic), sounddevice (ALSA), the VBAN spec; PipeWire/vban_emitter and ffmpeg-RTP as fallbacks | ~150-line sender |
| Ingest | RFC 3550 jitter estimator; Moon et al. lower-envelope mapping | Receiver, SUBSCRIBE, segments, health, ring |
| Features | numpy/scipy `sosfilt`, aubio-ledfx, pyebur128, soxr; LedFx/WLED smoothing and AGC | Feature graph |
| Beat | madmom online LSTM (pinned git), real-time PLP *algorithm* (MIT), Beat This! (MIT), Link's timeline model | **The clock, octave logic, holding and per-output scheduling. No open project predicts ahead per output; this is the part we build.** |
| ML | Essentia models (EffNet + heads, Key), CED-mini, onnxruntime | Gating, smoothing, structure memory |
| Structure | Foote novelty; FastLED Buildup/Drop (MIT, ported) | Causal sections, repeats, drop prediction |
| LLM | openai SDK 1.x (any OpenAI-compatible provider) | `utils/llm`, listener, fusion, Describe it |
| Director | `music_direction.py`'s trim maths; Maestro trims; Rekordbox/SoundSwitch phrase grammar; busking practice | Vibes, layers, cue rules, colour roles |
| Lights | Afterglow and LX designs (reference), OFL JSON, coloraide, `hue-entertainment` (in use) | Look engine, registry, Art-Net sender |

**Rejected as engines:**
- LedFx: pixel strips, no prediction.
- QLC+: a second controller.
- OLA: an extra daemon.
- Chromatik: not open, JVM.
- BeatNet: pins old numba.
- allin1 and SongFormer: offline, GPU.
- Local audio-LLMs: no RAM or GPU free.
- WSLg mic: dead.
- Mirrored WSL networking: breaks all 8 portproxies, including the TVs' path.

---

## 13. Build order and cut-over

**Status, 16 Oct (as built; details in show_service/README.md):**

| Phase | State |
|---|---|
| P0 virtual Maestro | **Live.** show_service in production (`scripts/ctl show`, Spectre card), VBAN receiver, fast DSP, madmom beat clock, Art-Net into room's 6454. Hears PC22's mic (`pc22-mic`, d4's agent) until the kiosk's mic arrives |
| P1 hearing words | **Built, off.** LLM listener (Gemini Flash, then gpt-audio-1.5, via `utils/llm`) waits for the owner's AI-listening switch; the CED privacy gate is live |
| P2 director + Music card | **Live.** Director, vibes, nudges, palettes (incl. from the art), room `show` adapter, `/show/*` pass-through, Music card on /room. Neutral until modes get `show` rows (owner) |
| P3 understanding | **Live.** ML worker (CED, EffNet + heads, key, Beat This!), medium lane, structure (sections, repeats, track change, build/drop/breakdown), `music.context` |
| P3b piano | **Built.** 98's kiosk sender + test piano; the kiosk deploy waits for the kit and the owner's OK (alsa-utils, audio group). Test piano runs through production |
| P3c Auto | **Built, off.** Supervisor live; no mode has an `autopilot` block yet (owner) |
| P4 native clock | **Partly.** 04 consumes `room.direction`; `music.clock` and `room.event` cueing next (04) |
| P5 lights | **Built, outputs off.** Fixture registry (+ OFL import), look engine, Hue Entertainment driver (68's hand-over contract; flip `hue.streamer: show` + `outputs.hue.enabled` is the owner's go), Art-Net LAN sender + virtual node (no node bought; ArtPollReply needs ca's relay), screen-palette hand-pick hold |
| P6 calibration | Waits for the photodiode kit |

**As-built differences from §6–§9:**
- **Song changes.** Detected from a loudness gap (momentary LUFS) plus a genre/key/tempo jump, or two independent jumps without a gap. Relative and parallel keys don't count as a jump.
- **Song ends** (owner, 17 Oct; README S5.17; §8.5 "Song ends"). Built, not yet live (it loads at the next show_service restart).
  - **Seeing and hearing the end:** structure predicts the end from Sonos's position and length (`track_end_armed`, 20 s ahead) and hears it in a silence of 3 s after music (`track_end`, `via: dsp`).
  - **Choosing the transition:** the director picks it synchronously (`ctx.track_transition`) and it rides on the event (`transition`, or `held`).
  - **Lamps:** they dip to the feel's floor across a gap. The new song's look lands at the junction, whatever the feel's change rule.
  - **Event ids:** every room.event now has a stable `id` (§11.1).
  - **Renderer:** 04's side, playing the transition at that change, is next.
- **Composition and balance, step 2** (owner rulings, 17 Oct; §8.10; README S5.18). Built, in observe. It loads at the next show_service restart.
  - **What's built:** show_service/compose.py (pure), the new enabled-set reader (live screens, Hue exactly `dmx`), and the per-feel limits (vibes.yaml `compose`, show.yaml `director.compose`).
  - **observe** (shipped): `room.look.compose` carries every enabled appliance's plan and the balance. Nothing else changes, and a test proves the outputs are identical frame for frame.
  - **drive** (one show.yaml line, hot-reloaded): room.direction's `intensity`, `pulse_depth` and `peak`; the one tempo fold; the lamps' dmx rung at `looks.middle_depth`; and the no-beat lamp swell from the plan instead of the raw energy.
  - **Not yet:** the screens' INTENSITY channel, APL, and the renderer reading `pulse_depth` and `peak` (step 3, 04/75).
- **Auto triggers** (owner, 17 Oct; §8.9; README S5.19). Built, not yet live (it loads at the next show_service restart).
  - **Light words:** `light_below` and `light_above` read the room's light sensor (show.yaml `situations.light`).
  - **Editor routes:** `GET /api/v1/autopilot/rules` and `POST /api/v1/autopilot/check` give each mode's triggers in plain words, its problems and its live readings, per node path. The Room page's editor (2f) is built on them.
  - **Empty shapes:** `from: []` now means any mode (before, it meant none); `enter_when: {}` means never by itself.
  - **No mode has a light rule yet:** writing one is the owner's call, in modes.json or the editor.
- **Listening** (owner, 17 Oct; §7; README S5.20). Built, not yet live (it loads at the next show_service restart).
  - **Provider:** Gemini Flash first (`gemini-3.8-flash`; a newer plain Flash is discovered from `models.list()` if it goes), gpt-audio-1.5 the fallback. Without `GEMINI_API_KEY` Gemini is skipped. The owner's key is in .env since 17 Oct; models.list and one text-only check worked.
  - **Gate:** the switch, Auto, the mic, the feel, then substantive music and the loudness floor. The mode's show.main `listening` no longer gates.
  - **Cost on the card:** today's spend, the evening's estimate and the provider, even while off; `/listening` carries the whole estimate.
  - **Sturdier answers:** the tool's words are plain strings and the call is lenient, so a stray word never fails an answer or charges the clip twice.
  - **Auto words:** `mood_in`, `building`, `track_changed`, and `genre_in`'s fallback to the listener (§8.9). No mode uses them yet: the owner's call.
- **GOBO** (the screens' section cue through the virtual Maestro). With no feel set, structure steps it on each confirmed section. With a feel, the director owns it: one step per look change, under its restraint rules.
- **Speech for the privacy gate.** The max of CED's speech, conversation and crowd groups.
- **Input meter.** The card's `hearing` carries:
  - `input`: the active stream's raw peak and RMS dBFS over 0.5 s;
  - `input_trace`: its last ten 100 ms bins, with a `seq` so the page appends only new ones.

  `hearing.level` stays as energy (loudness against the mode's expected loudness, gated), so it is not a meter. When nothing comes in, `hearing.text` says why: "No microphone connected yet", "The microphone isn't answering", "No microphone plugged in" or "The microphone stopped sending".

  d4's fast path, WS `/v1/levels` (show_service/levels.py), sends at most 60 frames a second, only while someone is watching. Each frame has the peak since the last frame and the sender's capture time. `{"ping"}`/`{"pong", "srv"}` lets the page align clocks and show its delay. *Measured:* it lags a direct read of the same mic by 16–17 ms, at about 57 frames/s in the browser.
- **Mic choice** (the owner's ask via d4, 16 Oct; show_service/inputs.py).
  - **What's listed:** every room-sound input is listed with no names hard-coded. These are the permanent senders' streams, less test, MIDI and `room_sound: false` streams. Each shows the devices its sender reports.
  - **Routes:** `GET /api/v1/inputs` and `PUT /api/v1/input {stream|null, device?}`.
  - **Effect of a choice:** the stream ranks after a playing test stream and before show.yaml's order, and its device rides that sender's SUBSCRIBEs. The choice is kept in `input.json`, and each stream keeps its own device.
  - **Labels:** show.yaml `senders.<name>.label` gives the Room page's group label.
  - **On the card:** `hearing.mic` = {stream, device, chosen}.
  - **Who builds what:** the page and the PC22 agent are d4's. 98's kiosk sender supports both fields (7962bd28): every ALSA capture card is listed, and an exact-name hint reopens arecord on that card. It deploys with the kit.
- **Mic on demand** (the owner's rule, 16 Oct; show_service/mic.py, README S5.16). The room's mic runs only while something needs it: Auto is on, or the mode follows the music (a per-mode config row: room's `show.mic` PUTs `{"mode": true}`). Otherwise it is off, with the senders' capture devices closed (SUBSCRIBE `off`), and the Room page's Mute always wins.
  - **Routes:** `GET /api/v1/mic` → `{on, muted, wanted, why, claims, mode, streams: {capturing, off}, error}`; `PUT /api/v1/mic {muted?, mode?}`. Persisted in `mic.json`. The card carries it as `mic_state`, and `hearing.text` says "Mic muted" or "Mic off" when that is why no room sound comes in.
  - **Which mic:** with a Mic choice only the chosen stream captures; if it hasn't been live for 10 s, every room mic captures until it is (degrade, never refuse). The piano's MIDI and the test streams are never the mic.
  - **Privacy:** nothing captures before the decision exists (the receiver holds every possible mic off until a decision covers it). Audio of an off stream is dropped on arrival, the listener's ring forgets it, and a clip cut before the mic went off is never uploaded. Auto's audio words read unknown while the mic is off, so a rule on "quiet" never fires because the mic was switched off.
- **madmom** runs inline on show-rx. A separate thread cost more than it saved under the GIL (`SHOW_BEAT_THREAD=on` re-enables it).


**Phases are ordered by what the owner sees.** Each one ships to production when it works (CLAUDE.md). Sizes are for one session.

| Phase | Owner(s) | What | Visible result | Size |
|---|---|---|---|---|
| **P0 · Virtual Maestro** | c8; ca (ctl, PORTS, card) | show_rt with the PC22 sender (loopback + mic), receiver, fast lane, madmom (aubio fallback if the madmom build takes more than 30 minutes), a first PLL clock, and **ArtDmx to 127.0.0.1:6454 in patch.yaml's layout**. Status on `127.0.0.1:8703` and the card. | **TVs and Hue follow the real beat and energy**, through the existing bus, BeatFollower, music_direction and Hue "Follow" | 1–2 days |
| **P1 · Hearing words** | c8; 04 (`validate_direction`: done 15 Oct); 2f (one line on /room) | show_brain with the listener (gpt-audio), CED gate, `utils/llm`; a "Hearing:" line | "Hearing: jazz · building" | 1 day |
| **P2 · Director + Music card** | c8 (director, vibes, API, `show` adapter to aa's contract); 2f (`/show/*` pass-through, Music card, modes → vibes); 68 (Hue value alias) | Vibes from modes, steps, palette (incl. from the art), House lights, Keep, Next, chips, start/end choreography | The room has a director and a phone card | 2–3 days |
| **P3 · Understanding** | c8 | Slow worker: Beat This! corrections, EffNet + heads, key; sections, repeats, build/drop/breakdown, loudness gating | Choruses come back, drops land, "following loosely" for jazz | 2 days |
| **P4 · Native clock** | 04 with c8 (04's bus client fans music.* out to each session's SoundView; room.direction goes into the MusicDirection slot; the lead steps on buffer jumps); 2f (LIVE_V1 1.2) | show_brain serves :8701 (v2, K3 kept); the renderer reads `music.clock`, the bar pattern and cue hints; bar/phrase snaps; north calibrated | Both screens in one phase; predicted audio shaders | 1–2 days |
| **P5 · Lights** | c8 with 68; ca | Fixture registry; Hue streamer in show_rt behind the switch; look engine; Art-Net sender with tests; pinspots | Pinspots on the ball; lamps on the beat-phased look | when a node is bought |
| **P3b · Piano** (as soon as the kit arrives; parallel with P3) | 98 (kiosk `show-sender.service`: ATR4697 + adsilent MIDI; test piano); c8 (MIDI receiver, note table, harmony, the generic vibe fields); 04 (note scenes) | Notes and pedals on the bus; vibe fields; note scenes | **The room plays along with the Steinway** | 2 days |
| **P3c · Auto** | c8 (supervisor in show_brain, situations); aa (`reason`, `expect`, `skip` on PUT /mode, `sent_age_s` and the `autopilot` keys: **done 16 Oct, live at the next Flask restart**; the Sonos adapter with `now_playing`: next); 2f (Auto line and chips on /room) | The piano rule first; music and Sonos rules once CED and the Sonos adapter exist | **The piano starts and the room goes to Piano by itself** | 1–2 days |
| **P6 · Calibration** | c8; 10 (TV runs) | Photodiode runs for every output; latency.json `outputs` and `sources` | Measured, calibrated room | 1 day |

### 13.1 Before P0: the checklist

- **Git baseline:** each owner commits its own paths first (pathspec form, per §11 of AV_PROGRAMME). room_service/, config/room/, src/pages/Room/, room_in_app.py, scripts/ctl, tools/room/ and docs/PORTS.md are currently untracked. c8 commits `show_service/` and this doc as it goes.
- **ca:**
  - PORTS.md rows for 8703 (`127.0.0.1`) and 6980/udp (WSL socket, receiver-initiated);
  - a `show` entry in `scripts/ctl` (venv `~/.venvs/sm-show`, in `all`, started at boot, no hold line);
  - `deploy/sm-show.env`;
  - the show card and the PC22 audio-agent card;
  - the CLAUDE.md "Screen Machine cards now" list.
- **2f:** ~~an AV_PROGRAMME §11 row for c8, and the §4.4a, §6.3 and §11.3 amendments~~ (done 15 Oct). 2f also owns the LIVE_V1 1.2 proposal for bus v2 (send §11.1 when P4 is near). For P2, ping 2f once the API is up on :8703: one announced Flask restart carries the pass-through and the Music card.
- **68:** told that P0 sends ArtDmx to their receiver at 127.0.0.1:6454, universe 0, in patch.yaml's layout. This is exactly what the fake show does today. The test show yields to it, just as it yields to real Art-Net now.
- **04:** told that P0's pulses sit exactly on predicted acoustic beats (`maestro_ms` stays 0).

### 13.2 Cut-overs: each with a switch to flip back

| Step | Restart | Told first | Rollback |
|---|---|---|---|
| P0 start | none (new process) | 68, 04 | `scripts/ctl stop show`. Room's receiver goes stale and everything behaves as today. |
| P2 `/show/*` pass-through + `show` adapter + Hue alias | **one announced Flask restart** | all sessions (CLAUDE.md rule 3) | Remove `show` from modes.json, which hot-reloads |
| P2 Music card | frontend deploy | 2f deploys | `dist_prev/` |
| P4 bus move: room sets `dmx: {enabled: true, show_bus_port: 0}`, which already exists (the Lights card, identify, the adapter and fixtures all handle no bus) | none (the renderer reconnects) | 04, 68, ca (the card's ":8701" item) | room `show_bus_port: 8701`, show_brain bus off |
| P5 Hue streamer | **one announced Flask restart** (adapter decoupling) | 68, 2f | `hue.streamer: room` |

### 13.3 Testing (owner rule 6)

Builders check that their own work runs without crashing. **Shared plumbing gets real tests:**
- the VBAN parser and segments;
- clock mapping;
- the PLL's maths;
- the Art-Net packet and frame grid;
- the registry parser;
- the bus contract (K3 kept).

Thresholds are tuned by ear with the owner on real music, guided by one recording of a jazz trio and a pop/EDM playlist with cross-correlated ground truth. That is calibration, not a QA programme.

### 13.4 Packaging

**Hot venv (`~/.venvs/sm-show`):**
- numpy 2.x (pinned), scipy, soxr, pyebur128;
- `aubio-ledfx==0.4.12`;
- madmom from a pinned commit, as a local wheel;
- onnxruntime (CPU);
- openai 1.77.

**Slow-worker venv:**
- `essentia==2.1b6.dev1389`, the last cp312 build;
- ONNX exports of EffNet, CED and (if possible) Beat This!;
- otherwise CPU-only torch from the CPU index.

**Builds:** wheels are built once under `flock` + `nice -n 19`. Models go to `~/.cache/screen-machine/models` with checksums, never into git.

---

## 14. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
**Settled by the owner, 15 Oct:**
- the ATR4697-USB room mic and the adsilent's USB MIDI, on the kiosk mini-PC;
- no line feed.

**Still open:**

| # | Decision | Recommendation |
|---|---|---|
| S1 | Kiosk access for the sender | Add `gjbm2` to the `audio` group and install `alsa-utils` when the kit arrives (98 has asked). Also the calibration kit: a USB stereo input adapter, a small electret mic and a BPW34 photodiode (a few pounds). |
| S2 | Room-mic audio to OpenAI | Only music-dominant seconds go up (speech below 0.3); per-vibe `listener` setting; an AI listening switch in the menu |
| S3 | First DMX purchase | One wired node (eDMX1 MAX) and 1–2 LED pinspots for the mirror ball |

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| Beat tracking on the owner's real music (jazz, live piano): ~75 % online at best, less on jazz; downbeats ~45–57 % | Calibrated confidence with hysteresis; holding; vibes with `beat_lock: off` need no beat; Beat This! corrections; tuning on recordings |
| Octave errors | Octave hypotheses; Beat This! votes; Half/Double on the console |
| TV lead moves (buffer jumps, powerline stalls) | The renderer's live lead; cue hints ≥ lead + 250 ms; a renderer fix to step the lead on buffer jumps (04) |
| Python jitter (GC 92 ms, GIL 6 ms, denormals 4×) | show_rt: lean, gc disabled or frozen, 1 ms switch interval, dither; the brain is separate |
| CPU contention with the 4K renders | ~0.5 core in total; slow worker niced and time-boxed; no GPU; batch jobs at nice 19 or SCHED_IDLE |
| WSL NAT | Receiver-initiated UDP for audio and Art-Net replies; the relay only as a fallback |
| Kiosk on powerline carries the sender up while two 4K streams come down; a sender fault could affect the kiosk | A separate unit from light-relay, nice'd capture, sample-index stamping (jitter only delays knowledge), health on the card; Wi-Fi as fallback path |
| adsilent v1 quirks: hot notes, missed very fast presses, delayed big chords | Hot-note cap in show_rt; the mic's onsets fill in missed notes for lights; the 2 Hz key state heals stuck notes |
| Auto surprises people (wrong switch, flapping, undoing a tap) | Confirm windows, slow exits, dwell, flap guard, `expect` (409 stale), holds after a person's choice, Undo as correction, a chip every time, side-effect `skip`, default off |
| Auto reacts to the room's own actions (Piano pauses Sonos; Disco starts music) | `sent_age_s` discount; never "quiet" as the exit for modes that pause Sonos; test streams and adsilent channel 2 ignored |
| Sonos UPnP turned off after Sonos's 2025 security advisory, or an S1 household | Owner checks first; aiosonos (S2, outbound wss) as a fallback behind the same reader |
| The Flask scheduler's direct TV standby/wake events bypass room_service | Flag to 2f before the TV adapter becomes real |
| Hue latency and smoothing unknown; firmware moves it | Photodiode per lamp; re-run after updates; pulses with attack ≥ 80 ms |
| DMX nodes and fixtures buffer or smooth | Measure before keeping; on-receipt nodes; smoothing off |
| LLM wrong, late, down, or models retired | Arm-don't-fire, segment anchoring, two agreeing reads, expiry, ids in config with startup checks, a budget that slows the cadence. The show runs DSP-only with no visible break. |
| Over-reactive "music sync" look | Phrase and section rules, holds, peak-token budget, lamp roles, key lights |
| Cut-overs on a live system with six sessions | Git baseline first; switches, not reverts; at most two announced Flask restarts |
| Licences: Essentia AGPL; madmom and Essentia models CC BY-NC-SA; aubio GPL | Fine for a private install; flag them if it is ever sold |
| Dependency rot (madmom 2018, aubio dormant, Essentia cp314-only) | Local wheelhouse, the aubio-ledfx fork, ONNX exports |

---

## Appendix: evidence

**Measured on PC22, 15 Oct, under render load:**
- Windows → WSL UDP one way: 0.56 / 0.83 / 1.64 ms (min / p50 / p95).
- ffmpeg dshow → WSL: 100 pkt/s, gaps p99 10.9 ms.
- WSLg RDPSource: 28.8 s block, zeros, then unloads.
- WSL → 192.168.1.255 is NAT'd; 255.255.255.255 stays inside WSL.
- WSL kernel PREEMPT_NONE, HZ = 100, `ulimit -r` 0 (no SCHED_FIFO).
- GC: 37–39 ms at ~200k objects, 89–94 ms at ~600k, 258–273 ms at ~2M. GIL interference up to 6.2 ms.
- Denormal `sosfilt` state: 4.2× slower.
- Fast lane: 0.2–0.45 ms per hop.
- madmom online LSTM: 1.4 ms median / 2.9 ms p99 per frame.
- aubio tempo: 0.04 ms per hop.
- Beat This! small0: 0.75–0.8 s per 20 s on 2 cores.
- EffNet: 6–9 ms per 2 s. CED-mini: 22 ms per 2 s.
- The renderer's initial lead ≈ 545 ms (`sessions.py` `_initial_lead`: server 3 frames + buffer 300 + frame + photon 179).

**Literature, specs and documentation:**
- Real-time PLP: Meier, Chiu & Müller, TISMIR 2024. RNN-PLP-On 74.7 % F1; L-correct at L=2 52.7 %, 71.6 % with a ±40 % tempo prior; lookahead 580 ms costs −2.3 F1.
- Online beat F1 (GTZAN): BeatNet+ 80.6; downbeats 47–57.
- Perception:
  - BT.1359 and EBU R37: light lagging sound detectable at ~45 ms.
  - Cecere, Gross & Thut 2016: visual-leading tolerance much wider.
  - Fujisaki & Nishida: no sync judgement above ~4 Hz.
- Art-Net 4 rev 1.4dp (Oct 2025): unicast, 44 Hz maximum, Port-Address 0 deprecated, ArtSync broadcast-only.
- DMX512: a full frame is 22.7 ms.
- MMAU-Pro music: Gemini 3.1 Pro 76.0, Gemini Flash ~71, gpt-audio 67.3, GPT-4o-Audio 63.1, captions + GPT-4o 40.6. CMI-Bench: audio LLMs near zero on key, beat and arousal.
- Skip-BART (ICLR 2026): emotion → hue 2.67/7, human 4.51.
- Colour and emotion: Whiteford 2018; Palmer 2013.
- References and libraries: Afterglow, Chromatik/LX, QLC+ EFX, Open Fixture Library 12.6, GDTF 1.2, VBAN spec rev 13, FastLED audio detectors.
- OpenAI and Gemini model, pricing and deprecation pages *(retrieved 15 Oct 2026; re-verify at build; ids and prices live in `config/llm.yaml` only)*.

The raw research returns (candidates, verdicts, URLs) are in c8's workflow journals for 15 Oct. Ask c8.
