<!-- Research 2026-10-11: four independent angles (Samsung documents, reviewers, owner reports, our EDID),
each claim re-checked against its source by a separate verifier; only confirmed/partly-confirmed claims used.
Replaces the earlier, withdrawn settings list that had been assembled from other Samsung models. -->

# 2025 Frame Pro: settings for the kiosk input

This answer uses only three kinds of source:
- Samsung's 2025 e-Manual, which Samsung UK lists for your exact model. Both TVs report QE75LS03FWUXXU.
- Samsung's Frame Pro spec and press pages.
- Reviewers who tested the 2025 Frame Pro, mainly rtings.

Anything taken from another model is labelled. Where no reliable source exists, the answer says it is unknown.

## Headline
1. **The setting is Input Signal Plus**, at General & Privacy > External Device Manager, not Connection. It is set per HDMI connector. Samsung: "The native resolutions are 3840 x 2160 @ 60 Hz (UHD models) ... with the Input Signal Plus set to On. The native resolution is 3840 x 2160 @ 30 Hz with the Input Signal Plus set to Off." Your EDID matches the Off state. That is not proven until someone toggles it.
2. **Don't expect sharper stills from it.** Today's link is 4K30 in full RGB 8-bit, so stills already arrive at full 4K. Turning Input Signal Plus On mainly gives 60 Hz (smoother slow video, and Samsung's recommended PC mode) and lets the TV accept HDR. (Inference.)
3. **rtings on this model:** "We used the 'Movie' Picture Mode because it's the most accurate out of the box". Their SDR settings are Movie, Gamma 2.2, Color Tone Warm2 and Color Space Auto, with "All other settings ... either disabled or left off."
4. **There is a trade-off.** rtings: "For chroma 4:4:4, you must have the input label set to 'PC' and Game Mode turned on." Samsung: "When Game Mode is enabled, Picture Mode and Sound Mode are switched to Game automatically." So on rtings' finding, confirmed 4:4:4 comes with the Game picture mode, not Movie. Test whether the difference shows on your content.
5. **The wireless link is compressed.**
   - Samsung Korea describes the Wireless One Connect box in general as using a "high-efficiency, high-compression codec" (in an 8K context).
   - Samsung Chile says it carries 4K "without significant compression".
   - choose.tv saw PC text degrade over the wireless link.
   - The panel's Micro-HDMI is the only route reviewers found clean.

## What the TVs are set to now (read 2026-10-13 over Samsung IP Control)

Read from both TVs over the TVs' own LAN API (Samsung IP Control, `https://<tv>:1516`, see
`docs/GAME_MODE_AUTO_SPIKE_HANDOVER.md` §10) at 19:16 UTC (kiosk on X11) and 19:37 UTC (kiosk on sway). Both TVs
read the same values both times. Where they differ from the recommendations below, the TVs' values are the owner's
current choice and stand unless the owner changes them.

| Setting | On both TVs now | Recommended below | Note |
|---|---|---|---|
| Picture Mode | **Filmmaker Mode** | Movie | Owner's choice; Filmmaker is Samsung's other "accurate" mode |
| Gamma | **BT.1886** | 2.2 | |
| Colour Tone | Warm2 | Warm2 | |
| Colour Space | Auto | Auto | |
| Local Dimming | **Standard** | Low | |
| Contrast / Sharpness / Brightness / Colour / Tint | 45 / 10 / 0 / 25 / 0 | - | Colour and Tint are still offered, so the TVs do not treat the kiosk as a PC input |
| Game Mode | **Auto** (not active: Auto engages only on an ALLM request, which the kiosk never sends) | Off | **Owner, 2026-10-13: no Game Mode, ever** (Game Bar overlay and flicker). 4K60 does not depend on it |
| Kiosk link | 3840x2160 @ 60 Hz, EDID with the HDMI Forum block | Input Signal Plus On | Input Signal Plus itself has not been read; the EDID behaves as if it is On |

The kiosk's display server changed from X11 to Wayland (sway) at 19:31 UTC; sway tags the signal as content type
"Graphics" (X sent "No Data"). None of the values above changed across that switch.

## Settings table

The settings below apply to both TVs and to the kiosk input on each. Paths start at Settings > All Settings unless a row says otherwise. They come from the Aug 2025 manual, and the TVs run newer firmware (0090_1301, Sep 2026).

### Connection
| Setting | Menu path | Set to | Why | Source | Evidence |
|---|---|---|---|---|---|
| Input Signal Plus | General & Privacy > External Device Manager > Input Signal Plus, then the kiosk connector | On, for that connector | **Off:** 4K50/60 only as 8-bit 4:2:0, PC native 4K30, no HDR. **On:** 4K60 RGB/4:4:4 at 8 or 10 bit, PC native 4K60. The screen may flicker when switched. The default is undocumented. | M p.165-166, 239, 241, 245 | Samsung |
| HDMI Black Level | General & Privacy > External Device Manager > HDMI Black Level | Match the PC's range (Auto if offered; the PC sends limited-range RGB today) | A mismatch crushes or lifts blacks. Only available with RGB 4:4:4 input. | M p.166 | Samsung (when available); value is inference |
| Low Signal Check | Connections > Wireless One Connect > Low Signal Check | Your choice | Warns of a weak box link. Turn it Off if the warnings show over the kiosk. | M p.16 | Samsung |

### Picture (set these while the kiosk input is on screen)
| Setting | Menu path | Set to | Why | Source | Evidence |
|---|---|---|---|---|---|
| Picture Mode | Picture > Picture Mode | Movie | Most accurate out of the box (rtings). Flicker is 960 Hz vs 120 Hz in most other modes, which matters up close. FILMMAKER MODE is the rated-neutral alternative; its flicker is unmeasured. | RS, RR, HF, CT, ID | Reviewer |
| Gamma | Picture > Expert Settings > Gamma | 2.2 | rtings' SDR setting. | RS | Reviewer |
| Colour Tone | Picture > Expert Settings > Colour Tone | Warm2 | rtings' SDR setting. Before calibration: white balance dE 2.67, 6,100 K. | RS, RR | Reviewer |
| Colour Space Settings | Picture > Expert Settings > Colour Space Settings | Auto | rtings' SDR and HDR setting. | RS | Reviewer |
| Brightness | Picture > Expert Settings > Brightness | Set for your room | rtings: 0 gives about 100 nits, "far too dark for most people". | RS | Reviewer |
| Contrast Enhancer | Picture > Expert Settings > Contrast Enhancer | Off | rtings has it Off. Samsung lists it as a dimming cause. | RS; M p.253 | Reviewer + Samsung |
| Picture Clarity Settings | Picture > Expert Settings > Picture Clarity Settings | Off | rtings left other settings off. hifi.de: motion smoothing is off by default in Filmmaker. | RS; HF | Reviewer |
| Local Dimming | Picture > Expert Settings > Local Dimming | Low | There is no Off. Standard follows room light. rtings: it can't be turned off, is ineffective, and was tested at Low. | M p.162; RR | Samsung (options); Low is inference |
| Auto HDR Remastering, Colour Booster / Color Booster Pro, Blue Light Reduction | Picture > Expert Settings | Off | rtings left every unlisted setting off. | RS | Reviewer |
| EyeComfort Mode | Picture > EyeComfort Mode Settings | Off | Warms colours and dims the screen after sunset. | M p.164 | Samsung; Off is inference |
| Sharpness | Picture > Expert Settings > Sharpness | No sourced value | rtings' value of 5 was for upscaling only. | RS | none |

### Mode and input label
| Setting | Menu path | Set to | Why | Source | Evidence |
|---|---|---|---|---|---|
| Game Mode | Advanced Features > Game Mode Settings > Game Mode | Off | Forces the Game picture mode, disables some functions, and Samsung says "the screen may shake somewhat". For a PC, Samsung says you switch it On yourself. | M p.123; RS | Samsung + Reviewer |
| Input name and icon | Home > left > Connected Devices > hold Select > Edit | Not "PC", unless you choose the 4:4:4 route | The PC name adds Entertain and Graphic (120 Hz flicker) and changes the mode list. On its own it has no documented gain. | M p.22, 160; RR; RS | Samsung + Reviewer; choice is inference |

### Power, energy and AI (apply to the whole TV)
| Setting | Menu path | Set to | Why | Source | Evidence |
|---|---|---|---|---|---|
| Adaptive Picture | Advanced Features > AI Mode Settings > AI Mode > Adaptive Picture | Off for picture | Changes brightness and picture in real time. id.nl and T3 advise turning it off. | M p.114; ID; T3 | Samsung + Reviewer |
| Brightness Optimisation | General & Privacy > Power and Energy Saving | Off | Follows ambient light. | M p.175, 253 | Samsung |
| Energy Saving Solution | General & Privacy > Power and Energy Saving | Off | Samsung lists it as a cause of flicker and dimming. | M p.253 | Samsung |
| Motion Lighting | General & Privacy > Power and Energy Saving | Off | Changes brightness with on-screen motion. | M p.175 | Samsung |
| Screen Saver | General & Privacy > Power and Energy Saving | Off, if present | Starts after a still has been on screen for 2 hours or more. | M p.175 | Samsung |
| Auto Power Saving | General & Privacy > Power and Energy Saving | Off | Switches the screen off when it judges the room idle. | M p.176 | Samsung |
| Auto Power Off | General & Privacy > Power and Energy Saving | Off, or the longest period | Turns the TV off if the remote goes unused; also works in Art mode. The defaults are undocumented. | M p.176, 61 | Samsung |
| Auto update | Settings > Support > Software Update > Auto update | Your choice | "Video and audio settings will be reset to their defaults after a software update." | M p.177 | Samsung |

## Answers by question

**Q1. Input Signal Plus**
- **Path:** see the table (M p.165-166). Samsung's 2026 Frame Pro manual (LS03HW, a different model) keeps the same path.
- **Per connector:** yes. "When you select the HDMI connector you want to use for Input Signal Plus ... set ... On, the TV screen may flicker."
- **What it changes (LS03FW is in Samsung's "up to 144 Hz" class):**
  - Off: 4K50/60 only as 8-bit YCbCr 4:2:0, and HDR from external devices cannot be received.
  - On: 4K50/60 and 4K120 in RGB, 4:4:4, 4:2:2 and 4:2:0, at 8 or 10 bit.
  - Samsung documents formats, not the EDID.
- **Box ports:** rtings says the box's HDMI ports support the full 48 Gbps, while the panel's Micro-HDMI port is limited to 40 Gbps.
- **Default:** unknown.
- **Quirks:**
  - Samsung says the setting may be unavailable for a source that supports only UHD 24/30 Hz or FHD, and to turn it off in that case.
  - Software updates reset "video and audio settings". Whether that includes Input Signal Plus is unknown.
  - Resetting on reconnect has only been reported on a Q60C (a different model).
  - One Frame Pro owner using an Atlona matrix lost hot-plug after the TV had been off for 5-10 minutes, whether Input Signal Plus was on or off.

**Q2. Picture modes and settings**
- **Modes:** Dynamic, Standard, Eco, Movie and FILMMAKER MODE. Entertain and Graphic appear only when the input is named PC. Game is used whenever Game Mode is on.
- **Accuracy:**
  - rtings found Movie the most accurate. Before calibration: white balance dE 2.67, colour dE 2.44, gamma 2.18, 6,100 K.
  - hifi.de calls Filmmaker and Film neutral.
  - choose.tv got its best results in Filmmaker, with reds and skin tones slightly oversaturated.
  - id.nl calls the Filmmaker calibration "generally excellent".
- **Calibration:** rtings found the TV's calibration controls very effective but does not publish its values, which differ per unit. Smart Calibration (Picture > Smart Calibration, using a phone) exists, but no reviewer tested it.

**Q3. Game Mode**
- Consoles switch Game Mode to Auto. For a PC, you switch it On yourself.
- Game Mode On or Auto activates FreeSync (VRR). FreeSync Premium Pro works only through the Micro-HDMI port.
- **Will the PC trigger it?**
  - The current EDID advertises no ALLM, so the PC cannot request Game Mode.
  - Linux i915 has no ALLM code (inference).
  - One Frame Pro owner saw no automatic Game Mode with a Nintendo Switch.
- **Input lag through the box at 4K60:** about 35-45 ms.
  - TechRadar: 35-37 ms.
  - id.nl: 40-45 ms.
  - hifi.de: about 40 ms, even in Game Mode.
  - choose.tv: 40 ms.
  - rtings: 37.8 ms at 4:4:4.
- **Input lag through Micro-HDMI:**
  - rtings: 9.2 ms in Game Mode, 10.1 ms at 4:4:4, and 68.8 ms outside Game Mode.
  - id.nl: 13.5 ms.
  - hifi.de: about 9 ms.

**Q4. PC label**
- Edit path: Home > left > Connected Devices > hold Select > Edit.
- Samsung documents only the extra Entertain and Graphic modes.
- rtings found:
  - 4:4:4 needs the PC label plus Game Mode.
  - The mode list changes under the PC label.
  - Entertain and Graphic flicker at 120 Hz.
- Unknown: whether Movie stays available under the PC label, and whether the TV labels the mini-PC as PC automatically.

**Q5. Wireless One Connect**
- **Samsung UK spec:** 5 HDMI ports, "4K 144Hz (for HDMI 1/2/3/4/5)", box "Wireless One Connect (Y25 4K)".
- **Placement:** within 10 m in the same space, not inside a closed metal cabinet. Samsung warns of possible "choppy video or audio".
- **Codec:** compressed. Samsung publishes no codec name, chroma or bit depth.
- **What passes:** rtings ran 4K60 4:4:4 through the box and says 4:4:4 "is displayed properly with all supported modes" (it doesn't say which port).
- **Problems seen:**
  - choose.tv: over wireless, "The fonts start to look simply worse ... the letters lose sharpness".
  - An owner's Apple TV dropped frames on the box, and the problem went away on Micro-HDMI.
  - id.nl had brief dropouts through a solid wall.
  - rtings saw occasional freezes that needed both the TV and the box rebooted.
- **Verdict:** the box accepts 4K60 RGB. Whether that signal reaches the panel without visible loss is unknown.

**Q6. Power and Art**
- **Power and Energy Saving lists:** Energy Saving Solution, Brightness Optimisation, Minimum Brightness, Motion Lighting, Screen Saver, Auto Power Saving, Auto Power Off, Power Off Mode, Sleep Timer, On Timer and Off Timer.
- **No Signal Power Off:** not in the 2025 manual. Whether it exists is unknown.
- **Auto Power Off defaults:** unknown. A generic Samsung UK article (older models) says on by default, 4 hours. EU ecodesign rules require a 4-hour auto-standby to be enabled when the TV is delivered; whether that applies here is inference.
- **Art Mode:**
  - The motion sensor "only operates in Art Mode".
  - Art Mode Options > Sleep Options holds Sleep After, Motion Detector and Night Mode.
  - By default the screen turns off when the room dims in Art mode.
  - Auto Power Off can switch the TV off in Art mode.
- **The box:** when the screen is off, the box goes into power saving.
- **Your logs (May 2025):** brightness sensor off and motion timer off on both TVs.
- **Frame Pro owners report:**
  - Random switches to Art Mode every few minutes during HDMI use.
  - A 'lost connection' message, instead of Art Mode, when the source is switched off.
  - Sluggishness after a few days that needs a power cycle.
- **Undocumented:** how Art Mode behaves with a constant HDMI signal.

## Must check on the TV
- Input Signal Plus: what it shows today for the kiosk port, and how the list names the ports. The EDID suggests box HDMI 1, which is inference.
- Menu wording on firmware 0090_1301.
- Whether Input Signal Plus survives a PC reboot, box sleep or re-pair, Art Mode, a power cycle and a firmware update.
- Option names for Gamma, Colour Tone, Colour Space, Picture Clarity, Contrast Enhancer and HDMI Black Level.
- The current input label, and whether Movie remains available under the PC label.
- A 4:4:4 test pattern in Movie, compared with Game Mode plus the PC label.
- If possible, the box compared with Micro-HDMI for softness.
- Sharpness, using a 1-pixel line pattern.
- Black-level match, using near-black and near-white patterns.
- Local Dimming: Low or High.
- Auto Power Off options, and whether Screen Saver, Power Off Mode or a no-signal power-off exists.
- Art Mode drop-outs during HDMI use, and HDMI disconnects when the screen goes off.
- That Game Mode stays Off.
- Low Signal Check warnings.

## Verify from the mini-PC
1. Re-capture the EDID the same way edid-2026-10-11.txt was captured, and diff the two.
2. **Success looks like:**
   - An HDMI Forum block (OUI C4-5D-D8) appears, with a 600 MHz maximum TMDS and SCDC.
   - VIC 96/97 appear in the normal video block.
   - The preferred timing is 4K60 at 594 MHz.
   - The range limit is 600 MHz or more.
   - The product ID probably changes, likely to 0x7839.
   This is predicted from other Samsung TVs and has not been seen on LS03FW yet.
3. **Ignore:** the HDR blocks (already present now) and the HDMI 1.4 block's 340 MHz figure.
4. **Check the PC:**
   - xrandr should list 3840x2160 at 60.00 Hz (594 MHz).
   - i915_current_bpc should read 8.
   - The PC's ceiling is 4K60 RGB 8-bit: no FRL, so no 4K60 10-bit RGB and no 4K100+ (inference).
5. **Precautions:**
   - Use an 18 Gbps-certified cable, under 3 m.
   - Change one TV first.
   - Keep a rollback: force 4K30, or turn Input Signal Plus Off. A 2018 Samsung paired with an Intel Gemini Lake board went black at 594 MHz.

## Corrections to the earlier advice
1. **Input Signal Plus:** the name is confirmed, and turning it On is confirmed. The path is corrected to General & Privacy > External Device Manager. The 'Connection' path came from a 2022 article about other models.
2. **Game Mode off:** confirmed. rtings' accuracy settings use Movie, and Game Mode would force the Game picture mode. New trade-off: rtings says 4:4:4 needs the PC label plus Game Mode.
3. **Movie mode:** confirmed (rtings). FILMMAKER MODE is a valid alternative.
4. **Non-PC label:** partly supported. This is our inference from documented effects; no source recommends it directly.
5. **Colour Space Auto, Warm2, Gamma 2.2:** confirmed by rtings for this model. Samsung lists no option values.
6. **Picture Clarity Custom, all 0:** corrected to Off. No source supports "Custom, all 0".

**Missed earlier:**
- Local Dimming has no Off.
- The ambient, AI and energy features in the power table above change the picture or switch the screen off.
- Software updates reset picture settings.
- The wireless box compresses the signal.
- The TVs are QE75, not QE65.

## Sources
- **Samsung 2025 e-Manual BN81-27185C-660:** https://downloadcenter.samsung.com/content/UM/202509/20250912035644001/BN81-27185C-660_EUG_ROPDVBEUF_EU_ENG_250804.0.pdf
- **Samsung UK support page for QE75LS03FWUXXU:** https://www.samsung.com/uk/support/model/QE75LS03FWUXXU/
- **Samsung UK product page:** https://www.samsung.com/uk/lifestyle-tvs/the-frame/ls03fw-65-inch-the-frame-pro-neo-qled-4k-vision-ai-smart-tv-black-qe65ls03fwuxxu/
- **rtings settings:** https://www.rtings.com/tv/reviews/samsung/the-frame-pro-2025/settings
- **rtings test results:** https://www.rtings.com/tv/tools/compare/samsung-the-frame-2024-samsung-the-frame-pro-2025/56163/88497
- **hifi.de:** https://hifi.de/test/samsung-the-frame-pro-test-252449
- **choose.tv:** https://www.choose.tv/us/tv/reviews/samsung/samsung-the-frame-pro_AGM
- **id.nl:** https://id.nl/huis-en-entertainment/beeld-en-geluid/tv/review-samsung-the-frame-pro-hoeveel-beter-is-de-pro-versie-echt
- **T3:** https://www.t3.com/tech/tvs/samsung-the-frame-pro-review
- **TechRadar:** https://www.techradar.com/televisions/i-tested-samsungs-new-wireless-tv-connection-box-and-gamers-need-to-know-one-thing
- **Samsung Newsroom Korea:** https://news.samsung.com/kr/%ec%98%81%ec%83%81-%ec%9d%b4%ea%b2%83%eb%b6%80%ed%84%b0-%ec%97%86%ec%95%b4%eb%8b%a4-%eb%ac%b4%ec%84%a0-%ec%9b%90-%ec%bb%a4%eb%84%a5%ed%8a%b8-%eb%b0%95%ec%8a%a4
- **Samsung Newsroom Chile:** https://news.samsung.com/cl/the-frame-pro-redefine-la-elegancia-y-la-conectividad-con-tecnologia-inalambrica
- **Local EDID and xrandr captures:** docs/kiosk-captures/edid-2026-10-11.txt and xrandr_verbose.txt
- **Art-mode logs:** ~/archive/screen-machine-root-2026-10-14/untracked/art_mode_report_north_screen.log and art_mode_report_south_screen.log (moved out of the repo root on 2026-10-14; they contain a TV token)

---

## Check on the TV (no reliable source covers these)
- Input Signal Plus: what it shows today for the kiosk port (On or Off), and how the list names the ports (box HDMI 1-4, and whether the panel's Micro-HDMI appears as HDMI 5). The EDID's CEC address 1.0.0.0 suggests the PC is on box HDMI 1. That is our inference, so confirm it.
- Menu wording on current firmware 0090_1301 (Sep 2026). Every path here comes from the Aug 2025 e-Manual.
- Whether Input Signal Plus stays On after each of these: a PC reboot or HDMI re-plug, the box sleeping or re-pairing, a trip into Art Mode, a TV power cycle, and a firmware update. No Frame Pro source covers this. The only report of it switching itself off is from a Q60C owner (a different model).
- Exact option names for Gamma, Colour Tone, Colour Space Settings, Picture Clarity Settings, Contrast Enhancer and HDMI Black Level. Samsung's manual lists none of them. The values 2.2, Warm2 and Auto are rtings' names.
- What the kiosk input is labelled now, whether the TV auto-detected the mini-PC as 'PC', and whether Movie and FILMMAKER MODE remain available if it is labelled PC.
- Chroma trade-off. Show a 4:4:4 test pattern (fine coloured text and lines on coloured backgrounds) at 4K60, first in Movie with a non-PC label, then with Game Mode on and the PC label. Decide whether the difference is visible on your art.
- Wireless link softness. If possible, compare the same still through the box and through a Micro-HDMI cable to the panel. choose.tv saw PC text degrade over the wireless link.
- Sharpness for native 4K input. No source gives a value. Use a 1-pixel line pattern and pick the value that shows no halos.
- Black level. With near-black and near-white patterns, check that HDMI Black Level matches the PC's range. Today the PC sends limited-range RGB.
- Local Dimming: its default in Movie, and whether Low or High suits your content. Standard follows room light.
- Power: the options and default of Auto Power Off, and whether Screen Saver, Power Off Mode or any no-signal power-off exists on this model. None of these defaults is documented.
- Art Mode behaviour on a 24/7 HDMI input. Owners report two things: the Frame Pro drops into Art Mode every few minutes during HDMI use, and it shows a 'lost connection' message when the source turns off. Also check whether the PC sees an HDMI disconnect when the screen goes off, since Samsung says the box enters power saving then.
- That Game Mode stays Off on the kiosk input over several days.
- Whether Low Signal Check warnings appear on the kiosk.

## Verifying the change from the mini-PC
**What we have now (measured on both outputs)**
- Both outputs get the same 256-byte EDID, product 0x7838.
- The HDMI 1.4 block allows a maximum TMDS clock of 340 MHz. There is no HDMI Forum block (OUI C4-5D-D8).
- The preferred timing is 3840x2160@30 (297 MHz), and the range limit is 300 MHz.
- 4K50/60 (VIC 96/97) appear only in the 4:2:0-only block.
- xrandr lists 4K only up to 30 Hz.
- The link is 4K30 RGB 8-bit. It is almost certainly limited range (inference from the i915 driver code).

**What Samsung documents for On**
- The TV accepts 4K60 RGB/4:4:4 at 8 or 10 bit, and the PC's native mode becomes 4K60.
- Samsung says nothing about the EDID itself.

**What should change if it works**
This is a prediction from other Samsung TVs (a 2018 UE40NU7100 on/off pair, and 2024-2025 Samsung EDIDs in the linuxhw database). It has not been seen on LS03FW yet.
1. **The decisive sign:** an HDMI Forum VSDB (OUI C4-5D-D8) appears, with a maximum TMDS of 600 MHz and SCDC present. It may also carry FRL, ALLM or VRR fields.
2. VIC 96/97 appear in the normal Video Data Block. The 4:2:0-only block is replaced by a 4:2:0 Capability Map.
3. The preferred timing becomes 3840x2160@60 (594 MHz), and the range-limit maximum clock rises to 600 MHz or more.
4. The product ID will probably change. Other Samsung pairs went from the Off ID to Off ID + 1, so expect 0x7839. The EDID may also grow to 512 bytes.

**What not to rely on**
- The HDR blocks are already present while the setting appears to be Off.
- The HDMI 1.4 block's 340 MHz figure may not change.

**On the PC**
- `xrandr --verbose` should list 3840x2160 at 60.00 Hz (594.000 MHz). Xorg prunes that mode today because the sink limit is 340 MHz.
- After switching to it, `/sys/kernel/debug/dri/0/crtc-N/i915_current_bpc` should read 8.
- If the kiosk scripts pin 30 Hz, update them.

**Limits of this PC (inference from Linux i915)**
- The ceiling is 594 MHz TMDS with no FRL, so the best possible mode is 4K60 RGB 8-bit.
- 4K60 10-bit RGB and any 4K100+ mode are out of reach.
- 4K30 could run at 10 or 12 bit.

**How to check**
1. Capture the EDID the same way edid-2026-10-11.txt was captured. sysfs reported 0 bytes at the time.
2. Diff it against edid-2026-10-11.txt and decode it with edid-decode.
3. The TV may only re-send its EDID on a hot-plug. The flicker Samsung warns about is probably that (inference).
4. If the EDID does not change, either the wrong connector was switched or the box ignores the setting. Check on the TV.

**Risks and rollback**
- 4K60 RGB is an 18 Gbps signal. Use a certified Premium High Speed or Ultra High Speed cable; Samsung recommends one under 3 m.
- On a 2018 Samsung (a different model) with an Intel Gemini Lake board, the old equivalent setting gave 'no signal' in every 594 MHz mode.
- So change one TV first. Keep a way in to force 3840x2160@30 with xrandr, or turn Input Signal Plus back Off for that port.

## Corrections to the earlier (withdrawn) advice
1. **"Input Signal Plus via Settings > All Settings > Connection > External Device Manager"**
   - Name: **confirmed**.
   - Turning it On for the PC's port: **confirmed**. Samsung: the native PC resolution is 4K60 only with it On, and 4K30 with it Off.
   - Path: **corrected**. The right path is Settings > All Settings > General & Privacy > External Device Manager > Input Signal Plus, then select the connector. The 'Connection' path came from a 2022 Samsung UK article about other models.

2. **"Game Mode off": confirmed, with a new trade-off.**
   - Samsung: Game Mode switches Picture Mode to Game, and a PC needs Game Mode switched On by hand.
   - rtings' accuracy settings use Movie, which means Game Mode is off.
   - New: rtings says 4:4:4 chroma needs the PC label and Game Mode.

3. **"Movie mode": confirmed.** rtings, on this model: "the most accurate out of the box" for SDR. Its backlight flicker was measured at 960 Hz. FILMMAKER MODE is a supported alternative (hifi.de, choose.tv, id.nl).

4. **"Non-PC label": partly supported. This is our inference, not a quoted recommendation.**
   - Samsung documents only one effect of the PC label: it adds the Entertain and Graphic modes. rtings measured both at 120 Hz flicker.
   - rtings says the mode list changes under the PC label.
   - 4:4:4 also needs Game Mode.
   - No source shows that the PC label on its own helps.

5. **"Colour Space Auto, Warm2, Gamma 2.2": confirmed.** These match rtings' SDR settings for the 2025 Frame Pro. Samsung's manual lists these controls but not their options, so the value names come from rtings.

6. **"Picture Clarity Custom, all 0": corrected.** The sourced value is Off (rtings: "All other settings were either disabled or left off"; hifi.de: off by default in Filmmaker). No source supports "Custom with all sliders at 0".

**Missing from the earlier advice:**
- Local Dimming has no Off.
- These settings change the picture or switch the screen off: Brightness Optimisation, Energy Saving Solution, Motion Lighting, Contrast Enhancer, Adaptive Picture (AI Mode), EyeComfort Mode, Screen Saver, Auto Power Saving and Auto Power Off.
- Software updates reset picture settings.
- The wireless box compresses the signal.
- The task notes said QE65, but both TVs report QE75LS03FWUXXU. Samsung lists the same manual and firmware for both.
