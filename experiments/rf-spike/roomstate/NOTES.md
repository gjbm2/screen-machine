# roomstate: notes for ROOM_STATE_MODEL.md

**For:** screen-machine-2f's spec panel and reviser. **From:** screen-machine-aa (RF/blinds spike), 13 Oct 2026, late.
**Status:** superseded. The code here now implements `docs/av/ROOM_STATE_MODEL.md` v1.1 (2f), and where this file and the spec differ, the spec wins. See `V1.1_PORT_NOTES.md` for the port. This file is kept for the hub facts (§4) and the history.

Run the tests from `experiments/rf-spike`: `.venv/bin/python -m pytest -q roomstate/tests rfspike/tests` (56 tests, pure asyncio with fakes, about 11 s, no I/O and no RF).

## 1. The model in five concepts

1. **Target.** A target is `subsystem.member` and holds one JSON value: `blinds.ch02` = 100, `sonos.lounge.volume` = 25, `plug.dmx_power` = "on", `tv.north.display` = "hdmi", `screens.devtest` = "art" or `{"live": {…}}`, `ball.main` = "spinning".
   - **Selectors:** an exact target, a wildcard (`screens`, `tv.*`), a group name, or a list of these.
2. **Mode.** A mode has a `label`, a `class` (e.g. `live`) and `extends`; an `abstract` mode is a base for others. Its `state` maps selectors to entries. Later entries override earlier ones target by target, a child's entries come after its parent's, and `null` unmanages a target.
3. **Entry verbs.**
   - A plain value, or `{"set": v}`, sets the target.
   - `{"store": slot, "set": v}` stores the current value if the slot is empty, then sets.
   - `{"store": slot}` stores only.
   - `{"retrieve": slot, "else": v|"leave"}` sets the target back to the stored value and clears the slot.
   - Options: `policy` (respect, enforce or ignore), `max_age_s`, and `opts` (adapter hints such as `{"over_s": 5}` for a volume ramp).
4. **Transition steps.** The first entry in `transitions` whose `from` and `to` match (a mode name, `@class` or `*`) supplies an ordered step list:
   - a selector (one step);
   - a list of selectors (run in parallel);
   - `{"targets": …, "on_fail": "abort"}`;
   - `{"delay": s}`;
   - `{"signal": "sonos.pause", "args": {…}}`;
   - `"rest"`, which covers every change not named in an earlier step.

   The old mode's `on_exit` signals run first and the new mode's `on_enter` signals run last.
5. **One state file.** It lives at `~/.local/state/screen-machine/room/state.json` (LIVE_V1 §0) and holds `mode`, `transition`, `believed`, `desired`, `stored` and `drift`. It is written atomically (tmp file, fsync, rename) after every change. See `state.py`.

The owner's example, written in this grammar (`example_room.json`):
```json
"normal": {"state": {"privacy_blinds": {"retrieve": "pre_live"}}},
"live":   {"abstract": true, "class": "live",
           "state": {"privacy_blinds": {"store": "pre_live", "set": 100, "policy": "enforce"}}}
```
Every live mode `extends: live`, so adding one needs no new rules (test: `a_new_live_mode_needs_no_new_rules`). The Frame display mode works the same way, with `"tv": {"store": "pre_live", "set": "hdmi"}` in live and `"tv": {"retrieve": "pre_live"}` in normal.

**FROM versus TO:** the owner's "FROM mode A STORE" is written on the destination side, as "store if empty" in the live base. The effect is the same: the slot holds whatever was there before the room was first taken over, so live→live hops, interruptions and a non-live mode in between all keep the original value.

## 2. The store and retrieve rules (2f's §2.5, as implemented)

- **When a value is stored.** At the start of a transition, before its first command, every `store` entry with an empty or expired slot reads the target and saves it. A full slot is never overwritten. This gives:
  - no re-store on a live→live hop;
  - a re-entry after an interruption keeps the original value;
  - a second departure while a retrieve is still moving the blinds can't capture a half-way position (test: `rapid_toggling_never_stores_a_transient_position`).
- **When a slot is cleared.** A retrieve clears a target's slot when its command succeeds, or immediately if the target already holds the stored value.
  - **Must not get wrong:** the reference first cleared only on a successful command. A blind already at its stored value then kept a stale slot forever, and the next store skipped it. The tests caught this.
  - A member whose command fails or times out keeps its slot, so a retry still knows where to go (test: `a_blind_that_never_arrives_keeps_its_slot_for_a_retry`).
- **What an interruption does.** A new request cancels the running transition. The coordinator then re-plans from what it believes and re-reads every readable target first. The cancelled transition's target becomes the new transition's `from`, so the exit sequence (ball off before the blinds go up) still applies.
- **What survives a restart.** The slots, the believed state, and any transition with `status: running` are all in the state file. `start()` resumes that transition under the same id. Signals are recorded as `signal:<step index>:<action>` and are not fired again (test: `restart_mid_transition_resumes_without_refiring_signals`).
- **Staleness.** With `max_age_s` set on a store or retrieve, an older slot is treated as empty: a store overwrites it, and a retrieve falls back to `else`. This covers only part of the "raise the blinds at 1 am" case (§5 Q1).

## 3. Edge cases a generic model must not get wrong

1. **Store a moving member only once it has settled.** If the remote moved a blind 2 s earlier, the store waits up to `change_time_s` for it to stop (test: `store_waits_for_a_moving_member_to_settle`).
2. **Edge (send-only) targets.**
   - A store saves the *believed* value together with its confidence (`unconfirmed`). If the value is `unknown`, nothing is stored and the retrieve uses `else`.
   - Before an edge command goes out, the coordinator marks the target `unknown`; it becomes `unconfirmed` once the send succeeds. A crash mid-send therefore leaves it `unknown`, and the next transition re-sends.
   - **edge_discrete** sends only on a change, or when the value is unknown, or when the adapter sets `resend: true`.
   - **edge_toggle** never sends while the value is unknown, because one blind flip would invert the state for good.
3. **Default policies.** Each policy follows a reason:
   - `set` entries default to `respect`.
   - `retrieve` entries default to `ignore`: once the blinds are back, hand changes in Normal are the house's business and raise no drift alarm.
   - The live base marks the privacy blinds `enforce`.
4. **Skip a delay whose preceding step changed nothing.** A live→live hop with nothing to do then doesn't wait out the DMX boot delay.
5. **`on_fail: abort` on the blinds step is the privacy guard.** If the hub is unreachable, the screens never go live and the ball never starts (test: `unreachable_hub_aborts_before_anything_flashes`).
   - Weak blinds trigger the guard too. ch10–13 read −91 to −96 dBm, so if a privacy blind doesn't arrive, disco is aborted.
   - Choose `abort` or `continue` deliberately for each install.
6. **A retrieve with nothing stored does nothing by default** (`else: leave`). At first boot, Normal therefore touches only what it sets, and no blind moves.

## 4. Real Bloc hub behaviour that the model and adapter rely on

These were measured on 13 Oct; nothing has been written to a blind yet (no key).
- **Positions:** 0 is raised and 100 is lowered, per blind; blinds differ (100, 80, 0). Equality uses a tolerance of 3.
- **Reads:** `GetDeviceList` and `ReadDevice` need no key and come from the hub's cache, with no radio traffic and about 70 ms over unicast UDP from WSL. Multicast pushes don't reach WSL, so the adapter polls.
  - Poll moving members at about 2 Hz and the rest every 10–30 s.
  - Never use op 5 (status query) for polling: it transmits.
- **Writes:**
  - They need AccessToken = AES-ECB(app key, token). The client refreshes the token once if the hub rejects a write.
  - Every hub call is serialised behind one lock, with 250 ms after each write, because each write becomes a 433 MHz frame.
- **Arrival:** a blind counts as arrived only when it reads `operation == stopped` *and* its position is within tolerance of the target. A late cache update shows the old position, which doesn't match, so it can't be mistaken for arrival.
- **The cache does see hand moves** (tested 13 Oct, 23:32, while watching the cache at about 1 Hz). The owner raised and then lowered the three blinds that matter, ch02, ch03 and ch14, by hand. The hub's cache followed all three both ways, with no query and no RF from us.
  - **It updates only when a motor stops.** During travel the cache kept the old position, with `operation: stopped` and never `closing`. Then it jumped straight to the final position: ch02 at +49.9 s, ch14 at +50.2 s, ch03 at +60.2 s.
  - **ch03 reports about 10 s after the other two.** An earlier "third blind missing" was a read taken before ch03 had reported.
  - **What this means:**
    - Drift detection with `respect` and `enforce` works, but only after a hand move has finished.
    - A hand move still in progress looks `stopped` at the old position, so a store taken mid-move saves the pre-move value.
    - After a hand move, wait at least about 15 s before trusting the cache.
    - Whether the cache shows `closing`/`opening` during a move *we* command is still untested.
  - **Other blinds may move by themselves.** ch10 went up during the test on what the owner says are the Bloc app's own timers. Unmanaged targets must be ignored, and managed ones go through their policy.
- **Not yet tested; the next owner session covers them:**
  - how long after a write the cache starts moving;
  - real travel times;
  - whether STOP works mid-travel.
- **Health:** batteries range 9.61–11.76 V (ch10 lowest), and RSSI per blind is in `config/room/rf/blinds.json`. Both are worth surfacing as health later.
- **The Bloc app** may have its own timers that move blinds. That is treated as a change made by hand: `enforce` re-lowers during live modes.

## 4a. Self-describing API for the Room page's editors

The owner asked for "sensible edit surfaces for these states in the control plane web app". 2f builds the endpoints and the editors; the model supplies everything below, tested in `tests/test_describe.py`.

- **Value schemas** (`schema.py`):
  - Each adapter declares `value_schema(member)` in the renderer catalog's subset (LIVE_V1 §1.1): `type`, `minimum`, `maximum`, `enum`, `default`, and `x-ui {label, step, unit, widget}`.
  - Two `x-ui` additions: `presets` (blinds: `{"raised": 0, "lowered": 100}`) and `labels` (display names for enum values, e.g. `art_mode` → "Frame Art Mode").
  - ParamForm can therefore render member values and scene params with one component. `{}` accepts any value; screens use it, because their live spec has its own editor.
  - Adapters also declare `actions` (`{name: {label, schema}}`) for signal pickers, and `label(member)`. The config's `"labels"` map overrides `label(member)`.
- **`validate(raw, members, schemas)`** returns `[{path, message}]`:
  - every problem at once, with RFC 6901 JSON pointers into the config (e.g. `/modes/disco/state/privacy_blinds/set: blinds.ch02: 120 is above maximum 100`);
  - each value is checked against its target's schema;
  - `[]` means the config can be saved.
- **`revision(raw)`** is a 12-hex content hash, for the editor's etag: save with `If-Match`, and get 409 if someone else saved first. `status()` gains `config_revision`.
- **`room_config_schema(target_schemas, groups)`** is a JSON Schema for the whole file, for a raw-JSON editor. Per-target value schemas sit under `$defs`.
- **`describe()`** gives each editor what it needs in one JSON object:
  - targets, with subsystem, member, kind, label, schema and groups;
  - groups, and each subsystem's members and actions;
  - every mode, resolved: label, class, extends, abstract, and per target `{verb, value, slot, else, policy, max_age_s, opts, from}`. `from` names the mode the entry came from, so an editor can grey out inherited rows.
- **`plan(mode, refresh=False)`**: a dry run that changes nothing.
  - Steps use LIVE_V1 §3's shape, `{step, subsystem, action, status, detail}`, and items with the same `step` run in parallel.
  - The real transition records the same list (test: `plan_changes_nothing_and_matches_the_real_run`), so the preview and the live view can share one component.
  - Actions: `store:<slot>`, `keep:<slot>`, `set:<v>`, `retrieve:<slot>=<v>`, `leave`, `delay:<s>s`, `signal:<sub>.<action>`.
  - A skipped item says why: "already 100 (confirmed)", "nothing stored: leave", "stored value too old: else 0", "nothing changed before it", "already fired".
- **`set_target(target, value, cause)`** is a change made by hand. It is checked against the schema, and the current mode's policy then applies: `respect` records drift, `enforce` puts the value back at the next reconcile, and `ignore` does nothing.
- **`stored_edit(slot, target, value, cause)`** and **`stored_forget(slot, target|None, cause)`** correct or drop stored values. The value is checked, and the change is logged with its cause.
- **`reload(config, cause)`** swaps in a validated config.
  - Stored values are kept. The current mode's desired values are recomputed for reconcile, but nothing is sent until the next switch.
- **Rules for all of the above:**
  - `set_target`, `stored_*` and `reload` raise `Busy` while a transition is running. That is 2f's "no hot-swap mid-plan" rule.
  - Every edit goes into the state file's `log` (the last 200 entries) and is emitted as LIVE_V1's `{"type": "log"}` event.

### 4b. Changes after the edit-surfaces review (14 Oct, about 01:00)

The list is in `EDIT_SURFACES_PROPOSAL.md` (the table at the top). The ones a generic model must not get wrong:
- **An aborted switch must put the old mode's desired values back.** Otherwise the failed target mode's `enforce` rules stay in charge while the room is still in the old mode.
- **The config revision must include key order,** because later entries win.
- **A reload must not move anything.** Desired values stay and policies only relax; the next switch or a re-apply picks up the new values.
- **Re-applying the current mode must fire no on_exit or on_enter signals.**
- **Status must report ages,** never wall-clock times.
- **Refusals carry codes:** `transition_running`, `device_busy`, `enforced` (bypassed with `hold`), `toggle_state_unknown` (fixed with `assume`) and `unknown_stored`.

## 5. Owner questions (with a recommended default)

| # | Question | Default |
|---|---|---|
| Q1 | A store made at 18:00 with blinds raised, and a show ending at 01:00: should the retrieve raise the blinds at 1 am? | Retrieve anyway, with `max_age_s: 43200` as the only guard. A "never raise between 23:00 and 07:00" window would be one small extra option on retrieve if the owner wants it. |
| Q2 | If a privacy blind fails to lower, should the show still start? | `abort`, as in the example. Switch to `continue` if weak channels cause trouble. |
| Q3 | If the ball relay can only toggle, what is its state at boot? | The owner confirms it is off once; after that the coordinator tracks it. Prefer a latched relay. |

## 6. What the reference does not do

- **Hot reload of the config.** Slots are keyed by target, so they survive a reload. Targets that are no longer configured are ignored on retrieve and could be pruned.
- **The reconcile loop timer.** `reconcile_once()` exists; room_service decides how often to call it, from each adapter's `poll_s`.
- **Reporting `available: false`** for subsystems with no adapter (LIVE_V1 §3.2). The reference fails those steps instead.
- **Per-transition locking for requests from two clients.** The last request wins, and the cancelled transition emits `cancelled`.

## 7. Files

| File | What it is |
|---|---|
| `contract.py` | Adapter contract v0.2: `read(members)`, `apply({member: value}, opts)` → Outcome with `.members`, and `signal()` |
| `model.py` | Config loader and validator: groups, selectors, modes, entries, transitions |
| `coordinator.py` | Plan, store, retrieve, steps, cancel and re-plan, resume, reconcile, and `status()`, which adds `subsystems.<s>.members` and `stored` to LIVE_V1 |
| `state.py` | The state file and its atomic write |
| `fakes.py` | `FakeLevel` (travel time, cancel stops half-way, offline, members that fail) and `FakeEdge` (discrete or toggle, with physical state) |
| `example_room.json` | The worked config: normal, an abstract live base, ambient, disco, privacy blinds, tv, sonos, plug, ball, screens, mask |
| `tests/test_scenarios.py` | 29 scenario tests |
| `tests/test_describe.py` | 20 tests of the editor API: validate, lint, rules, describe, plan (and plan on a draft), manual changes (hold, assume, cancellation), stored edits, restore, reload |
| `EDIT_SURFACES_PROPOSAL.md` | The edit-surfaces proposal for 2f, with screen-machine-aa's review |
| `schema.py` | Value-schema checker and helpers, and the JSON Schema for the whole config |
