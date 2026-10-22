# Powerline Ethernet for the kiosk mini-PC (research, 2026-10-11)

Why: the kiosk mini-PC's Wi-Fi tops out at ~15-20 Mbit/s (see [KIOSK_INFRASTRUCTURE_NOTES.md](KIOSK_INFRASTRUCTURE_NOTES.md#4-wi-fi)),
running Ethernet cable to the drawing room is not possible, and the owner says powerline is the only wired option.
Need: steady 25-60 Mbit/s (two 4K HEVC streams plus web traffic), low jitter, 24/7.

This is the synthesis of a web-research pass in which every claim was re-checked against its source by an
independent verifier; only confirmed or partially-confirmed claims were used. Prices and stock are as of 2026-10-11.
Source tags: *vendor*, *indep. test*, *owner report*, *forum*, *standard*, *news*.

**Short answer:** buy a matched pair of **G.hn 2×2 MIMO ("2400"-class) adapters with a Gigabit port and a filtered pass-through socket**. My first pick is the **devolo Magic 2 LAN** starter kit at £139.99, because it is in stock in the UK now. The adapters do **not** need to be on the same ring or the same MCB. They need to be on the same consumer unit / supply, and plugged straight into wall sockets. Only a test between your two actual sockets will show whether you get enough for two 4K streams, so buy from somewhere you can return it.

Source tags: *vendor*, *indep. test*, *owner report*, *forum*, *standard*, *news*.

---

## 1. What to buy

**The class: G.hn, 2×2 MIMO, Gigabit Ethernet, filtered pass-through socket, both ends the same model.**

**Why G.hn rather than HomePlug AV2**
- **G.hn is still being developed.** The ITU has issued amendments to G.9960 in 01/24 and 03/25 ([ITU](https://www.itu.int/rec/T-REC-G.9960), *standard*).
- **HomePlug appears to have stopped.** Its specs went public domain in 2016, and no further development was mentioned ([Wikipedia](https://en.wikipedia.org/wiki/HomePlug), *tertiary*).
- **Two same-house comparisons favour G.hn:**
  - An owner measured 230/296 Mbit/s (iperf) on a TP-Link G.hn 2400 kit, against 75.7/93.6 Mbit/s on HomePlug AV2 1200. Ping was mixed: 14/3 ms against 6/5 ms ([nedprod](https://www.nedprod.com/Niall_stuff/vdiary/archives/1743274242.html), *owner report*).
  - ServeTheHome called G.hn Wave 2's ping and jitter "an enormous upgrade over the TP-Link Powerline AV2000 units". The numbers are only in charts ([STH](https://servethehome.com/powerline-av-take-2-nexuslink-g-hn-wave-2-2000-powerline-attempt/2), *indep. test*).
- **One test went the other way.** Tech Advisor got 119 Mbps two floors away with devolo Magic 2, and about 130 Mbps with an older AV1000 kit in the same house ([Tech Advisor](https://www.techadvisor.com/review/devolo-magic-2-wifi-powerline-mesh-wi-fi-review-3689901/), *indep. test*).
- So G.hn is the better bet, not a guaranteed win.

**Things to know before choosing**
- **MIMO needs the earth wire.** MIMO adapters also use the earth wire, and UK sockets all have one ([Berger et al. survey](https://www.uv.es/larsber/files/pub_jo_14_MIMO_power_survey_authors_copy.pdf), *peer-reviewed survey*).
- **Ignore the number on the box.** It is the raw link rate.
  - TP-Link says "Actual throughput is typically 20–30% of the powerline rate" ([TP-Link](https://www.omadanetworks.com/bg/support/faq/2928/), *vendor*).
  - An iperf3 test found 13–26% ([Brejcha 2020](https://www.jiribrejcha.net/2020/08/throughput-speed-test-of-tp-link-and-devolo-magic-2-wi-fi-power-line-adapters-plc/), *indep. test*).
  - A higher class was not faster in one UK test. Expert Reviews measured the AV1000 TL-PA7017 at 191/183 Mbit/s (168 across the house), but the AV2000 TL-PA9020P at only 103/115 ([Expert Reviews 2024](https://www.expertreviews.co.uk/technology/wifi-home-networking/best-powerline-adapter), *indep. test*; this was not a controlled comparison).

**What to avoid**
- **Any adapter with a 10/100 Ethernet port.** For example, the AV600 TL-PA4010 is capped at 100 Mbps ([TP-Link](https://www.tp-link.com/uk/home-networking/powerline/tl-pa4010-kit/), *vendor*).
- **Mixing G.hn and HomePlug.** They "cannot interoperate" and "can adversely affect each other" ([SmallNetBuilder](https://www.smallnetbuilder.com/basics/lanwan-basics/smallnetbuilders-powerline-faq-2015/), *indep.*). Remove any old powerline kit in the house.
- **Mixing brands.** No one has checked whether devolo and TP-Link G.hn units actually pair.

### Pick 1: devolo Magic 2 LAN

One Gigabit port plus a pass-through socket.

- **Price and stock:** Starter Kit £139.99, add-on £74.99. Both showed "Available, delivery time: 2–5 days" on devolo's UK store on 11 Oct 2026 ([devolo UK](https://www.devolo.co.uk/magic-2-lan), *vendor*).
- **Three-port version:** the Magic 2 LAN triple kit is £149.99. Its UK datasheet confirms a BS 1363 plug and a 13 A socket ([datasheet](https://shop.bt.com/content/uni2/documentation/fsbw/datasheets_magic_2_lan_triple_en_uk.pdf), *vendor*).
- **Standard:** G.hn, HomeGrid-certified, MIMO ([devolo UK](https://www.devolo.co.uk/magic-2-lan)).
- **Easy to monitor from Linux:**
  - Its web page shows each adapter's Transmit/Receive Mbps ([manual](https://www.devolo.com/media/ab/ba/ea/1753864023/Manual_devolo_Magic_2_LAN_DE_EN_FR_NL.pdf?ts=1753864023)).
  - Home Assistant can read the link rates from firmware 7.10 ([HA docs](https://www.home-assistant.io/integrations/devolo_home_network/)).
- **UK security updates** are promised until 30.04.2029 ([devolo PSTI](https://www.devolo.com/en/psti-statements-of-compliance-521), *vendor*).
- **Independent results.** These are from the Magic 2 WiFi models, which use the same G.hn powerline:
  - About 230–520 Mbit/s in near-ideal conditions, and 56–117 Mbit/s in the worst case, routed via the meter cabinet ([ocinside](https://www.ocinside.de/review/devolo_magic_2_wifi_next/5/), *indep. test*).
  - 456 Mbps in the same room, and 119 Mbps two floors away ([Tech Advisor](https://www.techadvisor.com/review/devolo-magic-2-wifi-powerline-mesh-wi-fi-review-3689901/)).
- **Risk:** devolo went insolvent in Feb 2022 and again in Dec 2023. An Austrian investor took over the business in April 2024 and said it intends to keep the powerline line ([Trending Topics](https://www.trendingtopics.eu/devolo-deutscher-powerline-hersteller-durch-oesterreichischen-investor-gerettet/), *news*).

### Pick 2: TP-Link PG2400P KIT

G.hn 2×2 MIMO, "2+2" Gigabit ports, filtered pass-through ([TP-Link UK](https://www.tp-link.com/uk/home-networking/powerline/pg2400p-kit/)). About £100.

- **Stock is patchy.**
  - Scan has it at £99.98 on pre-order, "Item due on 28 October 2026" ([Scan](https://scan.co.uk/products/tp-link-pg2400p-ghn2400-dual-pack-passthrough-powerline-kit-2plus2-gbe-power-socket-ghn-mimo)).
  - An Amazon UK search on 11 Oct showed about £104, "Only 9 left in stock". I don't have a stable product link for this.
- **Check the plug before ordering.** The UK product page says "EU", but TP-Link's UK datasheet says "Plug Type: UK" ([datasheet](https://www.tp-link.com/uk/document/87695/)).
- **Support:** HomeGrid-certified in May 2024 ([HomeGrid](https://homegridforum.org/certified-systems/tp-link-corporation-pte-ltd-g-hn2400-passthrough-powerline-adapter/)). TP-Link states UK support until "31/12/2029 or later" ([PSTI](https://static.tp-link.com/upload/manual/2026/202609/20260907/Statements%20of%20PSTI%20Compliance_TP-Link.pdf)).
- **No independent lab test was found.** One UK owner whose post links to this kit saw a 16 ms idle ping, but 157 ms at full saturation (bufferbloat). He fixed it by capping the speed at 300 Mbit/s ([fatlemon](https://www.fatlemon.co.uk/2025/06/lan-speed-record-wi-fi-vs-powerline-vs-ethernet/), *owner report*, internet speed test).

**Cheaper HomePlug fallback:** the TP-Link TL-PA7017P KIT (AV1000, Gigabit, filtered pass-through, UK plug). It is £74.99 and in stock at [Scan](https://www.scan.co.uk/products/tp-link-tl-pa7017p-kit-av1000-gigabit-passthrough-powerline-starter-kit-ofdm-8023u-ab). Its sibling without a pass-through socket measured 168 Mbit/s across a house ([Expert Reviews](https://www.expertreviews.co.uk/technology/wifi-home-networking/best-powerline-adapter)).

---

## 2. Same ring / same fuse?

**No, that is not required.** Both sockets must be on the **same installation / consumer unit (the same supply)**. Being on the same ring may help a little. No one has published UK measurements of how much, and the vendors contradict each other, so the real answer comes from testing your sockets. ETSI's four UK test homes differed by about 25 dB in median signal loss ([ETSI TR](https://www.etsi.org/deliver/etsi_tr/101500_101599/10156203/01.01.01_60/tr_10156203v010101p.pdf)), so differences between houses outweigh general rules.

- **Different MCBs or rings on one consumer unit: expected to work.**
  - TP-Link says AV2 adapters communicate "as long as they are on the same main circuit (panel)" ([KB 213](https://community.tp-link.com/en/home/kb/detail/213)).
  - TP-Link also says "Generally speaking, PLC can be used across Circuit Breakers/ Fuse Boxes/ Fuse Boards" ([FAQ 406](https://www.omadanetworks.com/uk/support/faq/406/), Q3.10). But Q3.4 of the same FAQ answers "No" for different circuits, so TP-Link contradicts itself.
  - devolo says its kit works "regardless of fuses and different phases" ([devolo FAQ](https://www.devolo.co.uk/en/support/faq)). That is marketing, with no data behind it.
  - UK owners report pairs working across two rings joined only at the consumer unit ([WhatHiFi](https://forums.whathifi.com/threads/powerline-adapters-how-successful-or-reliable.67213/latest), *owner report*, 2011).
- **Why the same ring can help.** On different circuits the signal passes through the board, where power "disperses" into the other circuits. In one measured example this was worse than a same-circuit link of similar length ([Cañete 2002](https://www.plc.uma.es/articulos/Broadband_Modelling_of_Indoor_Power_Line_Channels_2002.pdf), continental flats). Expert Reviews saw "slightly improved" results on the same ring main, but did not say how it measured ([Expert Reviews](https://www.expertreviews.co.uk/technology/wifi-home-networking/devolo-magic-2-wifi-review)). A forum post agrees ([ISPreview](https://www.ispreview.co.uk/talk/threads/wi-fi-and-or-power-line-adaptor-recommendations.38168/)). All of this evidence is weak.
- **RCDs, including split-load boards.** Most UK socket circuits are RCD-protected, so you can't avoid them.
  - devolo: "RCDs have a very small, negligible effect".
  - AVM advises avoiding links across GFCIs, the US name for RCDs ([AVM](https://uk.fritz.com/service/knowledge-base/dok/FRITZ-Powerline-1220/795_Powerline-connection-is-slow/)).
  - One electrician reported a "significant drop through the dual RCDs and MCBs" ([micoolpaul](https://micoolpaul.com/2023/07/10/how-to-actually-increase-powerline-adapter-throughput/), *owner report*).
  - There are no measurements. On a split-load board, trying two sockets on the same RCD side first is my inference, not tested advice.
- **RCBOs, AFDDs and surge protectors (SPDs) in the board.**
  - TP-Link says breakers with surge, ground-fault or arc-fault functions "can also interfere with, or prevent" a link ([KB 213](https://community.tp-link.com/en/home/kb/detail/213)).
  - An IET forum contributor says "sometimes SPDs cause powerline Ethernet links to fail" ([IET](https://engx.theiet.org/f/wiring-and-regulations/30588/multiple-afdd-s-tripping/142651), *forum anecdote*). The AFDD effect is rumour only.
  - BS 7671 A2 recommends AFDDs for ordinary homes ([Voltimum/Electrium](https://www.voltimum.co.uk/news/electrium/what-amendment-2-says-about-arc-fault)), so a newer board may have them.
- **Sub-board.** No vendor statement covers this. A sub-board fed from the main board is on the same supply, but the signal crosses an extra board, so test it. A different supply will not work: "powerline networks won't work across transformers" ([SmallNetBuilder](https://www.smallnetbuilder.com/basics/lanwan-basics/smallnetbuilders-powerline-faq-2015/)).
- **Phases.** TP-Link says adapters work across phases, "but the Powerline rate will be affected" ([KB 213](https://community.tp-link.com/en/home/kb/detail/213)). Typical UK homes are single-phase. If yours is three-phase, devolo sells a DIN-rail coupler for £184.99 that must be fitted by an electrician ([devolo](https://www.devolo.co.uk/en/products/magic-2-lan-dinrail-add-on)).
- **Never put either adapter behind a UPS.** TP-Link: "Usually, UPS has a filter function. It can cut the powerline connection." ([FAQ 406](https://www.omadanetworks.com/uk/support/faq/406/)).

---

## 3. How to install

1. **Unplug every other powerline adapter in the house.** Separate networks on the same wiring share the bandwidth ([FAQ 406](https://www.omadanetworks.com/uk/support/faq/406/)).
2. **Pair the two adapters side by side first,** then move them into place ([TP-Link guide](https://static.tp-link.com/2019/201908/20190821/7106508379_TL-PA9020P%20KIT_QIG_V1.pdf)).
3. **At the switch end,** plug adapter A **directly into a wall socket**. Do not use an extension, power strip, surge strip or UPS ([AVM](https://uk.fritz.com/service/knowledge-base/dok/FRITZ-Powerline-1220/795_Powerline-connection-is-slow/), [TP-Link](https://community.tp-link.com/us/home/kb/detail/338)). Use a single socket, or leave the other half of a double socket empty: devolo says sockets side by side "behave like a power strip" ([manual](https://www.devolo.com/media/ab/ba/ea/1753864023/Manual_devolo_Magic_2_LAN_DE_EN_FR_NL.pdf?ts=1753864023)). Run an Ethernet cable from the adapter to a UniFi switch port.
4. **In the drawing room,** plug adapter B directly into a wall socket and cable it to the mini-PC's Gigabit port.
   - Feed the mini-PC, the TVs and any chargers from a plain power strip plugged into **adapter B's pass-through socket**. devolo: "electronic devices with mains adapter can negatively affect PLC performance" ([manual](https://www.devolo.com/media/ab/ba/ea/1753864023/Manual_devolo_Magic_2_LAN_DE_EN_FR_NL.pdf?ts=1753864023)).
   - One test measured 13–15% more speed from the filtered socket ([Brejcha](https://www.jiribrejcha.net/2020/08/throughput-speed-test-of-tp-link-and-devolo-magic-2-wi-fi-power-line-adapters-plc/)).
   - Check that the total load is within the socket's rating. It is 13 A on the triple's UK datasheet; I haven't confirmed the rating for the single-port model.
5. **Keep noisy devices off the neighbouring sockets:** chargers, dimmers, switch-mode power supplies and motor appliances ([devolo guide](https://www.devolo.com/en/guide/powerline-optimisation), [FAQ 406](https://www.omadanetworks.com/uk/support/faq/406/)).
6. **Change these devolo settings** ([manual](https://www.devolo.com/media/ab/ba/ea/1753864023/Manual_devolo_Magic_2_LAN_DE_EN_FR_NL.pdf?ts=1753864023)):
   - Set a web-interface password. There is none by default.
   - Leave PowerSave **off**. It "may negatively affect" latency, and it is off by default.
   - Standby is on by default but only triggers when nothing active is on the Ethernet port. The always-on PC should keep the adapter awake; you can also switch Standby off.
   - Update the firmware.
7. **If the rate is poor, try other sockets.** One UK owner roughly doubled his link rate by moving to a different socket ([micoolpaul](https://micoolpaul.com/2023/07/10/how-to-actually-increase-powerline-adapter-throughput/)). Use the devolo Tx/Rx figure to compare sockets quickly.

---

## 4. How we'll test it

Tests run from the mini-PC against a wired machine at the switch end:

- **Link rate:** the Tx/Rx figure on devolo's web page. It is good for comparing sockets, but it is a gross rate and will be higher than real throughput ([devolo FAQ](https://www.devolo.co.uk/en/support/faq)).
- **Throughput:** `iperf3 -c <server> -t 60`, then `-R` (reverse) and `--bidir` ([iperf3 docs](https://software.es.net/iperf/invoking.html)). Test both directions: a test of older HomePlug kit found more than 1.5× asymmetry in about 30% of links ([EPFL](https://infoscience.epfl.ch/record/211905/files/main_1.pdf?version=1)).
- **Loss and jitter:** iperf3 UDP at a fixed rate, e.g. `-u -b 80M`. It reports jitter and lost/total datagrams ([Arm guide](https://learn.arm.com/learning-paths/servers-and-cloud-computing/microbenchmark-network-iperf3/basic-microbenchmarking/)).
- **Latency under load:** ping during the tests, or flent RRUL ([flent](https://flent.org/tests.html)). This catches bufferbloat like the 157 ms case above.
- **Over 24 hours, including evenings.** Link quality changes over "minutes or hours" as appliances switch on and off ([EPFL](https://infoscience.epfl.ch/record/211905/files/main_1.pdf?version=1)).

**Good enough.** These are my own engineering targets, not from a source. They are based on your 25–60 Mbit/s need.
- **TCP towards the mini-PC, worst hour:** about 120 Mbit/s or more is good (2× the 60 Mbit/s peak). 80–120 is marginal. Under 80 is a fail.
- **UDP at 80 Mbit/s:** under 0.1% loss.
- **Ping while loaded:** 99th percentile under about 50 ms.
- **Over 24 hours:** no link drops.

For reference, the worst independent results found were 40/61 Mbit/s at about 100 ft ([STH](https://servethehome.com/powerline-av-take-2-nexuslink-g-hn-wave-2-2000-powerline-attempt/2)) and 56–117 Mbit/s ([ocinside](https://www.ocinside.de/review/devolo_magic_2_wifi_next/5/)). A fail is possible. If it happens: try other sockets, including ones on the same ring, remove noise sources, and return the kit if it still fails.

---

## 5. Unknowns / things that depend on your house

- **The real throughput and jitter between your two sockets.** Only testing will tell.
- **Your board:** split-load or RCBOs, AFDDs, an SPD, a sub-board, single- or three-phase. There is no published data on how much signal each of these costs.
- **Same ring against different ring** in your house, and how much difference it makes.
- **G.hn against AV2 latency and jitter at 25–60 Mbit/s.** No independent figures were found.
- **PG2400P:** the real UK stock date, the plug type, and whether its power-saving can be turned off.
- **Your broadband type.** If it is VDSL: devolo says powerline can make VDSL unstable "in some cases", and it has compatibility profiles for this ([devolo FAQ](https://www.devolo.co.uk/en/support/faq)).
- **devolo support after 30.04.2029,** given its insolvencies.
- **BS 7671 Amendment 4 (April 2026):** whether it changed the AFDD rules was not checked.
- **The single-port Magic 2 LAN's pass-through rating.** 13 A is confirmed only for the triple.
- **AFDD nuisance trips with powerline.** Only forum anecdotes exist.

---

## Questions to settle before buying
- Can you share a photo of the consumer unit (and any sub-board)? I want to see whether it is split-load, RCBO, has AFDDs or a surge protector, and whether the supply is single-phase.
- Are the drawing-room sockets and the socket near the UniFi switch on the same consumer unit? Do you know whether they are on the same ring?
- Is the switch/router end on a UPS or a surge-protected strip? Adapter A must go into a plain wall socket.
- Are there any existing powerline adapters in the house (HomePlug or otherwise)? They need to be removed if we go G.hn.
- Is the broadband VDSL/FTTC or FTTP? This affects devolo's compatibility-mode setting.
- Is there an always-on wired machine near the switch (e.g. the media server) we can use as the iperf3 server?
- devolo Magic 2 LAN (in stock now, £139.99) or wait for the TP-Link PG2400P (~£100, pre-order due 28 Oct)? And do the TVs also need wired ports, which would favour the 3-port Magic 2 LAN triple?

---

## Candidate checked 2026-10-11: TP-Link TL-PA717 KIT (Amazon UK, from £39.99)

Verified from TP-Link UK's product, spec and support pages and the Amazon UK listing:

| | TL-PA717 KIT | Notes |
|---|---|---|
| Standard / class | HomePlug AV2 (also AV, IEEE 1901), AV1000 | Not G.hn: cannot pair with G.hn kit |
| Ethernet | 1 x Gigabit per adapter | OK |
| Pass-through socket | **None** (Amazon comparison: "Extra Power Socket ✘"; the TL-PA7017P has one) | Loses the filtered socket recommended above |
| MIMO | **Not stated** anywhere | Probably SISO (inference: TP-Link normally advertises MIMO) |
| Plug | UK | |
| Security | Pair/Reset button, 128-bit AES | |
| Power saving | Automatic "when not in use"; trigger not documented | An always-on PC should keep it awake; watch for it in tests |
| Firmware | V4 only release 2019-11-06; product page still live | Old product, no updates since 2019 |
| Independent tests | **None found**; one Amazon video titled "33Mbps through extension leads!" | TP-Link's own rule: real throughput is ~20-30 % of the link rate |

Assessment: in the right family for HomePlug, but the bottom of it. It lacks the filtered pass-through socket
and probably MIMO, and nothing independent shows its real throughput. It might pass the acceptance test, and at
about £40 with Amazon returns it is a cheap experiment, but the TL-PA7017P KIT (same class, pass-through,
sibling measured 168 Mbit/s across a house) or the devolo Magic 2 LAN (G.hn MIMO) are the better bets.
