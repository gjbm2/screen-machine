# Room AV: spec and evidence

- `../AV_PROGRAMME.md` is the programme of work. Start there.
- `AV_SYSTEM_SPEC_v0.1.md` is the owner's system specification (v0.1, 11 Oct 2026), copied verbatim. Where the programme and the spec disagree, the programme's §2.3 records the correction and its evidence.
- `evidence/` holds the research behind the programme. It was gathered read-only on 2026-10-13 by parallel research agents, with sources and a confidence label on each finding:
  - `r0_backend_integration.md`: Flask/WS process and port inventory, auth, scheduler and Alexa hooks, Siren in a second process, deploys.
  - `r1_display_page.md`: how `/display/<id>` decides what to show, the mask, overlays, reloads, TV wake, and the Samsung bugs.
  - `r2_streaming_lab.md`: what bd's lab has proven on the kiosk, the source seam for a show-driven renderer, and the gaps to a production stream service.
  - `r3_maestro.md`: MaestroDMX facts from the v1.5 manual (Jan 2026) and support articles: Art-Net, OSC, attributes, groups, audio input, quirks.
  - `r4_refactor_plan_vs_av.md`: a phase-by-phase critique of `REFACTOR_PLAN.md` against the AV direction.
  - `r5_network_sonos_osc_artnet.md`: WSL inbound UDP, Sonos local control (UPnP toggle, SoCo), python-osc, the Art-Net protocol.
  - `r6_video_and_photosensitivity.md`: WebRTC/MediaMTX vs MSE, NVENC capacity, N95 decode, flash limits (BT.1702, WCAG, IRIS).
  - `r7_hue.md`: the two bridges on the LAN, Entertainment limits, restore behaviour, Python libraries, artnet-bridge.
  - `FACTS_DIGEST_2026-10-13.md`: the lead session's running digest, including the late updates of the evening (Game Mode closed, hard cuts, compositing split, sway kiosk, 4K60).
- **Read the dated sections at the end of the digest first.** Several early facts were superseded the same evening: one X screen became sway; Game Mode on/off/auto became never; page crossfades became hard cuts.
- The Game Mode spike's record is `../GAME_MODE_AUTO_SPIKE_HANDOVER.md` (§9 research, §10 results and owner directives).
