# MaestroDMX facts the AV integration depends on: Art-Net output, OSC, attributes, fixture profiles and backups, Art-Net-only operation and limits, audio, hardware and firmware. Research as of 2026-10-13. Main primary source: the vendor's own "MaestroDMX User Manual, Software V1.5 (Jan 2026)" PDF (hosted at r2.gear4music.com/media/140/1406501/download_1406501.pdf; text extracted locally to /tmp/claude-1000/-home-gjbm2-dev-screen-machine/954809c3-b227-4c7a-9aec-b8a50dbbfd3f/scratchpad/pdfout/manual_v15.clean.txt), plus maestrodmx.freshdesk.com articles.

## 1. 1. Art-Net output: how many universes, how the destination is set, ArtPoll/ArtPollReply, numbering, packet rate, continuous vs on-change, Art-Net version
[high] WHAT IS DOCUMENTED:
- Since v1.5, Maestro outputs exactly ONE extra universe over Art-Net on its Ethernet port, alongside its one Internal DMX universe on XLR. Release notes: "MaestroDMX can send a single DMX universe over Art-Net". Manual p.36: "can now send data to an additional universe".
- Setup path: Stage tab, 3-dot menu next to the "Internal DMX" drop-down, "Add Output".
- Manual fields: universe name, "Define its IP address", "Name and add Nodes", "Decide which Universe of DMX", and an enable/disable toggle. "Add Node" sends the same Art-Net data to several devices with different IPs. So delivery is per-destination IP (unicast in practice); broadcast is not mentioned.
- "Offset Universes" toggle: the first universe is numbered either 0 or 1. Net/Sub-Net fields are not mentioned.
- "Discover" (Start button) scans for nodes in about 20 s and lists each node's IP and name. Release notes limit discovery to nodes "on the same subnet".
- Fixtures can be patched on the Art-Net universe or moved to it with "Move Fixture". Deleting the Art-Net output offers to move its fixtures to an "Unpatched" universe.
- Both outputs are live together: the manual describes moving fixtures between the "DMX stage" and the "Art-Net stage" of one stage, and the Patcher has a framedriver drop-down that switches the view between Art-Net and Internal DMX. No document states sustained concurrent output explicitly.

NOT FOUND in any vendor page or manual:
- Packet rate.
- Whether output is continuous or only on change.
- Art-Net protocol version (3 or 4).
- Whether ArtPoll or ArtPollReply are used. Discovery almost certainly uses ArtPoll and ArtPollReply (inference). Manual IP entry suggests the receiver does not have to answer ArtPollReply to get ArtDmx (inference, untested).
- sACN (not mentioned).
- The Port-Address / Net / Sub-Net mapping.

UNVERIFIED: a search-engine paraphrase of a retailer page said a future version will send both universes through a single Art-Net node. I could not find the source page.
-> IMPLICATION: 1. The Art-Net receiver must accept unicast ArtDmx on UDP 6454 for any universe number, and log the Port-Address it actually sees.
2. It should not depend on discovery. Configure the destination IP manually in Maestro. Optionally answer ArtPoll so the receiver shows up in Maestro's Discover list.
3. Packet cadence, jitter, keep-alive and stale behaviour must be measured in Phase 2 with a packet capture on the LAN, not inside WSL. Note the WSL problem: Maestro can only unicast to the Windows host IP, and portproxy does not forward UDP. That needs one of:
   - a Windows-side UDP relay;
   - WSL mirrored networking;
   - receiving on another Linux host.
4. "Add Node" lets the same universe go to two IPs, for example the server plus a sniffer or a backup consumer.
5. Budget is 512 channels for all virtual fixtures (one Art-Net universe).
Sources: https://maestrodmx.freshdesk.com/support/solutions/articles/153000252041-art-net-and-maestrodmx; https://maestrodmx.freshdesk.com/support/solutions/articles/153000252248-download-maestrodmx-software-package-1-5-1; https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (Manual V1.5 Jan 2026, pp.36-38); https://alia.com.au/introducing-maestro-dmx-v1-5/

## 2. 2. OSC: port, full address list and argument types, group indexing, feedback, show/cue/palette/brightness/blackout control, firmware limits
[high] PORT AND NETWORK:
- Port 7672 (TouchOSC article; manual p.79 "MaestroDMX Default Network Information ... Port: 7672").
- Other defaults: Ethernet 10.0.0.200, Wi-Fi 192.168.37.1, hostname maestro.local.
- The page says "The above information is modifiable via the WIFI and NETWORK control". It is unclear whether that covers the port.
- The transport (UDP) is not stated, but TouchOSC uses UDP.

FULL INPUT SPEC (manual pp.79-82, Freshdesk spec):
- /global/brightness float 0-1
- /audio/input string (input name); /audio/input/next
- /live/pause, /live/resume, /live/stop (no args)
- /live/N/pattern string (pattern name)
- /live/N/pattern/index int 0..n
- /live/N/palette int ("Palette ID defined in config")
- /live/N/palette/index int 0..n
- /live/N/fx/index int 0..n (FX palette by order)
- /live/N/ floats 0-1: brightness, excitement, background, motion/range, motion/speed, speed, energy, variance, decay, attack
- /live/N/shape int 0-15 ("for core patterns only, works better in 2D")
- /show/name string (first match if names are duplicated); /show/next; /show/previous; /show/index int 0..n
- /show/cue/index int 1..n (1-based); /show/cue/next; /show/cue/previous
- /show/play_pause; /show/play; /show/stop
- /triggers/{strobe,blinder,blackout,fog,effect} boolean (momentary), each with a /toggle variant
- /triggers/strobe/brightness and /triggers/strobe/rate float
- /triggers/blinder/brightness float
- /triggers/fog/interval, /duration, /volume, /speed float

INDEXING:
- N is documented only as "[N = group]". The base (0 or 1) is not stated.
- MIDI names the groups Group 1-4 and indexes pattern/palette/FX from 1. OSC *index* addresses are written as 0..n, except cue, which is 1..n.

FEEDBACK: none documented. No replies, status or subscriptions. The web UI "Observe[s] MaestroDMX's status when actions are triggered by a MIDI or OSC controller", but that is the web UI, not an OSC reply.

FIRMWARE:
- OSC was "coming soon" in the v1.3.2 manual (July 2024), so it arrived in v1.4.
- The TouchOSC article links "OSC Specification - v1.4".
- /show/play_pause was added in v1.5.

GAPS: no OSC address selects an FX palette by name, no address loads a cue by name, and nothing sets Kick Sensitivity or Activity Gain. MIDI is an alternative input via USB (channel 16, cue select 1-98, group CCs 30-37, 40-47, 50-57, 60-67).
-> IMPLICATION: 1. The OSC adapter targets <maestro-ip>:7672. Outbound UDP from WSL works.
2. All commands are fire-and-forget. The supervisor must treat Maestro state as "desired, unconfirmed" unless it is confirmed by Art-Net readback (see the readback finding).
3. Resolve the group index base (try /live/1 against /live/0) and the 0- vs 1-based index addresses on the owner's unit during commissioning. Prefer /show/name over index-based selection so reordering shows in the UI cannot break the app.
4. The spec's examples (AV_SPEC.md:178-188) all exist, but its claim that "Default OSC port documented as 7672" is now confirmed.
Sources: https://maestrodmx.freshdesk.com/support/solutions/articles/153000224681-open-sound-control-osc-specification; https://maestrodmx.freshdesk.com/support/solutions/articles/153000224682-using-touchosc-to-control-maestrodmx; https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.74-82); https://images.static-thomann.de/pics/atg/atgdata/document/manual/600134_maestrodmx_user_manual_v1.3.2.pdf; https://maestrodmx.freshdesk.com/support/solutions/articles/153000144505-midi-input-specification

## 3. 3. Attributes: full list, dynamic vs static, what ACTIVITY does, how PAN/TILT/GOBO/ZOOM animate, colour handling
[high] STATIC: "send a constant single value from 0-255" for the life of the stage, though Snapshots can override them.
- MASTER DIMMER, STATIC VALUE, LASER, STROBE, ROTATION, SPEED, SHUTTER.

DYNAMIC ("autonomously controlled"):
- LED colour: RED, GREEN, BLUE, COOL WHITE and WARM WHITE ("currently the same attribute"), AMBER, UV, CYAN, MAGENTA, YELLOW, KEY, HUE, SATURATION, VALUE.
- Function: DIMMER, PAN, TILT, ZOOM, ACTIVITY, COLOR WHEEL, GOBO, PRISM, PRESET.

TRIGGER:
- FOG ON/OFF, FOG VOLUME, FOG SPEED, BLINDER, EFFECT, BRIGHTNESS.
- BRIGHTNESS is "directly mapped" to the Global Brightness slider.
- FOG, BLACKOUT and EFFECT channels have ON and OFF values that are set by the trigger buttons.

DYNAMIC-ATTRIBUTE PROPERTIES:
- Output Range, Smoothing (seconds; adds lag), Invert.
- PAN/TILT also have Offset (centre) and Width (range).
- "Maestro will add mirroring and symmetry automatically".
- 16-bit FINE control is advertised.

ACTIVITY: "The Musical Activity Level, as viewed on the Audio page, is directly connected to the ACTIVITY attribute type". The examples are rotation speed, group dimmer, and laser intensity.
- It is a single global measure of musical "excitement", not volume. It is calibrated with Activity Gain (default 0; raise it for live music).
- FX-palette Activity Ranges such as 80-85 and 25-49 imply a 0-100 scale.

PAN/TILT:
- Moved by Maestro Patterns, around Offset within Width.
- Group parameter Mover Range: 0 parks the head at Offset. Mover Speed is "modulated" by music energy.
- The layout's Mover Mapping (1D, or a limited 2D grid) decides the spatial relationship between heads.
- The "Still" pattern has no moving-head support. Core Patterns have "no pan/tilt motion".
- Fixed aim is done with a Snapshot (Width 0, Offset aimed) recalled through an FX Palette, as in the wedding-cake example.

GOBO/PRISM: steps (name, DMX range, enabled) must be defined. Maestro then "autonomously trigger[s] the steps based on changes in the music", so values are discrete.

ZOOM and PRESET: listed as dynamic, but their behaviour is undocumented.

COLOR WHEEL: steps with a picked colour, plus optional split colours. Maestro syncs the wheel to its internal RGB palettes. Since v1.2 this also covers derbys and lasers.

COLOUR AND DIMMING:
- Palettes are RGB(W)(A)(UV) gradients and groups of gradients.
- For LED fixtures, the manual uses MASTER DIMMER (static, usually 255), and Maestro "takes care of the dimming via the levels of the color channels".
- The dynamic DIMMER is meant for colour-wheel fixtures with no LED colour, where it "will pulse the dimmer on the fixture in sync with the music".
- The conversion to CMY or HSV is not documented.
-> IMPLICATION: 1. ACTIVITY is global. FrameA and FrameB ACTIVITY channels will carry the same value, apart from per-channel range or smoothing, so one ACTIVITY channel is enough. Differences between screens must come from pixel or mover mapping and from groups.
2. Virtual PAN/TILT only moves under Maestro Patterns other than Still. Its spatial phase depends on the Mover Mapping layout.
3. A virtual GOBO with N defined steps works as an "effect/scene index" that changes on musical section changes. This is a good fit for the spec's effectIndex.
4. For the RGB virtual fixtures (screens, Hue), prefer RGB (or HSV) with MASTER DIMMER static, rather than RGB plus a dynamic DIMMER. Otherwise expect double dimming or pulsing.
5. A DIMMER-only virtual fixture should pulse with the music. This gives a cheap pulse signal, still to be tested.
6. Keep Smoothing at 0 on virtual channels and smooth in the server instead.
Sources: https://maestrodmx.freshdesk.com/support/solutions/articles/153000144498-understanding-attributes-in-maestrodmx; https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.27-31, 44-47, 58-61, 70-72); https://maestrodmx.freshdesk.com/support/solutions/articles/153000225568-fx-palette-examples

## 4. 4. Custom fixture profiles: JSON format, schema or example, import/export path, stage backup/restore format
[high] FORMATS:
- Profiles are .json. Manual p.26: "downloaded and saved to a .json file format".
- Stages download "as a JSON formatted file" and contain: name and description, Patcher (fixtures and groups), Layout (1D/2D pixel and mover mapping per group), Control (Snapshots including "Fixture Default"), and Global (FX Palettes used in Live Control and Show cues).
- Shows download as JSON, with cues, timing, order, per-group pattern/palette/FX/parameters, and toggles.
- The full-system "Backup User Configuration" (System, then Backup And Restore) covers stages, shows, uploaded profiles, colour and FX palettes, and audio settings, but not network settings. Its file format is not stated.

NO PUBLISHED SCHEMA: no schema or example JSON is published anywhere I could find.
- The vendor library at fixtures.maestrodmx.com is a single-page app. Its API (/api/1/fixture?name=&manufacturer=&category=, /api/1/fixture/{atla_id}, POST /api/1/fixture/validate) returns 401 without a Cognito login, so I did not pursue it.
- No GitHub or community generator was found.

UI PATHS:
- Create a profile: Fixture Profiles, 3-dot menu, "+ Add Profile", "ADD NEW". Add modes (name plus channel count), then set each channel's name, attribute type, static value and properties (GOBO/PRISM/COLOR WHEEL steps).
- Import: "+ Add Profile", then "Restore From Backup" or "Upload Profile File". This is one file at a time: "For each fixture profile you will need to repeat this process".
- Export: the top-level menu beside the fixture name, "Download".
- The Patcher can also create a "Custom Stage Fixture" on the spot, and "Save as Profile" turns it into a profile.
- Stage restore: Add Stage, "Restore Stage From Backup". Show restore: Add Show, "Restore From Backup".
- No channel-count limit per mode is stated.

KNOWN v1.5.1 BUGS:
- "Loading an older stage doesn't offer a fourth fixture group option".
- "After an update or backup restore, DMX data may not send. A power cycle fixes this."
-> IMPLICATION: 1. Generating profiles programmatically is possible only by reverse-engineering. Build one virtual-fixture profile by hand in the UI (or as a Custom Stage Fixture), download it, and treat that JSON as an undocumented template. Re-check it after every firmware update.
2. Version-control the exported profile, stage and show JSON and the full backup in the repo or state store. The spec already asks for this.
3. The receiver's channel layout config should be derived from, or checked against, the exported stage JSON so the patch and the decoder cannot drift.
4. Uploading is manual, one file at a time, so commissioning steps must be documented.
Sources: https://maestrodmx.freshdesk.com/support/solutions/articles/153000144499-fixture-profiles-overview; https://maestrodmx.freshdesk.com/support/solutions/articles/153000070482-creating-modifying-fixture-profiles; https://maestrodmx.freshdesk.com/support/solutions/articles/153000070486; https://maestrodmx.freshdesk.com/support/solutions/articles/153000241001-backup-restore-and-factory-reset; https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.24, 26, 32-36, 39-43, 57); https://fixtures.maestrodmx.com/assets/index-BUHmfFDB.js (API endpoints seen in bundle)

## 5. 5. Can Maestro run with only Art-Net-patched (virtual) fixtures, and what are the limits on fixtures, groups and channels?
[medium] ART-NET-ONLY: not explicitly documented, but strongly implied.
- Fixtures can be added to the Art-Net universe or moved there from the DMX stage. Nothing says Internal DMX must hold fixtures.
- DMX512 is one-way and the documents mention no RDM or fixture-presence detection. So Maestro cannot tell that no real lights are attached, and fixtures patched on Internal DMX with nothing plugged in will still "run".
- The DMX Channels View (v1.5) and the Control page show live output values per channel.
- Caveat: "Core Patterns do not require audio input to function, however, Maestro Patterns do". Autonomous behaviour needs an audio feed.

LIMITS:
- One Internal universe plus one Art-Net universe, 512 channels each, 1,024 in total.
- Exactly 4 Fixture Groups. Each group has its own pattern, palette, FX palette, parameters and trigger enables.
- Repeated fixtures ("Repeat Fixture Channels") count as a single fixture in the layout.
- 2D mapping is "limited": only regular grids.
- MIDI cue select reaches cues 1-98.
- No documented maximum on fixtures, stages, shows or profiles ("any number of Stages, Shows, and Fixture Profiles").
-> IMPLICATION: 1. The owner can start Phase 2 (virtual-fixture proof, Art-Net receiver, Hue mapping) now, without any DMX lights. Patch the virtual fixtures on Art-Net, optionally patch the planned moving-head profiles on Internal DMX unconnected, and feed audio.
2. With only 4 groups, a likely allocation is: heads, FrameA, FrameB, Hue. That leaves no spare group for a dedicated beat or readback fixture unless screens share a group.
3. Inside one group, Maestro spreads the pattern across fixtures ("pixels"). Patching each screen as several small RGB fixtures (for example 8-16 pixels × 3 channels) would give the renderer a 1D "pattern strip" (chases, gradients) rather than a single colour. This is a strong option for more varied visuals, still to be tested. The 512-channel budget allows it.
Sources: https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.8-10, 36-40, 43-44, 62, 75-76); https://maestrodmx.freshdesk.com/support/solutions/articles/153000070494-layout-fixture-groups-and-mapping; https://maestrodmx.freshdesk.com/support/solutions/articles/153000252041-art-net-and-maestrodmx

## 6. 6. Audio input (line vs mic, level guidance), latency or delay settings, beat or tempo exposure
[high] INPUT:
- The unit has one front-panel RCA stereo input: "stereo consumer line-level input signal (-10dBV max)" (manual p.9), "RCA stereo consumer line level (-10dBV)" (p.90).
- No mic input and no built-in mic.
- "Standard XLR to RCA cables are not recommended due to higher voltage that can over drive the audio circuit".
- A USB audio interface is also supported, for balanced signals or mics. "Only inputs 1 & 2 ... will be used". Known bug: it needs a power cycle to work.
- Ground loops: power Maestro separately from the audio source, or use an isolator.

LEVEL GUIDANCE (System, then Audio):
- Pre-Gain meter and panel LED: blue (none or low), green (OK), yellow or amber (upper end), red (clipping). Target green.
- Post-Gain is an auto-gain and should sit "near the top of the range when music is playing".
- Kick Sensitivity default 50: EDM 0-50, rock 50-75, live music 50-100. False positives are preferred to missed kicks.
- Activity Gain default 0. Raise it for live music and calibrate so the loudest material reaches near the top.

LATENCY:
- Only marketing ("Low-latency performance"). No figures and no output-delay setting.
- Per-channel Smoothing (seconds) adds lag.
- Known bug: lights may freeze for about 2 s after cue switches, with a fix planned for 1.6.

BEAT/TEMPO: not exposed via OSC, MIDI or Art-Net. Maestro does track kicks and beats internally:
- The kick icon in the UI.
- FX categories Kick Drum / No Kick Drum / Silence (section-level, not per hit).
- Chase triggers "from every Two Bars to 1/8 notes".
- Wash changes "on a multiple of the beat".
- Flash Bang fires on kicks or onsets.

MIXER CHECK: Yamaha's MG06X spec gives STEREO OUT +4 dBu nominal and +18 dBu max on balanced XLR. That is about 12 dB hotter than -10 dBV at nominal, and up to about 28 dB hotter at max.
-> IMPLICATION: 1. The spec's "mixer stereo out to Maestro RCA" path (AV_SPEC.md:319-329) needs a proper +4 dBu to -10 dBV attenuator or converter, or a mixer with a consumer-level output. A plain XLR-to-RCA lead is exactly what the vendor warns against.
2. Alternative: a class-compliant USB interface or USB mixer into Maestro's USB port, though it carries the power-cycle bug.
3. Maestro's auto post-gain already compresses level dynamics.
4. There is no beat phase for the renderer. Beat-locked visuals need either the server's own audio analysis (spec Phase 5) or a tested Art-Net proxy: a virtual fixture in its own group running Flash Bang or Chase. That costs one of the 4 groups and is limited by Art-Net cadence.
5. There is no Maestro-side delay to align the heads with the slower screens.
Sources: https://maestrodmx.freshdesk.com/support/solutions/articles/153000069597-configuring-audio; https://maestrodmx.freshdesk.com/support/solutions/articles/153000173658-troubleshooting-audio-input; https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.9-10, 21-23, 59-60, 70-71, 90); https://maestrodmx.freshdesk.com/support/solutions/articles/153000252248-download-maestrodmx-software-package-1-5-1; https://jp.yamaha.com/files/download/other_assets/6/331496/MG06X.pdf

## 7. 7. Hardware and edition variants; current firmware as of 2026
[high] HARDWARE: one model, "MaestroDMX model mDMX" by Limbic Media.
- Power: USB-C 5 V, 2 A max.
- 2× USB-A 2.0 (Wi-Fi dongle, MIDI, USB audio, update stick).
- 10/100/1000 Ethernet.
- RCA stereo line in.
- One 3-pin XLR DMX out (one universe, 512 channels).
- Indicator LED.
- TP-Link TL-WN725N 2.4 GHz Wi-Fi dongle, required. It runs AP "Maestro-XXXXXX" with password "mymaestro" at 192.168.37.1, or joins an existing network as a client.
- Aluminium case, 0.94 kg, 0-40 °C.
- FCC/CE/UKCA Class A, with a residential EMI warning.

NO OTHER EDITIONS FOUND. Retail variants are the standalone unit (Thomann UK £859) and a "Bundle" with a Flyht Pro case and a DMX F-M adapter (£888, "Available since October 2025").

ACCESSORY: Maestro FS-1 USB MIDI foot controller (cue next/prev/play-pause/stop, strobe, blinder). Retailers listed it from June 2026.

FIRMWARE:
- Current is v1.5.1, the only package in Freshdesk "Current Software" when checked on 2026-10-13. Package build stamp 2026-02-27; article modified 11 Mar 2026.
- The manual is "Software V1.5 (Jan 2026)".
- v1.6 is referenced only as the planned fix for the cue-freeze bug. No 1.6 release was found.
- Updates: v1.4+ over the air via System, then Software Update (.mae file). v1.3.2 or older via a FAT32 USB stick. Check the installed version at System, then System Info.

NETWORK DEFAULTS:
- Ethernet defaults to static 10.0.0.200. DHCP is available.
- "Unexpected behavior may occur if both WiFi and Ethernet are configured with the same subnet ranges".
-> IMPLICATION: 1. Commissioning step: read the owner's firmware version from System Info and update to 1.5.1 (Art-Net needs 1.5 or later).
2. Move Ethernet to DHCP with a router reservation on 192.168.1.x.
3. Keep Wi-Fi in AP mode on its own subnet, with a changed SSID password, rather than as a client on the home LAN.
4. Address Maestro by IP, not maestro.local: mDNS does not cross the WSL NAT.
5. Archive the stage, show, profile and full backups before any update.
Sources: https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.8-20, 88-91); https://maestrodmx.freshdesk.com/support/solutions/articles/153000252248-download-maestrodmx-software-package-1-5-1; https://maestrodmx.freshdesk.com/support/solutions/153000054801; https://maestrodmx.freshdesk.com/support/solutions/articles/153000144259-software-update-and-system-information; https://maestrodmx.freshdesk.com/support/solutions/articles/153000144258-configure-networking-settings-lan-; https://www.thomann.co.uk/limbicmedia_maestrodmx_bundle.htm; https://www.thomann.it/limbicmedia_maestro_fs_1_footcontroller.htm

## 8. Show vs Live mode semantics, triggers, and readback (affects the supervisor design)
[medium] LIVE VS SHOW MODE (manual p.55):
- "At any moment, you can switch over to Live Control by modifying any of the parameters in the Live Control section ... When in Live Control Mode, the Show is no longer running".
- To return to the show, press Play.
- Cue properties: duration, which can be infinite, plus In and Out transition times. The manual notes "Most Users set these sliders to 0 to avoid any blackout".

TRIGGER AND FX LIMITS:
- "The Trigger Buttons will only work if MaestroDMX is playing either in Live Mode or via Cues".
- v1.5.1 known bug: "Triggers and FX Palettes don't work when the show is stopped or paused. This is by design for now."
- Triggers act on all lights. STROBE means "All lights will strobe". They can be disabled per fixture group.
- Music-based blackouts and Blackout on Silence are per-group toggles, both default OFF.
- Global Brightness multiplies with per-group brightness.

READBACK OPTIONS (inference from the attribute docs, untested):
- A virtual-fixture channel typed BRIGHTNESS mirrors Global Brightness.
- Channels typed EFFECT, FOG ON/OFF or BLINDER mirror trigger state through their ON and OFF values.
- A static channel overridden by a per-cue Snapshot, recalled via that cue's FX Palette in the Default category, could encode a "cue/mode ID" readable over Art-Net. This only works while the show is playing.

REST: no official REST API. Support staff said "some users already use the existing REST API" and "We are considering, at some point in the future, official REST API support". There is no timeline.
-> IMPLICATION: 1. Sending /live/N/* tweaks (the spec's excitement, energy and motion examples) while a saved show is playing will probably knock Maestro out of Show mode into Live mode. Bake per-mode artistic parameters into cues, which can be infinite, and use /show/name plus /show/play, or drive everything in Live mode. Do not mix the two.
2. The room-UI "Blackout" should not rely on /triggers/blackout when stopped. Use /global/brightness 0 as well, and verify.
3. Put the virtual-fixture groups' STROBE and BLINDER trigger enables OFF, and clamp flashes server-side for photosensitivity.
4. Per-cue ID channels, plus BRIGHTNESS and EFFECT echo channels on the Art-Net universe, could give the supervisor observed state without undocumented APIs. Worth testing in Phase 2.
5. Remember Global Brightness also dims the screen and Hue fixtures.
Sources: https://r2.gear4music.com/media/140/1406501/download_1406501.pdf (pp.29, 47, 49-55, 60-61); https://maestrodmx.freshdesk.com/support/solutions/articles/153000252248-download-maestrodmx-software-package-1-5-1; https://maestrodmx.freshdesk.com/support/discussions/topics/153001058434

## SURPRISES
- Audio level mismatch with the spec's mixer: the spec says mixer stereo out goes into Maestro (AV_SPEC.md:319-329). Maestro's only analogue input is RCA consumer line level, "-10dBV max", and the vendor explicitly warns that "Standard XLR to RCA cables are not recommended" because they can overdrive the input. The Yamaha MG06X STEREO OUT is +4 dBu nominal and +18 dBu max on balanced XLR, at least 12 dB too hot. A -10/+4 attenuator or converter, or a USB-interface route, is required.
- Moving heads in quiet modes: Maestro Patterns, the only ones that move pan/tilt and adapt to music, "require audio input". Core Patterns have "no pan/tilt motion", and the "Still" pattern has no moving-head support. The spec's Ambient mode ("Gentle movement", Sonos possibly low or off; AV_SPEC.md:292) and any no-music state therefore cannot produce autonomous head movement. Behaviour at silence is undocumented.
- Changing Live parameters exits Show mode ("When in Live Control Mode, the Show is no longer running"). The spec's pattern of loading a show and then sending /live/1/excitement etc. (AV_SPEC.md:178-188) will likely drop out of the show's cue sequence.
- Triggers and FX Palettes do nothing when the show is stopped or paused (a v1.5.1 known bug, "by design for now"). A /triggers/blackout from the room UI may silently fail in some states, and the FX-palette mirror-ball snapshot only works while the show is playing.
- Fixture groups are capped at 4, and the 4th group is recent (older stages don't offer it). Heads, FrameA, FrameB and Hue use all four. There is no spare group for a beat-proxy or readback fixture unless screens share a group, which removes their independent patterns.
- ACTIVITY is one global value, the Audio-page Activity Level. FrameA and FrameB ACTIVITY channels will be identical, so the spec's per-screen ACTIVITY channels (AV_SPEC.md:165-166) add no independence.
- The spec's Hue-Ambience fixture is RGB + DIMMER (AV_SPEC.md:167). The manual says LED-colour fixtures should use a static MASTER DIMMER, with Maestro dimming via the colour channels. A dynamic DIMMER is designed to pulse with the music on non-LED fixtures. Mixing the two risks double dimming or pulsing on Hue.
- Global strobe and blinder triggers act on "All lights", including Art-Net virtual fixtures. Unless disabled per group, screens and Hue will strobe, which matters for photosensitivity.
- Maestro has no OSC feedback, no output-delay setting, and no beat or tempo output, even though it tracks kicks, beats and bars internally (Chase "every Two Bars to 1/8 notes"). This confirms the spec's caution (AV_SPEC.md:172). An Art-Net beat proxy is possible only by spending a fixture group on a kick-driven pattern.
- Maestro's own Wi-Fi AP is on by default with the publicly documented password "mymaestro". OSC (7672) and the web app have no documented authentication. A guest on the home LAN or the Maestro AP can control lighting directly, bypassing any authenticated room UI. This conflicts with spec section 12 ("prevent untrusted guests") and supports the lead's guest-on-LAN concern.
- Ethernet defaults to static 10.0.0.200, not DHCP. Art-Net Discover only finds nodes "on the same subnet", so a receiver inside WSL NAT will never be discovered, and Maestro must unicast to the Windows host IP, where inbound UDP 6454 is not forwarded to WSL.
- There is no published fixture-profile or stage JSON schema. The vendor's fixture library API needs a login (401). Programmatic profile generation means reverse-engineering a hand-made export.
- Current firmware is still 1.5.1 (build 2026-02-27) as of 2026-10-13. The 1.6 fix for the "lights freeze for 2 seconds after switching cues" bug has not shipped. Also: "After an update or backup restore, DMX data may not send" until power-cycled.

## OPEN QUESTIONS
- Art-Net output: packet rate, whether it sends continuously or only on change (plus any keep-alive), Art-Net version, Port-Address (Net/Sub-Net/Universe) mapping with and without "Offset Universes", broadcast support, and whether unicast ArtDmx flows to a manually entered IP with no ArtPollReply. Measure with a LAN packet capture.
- Does Maestro output a populated Art-Net universe when Internal DMX has no fixtures patched, and do both universes stay live together over hours?
- OSC: is the group index N 0- or 1-based? Is the transport UDP only? Is port 7672 configurable? Do /live/N/* messages during a playing show switch Maestro to Live mode?
- What DMX/Art-Net values are output when a show is Stopped, Paused, at Silence, and during the about-2 s cue-switch freeze: hold last, zero, or Fixture Default?
- How do Maestro Patterns behave with very quiet input such as solo piano or speech, and can Ambient-style head movement be achieved without music?
- How is the Activity Level (apparently 0-100) mapped to ACTIVITY channel DMX values, linearly across the Output Range? How fast does it respond?
- How do ZOOM and PRESET dynamic attributes actually animate?
- Can a per-cue Snapshot on a static virtual channel, recalled via FX Palette Default, reliably encode the current cue or mode for Art-Net readback?
- What firmware is on the owner's unit (System, then System Info), and is the Wi-Fi AP able to be secured or disabled?
- What is the exact JSON shape of a downloaded profile, stage and show on the owner's unit, and is it stable across 1.5.1 and 1.6?
- When will 1.6 ship, and is the unverified retailer claim that a future version will send both universes through a single Art-Net node real?
- How many Art-Net nodes can one output be configured with via "Add Node", and can more than one Art-Net output be added?