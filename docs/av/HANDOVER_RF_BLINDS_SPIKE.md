# Handover: RF and blinds spike (mirror ball + Bloc Blinds)

**For:** the agent session that picks up this spike, and the owner.
**From:** screen-machine-2f, which maintains `docs/AV_PROGRAMME.md` (v2) and is building the room coordinator (`room_service`).
**Date:** Tue 13 Oct 2026, late evening.

## 1. Goal

Find a reliable way for software on the workstation to:
1. start and stop the **mirror-ball motor**, and
2. lower and raise the **Bloc Blinds**,

and deliver a small adapter for each that the room coordinator can call during mode transitions. Example: entering `disco` lowers the blinds (so the neighbours don't see flashing lights) and starts the ball; leaving `disco`, in any direction, stops the ball.

This is a spike. The deliverable is evidence and a working prototype, not production code.

## 2. How your adapters will be used

Read `docs/AV_PROGRAMME.md` §2 first. In short:
- A mode declares held **state** (`mirror_ball: spinning`, `blinds: lowered`) plus one-shot actions.
- On a transition, the coordinator compares the room's believed state with the target mode and sends only what changed. It orders and times the steps, e.g. nothing flashes until the blinds have finished lowering.
- Each subsystem is one of these kinds:
  - **level** (settable, and can be read back);
  - **edge with separate codes** (send-only, with distinct ON/OFF or UP/DOWN/STOP codes, so re-sending is safe);
  - **edge toggle** (one code flips the state; to be avoided);
  - **one-shot action**.

**Provisional adapter contract.** room_service will define the final one; keep your prototype close to it.

```python
class Adapter:
    name: str                  # "mirror_ball", "blinds.living_room"
    kind: str                  # "level" | "edge_discrete" | "edge_toggle" | "action"
    states: list[str]          # ["stopped", "spinning"]; ["raised", "lowered"]
    change_time_s: dict        # time to reach each state, e.g. {"lowered": 35.0, "raised": 35.0}
    async def apply(self, desired: str) -> "Outcome":   # ok | failed | unconfirmed (+ detail)
        ...
    async def read(self) -> str | None:                 # level adapters only; None if unknown
        ...
```

What the kind is worth to the coordinator:
- **Blinds:** if a route gives position feedback, they become **level** (readable). Otherwise they are **edge_discrete**, with a measured travel time.
- **Mirror ball:** wants separate ON and OFF codes (**edge_discrete**). A toggle code can drift out of step with no way to detect it.

## 3. What is known (web research, 13 Oct; not yet tested here)

**The radio unit, Amazon UK B083LLLVPN, is a BroadLink RM4 Pro.**
- It is a Wi-Fi IR + RF transmitter and learner hub that covers 433 MHz and 315 MHz. It transmits only; it is not a receiver relay.
- **Setup:** the BroadLink app is needed once, to join it to 2.4 GHz Wi-Fi. The device must then be left **unlocked** in the app, or local control fails.
- **Local control** needs no cloud:
  - `python-broadlink` (https://github.com/mjg59/python-broadlink) supports the RM4 Pro. RF learning is `sweep_frequency` → `check_frequency` → `find_rf_packet` → `check_data`; replay is `send_data`.
  - Home Assistant's Broadlink integration is local polling.
  - No Tasmota or ESPHome firmware was found; none is needed.

**Mirror ball.**
- The RM4 Pro can't switch anything on its own. The motor needs a **433 MHz receiver**: an RF mains socket, or a learning-code (EV1527-style) relay.
- Such relays usually offer momentary, toggle and **latched** modes. Latched mode uses separate ON and OFF codes. **Use latched**, and have the RM4 Pro learn the codes from the relay's own fob.
- **Unknown:** whether the owner already has a receiver on the ball's motor (§6, Q1).

**Bloc Blinds.** These findings are strongly inferred, not confirmed.
- The smart range is **Bloc iQ**: a 15-channel Smart Remote, a £99 **Smart Hub** (2.4 GHz Wi-Fi; needed for the app, Alexa and Google), and the Bloc Blinds app.
- It looks like a rebadged **Dooya "Connector"** system:
  - Home Assistant's "Bloc Blinds" entry is a virtual integration provided by the **Motionblinds** integration (confirmed).
  - That integration talks **locally** to Dooya Connector-style bridges over UDP multicast `238.0.0.18`, ports 32100/32101, using a 16-character key.
  - Bloc's way of getting the key: app → Settings → About → tap the icon 5 times.
  - That integration gives **positions and feedback**, so the blinds would be a **level** subsystem.
- Motor pairing: hold P1 on the motor head, then STOP on the remote. This looks like Dooya's procedure.
- There is no evidence of Somfy RTS rolling codes. The RF frequency is very likely 433.92 MHz but not confirmed.
- **RM4 Pro learning the Bloc remote** is plausible, but untested for Bloc. One Home Assistant forum user reports an RM4 Pro learning Dooya curtain codes reliably. This route gives no position feedback.
- A fallback that handles Dooya bi-directional motors: an RFXtrx433XL. Somfy TaHoma is not relevant.

**Sources:**
- https://www.amazon.co.uk/dp/B083LLLVPN
- https://www.home-assistant.io/integrations/broadlink/
- https://github.com/mjg59/python-broadlink
- https://www.home-assistant.io/integrations/bloc_blinds/
- https://www.home-assistant.io/integrations/motion_blinds/
- https://www.blocblinds.co.uk/blociq-smart
- https://www.blocblinds.co.uk/blociq-smart-buynow
- https://community.home-assistant.io/t/help-with-rf-blinds-rfxcom/761818
- https://community.smartthings.com/t/window-sunshade-controller-dd7002b-integration/169556

## 4. Network caveat (important)

The workstation runs the room software inside **WSL2 behind NAT**, and the two directions behave differently:
- **Inbound:** no LAN UDP, and no multicast, reaches WSL. The Windows `netsh portproxy` forwards TCP only.
- **Outbound:** UDP to LAN devices is untested (v1 archive §1, "WSL network"; research file r5).

Consequences:
- **python-broadlink** works over UDP to the device. Device discovery uses broadcast, which may fail from WSL, so **connect by IP** (`broadlink.hello(ip)`). Check that unicast replies come back through the NAT.
- **Motionblinds:** the hub's status reports are multicast and won't reach WSL. Unicast request/response may work. Test both, and say which works.
- If UDP from WSL fails, report it, with what a Windows-side relay or a Windows-native helper would need. Don't install one: Windows changes are the owner's.

## 5. Steps

**0. Owner (before or at the start of the spike):**
- Join the RM4 Pro to 2.4 GHz Wi-Fi in the BroadLink app, leave it **unlocked**, and give the agent its IP. A DHCP reservation is preferred.
- Answer the questions in §6.

**1. Agent, read-only:**
- Create a venv under `experiments/rf-spike/` (not system-wide). Install `broadlink` and, if the hub route is in play, `motionblinds`.
- Connect to the RM4 Pro by IP, authenticate, and read its type and firmware.
- **Don't transmit.**

**2. Agent + owner, with the owner present and watching the devices:**
- **Learn** ball ON and ball OFF from the receiver's fob, and UP, DOWN and STOP for one blind channel from the Bloc remote: sweep, then hold the button, then press once.
- **Replay** each code with the owner's OK. For each, record:
  - success in 10 tries;
  - send-to-movement latency;
  - range and position: where the RM4 Pro sits, and whether it reaches every blind.
- **Time each blind's full travel**, up and down, with a stopwatch.
- **Check STOP:** does it stop mid-travel? Do blinds on other channels move by mistake?

**3. Agent, if RM4 Pro learning fails for the blinds, or if position feedback is wanted:**
- Try the Bloc iQ hub through `motionblinds`, if the owner has the hub: get the key (as in §3), then read positions.
- Move one blind only with the owner's OK.
- Report whether multicast and unicast work from WSL (§4).

**4. Agent:**
- Write the prototype adapters (`mirror_ball`, `blinds`) to the contract in §2.
- Give them a CLI for manual tests, e.g. `python -m … ball on|off`, `python -m … blinds <channel> up|down|stop`.
- Record the measured travel times.
- Write the report (§7).

## 6. Questions for the owner

| # | Question |
|---|---|
| Q1 | Does the ball's motor already have a 433 MHz receiver (an RF socket or relay)? Make and model? Does it have latched ON/OFF codes or a single toggle? If there's no receiver, buy a latched-mode 433 MHz learning relay, or an RF mains socket with separate ON and OFF codes. |
| Q2 | Which Bloc remote and motors? How many blinds, on which channels, and which ones should a mode lower? |
| Q3 | Do you have the Bloc iQ Smart Hub? (It's the route with position feedback.) |
| Q4 | Where will the RM4 Pro sit? It needs to reach every blind and the ball's receiver. |
| Q5 | When a mode that lowered the blinds ends, should they stay down (`leave`) or go back up (`restore`)? This is AV programme decision D5. |

## 7. What to hand back

| Deliverable | Location |
|---|---|
| Report: what works, success rates, latencies, travel times, WSL networking results; recommended route and kind (level / edge_discrete) for the ball and for the blinds; anything the owner must buy or configure | `docs/av/RF_BLINDS_SPIKE_REPORT.md` |
| Prototype adapters and the CLI | `experiments/rf-spike/` |
| Learned RF codes and device addresses (not secret) | `config/room/rf/` (JSON) |
| Keys (e.g. the Bloc hub key) | `~/.config/screen-machine/rf/`, file mode 0600 (**never in git**) |

When done, send screen-machine-2f a short message pointing at the report.

## 8. Rules for this checkout

1. **Transmit only with the owner's OK, while they are present.** Blinds and the ball motor are physical. Nothing transmits from tests or loops; replays are manual, one at a time.
2. **Git:** never run `git stash`, `checkout`, `reset`, `pull`, `clean` or `add -A`. Commit only with the owner's OK, only your own paths, in pathspec form (`git commit -m … -- <paths>`). Other sessions' changes are already staged in the index.
3. **Your paths:** `experiments/rf-spike/`, `config/room/rf/` and the report file. Touch nothing else.
4. **Processes:** no broad `pkill`; don't stop other sessions' processes. `start.sh`'s `pkill -f "python.*app\.py"` can kill your own shell, so don't run it.
5. **No system packages, Windows, router, kiosk or TV changes.** Those are the owner's.
6. **No secrets in git.**
