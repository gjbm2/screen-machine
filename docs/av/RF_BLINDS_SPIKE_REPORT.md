# RF and blinds spike: report (interim)

**For:** the owner and screen-machine-2f. **From:** screen-machine-aa. **Date:** Tue 13 Oct 2026, late.
**Status:** the blinds route is **proven read-only**. Moving blinds waits on the hub key and an owner session (§5). The mirror ball waits on the RM4 Pro, due later this week.

## 1. Recommendations

| Subsystem | Route | Kind | Confidence |
|---|---|---|---|
| **Blinds** | The **Bloc iQ Smart Hub** over its local UDP protocol (a Dooya/Motion "Connector" bridge). The RM4 Pro isn't needed for the blinds. | **level**: every blind reports its position, so the coordinator can read back each one (0 raised, 100 lowered) | Reads proven on all 13 blinds. Writes are the documented protocol but untested here. |
| **Mirror ball** | The BroadLink RM4 Pro plus a 433 MHz receiver relay in **latched** mode, with separate ON and OFF codes | **edge_discrete** | Not started: no RM4 on the network yet. The state model handles it (§4). |

## 2. Network results from WSL2 (NAT)

| Test | Result |
|---|---|
| UDP control (DNS to the router) | OK, 4 ms |
| Unicast request and reply to the hub (`GetDeviceList`, `ReadDevice` on :32100) | **Works**, about 70 ms: the hub replies to the sender's port, so the NAT passes it back |
| The hub's multicast pushes (238.0.0.18:32101) | **Not received**: 0 packets in a 75 s passive listen |
| BroadLink hello sweep | No device found, as expected: the RM4 isn't set up yet |

**No Windows relay is needed for the blinds**: the adapter polls the hub's cache instead of waiting for pushes. The Windows UDP relay planned for Art-Net (AV v1 AV-D2) is still needed for that later. Built as a generic forwarder, it could also carry the hub's pushes with one more config line, if instant updates are ever wanted.

## 3. The hub (measured 13 Oct)

- **Address:** 192.168.1.136, mac a4cf1288dd8c, firmware A1.0.9_B0.2.0, protocol 0.9.
  - **Owner:** please add a DHCP reservation for this address.
- **Blinds:** 13 battery roller blinds on channels 2–14, all bi-directional. Addresses and signal strengths are in `config/room/rf/blinds.json`.
- **Reads:** reading from the hub's cache needs **no key** and sends nothing over RF.
  - The status query (operation 5) does transmit. The adapter never polls with it.
- **Writes:** they need the app key (Settings → About, tap the icon 5 times).
  - The key goes in `~/.config/screen-machine/rf/bloc_hub.json`, mode 0600, and never in git.
- **Signal:** −71 to −96 dBm. Channels 10–13 (−91 to −96) may miss commands.
- **Batteries:** 9.61–11.76 V; ch10 is the lowest.

## 4. The state model

The owner asked for one general mechanism: store, set and retrieve on any subsystem, for any subset of its members, driven by mode transitions. That covers blinds, the ball, Frame display mode, screens, Sonos, volume and smart plugs.

- **Reference implementation:** `experiments/rf-spike/roomstate/`, which has five concepts, JSON config, one atomic state file and 29 scenario tests. The details are in `roomstate/NOTES.md`.
- **The spec:** screen-machine-2f owns it in `docs/av/ROOM_STATE_MODEL.md` and builds on this reference.
- **The blinds adapter:** `experiments/rf-spike/rfspike/bloc_hub.py`, class `BlocBlinds`, written to the reference's adapter contract (v0.2).
  - It moves each blind independently and checks the hub's cache for arrival.
  - Each blind reports one of three results: ok, unconfirmed (sent, but no arrival) or failed (not sent).
  - It has 7 tests against a fake hub, and one of them runs it under the reference coordinator.

## 5. Still to do, with the owner present (about 10 min)

1. **Hub key:** run `cd experiments/rf-spike && .venv/bin/python -m rfspike set-key`.
2. **Which blinds:** the channel numbers of the three privacy blinds.
3. **Tests on the real blinds,** for each of those three channels:
   - **lower and raise:** `blinds time chNN down|up --confirm` records the travel time in `blinds.json`;
   - **an in-between position:** `blinds set chNN 50 --confirm`, which tests restoring a value such as 80;
   - **STOP mid-travel;**
   - **a group move;**
   - **reliability:** success in 10 tries, and latency from send to the blind starting to move.
4. **Hand moves:** while `blinds watch <group>` runs, press the physical Bloc remote. This shows whether the hub's cache sees hand moves, which decides whether `respect` and `enforce` can work.
5. **Bloc app timers:** check whether any are set; they would move blinds behind the coordinator's back.

## 6. For the owner to buy or decide

- **Ball receiver (handover Q1):** does the ball's motor already have a 433 MHz receiver? If not, buy a learning relay with a **latched** mode (separate ON and OFF codes), or an RF mains socket with separate codes.
- **Restoring blinds late at night:** if a show ends at 1 am, should the blinds go back up to where they were? The default is yes, but stored positions expire after 12 h. A "never raise at night" window could be added (NOTES §5 Q1).
- **If a privacy blind fails to lower:** the default is to abort the show before anything flashes (NOTES §5 Q2).

## 7. Files

| Path | What |
|---|---|
| `experiments/rf-spike/rfspike/` | The hub client, the `BlocBlinds` adapter, and a CLI (`python -m rfspike`; anything that moves a blind needs `--confirm`) |
| `experiments/rf-spike/roomstate/` | Reference coordinator, contract v0.2, example room JSON, NOTES.md, tests |
| `experiments/rf-spike/probe_lan.py`, `probe_multicast.py` | The WSL network probes (read-only) |
| `config/room/rf/blinds.json` | Hub and blind addresses (not secret) |
