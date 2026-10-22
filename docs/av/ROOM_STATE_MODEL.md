# Room state model

**For:** the owner (§0–§2) and the room_service implementers (all of it).
**From:** screen-machine-2f's design panel (the synthesis), 13 Oct 2026, late; revised 14 Oct after a review pass (§11).
**Status:** v1.1. room_service's coordinator is built to this spec. Changes go to screen-machine-2f.
**Base design:** screen-machine-aa's reference implementation in `experiments/rf-spike/roomstate/`: the adapter contract v0.2, `model.py`, `state.py`, `coordinator.py`, `fakes.py`, `schema.py`, `example_room.json`, `NOTES.md`, `EDIT_SURFACES_PROPOSAL.md` and 50 tests (29 scenario tests and 21 editor tests, all passing at 00:23 on 14 Oct). This spec **adopts** it. The model, the store/set/retrieve verbs, store-if-empty, clear-on-match, the edge rules, the editor API and the tests are aa's work. Three tracers then ran it against scenarios S1–S14, and a review pass traced the result again. This spec adds their fixes, keeping the concept count where aa left it.
**Related:** `docs/AV_PROGRAMME.md` §2–§3 (modes, transitions, the art↔live cut); `docs/av/contracts/LIVE_V1.md` §3 (the API this extends, additively); `docs/av/AV_PROGRAMME_v1_2026-10-13.md` §3.7 (Hue snapshot and restore; never fight a takeover).

---

## 0. Summary

**A mode is a whole-room configuration. A transition is a managed change from one mode to another.** The coordinator compares what the room is believed to be doing with what the requested mode wants. It changes only the differences, in the order the owner wrote down. Afterwards it keeps the room in that mode, with a policy for each target that says what to do about changes made by hand.

The model has five concepts, the same five as aa's reference:

| # | Concept | In one line | Example |
|---|---|---|---|
| 1 | **Target** | One controllable value, written `subsystem.member`, holding JSON. A selector names several targets at once. | `blinds.ch02` = 100; `screens.north-screen` = `"art"`; the group `privacy_blinds` |
| 2 | **Mode** | A named map from selectors to entries. `extends` inherits from a parent and `abstract` marks a base. `@live` means `live` and every mode built on it. | `disco` extends `live`, which extends `house` |
| 3 | **Entry verbs** | `set` a value. `store` the current value in a slot first, but only if the slot is empty. `retrieve` it later, and clear the slot. Each entry has a policy for hand changes. | live: `{"store": "pre_live", "set": 100}`; house: `{"retrieve": "pre_live"}` |
| 4 | **Transition rules** | A table of ordered steps for (from, to) pairs: targets, parallel groups, delays, signals, `rest` and abort conditions. Modes can add one-shot `on_enter` and `on_exit` signals. | into `@live`: mask, then blinds (abort if they fail), DMX power, 2 s, screens, ball, rest |
| 5 | **State file** | One JSON file outside the repo, written atomically. It holds the mode, the transition, beliefs, desired values, stored slots and drift. | `~/.local/state/screen-machine/room/state.json` |

**What the owner gets:**
- **The same three verbs for everything.** Blinds, TV display mode, Sonos volume and playback, and Hue later all use store, set and retrieve. Nothing is special-cased.
- **Each target goes back to its own value.** After a live episode, each blind, each TV and the Sonos volume return to where they were before it started (D5).
- **No re-store on live → live.** Disco → ambient keeps the original stored values, and the scene changes inside the stream.
- **Interruptions are safe.** If you change your mind mid-transition, the room heads for the new mode, still restores the *original* values, and keeps a safe order. A blind sent back to where it started is not counted as "there" until the hub can prove it.
- **Restarts continue.** room_service picks up where it stopped, and one-shot signals never fire twice.
- **Hand changes are respected, or re-asserted once. They are never fought.**
- **A failed privacy step stops the show before anything lights up**, and the room goes back to the mode it was in. **A failed lights-off step keeps the blinds down** and waits for you.

**What changed from the reference:** there are no new owner concepts. The fixes are rules and state fields (§1.3), and `class` is gone because `@name` replaces it. The review (§11) added two options to the existing `on_fail` key (`rollback`, `allow_skipped`) and nothing else the owner has to learn. Two possible concepts are deferred: overlays such as photosafe or guests (S14), and precedence between requesters (S13).

**Files:** the owner's config is `config/room/modes.json` (in git). The runtime state is `~/.local/state/screen-machine/room/state.json` (not in git).

---

## 1. Concepts and vocabulary

### 1.1 The five concepts

1. **Target.** A target is `subsystem.member` and holds one JSON value. Examples:

   | Target | Values |
   |---|---|
   | `blinds.ch02` … `blinds.ch14` | 0 (raised) to 100 (lowered) |
   | `tv.north-screen` | `"standby"`, `"art_mode"`, `"hdmi"` |
   | `screens.devtest` | `"art"`, or `{"live": {"scene": …, "transitions": {…}, "params"?: …, "profile"?: …}}`; read-only `"failed"` when the controller has given up (§5.4) |
   | `sensor_mask.main` | `"follow"`, `"suspended"` |
   | `page.north-screen.timed_reload` | `true`, `false` |
   | `mirror_ball.main` | `"spinning"`, `"stopped"` |
   | `plug.dmx_power` | `"on"`, `"off"` |
   | `sonos.lounge.volume`, `sonos.lounge.playback` | 0–100; `"paused"`, `"stopped"`, `{"favourite": "Disco"}` or `{"uri": …}` |

   A **selector** names one or more targets: an exact target, a prefix (`sonos.lounge`, `screens`), a wildcard (`page.*.timed_reload`), a group, or a list of these.
2. **Mode.** A mode has a `label` and a `state` map from selectors to entries. It can also have one-shot `on_enter` and `on_exit` signals.
   - `extends` inherits a parent's state and overrides parts of it.
   - An `abstract` mode is a base: it can't be requested.
   - `@name` in a transition rule means that mode and every mode that extends it, directly or not.
3. **Entry verbs.**
   - `set`: hold this value.
   - `store`: before changing the target, save its current value in a named slot, unless the slot already holds one.
   - `retrieve`: put the slot's value back, then clear the slot. If nothing is stored, use `else`, or leave the target alone.
   - Every entry has a **policy** for changes made by hand: `respect`, `enforce` or `ignore`.
4. **Transition rules.** The first rule whose `from` and `to` match supplies the ordered steps. Only targets that need changing are sent. A step whose targets need nothing costs nothing.
5. **State file.** Everything the coordinator knows or has promised lives in one file. It is rewritten atomically after every change.

### 1.2 Vocabulary

Each term is defined once, here.

| Term | Meaning |
|---|---|
| **subsystem** | One kind of device behind one adapter: `screens`, `tv`, `sensor_mask`, `page`, `blinds`, `mirror_ball`, `plug`, `sonos`; later `hue`, `maestro`. |
| **member** | One controllable thing inside a subsystem: `ch02`, `north-screen`, `lounge.volume`, `main`. A subsystem with a single member calls it `main`. |
| **target** | `subsystem.member`: the unit the coordinator stores, sets, retrieves, believes and reports. |
| **value** | The JSON a target holds. Each adapter defines its own values and normalises them (§5.2). |
| **selector** | A way of naming targets in config (§2.4). |
| **group** | A named list of selectors, defined only in `modes.json`. |
| **mode** | A named whole-room configuration (§1.1). |
| **abstract mode** | A base mode that can't be requested, such as `house` or `live`. |
| **family** | A mode together with its ancestors, outermost first. The family of `disco` is `house`, `live`, `disco`. |
| **entry** | What a mode says about one target: a verb plus options. |
| **slot** | A named store, such as `pre_live`. It holds one saved value per target. |
| **episode** | The time between a store into an empty slot and the end of that slot entry (§4.6). |
| **else** | The value a `retrieve` uses when its slot is empty or too old. The default is `"leave"`: send nothing. |
| **policy** | What reconcile does when a target drifts. `respect`: report the drift. `enforce`: re-assert once, then report it as `overridden`. `ignore`: don't watch the target; after the mode has applied it on arrival, it is the user's (§4.4). Defaults: `set` → respect; `retrieve` → ignore. |
| **opts** | Hints passed to the adapter, such as `{"over_s": 5}` for a volume ramp. An adapter ignores hints it can't use. |
| **transition rule** | One entry in `transitions`: `from`, `to`, `steps`. |
| **step** | One item in a rule: a selector or a parallel list of selectors, a `{"targets": …}` object, a `{"delay": s}`, a `{"signal": …}`, or `"rest"`. |
| **rest** | Every target that needs changing and isn't named by any other step of the rule. It is added at the end if a rule leaves it out. |
| **signal** | A one-shot action that isn't held state: Sonos pause, a Maestro cue. Written `subsystem[.member].action`. |
| **on_enter / on_exit** | A mode's signals, fired when the room enters or leaves that mode's family (§4.5). |
| **on_fail** | What a failed step does: `"continue"` (the default), `"abort"`, or `{"abort_on": <selector>, "rollback"?: bool, "allow_skipped"?: bool}`. |
| **guarded target** | A target named by the `abort_on` of the step that sends it (with `"abort"`, every target of the step). |
| **abort** | A transition stops at the failed step. By default the room then rolls back (§4.7); with `"rollback": false` it stays part-way and waits for the owner. |
| **rollback** | After an abort, one automatic transition back to the last mode the room completed. A rollback is never itself rolled back. |
| **re-apply** | A request for the mode the room is already in, with nothing unfinished: state only, no signals, no new stores (§4.2). |
| **belief** | What the coordinator thinks a target holds: `{value, confidence, at}`. For a level target the value is the last reading or a proven arrival, never a value that was only commanded. |
| **confidence** | `confirmed`: read back. `unconfirmed`: an edge command that was sent (edges can't be read). `stale`: the last read failed, so this is the last good value. `unknown`: never known, or an edge command that didn't go out cleanly. |
| **in flight** | A level command has been sent and its arrival isn't proven yet: `believed.inflight` (§3.3). |
| **proof of arrival** | A reading that can only come from the device having done what was last commanded (§4.4). A cache that still shows the pre-move value is not proof. |
| **desired** | What the current or target mode wants for each target, with its policy. |
| **settled** | The desired value has been confirmed, or was already there. A target that isn't settled is retried (§4.8). |
| **drift** | A settled target that reads back different from its desired value. |
| **overridden** | A target under enforce that drifted again after its one re-assert, or that someone set by hand with `hold`. It is reported and left alone until the next transition. |
| **reconcile** | The background loop that reads targets, records drift, re-asserts under enforce and retries unsettled targets. |
| **outcome** | What an adapter reports for each member: `ok`, `failed` or `unconfirmed`, plus `retry: false` when the device side refused on purpose (§5.2). |
| **row** | One line of progress in status (LIVE_V1's `steps`): one per target command, signal or delay. |
| **row status** | `pending`, `running`, `ok`, `failed`, `unconfirmed`, `cancelled`, `skipped`. |
| **transition status** | `running`, `ok`, `partial` (some rows not ok), `failed` (aborted, or an error), `cancelled`. The `phase` (`started`, `running`, `done`, `cancelled`) is LIVE_V1's event vocabulary, kept alongside. |
| **source** | Who asked for a transition: `web`, `scheduler`, `alexa`; and, set only by room_service itself, `boot`, `resume`, `rollback`. |
| **via** | The destinations of unfinished transitions since the room last completed one (§4.3). |
| **entered** | The modes whose `on_enter` has fired and that no completed transition has left yet (§4.5). |
| **placeholder** | A subsystem named in config that has no working adapter. Its rows are `skipped` and status reports it as `available: false`. |
| **dry run** | An adapter that reads but has its writes turned off. Its rows are `skipped`. |

### 1.3 Changes from the reference

Every change names the scenario that needs it. "Concept?" says whether the change adds something the owner has to learn. Rows marked **R** come from the review pass (§11).

| # | Change | Scenario | Tracer finding | Kind | Concept? |
|---|---|---|---|---|---|
| 1 | Requests are serialised by a lock, and the newest request cancels everything before it | S13 | F2, G8, I-F2 | rule | no |
| 2 | An unfinished transition keeps `mode.target`. A restart resumes toward it, whatever stopped it (crash, SIGTERM, cancel) | S5 | F3 | rule | no |
| 3 | `mode.via`: when choosing the transition rule, an unfinished chain counts as being in every mode it was heading to | S4, S8, S9 | F4 | field | no |
| 4 | An abort rolls back once to the last completed mode | S8, S9 | F4, G7, I-F3 | rule | no |
| 5 | `mode.entered`: signals fire for the modes actually entered and left, at family edges. `on_enter`/`on_exit` on a base mode work. A mode stays entered until a transition that leaves it completes (R18). | S4, S11, S12 | F5, G1, I-F5, F23, R18 | field | no |
| 6 | In-flight marks are written before each level command and kept until arrival is **proven**. A re-targeted command never forgets the one before it, and a reading equal to the pre-move value is not proof (R1). | S4, S5 | F1, I-F1, R1 | field | no |
| 7 | Side effects are at most once: the ledger (in-flight mark, `entered`, `leaving`, `fired`) is saved before each send | S5, S10, S11 | F9 | rule | no |
| 8 | Any exception ends a transition as `failed`. Each task keeps the config it started with, rollback included, so config edits can't wedge it (R9). | S5 | F10, R9 | rule | no |
| 9 | Every adapter call has a timeout | S8, S9 | F11, I-F8 | rule | no |
| 10 | `desired.settled` and `desired.retry`: commands that didn't land are retried with backoff. The retry is dropped only if a reading shows something none of our own commands explains (R2). Edges in an unknown state are retried too (R3). | S9 | F12, R2, R3 | field | no |
| 11 | Slot lifetime: a slot entry lives while the room is in modes that store into that slot or retrieve the entry. After a failed retrieve it is `closed` and stays valid until a reading shows someone moved the target (R6). `max_age_s` applies at retrieve only. | S7, S8 | F13, G4, R6, R17 | rule + field | no |
| 12 | `on_fail: {"abort_on": <selector>}`, with options `rollback` (R4) and `allow_skipped` (R8). A guarded value the room already held doesn't abort (R7). | S1, S8 | F14, G7, R4, R7, R8 | extends an existing key | no |
| 13 | `read()` returns canonical values, and `normalize()` runs on every config value at load. It is pure and offline (R16). | S2 | F15, G5, I-F4, R16 | contract | no |
| 14 | `@name` means a mode and its descendants, so `class` is dropped. `@` names are checked, a bare abstract name in a rule is an error, and an explicit `* → *` rule is required. | S1 | F16, G3, G11, R17 | config | **removes one** |
| 15 | The example config suspends the mask first, powers DMX only after the blinds are down, and stops with the blinds down if DMX power can't be confirmed off (R4) | S1 | F6, F18, G3, I-F10, R4 | config | no |
| 16 | A failed read makes the belief `stale` and marks the subsystem unavailable. A store keeps the belief's own time and confidence. | S8, S9 | F7, G9, I-F7 | field | no |
| 17 | Enforce re-asserts once, then reports `overridden`, and reconcile's applies can be cancelled. A tick that overlaps a request drops what it read (R5). | S6 | F8, G13, I-F15, R5 | rule | no |
| 18 | An edge toggle in an unknown state gets an `unconfirmed` row, "toggle state unknown; not sent", and makes the transition `partial` (R21) | S10 | F17, G6, R21 | rule | no |
| 19 | `request_mode` finalises the record of a task that never ran | S5 | F19 | rule | no |
| 20 | Screens: an entry interrupted before `go` aborts; one interrupted after `go` completes and then exits. Adapter cleanup takes at most 3 s. | S4 | F20, I | contract | no |
| 21 | `source` is recorded on every transition; the API rejects the reserved values | S13 | F21, G8, R20 | field | no |
| 22 | Four merge rules for `extends` (rule 1 reworded, R17) | S3 variant | G2, R17 | rule | no |
| 23 | Selector precedence by specificity; prefix expansion. The per-member map sugar is dropped with the `modes.yaml` fallback (R23). | LIVE_V1 §3.2 migration | G10, I-F4, R23 | rule | no |
| 24 | Placeholders are accepted, their rows are skipped, and status reports `available: false`. A skipped **guarded** target counts as not ok (R8). | S9 | G5, I-F7, R8 | rule | no |
| 25 | Rows use the LIVE_V1 shape plus a stable `id`, and the whole plan is written when the transition starts | UI | G6, I-F6, R19 | API | no |
| 26 | State file version 2, mode 0600, saved only on change | S5 | I-F13 | rule | no |
| 27 | Names: `mirror_ball`; `tv.<screen id>`; signals `subsystem.member.action` | consistency | G11, I-F12 | naming | no |
| 28 | Sonos playback is state in the example (store, set, retrieve), not a pause/play pair. Piano and Off hold it paused as state (R13). | S12 | G12, R13 | config | no |
| 29 | A `dry_run` attribute on adapters | §9 slice (blinds before writes) | I-F11 | contract | no |
| 30 | The owner can tell the room what an unreadable target is doing (aa's `assume`), admin-gated | S10 | aa Q3, R20 | API | no |
| 31 | `Outcome.retry = false` for deliberate refusals (writes not approved, Hue taken over, a screen controller that gave up): never retried, never accepted away | S8, S9, Hue | R12, R15 | contract | no |
| 32 | Adapters whose state lives in room_service's memory persist it, so `read()` stays truthful after a restart | S5, steer f | R11 | contract | no |
| 33 | `ignore` means "the user's after arrival": a re-apply, or a return after an interrupted exit, leaves an `ignore` target alone unless the interrupted chain changed it | S12 | R13, R18 | rule | no |
| 34 | Hand sets of any target go through one path with policy; an enforced target needs `hold` (aa) | S13 | R14, R21 | API | no |
| 35 | Status and events stay compatible with the Room page: `mode.last_transition`, row `id`, `phase`; a resumed transition gets a new id | UI | R19 | API | no |

**Not adopted, because they're heavier than needed (owner steer c):**
- **Per-member `requires`** (screens.north live only if tv.north is on HDMI): deferred. Step order is enough (S8).
- **Aliases for blind labels:** deferred. Groups do the job, and aa's `labels` map names targets for the Room page.
- **`Adapter.matches()`:** canonical reads (#13) do the same job.
- **Never aborting on `unconfirmed`** (G7): rejected. A privacy blind has to be read back down, and #12 keeps the TV out of the abort.
- **A separate `@live → @live` rule:** not needed. The entry rule is safe from any starting point (§2.3), and `via` (#3) chooses it.
- **A `config_hash` replan** (G): not needed, because signal keys don't depend on step positions (§4.5).
- **Review proposals rejected** (reasons in §11): a `hold` value for `on_fail`; direction-specific abort rules; an implicit store when a child sets over a retrieve; a default `max_age_s`; re-applying the mode at every boot; dropping closed slots on give-up.

---

## 2. Owner config in JSON

### 2.1 Files

| File | Holds | In git | Written by |
|---|---|---|---|
| `config/room/modes.json` | labels, groups, modes, transition rules | yes | the owner, with 2f |
| `config/room/room.yaml` | service config (LIVE_V1 §3.1) | yes | 2f |
| `config/room/<subsystem>.json` | device addresses: `rf/blinds.json` (from the spike), `tv.json`, `sonos.json`, `plugs.json`; for a placeholder, optionally its `members` and `values` | yes (no secrets) | the spike, 2f |
| `~/.config/screen-machine/…` | keys and tokens, mode 0600 | no | each service |
| `~/.local/state/screen-machine/room/state.json` | runtime state (§3) | no | room_service only |
| `~/.local/state/screen-machine/room/<adapter>.json` | state an adapter keeps in room_service's memory (the mask's hold, the page policy), and `screens.json` (the controller's own) | no | that adapter (§5.2 rule 10) |

- room_service reloads `modes.json` when it changes. A bad edit keeps the previous config and logs an error.
- **A reload moves nothing** (aa's rule). The current mode's desired values stay; policies may only relax (enforce → respect → ignore); targets the new config no longer manages are dropped from `desired`. Values the new config would change are listed in `status.config.pending_targets` until the next switch or a re-apply (§4.8).
- **A running transition finishes with the config it started with,** rollback included. A reload that arrives during a transition is applied when the transition ends.
- There is no fallback to `modes.yaml`: the slice ships `modes.json` (§9.2).
- Keys that start with `_` are comments and are ignored.

### 2.2 The complete example

This covers the five modes: `normal`, `off`, `piano`, `ambient`, `disco`. It uses groups, two abstract bases, `extends`, all three verbs, signals, and transition rules with delays, ordering and two abort conditions.

```json
{
  "_note": "Room modes. See docs/av/ROOM_STATE_MODEL.md. Keys starting with _ are comments.",
  "default": "normal",

  "groups": {
    "all_blinds": "blinds",
    "privacy_blinds": ["blinds.ch02", "blinds.ch03", "blinds.ch14"],
    "frames": ["screens.north-screen", "screens.south-screen"],
    "frame_tvs": ["tv.north-screen", "tv.south-screen"]
  },

  "modes": {
    "house": {
      "abstract": true,
      "_note": "What every mode wants unless it says otherwise. retrieve = put back what a live mode stored.",
      "state": {
        "screens": "art",
        "sensor_mask": "follow",
        "page": true,
        "mirror_ball": "stopped",
        "plug.dmx_power": "off",
        "privacy_blinds": {"retrieve": "pre_live", "max_age_s": 43200},
        "frame_tvs": {"retrieve": "pre_live"},
        "sonos.lounge": {"retrieve": "pre_live"}
      }
    },

    "normal": {"label": "Normal", "extends": "house"},

    "piano": {
      "label": "Piano", "extends": "house",
      "_note": "Sonos paused on arrival; after that it is the user's (ignore).",
      "state": {"sonos.lounge.playback": {"set": "paused", "policy": "ignore"}}
    },

    "off": {
      "label": "Off", "extends": "house",
      "state": {"frame_tvs": "standby",
                "sonos.lounge.playback": {"set": "paused", "policy": "ignore"}}
    },

    "live": {
      "abstract": true, "extends": "house",
      "_note": "Every live mode: blinds down and TVs on HDMI, each storing its old value first.",
      "state": {
        "sensor_mask": "suspended",
        "page": false,
        "privacy_blinds": {"store": "pre_live", "set": 100, "policy": "enforce"},
        "frame_tvs": {"store": "pre_live", "set": "hdmi"},
        "frames": {"live": {"scene": "fractal-zoom",
                            "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}}}
      },
      "on_enter": [{"signal": "log.info", "args": {"msg": "show starting"}}],
      "on_exit": [{"signal": "log.info", "args": {"msg": "show over"}}]
    },

    "ambient": {"label": "Ambient", "extends": "live"},

    "disco": {
      "label": "Disco", "extends": "live",
      "state": {
        "frames": {"live": {"scene": {"preset": "fractal-zoom", "params": {"zoom_rate": 0.6}},
                            "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}}},
        "plug.dmx_power": "on",
        "mirror_ball": "spinning",
        "sonos.lounge.volume": {"store": "pre_live", "set": 60, "opts": {"over_s": 5}},
        "sonos.lounge.playback": {"store": "pre_live", "set": {"favourite": "Disco"}}
      }
    }
  },

  "transitions": [
    {
      "_note": "Into any live mode, from anywhere, including from another live mode.",
      "from": "*", "to": "@live",
      "steps": [
        ["sensor_mask", "page"],
        {"targets": ["privacy_blinds", "frame_tvs"], "on_fail": {"abort_on": "privacy_blinds"}},
        "plug.dmx_power",
        {"delay": 2},
        "frames",
        "mirror_ball",
        "rest"
      ]
    },
    {
      "_note": "Out of a live mode: ball off, DMX power off (if that can't be confirmed, stop with the blinds still down), screens back to art, then blinds up.",
      "from": "@live", "to": "*",
      "steps": [
        "mirror_ball",
        {"targets": "plug.dmx_power", "on_fail": {"abort_on": "plug.dmx_power", "rollback": false}},
        "frames",
        "rest"
      ]
    },
    {
      "_note": "Everything else.",
      "from": "*", "to": "*",
      "steps": ["rest"]
    }
  ]
}
```

`all_blinds` stands for all 13 blinds (`blinds.ch02` … `blinds.ch14`). No mode uses it yet. The other ten blinds are unmanaged, so they never move, and their own Bloc timers are left alone (aa NOTES §4). Making them part of a live mode is one line (owner question Q9).

The example is written for the full hardware. **Until the DMX plug and the blind writes exist, the shipped `modes.json` adds `"allow_skipped": true` to both guarded steps** (§9.1); status then says "guard not enforced" on every live transition.

### 2.3 Reading the example

| Line | What it does |
|---|---|
| `house` (abstract) | The house values every mode inherits: screens in art, mask following the sensor, the page's timed reloads on, ball stopped, DMX off. Every target a live mode stores is retrieved here. |
| `"privacy_blinds": {"retrieve": "pre_live", "max_age_s": 43200}` | Returning from a live episode puts each privacy blind back at its own stored position. A slot older than 12 h is not used (§10 Q1). The default policy is `ignore`, so in Normal the blinds are the house's business. |
| `"sonos.lounge": {"retrieve": "pre_live"}` | A prefix: it covers `sonos.lounge.volume` and `sonos.lounge.playback`. After disco, the volume and whatever was playing come back. In Normal, Sonos is the user's (policy `ignore`). |
| `normal` | Just `house`. |
| `piano`, `off` | `house`, but Sonos playback is paused on arrival as **state**, so it happens in the same step as everything else, not after the blinds' travel (R13). The policy is `ignore`, so after that Sonos is the user's. The volume is still retrieved. `off` also sets the TVs to standby. |
| `live` (abstract) | What every live mode shares. The mask holds its pre-show value. Timed page reloads are off. The privacy blinds are stored and then lowered, under enforce. The TVs are stored and then switched to HDMI. Both frames go live. Its `log.info` signals fire once on entering and once on leaving the live family, never on a hop. |
| `ambient` | Just `live`. Sonos isn't overridden, so ambient inherits house's retrieve: going disco → ambient brings back the pre-disco music and volume. |
| `disco` | `live` plus a faster zoom, DMX power on, the ball spinning, and Sonos volume and playback stored and then set. |
| Rule 1, `* → @live` | The entry order. The mask is suspended first, before anything darkens the room. Blinds and TVs move in parallel, and the step waits for the blinds to read back down; if a privacy blind doesn't arrive, the transition aborts and rolls back. DMX power comes on only after that, then a 2 s boot delay, then the screens go live, then the ball, then the rest (Sonos). |
| Rule 2, `@live → *` | The exit order. The ball stops, then DMX power goes off. **If DMX power can't be confirmed off, the transition stops there** (`rollback: false`): the blinds stay down, status says why, and the Room page offers Retry and Back. Otherwise the screens cut back to art (a matched cut), then the rest in parallel: blinds and TVs retrieved, mask back to follow, reloads on, Sonos retrieved. |
| Rule 3, `* → *` | Everything else (normal ↔ piano ↔ off) in one parallel step. |

**The safety principle for writing rules.** A step only costs time when one of its targets needs changing. That makes it easy to write rules that are safe from any starting point:
- every rule whose `to` is live starts with the privacy step, guarded with `abort_on`;
- every rule whose `from` is live stops the lights, guarded with `abort_on` and `rollback: false`, before anything raises the blinds.

Rule 1 is used for live → live hops too. In a real hop the privacy step has nothing to do, so it costs nothing, and a privacy failure there can't abort, because the room already held those values (§4.7). After an interrupted entry, though, it lowers the blinds before anything lights up (S4, §4.3). A disco → ambient hop waits the 2 s delay because DMX power changes. Add a separate `@live → @live` rule only if that matters, and start it with the privacy step.

### 2.4 Grammar

**Top-level keys:** `default`, `labels` (target → display name for the Room page; aa), `groups`, `modes`, `transitions`, `description`. Mode keys: `label`, `extends`, `abstract`, `state`, `on_enter`, `on_exit`, `description`.

**Selectors** (in `state` keys, `groups` values and steps):

| Form | Means | Specificity |
|---|---|---|
| `"blinds.ch02"` | one target | 3 (highest) |
| `"privacy_blinds"` | a group from `groups` | 2 |
| `"blinds"`, `"sonos.lounge"` | a prefix, at a dot boundary: every target under it | 1 |
| `"page.*.timed_reload"` | a wildcard (fnmatch); `*` can cross dots | 1 |
| `["a", "b"]` | a list: each item on its own terms | per item |

- **Within one mode,** the more specific selector wins for a target. If two selectors of equal specificity name the same target with different entries, that is a ConfigError. JSON key order never matters.
- **Across `extends`,** the child's entry is merged over the parent's for each target (merge rules below).
- Group names can't contain `.`, shadow a subsystem, be integer-like (aa: browsers reorder them), or be `rest` or `*`.
- A selector that matches no target is a ConfigError, unless its subsystem is a placeholder (§5.3).

**Entries:**

| Entry | Meaning |
|---|---|
| `v` (any JSON value without verb keys) | the same as `{"set": v}` |
| `{"set": v}` | hold `v`. Write it this way when `v` is an object with a key called `set`, `store` or `retrieve`. |
| `{"store": slot, "set": v}` | save the current value in `slot` if the slot is empty (or closed and the target hasn't been moved since, §4.6), then hold `v` |
| `{"store": slot}` | save only; change nothing |
| `{"retrieve": slot, "else": v}` | put back the saved value and clear the slot. If nothing usable is stored, hold `v`. `else` defaults to `"leave"`: send nothing. |
| `null` | unmanaged: drop an inherited entry |
| options on any entry | `"policy": "respect" \| "enforce" \| "ignore"`; `"opts": {…}` (adapter hints) |
| option on any entry: `skip_if` ("Leave it as it is while") | `{"<target or selector>": [value patterns], …}`. When the mode is entered, or a step would send the row, and any named target's believed value matches one of its patterns, the row is left out of that entry: not stored, not set, a skipped row saying "left alone: <target> is <value>". An enforce re-assert or a retry waits while it holds. An unknown or unreadable value never matches. Patterns as in `when`: equal, `"*"`, an object key, a string with `*` `?` `[` (fnmatch), or an object whose every key's pattern matches (`{"uri": "http://*/piano/*"}`). |
| option on `retrieve` only | `"max_age_s": n`. A stored value whose `at` is older than this is ignored, and `else` applies. |

`retrieve` can't be combined with `set` or `store`.

**Merging through `extends`.** For each target, the child's entry is applied over the parent's:
1. A plain value or `{"set": v}` replaces the value, and any option the child names. Over an inherited set or store entry, everything the child doesn't name (store, policy, opts) is inherited. Over an inherited `retrieve`, it becomes a set entry: the retrieve's options are dropped, and the policy is the child's or the set default (`respect`).
2. An object containing `store` or `retrieve` replaces the inherited entry completely.
3. An object with only options (`policy`, `opts`, `else`, `max_age_s`) patches the inherited entry.
4. `null` drops the entry.

Rule 1 over a retrieve means the child no longer restores that target. **Inside a live family that is usually a mistake** (a `karaoke` that writes `"sonos.lounge.volume": 70` doesn't store the volume first, so it can't come back), and the loader warns about it (§2.5). Write `{"store": "pre_live", "set": 70}`, as disco does.

`on_enter` and `on_exit` are not merged. Each mode's own signals fire at its own family edge (§4.5), so a base mode's signals are shared by every child without being copied.

**Steps** (inside a transition rule):

| Step | Meaning |
|---|---|
| `"frames"` (a selector) | one step: send every pending target it names, wait for all of them |
| `["sensor_mask", "page"]` | the same, for several selectors in parallel |
| `{"targets": sel, "on_fail": …}` | as above, with an abort condition |
| `{"delay": s}` | wait `s` seconds, but only if the step before it sent something |
| `{"signal": "sonos.lounge.pause", "args": {…}}` | fire a one-shot signal at this point |
| `"rest"` | every pending target not named by any other step of this rule. It is added at the end if a rule leaves it out. |

**`on_fail`:**
- `"continue"` (the default): report and go on.
- `"abort"`: the same as `{"abort_on": <every target of the step>}`.
- `{"abort_on": sel}`: abort if a guarded target isn't `ok`. Options:
  - `"rollback": true` (the default) rolls back to the last completed mode (§4.7). `false` stops part-way: the room stays as it is, status says `failed`, and the owner chooses Retry or Back (Q10, Q12).
  - `"allow_skipped": false` (the default): a guarded target that is skipped (placeholder, dry run) counts as not ok. `true` lets the transition go on, and it ends `partial` with "guard not enforced". Use it only while hardware is missing.

"Isn't `ok`" means `failed`, `unconfirmed` (including a toggle in an unknown state), or `skipped` without `allow_skipped`. A privacy blind that sent but never proved its arrival at 100 aborts. **A guarded target doesn't abort when the room already held that value:** the last completed mode and every mode in `via` set it to the same value (a hop, a re-apply). Its failure is then reported and retried (§4.7).

**Transition rules:** `{"from": sel, "to": sel, "steps": […]}`.
- `from` and `to` take a concrete mode name, `@name` (that mode and its descendants) or `*`. Each can be a list of these. A bare abstract name (`"live"`) is a ConfigError: "did you mean `@live`?".
- `*` in `from` also matches "no mode yet" (first boot).
- **The first matching rule wins,** so put specific rules first and the `* → *` catch-all last.

**Signals:** `"<subsystem>.<action>"` or `"<subsystem>.<member>.<action>"`. The first dot ends the subsystem name and the last dot starts the action; anything in between is passed to the adapter as the member (a Sonos zone, say). `args` is optional.

### 2.5 Checked at load

A file that fails any of these checks isn't loaded; the previous config stays in force. With no previous config (first boot), room_service starts with no modes, status reports the error, and `PUT /mode` returns 404.
- JSON syntax; unknown keys at the top level, per mode and per rule (other than `_…`), reported with a JSON pointer (aa's `validate`).
- `extends` cycles; `extends` naming an unknown mode; the default mode missing or abstract; integer-like mode, group and selector names.
- Unknown `@name` or mode names in transition rules; a bare abstract name. **A `* → *` rule must exist,** so the fallback order is always written down.
- Every selector resolves. Selector conflicts are reported as described in §2.4.
- Every `set` and `else` value is passed through its adapter's `normalize()`, which is pure and offline (§5.2), so a typo such as `"spining"` fails at load. A placeholder checks values against the `values` in its device config when it has them.
- Verb combinations, policies, `on_fail` options, and `max_age_s` (allowed on `retrieve` only).
- **Every (slot, target) that some mode retrieves is stored by some mode,** which catches slot-name typos.
- The old v1 keys `sequence` and `after` are rejected, with a message pointing to `transitions`.

Warnings, which don't stop the load (aa's `lint()` codes where they exist):
- `slot_never_retrieved`, `slot_never_stored`, `rule_unused` (a rule that an earlier rule always shadows);
- a concrete (from, to) pair that falls to the catch-all while one side is in a family that has its own rules;
- `set_over_retrieve`: a mode whose family stores into slot S sets a target that a base retrieves from S, without storing it (the karaoke mistake, §2.4);
- `guard_inactive`: an `abort_on` selector whose targets are placeholders or dry run;
- `placeholder_values_unchecked`: a placeholder with no `values` in its device config.

`GET /api/v1/modes` shows the resolved steps for every pair (§7).

### 2.6 Adding a mode

- A new live mode is one line, and it gets the blinds, the TVs and the safe order for free: `"karaoke": {"label": "Karaoke", "extends": "live"}`.
- A live mode that changes something house restores stores it first: `"karaoke": {"extends": "live", "state": {"sonos.lounge.volume": {"store": "pre_live", "set": 70}}}`.
- A variant: `"disco-photosafe": {"label": "Disco (photosafe)", "extends": "disco", "state": {"mirror_ball": "stopped", "plug.dmx_power": "off"}}`.
- Lowering one more blind in a single mode: `"dim": {"extends": "live", "state": {"blinds.ch05": {"store": "pre_live", "set": 100}}}`. Then add `"blinds.ch05": {"retrieve": "pre_live"}` to `house`.

---

## 3. Runtime model and the persisted state file

### 3.1 What the coordinator holds

| Part | Meaning |
|---|---|
| `mode` | The last completed mode (`current`), the mode it is heading for (`target`), `via`, `entered`, `leaving`, and when the current mode was reached (`since`) |
| `transition` | The unfinished transition (running or cancelled), or `null` |
| `last_transition` | The most recent finished, failed or cancelled transition, kept for status |
| `believed` | One belief per target the coordinator has read or sent |
| `desired` | What the current (or target) mode wants, per target |
| `stored` | Slots: per slot, per target, the saved value |
| `drift` | Settled targets that read back different from desired |
| `config_pending` | Targets a reload would change at the next switch or re-apply (aa) |
| `log` | The last 200 edits and corrections, with their cause (aa) |

In memory only: the set of targets an adapter currently reports as `moving`; subsystem health; the tasks, locks and the request generation (§4.1).

### 3.2 The file

`~/.local/state/screen-machine/room/state.json`. This is a snapshot taken at about 30 s into S1:

```json
{
  "version": 2,
  "saved_at": 1760400131.2,
  "mode": {
    "current": "normal",
    "target": "disco",
    "via": [],
    "entered": ["house", "normal"],
    "leaving": ["normal"],
    "since": 1760390000.0
  },
  "transition": {
    "id": "t-3f9a12c4",
    "resumes": null,
    "from": "normal",
    "via": [],
    "to": "disco",
    "source": "web",
    "status": "running",
    "phase": "running",
    "reapply": false,
    "started_at": 1760400100.0,
    "detail": "",
    "fired": [],
    "steps": [
      {"id": "i0", "subsystem": "sensor_mask.main", "action": "set suspended", "status": "ok", "detail": "",
       "step": 1, "kind": "set", "value": "suspended"},
      {"id": "i4", "subsystem": "blinds.ch02", "action": "store pre_live, set 100", "status": "running", "detail": "",
       "step": 2, "kind": "set", "value": 100, "stored": 0, "guarded": true},
      {"id": "i7", "subsystem": "plug.dmx_power", "action": "set on", "status": "pending", "detail": "",
       "step": 3, "kind": "set", "value": "on"},
      {"id": "i8", "subsystem": "delay", "action": "wait 2 s", "status": "pending", "detail": "",
       "step": 4, "kind": "delay"}
    ]
  },
  "last_transition": null,
  "believed": {
    "blinds.ch02": {"value": 0, "confidence": "confirmed", "at": 1760400099.0, "sent_at": 1760400101.0,
                    "inflight": {"value": 100, "from": 0, "earlier": [], "earlier_until": 0,
                                 "until": 1760400176.0}},
    "tv.south-screen": {"value": "art_mode", "confidence": "confirmed", "at": 1760400099.6, "sent_at": 1760400101.0,
                        "inflight": {"value": "hdmi", "from": "art_mode", "earlier": [], "earlier_until": 0,
                                     "until": 1760400191.0}},
    "mirror_ball.main": {"value": "stopped", "confidence": "unconfirmed", "at": 1760390010.0, "sent_at": 1760390010.0}
  },
  "desired": {
    "blinds.ch02": {"value": 100, "policy": "enforce", "slot": null, "settled": false, "retry": null, "reasserted": false},
    "sonos.lounge.volume": {"value": 60, "policy": "respect", "slot": null, "settled": false, "retry": null, "reasserted": false}
  },
  "stored": {
    "pre_live": {
      "blinds.ch02": {"value": 0, "at": 1760400099.0, "confidence": "confirmed",
                      "from_mode": "normal", "stored_at": 1760400100.3, "closed": false},
      "tv.south-screen": {"value": "art_mode", "at": 1760400099.6, "confidence": "confirmed",
                          "from_mode": "normal", "stored_at": 1760400100.3, "closed": false}
    }
  },
  "drift": {},
  "config_pending": [],
  "log": []
}
```

(Only a few targets are shown.)

### 3.3 Field reference

| Field | Type | Notes |
|---|---|---|
| `version` | int | 2. An unknown version is moved aside (§3.5). |
| `mode.current` | mode name or `null` | The last mode a transition **completed** (`ok` or `partial`). `null` before the first boot. |
| `mode.target` | mode name or `null` | Not `null` exactly while a transition is unfinished: running, cancelled, crashed, or failed and not rolled back. |
| `mode.via` | list of mode names | The destinations of unfinished transitions since `current` was reached. Cleared on completion. |
| `mode.entered` | list, outermost first | Modes whose `on_enter` has fired and that no completed transition has left. |
| `mode.leaving` | list | Modes in `entered` whose `on_exit` has already fired in an unfinished transition. They aren't fired again; cleared on completion (§4.5). |
| `mode.since` | epoch s | When `current` was reached. |
| `transition.id` | string | New for every transition, including a resumed one |
| `transition.resumes` | id or `null` | The cancelled or crashed transition this one carries on (same destination, `fired` carried) |
| `transition.from` | mode name or `null` | `mode.current` when the transition started |
| `transition.via` | list | A copy of `mode.via` at the start, used to choose the rule |
| `transition.source` | string | `web`, `scheduler`, `alexa`, `boot`, `resume`, `rollback`, … |
| `transition.status` / `.phase` | strings | The outcome (§1.2), and LIVE_V1's phase: `started`, `running`, `done`, `cancelled` |
| `transition.reapply` | bool | A request for the current mode with nothing unfinished (§4.2) |
| `transition.rollback_of` | `{id, to, detail}` or absent | On a rollback: the transition that aborted, so Retry and the failure text survive (§4.7) |
| `transition.fired` | list of keys | Rule signals already fired: `step:<action>:<n>`, the n-th occurrence of that action in the plan |
| `transition.steps` | rows | §7.2. Written in full, as `pending`, at the start. |
| `believed[t].value` | JSON or `null` | For a level target: the last reading. A command never writes it; only a proven arrival or a reading does. |
| `believed[t].confidence` | `confirmed` \| `unconfirmed` \| `stale` \| `unknown` | |
| `believed[t].at` | epoch s | When this value was last **confirmed** (read back, or sent for edges). A failed read keeps the old `at`. |
| `believed[t].sent_at` | epoch s or absent | When the coordinator last commanded this target (any kind). Used by the `ignore` rule (§4.4). |
| `believed[t].inflight` | object or absent | Level targets only, from the command until proof of arrival or `until` (§4.4): `value` (the latest commanded value); `from` (the reading before the first unresolved command); `earlier` (other values commanded since then, oldest first); `earlier_until` (by when every earlier command must have finished); `until` (latest send + `change_time_s`). |
| `desired[t].policy` | `respect` \| `enforce` \| `ignore` | |
| `desired[t].slot` | slot name or `null` | Present when the value came from a `retrieve`, so a later retry can clear the slot |
| `desired[t].settled` | bool | §4.8 |
| `desired[t].retry` | object or `null` | `{attempts, next_at, explained, gave_up?, final?, why?}`. `explained`: the readings our own commands account for (the pre-move reading, the values commanded). `final`: the device side refused on purpose (`Outcome.retry = false`), so it is never retried or accepted away. |
| `desired[t].reasserted` | bool | Enforce has used its one re-assert (or a hand set with `hold` took it) |
| `stored[slot][t].value` / `.at` / `.confidence` | | Copied from the belief, so `at` is when that value was last confirmed |
| `stored[slot][t].from_mode` | mode name | `mode.current` at the time of the store |
| `stored[slot][t].stored_at` / `.edited_at` | epoch s | `edited_at` after an owner correction (aa); the correction keeps `at`, so the expiry doesn't move |
| `stored[slot][t].closed` | bool | The episode is over but the retrieve didn't land. The value is kept, with `explained` (copied from the retry) and `final`, while the target still reads within `explained` (§4.6). |
| `stored[slot][t].orphan` | bool | No mode stores or retrieves this (slot, target) any more (a renamed slot, a removed blind). Kept and shown, never auto-deleted (§4.6). |
| `drift[t]` | `{observed, desired, at, reasserted?, overridden?, manual?, source?}` | |
| `config_pending` | list of targets | §2.1 |

### 3.4 Write rules

- **Location:** the XDG state directory, outside the repo. The checkout is shared by several sessions, is never stashed or reset, and gets redeployed. Runtime files in the tree end up dirty or committed (`routes/scheduler/_recent.json` already shows the problem). A checkout must never roll back stored blind positions. LIVE_V1 §0, `room_service/config.py` and aa's `state.py` already agree on this directory.
- **Atomic:** use `room_service.util.atomic_write_json`: a temporary file created in the same directory, fsync, `os.replace`. The file mode is 0600 and the directory 0700.
- **When:** save only on change. Reconcile ticks that change nothing don't write.
- **Order guarantees** (these make S4, S5, S10 and S11 safe):
  1. Stored slots are saved **before the first command** of a transition.
  2. Before each command, the target is marked on disk: level targets get (or extend) an in-flight mark; edge targets become `unknown`, keeping their value. Then the command is sent. **An unresolved mark is extended, never replaced** (§4.4).
  3. Before each signal, its ledger is saved (`leaving` for on_exit, `entered` for on_enter, `fired` for rule signals). Then the signal is sent. Signals are therefore **at most once**.
  4. After each outcome: the belief, `desired.settled` or `desired.retry`, any slot cleared, and the row status are saved together.
  5. The transition record is saved at the start (with the full plan), at every row change, and at the end.

### 3.5 Versioning

- `version` is 2. aa's reference wrote 1, and room_service v1 writes `coordinator.json`.
- On load, a file that is missing gives an empty state, so the coordinator boots into the default mode.
- A file with an unknown version, or one that won't parse, is renamed to `state.json.bad-<epoch>`. The coordinator starts empty, logs an error, and stores and retrieves nothing from it.
- v1's `coordinator.json` is not read. The first boot enters `normal`, which only sets what is already true today (screens in art, mask following).

---

## 4. Planner and executor

The pseudo-code is Python-shaped and complete enough to implement from. Names match aa's `coordinator.py` where they can. Helpers are defined in §4.12.

### 4.1 Tasks and locks

| Name | Purpose |
|---|---|
| `_req` (asyncio.Lock) | Serialises `request_mode`, `hand_set` and the owner corrections (§4.11). A second request waits for the first to finish *starting*, not for its transition to finish. |
| `_work` (asyncio.Lock) | One adapter-driving activity at a time: a transition, a reconcile apply, or a hand set. |
| `_task` | The transition task, which runs a transition and possibly its rollback. |
| `_rtasks` (set) | Reconcile applies and hand sets in flight. `request_mode` cancels every one. |
| `_gen` (int) | Bumped first by `request_mode` and `hand_set`. A reconcile tick that read under an older value drops what it read (§4.8). |
| `_moving` | In memory: targets whose last reading said `moving`. |

Timeouts:
- an apply waits at most `change_time_s + 15 s`; a proof of arrival (§4.4) waits at most until the mark's `until + 15 s`;
- a read waits at most the adapter's `read_timeout_s` (default 5 s);
- a signal waits at most 5 s;
- an adapter's cleanup after a cancel takes at most 3 s. `_stop_tasks` waits `asyncio.wait(tasks, timeout=3)`, logs any overrun as a contract breach, and goes on; whoever needs `_work` next waits for the overrunning task to let go.

Retry backoff: 30 s, 1 min, 2 min, 5 min, 10 min, then the retry gives up. When a subsystem comes back (health goes from unavailable to available), its targets that gave up start the backoff again (never the `final` ones).

### 4.2 Requesting a mode

```python
RESERVED_SOURCES = {"boot", "resume", "rollback"}       # set only by room_service; the API answers 400 bad_source

async def request_mode(self, name: str, source: str = "web") -> str:
    self.cfg.mode(name)                         # unknown or abstract -> ConfigError -> 404 unknown_mode
    self._gen += 1                              # a reconcile tick reading right now drops its result (§4.8)
    async with self._req:                       # F2: one request at a time
        await self._stop_tasks()                # cancel _task and every _rtask; <= 3 s each
        prev = self.s["transition"]
        if prev and prev["status"] == "running":            # F19: cancelled before its handler ran,
            prev["status"] = prev["phase"] = "cancelled"    # or a crash left it 'running'
            self._mark_rows(prev, ("pending", "running"), "cancelled")
            self._emit("cancelled", prev)
        m = self.s["mode"]
        reapply = m["target"] is None and name == m["current"]
        if m["target"] and m["target"] not in m["via"]:
            m["via"].append(m["target"])                    # F4: the room may be partly there
        carry = bool(prev) and prev["to"] == name           # same destination: carry on
        resuming = carry and source == "resume"
        tr = {"id": new_id(), "resumes": prev["id"] if carry else None,
              "from": m["current"], "via": list(m["via"]), "to": name,
              "source": prev["source"] if resuming else source,
              "status": "running", "phase": "running", "reapply": reapply,
              "started_at": now(), "detail": "",
              "fired": list(prev["fired"]) if carry else [], "steps": []}
        if prev:
            self.s["last_transition"] = prev
        m["target"] = name
        self.s["transition"] = tr
        self._save()
        self._task = asyncio.create_task(self._run(tr, self.cfg))   # the task keeps this config, rollback included
        return tr["id"]
```

- **The last request wins.** Every request cancels whatever ran before it, under the lock.
- `PUT /mode` returns `202 {transition_id, reapply}` once the new task is created (aa). It waits only for the old task's bounded cleanup.
- **A resumed or carried-on transition gets a new id** with `resumes` naming the old one, so the Room page never sees a `started` event for an id it has already marked `cancelled` (R19).
- **A request for the current mode, with nothing unfinished,** is a re-apply. It fires no signals (neither mode signals nor rule signals), and stores nothing new. It sends only targets that need it, resets enforce's re-assert and retries, and re-lowers an overridden blind. It leaves `ignore` targets alone unless the coordinator itself left them unfinished (§4.4). This is the Room page's "re-apply" button (S6). A re-apply can't abort on a guarded value, because the room already held it (§4.7).

### 4.3 Running a transition

```python
async def _run(self, tr, cfg):
    while tr is not None:                       # a transition, then possibly its rollback, in one task and one config
        tr = await self._run_one(tr, cfg)

async def _run_one(self, tr, cfg) -> dict | None:
    try:
        to = cfg.mode(tr["to"])                         # inside the try: a vanished mode fails the record cleanly
        async with self._work:
            # 1. Read every readable target the destination names, plus anything still in flight.
            await self._refresh(self._level(to.state) | self._inflight())
            # 2. Store, for slots that are empty, or closed and still valid (§4.6). Saved before any command.
            await self._store(to, tr)
            # 3. What the destination wants, and what must be sent.
            desired, slots = self._desired(to)          # slots: target -> slot, for retrieved values
            self.s["desired"] = {t: {"value": v, "policy": to.state[t].policy, "slot": slots.get(t),
                                     "settled": False, "retry": None, "reasserted": False}
                                 for t, v in desired.items()}
            self.s["drift"] = {}
            pending, unsent = {}, {}
            for t, v in desired.items():
                why = self._send_reason(t, v, to, tr)   # "send" | None | (row status, detail)
                if why == "send":
                    pending[t] = v
                elif why is None:                       # already there, or the user's (ignore): settled
                    self._settle(t)                     # a retrieve that needs nothing is done: slot cleared
                else:
                    unsent[t] = why                     # ("skipped", "dry run") or ("unconfirmed", "toggle state unknown; not sent")
            # 4. Plan: which signals fire, which rule applies, and every row as 'pending'.
            exits, enters = self._edges(to)             # §4.5
            steps = cfg.plan_steps({tr["from"], *tr["via"]}, to.name)
            tr["steps"] = self._rows(exits, steps, enters, pending, unsent, slots, tr, cfg)
            self._save(); self._emit("started", tr, plan=tr["steps"])
            # 5. Exit signals of the modes the room is leaving (innermost first). Each fires once:
            #    `leaving` records it, and the mode stays in `entered` until this transition completes.
            for name in exits:
                if name in self.s["mode"]["leaving"]:
                    continue
                self.s["mode"]["leaving"].append(name); self._save()
                for sig in mode_signals(cfg, name, "on_exit"):          # [] for a mode cfg no longer has
                    await self._fire(tr, sig, ledger=None)
            # 6. The steps, in order.
            worked = False
            for step in steps:
                if step.kind == "delay":
                    if worked:
                        self._row(tr, step.row, "running"); await asyncio.sleep(step.seconds)
                        self._row(tr, step.row, "ok")
                    else:
                        self._row(tr, step.row, "skipped", "nothing changed before it")
                    continue
                if step.kind == "signal":
                    if tr["reapply"]:                   # a re-apply is state only (§4.5)
                        self._row(tr, step.row, "skipped", "re-apply"); continue
                    await self._fire(tr, step, ledger="step"); worked = True
                    continue
                names = step.targets if step.kind == "targets" else self._rest(steps, pending, unsent)
                batch = {t: pending.pop(t) for t in names if t in pending}
                worked = bool(batch)
                out = await self._apply(tr, batch, slots) if batch else {}
                guarded = [t for t in names if t in desired and t in step.abort_on
                           and not self._held_already(t, desired[t], tr, cfg)]          # §4.7
                bad = [t for t in guarded
                       if (t in out and out[t].status != "ok")
                       or (t in unsent and (unsent[t][0] != "skipped" or not step.allow_skipped))]
                if bad:                                 # F14: only guarded targets can abort
                    tr["status"] = "failed"
                    tr["detail"] = f"stopped at step {step.n}: " + ", ".join(bad) + " not ok"
                    self._mark_rows(tr, ("pending",), "cancelled", "aborted: an earlier step failed")
                    self._finish(tr, completed=False)
                    return self._rollback(tr, step, cfg)    # §4.7; None when the step says rollback: false
            # 7. Enter signals of the modes the room is entering (outermost first).
            for name in enters:
                self.s["mode"]["entered"].append(name); self._save()
                for sig in mode_signals(cfg, name, "on_enter"):
                    await self._fire(tr, sig, ledger=None)
            # 8. Done.
            m = self.s["mode"]
            m["entered"] = [x for x in m["entered"] if x in to.family]       # the left modes go only now
            m["leaving"] = []
            tr["status"] = "partial" if self._any_not_ok(tr) else "ok"
            m.update(current=to.name, target=None, via=[], since=now())
            self._close_slots(to, cfg)                  # §4.6
            self._finish(tr, completed=True)
            return None
    except asyncio.CancelledError:
        tr["status"] = tr["phase"] = "cancelled"        # F3: target and via are kept; start() resumes
        self._mark_rows(tr, ("pending", "running"), "cancelled")
        self._save(); self._emit("cancelled", tr)
        raise
    except Exception as e:                              # F10: never leave a 'running' record behind
        tr["status"], tr["detail"] = "failed", describe(e)
        self._mark_rows(tr, ("pending", "running"), "cancelled", "error")
        self._finish(tr, completed=False); self._log("error", f"{tr['id']}: {describe(e)}")
        return None
```

**Choosing the rule (`via`, F4).** `plan_steps(from_set, to)` returns the steps of the first rule whose `to` matches `to` and whose `from` matches **any** mode in `{from} ∪ via`.
- An unfinished chain leaves the room somewhere between where it was and where it was heading, so it counts as being in all of those modes.
- With the example's rules: any request *into* a live mode gets the entry order. Any request out of a room that was in, or heading for, a live mode gets the exit order, so the ball and DMX stop before the blinds rise.

`_rest(steps, pending, unsent)` returns the pending and unsent targets not named by any targets step of the rule. (aa's code takes everything still pending; this is a small change.)

`_finish(tr, completed)` stamps `finished_at`, sets `phase` to `done` (unless `cancelled`), and moves `tr` to `last_transition`, setting `transition` to `null`. It clears `mode.target` only when `completed` is true, then saves and emits `done`. A cancelled record is the one exception: it stays in `transition` until the next request replaces it, so that request can carry on from it.

### 4.4 Store, desired values, send decisions, apply, refresh

```python
async def _store(self, to, tr):
    for t, e in to.state.items():
        if not e.store:
            continue
        slot = self.s["stored"].setdefault(e.store, {})
        have = slot.get(t)
        if have and not have["closed"]:
            continue                                    # keep the original (live -> live, interrupted, re-entry)
        b = await self._settled_belief(t)               # waits <= change_time_s while moving or in flight
        if have and not self._moved_since(have, b):     # closed, and nobody has moved it since the failed retrieve:
            have["closed"] = False                      # the original still stands, so the episode reopens (R6)
            continue
        if b is None or b["confidence"] == "unknown":
            slot.pop(t, None)                           # nothing usable: the retrieve will use `else`
            self._note(tr, t, "nothing stored: value unknown")
            continue
        slot[t] = {"value": b["value"], "at": b["at"], "confidence": b["confidence"],   # F7/G9
                   "from_mode": tr["from"], "stored_at": now(), "closed": False}
    self._save()

def _desired(self, to, mutate=True):                    # plan() and reload call it with mutate=False
    desired, slots = {}, {}
    for t, e in to.state.items():
        if e.retrieve:
            entries = self.s["stored"].get(e.retrieve, {})
            have = entries.get(t)
            if have and e.max_age_s is not None and now() - have["at"] > e.max_age_s:
                have = None                             # too old: use `else`; dropped at completion
            elif have and have["closed"] and not have.get("final") \
                    and self._moved_since(have, self.s["believed"].get(t)):
                if mutate:
                    entries.pop(t)                      # moved by hand since the failed retrieve: accept it
                have = None
            if have:
                desired[t], slots[t] = have["value"], e.retrieve
            elif e.else_ != "leave":
                desired[t] = e.else_
        elif e.sets:
            desired[t] = e.set
    return desired, slots

def _send_reason(self, t, v, to, tr):
    a, b, e = self._adapter(t), self.s["believed"].get(t), to.state[t]
    if a is None or a.placeholder:
        return ("skipped", "no adapter")
    m = self.s["mode"]
    if (e.policy == "ignore" and tr["to"] == m["current"] and not self._closed(t, e)
            and (b is None or b.get("sent_at", 0) <= m["since"])):
        return None                                     # R13/R18: a re-apply or a return; nobody here touched it
    if a.kind == "edge_toggle":                         # one code flips it: never send blind (S10)
        if not b or b["confidence"] == "unknown":
            return ("unconfirmed", "toggle state unknown; not sent")
        if same(b["value"], v, a):
            return None
        return ("skipped", "dry run") if a.dry_run else "send"
    if b and not b.get("inflight") and t not in self._moving and same(b["value"], v, a):
        if a.kind == "level" and b["confidence"] == "confirmed":
            return None                                 # already there
        if a.kind == "edge_discrete" and b["confidence"] == "unconfirmed" and not a.resend:
            return None                                 # sent before, trusted as saved
    return ("skipped", "dry run") if a.dry_run else "send"
```

**The `ignore` rule (R13, R18).** An `ignore` target is applied when the room arrives in its mode; after that it is the user's. So a request for the mode the room last completed (a re-apply, or a return after an interrupted exit) leaves it alone, unless the coordinator commanded it since the room arrived (`sent_at > mode.since`: the interrupted chain changed it, so it is put back) or it has a closed retrieve (the coordinator's own unfinished job). Example: in Piano the user plays Sonos tracks; Piano → Disco is changed back to Piano 3 s later, before Disco's rest step reached Sonos; the tracks keep playing.

```python
async def _apply(self, tr, batch, slots) -> dict[str, Outcome]:
    outs = await asyncio.gather(*(self._send(sub, vals, tr) for sub, vals in group_by_subsystem(batch).items()))
    out = {t: o for d in outs for t, o in d.items()}    # gather re-raises a cancel even if an adapter swallows it
    self._after_send(tr, out, slots)
    return out

async def _send(self, sub, vals, tr=None) -> dict[str, Outcome]:
    """Mark, send, prove. Updates beliefs only; the caller decides what an outcome means for desired and slots."""
    a = self.adapters[sub]
    pending = {}
    for m, v in vals.items():                           # F1/F9: mark on disk BEFORE sending
        t = f"{sub}.{m}"
        b = self._belief(t)
        b["sent_at"] = now()
        if a.kind == "level":
            fl = b.get("inflight")
            if fl is None:
                b["inflight"] = {"value": v, "from": b["value"], "earlier": [], "earlier_until": 0,
                                 "until": now() + a.change_time_s}
            else:                                       # R1: re-targeting an unresolved command; never forget it
                if not same(fl["value"], v, a):
                    fl["earlier"].append(fl["value"])
                    fl["earlier_until"] = max(fl["earlier_until"], fl["until"])
                fl.update(value=v, until=now() + a.change_time_s)
                pending[m] = {k: fl[k] for k in ("from", "earlier", "earlier_until")}
        else:
            b["confidence"] = "unknown"                 # the value is kept; a crash now leaves it unknown
        if tr:
            self._row(tr, t, "running")
    self._save()
    opts = merged_opts(sub, vals) | ({"pending": pending} if pending else {})   # a hint (§5.2 rule 3)
    try:
        out = await asyncio.wait_for(a.apply(vals, opts), a.change_time_s + 15)
    except asyncio.TimeoutError:                        # F11
        out = Outcome("unconfirmed", "timeout")
    except asyncio.CancelledError:
        self._save()                                    # marks stay: the device may still be moving
        raise
    except Exception as e:                              # a dead transport or an adapter bug
        out = Outcome("failed", describe(e)); self._health(sub, ok=False, error=describe(e))
    res = {}
    for m, v in vals.items():
        t, mo = f"{sub}.{m}", out.members.get(m, out)
        b = self._belief(t)
        if a.kind == "level":
            if mo.status == "ok" and not await self._prove(t):     # R1: the adapter may have read a stale cache
                mo = Outcome("unconfirmed", "arrival not proven", retry=mo.retry)
            if mo.status == "ok":
                self._believe(t, v, "confirmed"); b.pop("inflight", None)
            # unconfirmed or failed (R2): the belief keeps the last reading; the mark stays until proof or `until`
        elif mo.status == "ok":
            self._believe(t, v, "unconfirmed")          # an edge that was sent
        # an edge that failed or timed out stays `unknown` with its old value (R3)
        res[t] = mo
    self._save()
    return res

def _after_send(self, tr, out, slots, reasserts=()):
    for t, mo in out.items():
        d = self.s["desired"].get(t)
        if d is not None and mo.status == "ok":
            d["settled"], d["retry"] = True, None
            if t in slots:
                self.s["stored"][slots[t]].pop(t, None) # a retrieve that landed clears its slot
        elif d is not None and t in reasserts:          # enforce's one re-assert didn't land: report, never retry
            self.s["drift"].setdefault(t, {"observed": self._belief(t)["value"], "desired": d["value"],
                                           "at": now()})["overridden"] = True
        elif d is not None:
            r = d["retry"] or {"attempts": 0, "next_at": now() + BACKOFF[0]}
            r.update(explained=self._explained(t, d["value"]), final=not mo.retry, why=mo.detail)
            d["settled"], d["retry"] = False, r
        if tr:
            self._row(tr, t, mo.status, mo.detail)
    self._save()

async def _refresh(self, targets):
    by_sub = group_level_targets_by_subsystem(targets)
    results = await asyncio.gather(*(asyncio.wait_for(self.adapters[s].read(ms), self.adapters[s].read_timeout_s)
                                     for s, ms in by_sub.items()), return_exceptions=True)
    for (sub, members), res in zip(by_sub.items(), results):
        if isinstance(res, BaseException):              # F7: unreachable -> stale, unavailable, logged once
            for m in members:
                b = self.s["believed"].get(f"{sub}.{m}")
                if b: b["confidence"] = "stale"         # value and `at` are kept
            self._health(sub, ok=False, error=describe(res))
            continue
        self._health(sub, ok=True)                      # unavailable -> available restarts give-ups (§4.1)
        for m, r in res.items():
            t = f"{sub}.{m}"
            b = self._belief(t)
            if r.value is not None:
                self._believe(t, r.value, "confirmed")  # keeps the in-flight mark and sent_at
            fl = b.get("inflight")
            if fl and (self._arrived(fl, r, self.adapters[sub])
                       or (not r.moving and now() > fl["until"])):
                del b["inflight"]                       # proven arrival, or stopped somewhere else
            (self._moving.add if r.moving else self._moving.discard)(t)
```

**Proof of arrival (R1).** A cache that only updates when a motor stops (the Bloc hub, aa NOTES §4) keeps showing the pre-move position, as `stopped`, for the whole travel. A blind re-targeted back to where it started therefore *looks* arrived at once, in both directions: disco → normal → disco 3 s later (the blind heading up is "already at 100"), and S4's normal → disco → normal (the blind heading down is "already at 0"). A slow TV readback can fool it the same way. So:
- **An unresolved in-flight mark is extended, never replaced.** It keeps `from` (the reading before the first unresolved command), lists every other value commanded since (`earlier`), and `earlier_until`, by when all of those commands must have finished.
- **A reading proves arrival at `value`** only if the device is not moving, the reading equals `value`, and either `value` is neither `from` nor an `earlier` value (only a fresh reading can show it) or `earlier_until` has passed (every earlier motion is over, so the reading is current). This is `_arrived` (§4.12).
- The same test decides when `_refresh` drops a mark, and whether an adapter's `ok` is accepted. When it isn't, `_prove` re-reads every in-flight poll (2 Hz for blinds) until the reading proves it, or until `until + 15 s`, which gives `unconfirmed`.
- **Cost:** a blind sent back to where it started counts as arrived only once `earlier_until` has passed, up to `change_time_s` (75 s) after the first command. That happens only when a transition is reversed mid-travel. Re-sending the same value (a resume after a crash) doesn't add to `earlier`, so it isn't delayed.
- A restart between saving the extended mark and sending the command is safe: the resumed plan reads the stale value, the mark still says it isn't proven, and the command is sent.

**Notes:**
- `_settled_belief(t)` re-reads `t` every in-flight poll until it is neither moving nor in flight, for at most `change_time_s`. Then it returns the belief.
- **Known limitation (Bloc):** a hand move that is still in progress looks `stopped` at the old position in the hub's cache, so a store taken mid-move saves the pre-move value (aa NOTES §4).
- `same(a, b, adapter)` compares numbers within `adapter.tolerance` and everything else with `==`. Both sides are canonical (§5.2).
- Reads run in parallel across subsystems. Each adapter serialises its own device (the Bloc lock, one IP Control connection per TV).

### 4.5 Signals

```python
def _edges(self, to):
    fam = to.family                                     # e.g. ["house", "live", "disco"]
    entered = self.s["mode"]["entered"]
    exits = [m for m in reversed(entered) if m not in fam]     # includes names the config no longer has
    enters = [m for m in fam if m not in entered]
    return exits, enters

async def _fire(self, tr, sig, ledger):
    if ledger == "step":                                # rule signals: once per transition, even across a resume
        key = f"step:{sig.action}:{occurrence(tr, sig)}"
        if key in tr["fired"]:
            self._row(tr, sig.row, "skipped", "already fired"); return
        tr["fired"].append(key)
    a = self._adapter_for_signal(sig)
    if a is None or a.placeholder:                      # nothing sent; not recorded as fired
        if ledger == "step": tr["fired"].remove(key)
        self._row(tr, sig.row, "skipped", "no adapter"); return
    self._row(tr, sig.row, "running"); self._save()     # the ledger is on disk before the send (F9)
    try:
        out = await asyncio.wait_for(a.signal(sig.member, sig.action, sig.args), 5)
    except asyncio.TimeoutError:
        out = Outcome("unconfirmed", "timeout")
    except Exception as e:
        out = Outcome("failed", describe(e))
    self._row(tr, sig.row, out.status, out.detail); self._save()
```

- **`on_exit` of a mode fires when the room starts leaving that mode's family; `on_enter` fires when it has entered it.** That is decided by `entered`, not by mode names. So:
  - an interrupted entry never fires the exit signals of a mode the room didn't reach (S4, S12);
  - a re-apply fires nothing (S11);
  - `live.on_enter` fires once on entering any live mode, and not on disco → ambient.
- **A mode stays in `entered` until a transition that leaves it completes (R18).** Its `on_exit` fires at the start of the first transition that leaves it, and `leaving` stops a second firing if that transition is cancelled and another one leaves too. If the room comes back to the mode before any of them completes (piano → disco, then piano 3 s later), the mode never left, so its `on_enter` doesn't fire again. A cancelled transition's exit signals are not undone: signals are one-shots. Pairs that must stay balanced belong in state.
- **Rule signals** (`{"signal": …}` inside steps) fire every time their rule runs, including on live → live hops, because rule 1 serves hops. A re-apply skips them. For a one-shot tied to a mode, use the mode's `on_enter`/`on_exit`.
- **At most once.** The `leaving` or `entered` change, or the `fired` key, is saved before the signal goes out. A crash during a send loses that signal rather than repeating it. After a restart, a row still marked `running` becomes `unconfirmed` with "process stopped while sending" (S5, S11).
- A failed signal is reported and never retried. Signals are one-shots.
- **Exit signals fire before the steps and enter signals after them,** as in the reference.

### 4.6 Slot lifetime (end of a completed transition)

```python
def _close_slots(self, to, cfg):
    stores_into = {e.store for e in to.state.values() if e.store}
    for slot, entries in self.s["stored"].items():
        for t, have in list(entries.items()):
            if not cfg.uses(slot, t):                   # no mode stores or retrieves (slot, t) any more
                have["orphan"] = True                   # R9: kept and shown; forgotten only by the owner
                continue
            e = to.state.get(t)
            if e and e.retrieve == slot:                # a retrieve that landed was cleared already (§4.4)
                if expired(have, e):
                    del entries[t]                      # too old: `else` applied
                else:
                    r = (self.s["desired"].get(t) or {}).get("retry") or {}
                    have.update(closed=True, explained=r.get("explained", [have["value"]]),
                                final=r.get("final", False))   # didn't land: kept for the retry
                continue
            if slot in stores_into:
                continue                                # the episode goes on (live -> live, even a set inside it)
            del entries[t]                              # the episode is over for t
    drop_empty_slots()
```

- **A slot entry lives while the room is in modes that store into that slot or retrieve the entry (R17).**
  - A successful retrieve clears it straight away, and so does one that finds the target already there (§4.4).
  - A failed retrieve leaves it **closed** (below).
  - A live mode that *sets* a target another live mode stored keeps the entry: disco → karaoke (karaoke sets the volume) → normal still retrieves the pre-disco volume.
  - Arriving in a mode that doesn't store into the slot and doesn't retrieve the entry ends it. Example: normal → disco → off. `off` sets the TVs to standby without retrieving, and stores nothing, so the TV slots are dropped and never come back a week later.
- **Closed entries (R6).** A closed entry is the coordinator's own unfinished job: the episode is over, but the target didn't get back. It keeps `explained`, the readings our own commands account for (the value before the failed retrieve, the value it was sent to). While the target still reads within `explained`, the entry is valid:
  - reconcile retries it with backoff, and starts again when the subsystem comes back after giving up;
  - a later transition into a mode that retrieves it uses it, which finishes the job;
  - a store into it (the next episode) keeps the stored value and reopens the entry, because the target is still where the last episode left it, so the stored value is still the true "before".

  The first reading outside `explained` means someone moved the target. The entry is dropped and the new position is accepted: by reconcile (§4.8), by `_desired` at the next plan, or by `_store` (which then stores the new position). A `final` entry (a deliberate refusal, such as Hue taken over) isn't validated by readings: it waits for the next request, such as the owner's Reclaim.
- **This only happens on completion.** An interrupted transition never discards a stored value (S4).
- **`max_age_s` is checked only at retrieve.** It compares against the stored value's `at`, which is when that value was last confirmed. A store never overwrites an open slot, however old it is. That is why `max_age_s` is rejected on store entries.
- **Orphans (R9).** An entry that no mode stores or retrieves any more (a renamed slot, a blind removed from a group) is never deleted automatically. Status marks it `orphan`, and the owner forgets it (§7.3), or renames it back.

### 4.7 Abort and rollback

```python
def _held_already(self, t, v, tr, cfg) -> bool:
    """R7: the room already held this guarded value: the last completed mode and every mode in `via` set it."""
    names = [tr["from"], *tr["via"]]
    return all(n in cfg.modes and same(sets_value(cfg.modes[n], t), v) for n in names)

def _rollback(self, failed, step, cfg):
    m = self.s["mode"]
    back = m["current"] if m["current"] in cfg.modes else cfg.default
    if (failed["source"] == "rollback" or not step.rollback
            or m["current"] is None or back == failed["to"]):
        return None                                     # stay part-way: target stays set; Retry or Back
    if m["target"] not in m["via"]:
        m["via"].append(m["target"])
    m["target"] = back
    rb = {"id": new_id(), "resumes": None, "from": m["current"], "via": list(m["via"]), "to": back,
          "source": "rollback", "status": "running", "phase": "running", "reapply": False,
          "rollback_of": {"id": failed["id"], "to": failed["to"], "detail": failed["detail"]},
          "started_at": now(), "detail": "", "fired": [], "steps": []}
    self.s["transition"] = rb
    self._save()
    return rb                                           # _run runs it in the same task and config
```

- **What can abort.** Only a guarded target that isn't `ok` (§2.4), and only if the room didn't already hold that value (`_held_already`). So the privacy step aborts an entry (normal → disco), an interrupted exit turned back into disco (`via` holds normal), or a first boot; but not a hop (disco → ambient) or a re-apply, where a failing blind is reported as `partial` and retried. That also means a read blip during a hop, which leaves the blinds `stale` at 100 and re-sends them, can't strand the room (R7).
- **The aborted transition** keeps its rows: those that never ran are marked `cancelled`, "aborted: an earlier step failed" (aa). It goes to `last_transition`.
- **The rollback** goes back to the last completed mode, planned with `via` containing the aborted target, so the exit order applies: lights off before blinds up. Its desired values are the previous mode's, so reconcile never enforces the aborted mode's values (F4). Its record carries `rollback_of: {id, to, detail}`, so when it finishes, status still shows what failed and the Room page offers **Retry** (request `rollback_of.to` again) and **Back** (R22).
- **When there is no rollback** (the step says `rollback: false`, the failed transition was itself a rollback, there is nowhere to go back to at first boot, or the last completed mode *is* the destination, as when an interrupted exit is turned back): `mode.target` stays set and status says `failed`. While `target` is set and no task is running, reconcile only observes: it records drift and applies nothing. The owner requests a mode (Retry or Back). For the exit gate this is deliberate: the lights couldn't be confirmed off, so nothing raises the blinds until someone decides (Q12).
- An exception (a bug) ends the transition as `failed` without a rollback, because the rollback might hit the same bug. `target` stays set, so a restart tries again once.

### 4.8 Reconcile, drift and policies

Each subsystem gets its own loop, run every `poll_s`. While a transition holds a target in flight, that target is polled at 2 Hz by the transition itself.

```python
def _busy(self) -> bool:
    return self._req.locked() or (self._task and not self._task.done()) or any(not t.done() for t in self._rtasks)

async def reconcile_tick(self, sub):
    a = self.adapters.get(sub)
    if a is None or a.placeholder or self._busy():
        return                                          # a request, a transition or an apply owns the room
    gen = self._gen
    observe_only = self.s["mode"]["target"] is not None # failed and not rolled back: never enforce (F4)
    watched = [t for t, d in self.s["desired"].items()
               if t.startswith(sub + ".") and (d["policy"] != "ignore" or not d["settled"])]
    if a.kind == "level":
        await self._refresh(watched)
        if gen != self._gen or self._busy():
            return                                      # R5: a request came in while we read; this view is stale
    todo = {}
    for t in watched:
        d, b = self.s["desired"][t], self.s["believed"].get(t)
        if b is None or b.get("inflight") or t in self._moving or b["confidence"] == "stale":
            continue                                    # can't decide yet
        if not d["settled"]:                            # our command didn't land (S9): retry
            r = d["retry"]
            if r is None or r.get("final"):
                continue                                # never sent here, or refused on purpose (R12, R15)
            if b["confidence"] in ("confirmed", "unconfirmed") and same(b["value"], d["value"], a):
                self._settle(t); continue               # it got there after all (clears a retrieve slot)
            if a.kind == "level" and not any(same(b["value"], x, a) for x in r["explained"]):
                self._settle(t, accept=True); continue  # R2: something none of our commands explains:
                                                        # someone else moved it; accept, never fight
            if r.get("gave_up") or a.dry_run or observe_only or now() < r["next_at"]:
                continue
            if r["attempts"] >= 5:
                r["gave_up"] = True; self._log("warning", f"{t}: gave up after 5 retries"); continue
            todo[t] = (d["value"], "retry"); r["attempts"] += 1; r["next_at"] = now() + BACKOFF[r["attempts"]]
            continue
        if a.kind != "level" or d["policy"] == "ignore":
            continue
        if same(b["value"], d["value"], a):
            self.s["drift"].pop(t, None); continue
        drift = self.s["drift"].get(t) or {"observed": b["value"], "desired": d["value"], "at": now()}
        drift["observed"] = b["value"]
        if d["policy"] == "enforce" and not d["reasserted"] and not observe_only:
            todo[t] = (d["value"], "reassert"); d["reasserted"] = True; drift["reasserted"] = True
        elif d["policy"] == "enforce":
            drift["overridden"] = True                  # F8/G13: re-asserted once already; never fight
        self.s["drift"][t] = drift
    if todo:
        task = asyncio.create_task(self._reconcile_apply(todo, gen))   # under _work; cancellable
        self._rtasks.add(task); task.add_done_callback(self._rtasks.discard)
    self._save_if_changed()

async def _reconcile_apply(self, todo, gen):
    async with self._work:
        if gen != self._gen:
            return                                      # R5: a request or hand set came in while this waited
        todo = {t: (v, why) for t, (v, why) in todo.items()
                if t in self.s["desired"] and same(self.s["desired"][t]["value"], v, self._adapter(t))}
        if todo:
            await self._apply(None, {t: v for t, (v, _) in todo.items()}, self._slots(),
                              reasserts={t for t, (_, why) in todo.items() if why == "reassert"})
```

(`_apply(None, …)` passes `reasserts` through to `_after_send`; with no transition there are no rows.)

| Policy | When a settled target drifts | On leaving the mode |
|---|---|---|
| `respect` | Report the drift. Send nothing. | A retrieve still puts back the stored, pre-episode value. |
| `enforce` | Re-assert once. If it drifts again, or the re-assert doesn't land, report it as `overridden` and leave it alone until the next transition. | The same. |
| `ignore` | Don't watch the target. A re-apply or a return leaves it alone (§4.4). | The same. |

- **Unsettled targets are retried whatever their policy** (F12), with backoff. A retry stops when:
  - the target reads back as wanted (it is settled, and a retrieve's slot is cleared);
  - a reading shows something none of our own commands explains (R2): someone else moved it, so it is accepted, enforce counts it as used, and a closed retrieve slot is dropped. A blind that stalls part-way is accepted the same way, because it can't be told from a person stopping it;
  - the adapter said `retry: false` (`final`): reported, kept for the next request;
  - five attempts have failed (`gave_up`; restarted if the subsystem comes back);
  - the next transition starts.
- **The ambiguity that stays (R2).** A person who sends a moving blind back to where it started looks the same as a blind that never moved, so it is retried. Once a retry lands, any further change is drift under the policy, so it costs at most one re-send. The Room page also offers aa's **Restore** and Forget for a closed slot (§7.3).
- **edge_discrete** targets whose belief is `unknown` are retried, because re-sending them is safe, and they count as "there" only once a send has succeeded (R3). **edge_toggle** targets are never retried.
- **Hand changes during a transition** are handled by the next plan, which reads every target first.
- **Reconcile applies run in `_rtasks`,** which `request_mode` cancels. A mode request never waits behind a blind's travel (F8). A tick that overlaps a request drops what it read, and an apply re-checks the generation and the desired value once it holds `_work` (R5).
- A re-apply of the current mode resets `reasserted` and retries. That is the one-tap "re-lower" (S6).

### 4.9 Restart

```python
async def start(self):
    self.s = self.file.load()                           # §3.5 version handling
    self._unconfirm_running_signal_rows()               # at most once: running signal rows -> unconfirmed
    for t, b in self.s["believed"].items():
        if self._kind(t) == "edge_discrete" and b["confidence"] == "unknown" and t in self.s["desired"]:
            self._mark_unsettled(t)                     # S10/R3: a retry due now; or the next transition re-sends it
    m = self.s["mode"]
    if m["target"] is not None:                         # F3: crash, SIGTERM or cancel; resume toward it
        target = m["target"] if self.cfg.is_concrete(m["target"]) else self.cfg.default
        await self.request_mode(target, source="resume")
    elif m["current"] is None or not self.cfg.is_concrete(m["current"]):
        await self.request_mode(self.cfg.default, source="boot")
    self._start_reconcile_loops()                       # they skip while a transition runs
```

- `start()` returns once the transition is launched. A request in the meantime is safe: `request_mode` finalises any record whose task never ran (F19).
- **Readable targets are read again by the resumed plan before anything is sent.** Edge targets keep their saved beliefs; they are never re-sent just because of a restart. Only `unknown` discrete edges are re-sent (S10).
- Stored slots, `entered`, `leaving`, `fired` and in-flight marks all survive a restart. The resumed plan therefore stores nothing new, fires nothing twice, and re-sends to anything that was mid-travel.
- **A restart while settled (no transition) sends nothing** (R11). That is safe only because every adapter's `read()` stays truthful across a restart: an adapter whose state lives in room_service's own memory (the sensor mask's hold, the page policy) persists it and restores it on start (§5.2 rule 10). Without that, the mask would come back as `follow` mid-show and reconcile would only report it.
- **The screens controller adopts its sessions after a restart** (`adopt_wait_s` 20). While it does, the screens adapter's `read()` reports `moving`, and re-sending the same live spec doesn't start a second entry (§5.4).

### 4.10 Rules at a glance

| Topic | Rule |
|---|---|
| **Ordering** | A rule's steps run in order. A list in a step runs in parallel. Subsystems within a step run in parallel. Members within one subsystem go in one `apply()`, which the adapter serialises as it must. |
| **Delays** | A delay runs only if the step before it sent something. A no-op hop doesn't wait. |
| **Signals** | Rule signals fire where they sit, at most once per transition, and not on a re-apply. Mode signals fire at family edges, at most once; a mode stays entered until a transition that leaves it completes. |
| **Dependencies** | Expressed by order: TV before screens; DMX power before Maestro. There is no per-member `requires` (deferred, §10). |
| **on_fail** | `continue` by default. An abort happens only when a guarded target isn't `ok` (a skipped one counts, unless `allow_skipped`) and the room didn't already hold that value. It is followed by a rollback unless the step says `rollback: false`. |
| **Arrival** | A level command has arrived only when a reading proves it: a stale cache showing the pre-move value is not proof. |
| **Cancellation** | A new request cancels the transition and every reconcile apply or hand set. Devices keep moving and in-flight marks keep them "moving". The new plan starts from fresh reads. |
| **Interrupted chains** | `via` makes the room count as being in every mode it was heading to, so the safe rule is chosen. |
| **Stores** | Only into an empty slot, or a closed one whose target nobody has moved since, before the first command. A store never overwrites an open slot. |
| **Retrieves** | Clear the slot on success, or straight away if the target is already there. On failure the slot stays, closed, while the target still reads where our commands left it. |
| **Restart** | Resume toward `mode.target`. One-shots don't repeat; edges aren't re-sent unless unknown. Adapters keep their own in-memory state across restarts. |
| **Drift** | respect: report. enforce: once, then `overridden`. ignore: not watched, and the user's after arrival. Unsettled targets: retried, unless refused on purpose. |
| **Requests** | The last request wins, serialised. Each records its `source`. |

### 4.11 Changes by hand and owner corrections

```python
async def hand_set(self, t, value, hold=False, source="web"):  # PUT /room/targets/{t}, PUT /screens/{id}
    a, member = self._adapter_member(t)                 # 404 unknown_target
    v = a.normalize(member, value)                      # 400 bad_value
    self._gen += 1
    async with self._req:
        if self._task and not self._task.done():
            raise Refused("transition_running")         # 409
        d, b = self.s["desired"].get(t), self.s["believed"].get(t)
        if d and d["policy"] == "enforce" and not hold:
            raise Refused("enforced")                   # 409: "Disco keeps ch03 lowered. Hold it until the next mode change?"
        if a.kind == "edge_toggle" and (not b or b["confidence"] == "unknown"):
            raise Refused("toggle_state_unknown")       # 409: tell the room what it is doing first (assume)
        await self._stop_rtasks()                       # the last change wins; devices keep moving, marks stay
        task = asyncio.create_task(self._hand_apply(t, v, source))
        self._rtasks.add(task); task.add_done_callback(self._rtasks.discard)
    # 202 {accepted, effects}

async def _hand_apply(self, t, v, source):
    async with self._work:
        out = (await self._send(subsystem_of(t), {member_of(t): v}))[t]    # no row; a hand set is reported, not retried
        d = self.s["desired"].get(t)
        if d and not same(v, d["value"], self._adapter(t)):
            d["settled"], d["retry"], d["reasserted"] = True, None, True   # the user's value wins until the next transition
            if d.get("slot"):
                self.s["stored"].get(d["slot"], {}).pop(t, None)           # a closed retrieve is abandoned, not fought
            if d["policy"] != "ignore":
                self.s["drift"][t] = {"observed": v, "desired": d["value"], "at": now(), "manual": True,
                                      "overridden": d["policy"] == "enforce", "source": source}
        elif d:
            self.s["drift"].pop(t, None)
        self._log("hand_set", source, target=t, value=v, status=out.status)
        self._save()
```

- **One path for every target** (R14): the Room page's member editors (`PUT /api/v1/room/targets/{t}`, aa) and the v1 screen override (`PUT /api/v1/screens/{id}`) both call `hand_set`. A target the current mode doesn't manage has no desired entry, so it is simply set.
- **Policy decides what happens next.** `respect`: recorded as drift (`manual: true`). `ignore`: nothing. `enforce`: refused with `enforced` unless `hold` (admin, aa), which sets the target as `overridden` (`manual: true`), so enforce leaves it alone until the next transition. aa's code relaxes the policy to `respect` and flags `held`; the effect is the same.
- `hand_set` holds `_req` only while it starts. It takes `_work` in its task, runs as a cancellable `_rtask`, and a mode request supersedes it ("superseded by a mode switch", aa).
- **Owner corrections (admin, aa):** `assume(t, value)` sets an edge target's belief to `unconfirmed` without sending anything (a toggle ball after a restart); `stored_edit` corrects an existing slot entry and keeps its `at`; `stored_forget` drops an entry or a slot; `stored_restore` puts one stored value back now, through `hand_set` with `hold`, and clears it on arrival. All are refused while a transition runs (`transition_running`) or another change holds the device (`device_busy`), and each goes into `log` with its cause.

### 4.12 Helpers

| Helper | Definition |
|---|---|
| `_belief(t)` | The belief dict for `t`, created as `{value: null, confidence: "unknown", at: null}` if missing. Other code updates it in place. |
| `_believe(t, v, conf)` | In place: `value = v`, `confidence = conf`, `at = now()`. Keeps `inflight` and `sent_at`. |
| `_arrived(fl, r, a)` | `not r.moving and r.value is not None and same(r.value, fl.value)` and (`fl.value` is neither `fl.from` nor in `fl.earlier`, or `now() >= fl.earlier_until`). |
| `_prove(t)` | True if there is no mark, or the adapter's readback already meets the `_arrived` conditions; otherwise re-reads every in-flight poll until `_refresh` drops the mark (then: does the belief equal the commanded value?) or `until + 15 s` passes (False). |
| `_explained(t, v)` | The readings our own commands account for: `[mark.from, *mark.earlier, v]` while a mark exists, else `[belief.value, v]`, without duplicates. |
| `_moved_since(have, b)` | True when `b` is a confirmed reading outside `have.explained`. Stale or unknown beliefs give False (can't tell, so the original is kept). |
| `_closed(t, e)` | `e` retrieves a slot whose entry for `t` is closed. |
| `_settle(t, accept=False)` | `settled = True`, `retry = None`, and the target's retrieve slot entry (if any) is removed. With `accept` (someone else moved it): `reasserted = True`, and unless the policy is `ignore`, drift is recorded with `overridden` set under enforce. |
| `_mark_unsettled(t)` | `settled = False`, `retry = {attempts: 0, next_at: now(), explained: [], final: false}`. |
| `_slots()` | `{t: d.slot}` for the desired entries that came from a retrieve. |
| `_health(sub, ok)` | Tracks `available`, `last_ok_at`, `last_error`. On unavailable → available, resets `attempts`, `gave_up` and `next_at` for that subsystem's unsettled, non-final targets, and logs once. |
| `_rows(…)` | One row per pending target, per unsent target (created with its final status: `skipped` or `unconfirmed`, and `guarded: true` when a guarded step names it), per signal and per delay. `step` is 0 for exit signals, 1…N for the rule's steps in order (rest last, delays and signals numbered too), N+1 for enter signals. `id` is `i<k>` in row order, the same numbering aa's `plan()` uses. |
| `occurrence(tr, sig)` | 1 + the number of rule-signal rows with the same action earlier in this transition's plan. |
| `_any_not_ok(tr)` | A row is `failed` or `unconfirmed`, or a `skipped` row has `guarded: true` (only possible under `allow_skipped`). Other skipped rows never make a transition partial. |
| `_mark_rows(tr, from, to, detail?)` | Sets every row whose status is in `from` to `to`. |
| `_stop_tasks()` / `_stop_rtasks()` | Cancel `_task` and every `_rtask` (or only the `_rtasks`), then `asyncio.wait(…, timeout=3)`; an overrun is logged as a contract breach. |
| `sets_value(mode, t)` | The value a mode's entry for `t` sets (a plain value, set or store+set), or `None` for a retrieve, a store-only entry or no entry. |
| `mode_signals(cfg, name, key)` | The mode's own `on_enter` or `on_exit` steps, or `[]` if `cfg` has no such mode. |
| `cfg.uses(slot, t)` | Some mode stores into or retrieves (`slot`, `t`). |

---

## 5. Adapter contract v1.0

v1.0 is aa's v0.2 plus fixes: `normalize()`, canonical reads, the member argument on `signal()`, `dry_run`, `read_timeout_s`, `Outcome.retry`, the status vocabulary, the cancellation rules and state persistence. aa's editor additions (`value_schema()`, `actions`, `label(member)`) are kept as they are.

### 5.1 Protocols

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Value = Any                                             # JSON: str | int | float | bool | dict | list
Kind = Literal["level", "edge_discrete", "edge_toggle", "action"]
Status = Literal["ok", "failed", "unconfirmed"]

@dataclass
class Reading:
    value: Value | None                                 # canonical (as normalize() returns it); None = unknown
    moving: bool = False                                # mid-change, as far as the device can tell

@dataclass
class Outcome:
    status: Status
    detail: str = ""
    members: dict[str, "Outcome"] = field(default_factory=dict)   # per member, when apply() covered several
    retry: bool = True                                  # False: refused on purpose (writes not approved, taken over,
                                                        # the device's own guard tripped). Never retried by reconcile.

class Adapter(Protocol):
    subsystem: str                                      # "blinds"
    kind: Kind
    members: list[str]                                  # ["ch02", ..., "ch14"]
    change_time_s: float                                # the longest a change takes (blind travel, TV wake)
    poll_s: float | None                                # level: how often reconcile may read
    poll_inflight_s: float                              # level: how often to read while a command is in flight (blinds 0.5)
    read_timeout_s: float                               # default 5
    tolerance: float                                    # numeric equality band (blinds: 3)
    resend: bool                                        # edge_discrete: re-send even when the belief matches
    dry_run: bool                                       # True: reads work; the coordinator sends nothing (rows 'skipped')
    placeholder: bool                                   # True: no working adapter; members (and values) from device config
    actions: dict[str, dict]                            # aa: signal name -> {"label", "schema"} for the Room page's pickers

    def value_schema(self, member: str) -> dict:
        """aa: JSON Schema (the catalog subset plus x-ui) for one member's value; {} accepts anything."""

    def normalize(self, member: str, value: Value) -> Value:
        """Canonical form of a config value. Raise ValueError if it is invalid. Pure and offline: called at load."""

    def label(self, member: str) -> str:
        """aa: a display name for the member; the config's "labels" map overrides it."""

    def show(self, member: str, value: Value) -> str:   # optional: short text for rows ("live:fractal-zoom")
        ...

    async def read(self, members: list[str]) -> dict[str, Reading]:
        """Level kinds only. Must not move anything. Raise if the device can't be reached."""

    async def apply(self, desired: dict[str, Value], opts: dict) -> Outcome:
        """Drive each member to its value. Return when every member has arrived, failed or timed out."""

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        """A one-shot action. Not held state."""
```

`kind = "action"` is for adapters that only take signals and have no members, such as `log` and later Maestro cues.

### 5.2 Rules

1. **Canonical values.** `read()` returns values in the same form `normalize()` produces, so `same()` can compare them (F15). An adapter whose device reports more detail (a session id, defaulted parameters, a track position) leaves it out, or puts it in status, not in the value. A read can return a value config can't name when that is the truth (the screens' `"failed"`, §5.4).
2. **`read()` never moves anything.** It raises when the device is unreachable, and the coordinator marks the beliefs `stale`. An adapter maps "unreachable" to a value only when that is a positive fact (for example, IP Control reporting power off as `standby`).
3. **`apply()` semantics.** Per-member results go in `Outcome.members`, and the top-level status is the worst of them.
   - Level: `ok` means read back **after the command could have taken effect**. When `opts["pending"]` names a member (`{from, earlier, earlier_until}`: it is being re-targeted while an earlier command may still be running), a reading equal to `from` or an `earlier` value is not proof until `earlier_until` (§4.4). The coordinator applies the same test to every `ok`, so an adapter that ignores the hint is slower, never wrong.
   - Edge: `ok` means it was sent.
4. **Status vocabulary:** only `ok`, `failed` and `unconfirmed`. A timeout or a superseded wait is `unconfirmed`. An exception counts as `failed`. Set `retry=False` when the device side refused on purpose and repeating would be fighting or pointless: writes not approved (v0 TVs), Hue `taken_over`, a screen controller that has given up.
5. **Idempotent.** Applying the same values again is safe. The exception is `edge_toggle`, which the coordinator never re-sends.
6. **Cancellation stops the wait, not the device.**
   - Clean up within 3 s and re-raise `CancelledError`. Never swallow it.
   - While a device-side process continues (blind travel, a screens handshake, session adoption after a restart), `read()` reports `moving=True` where the device can tell.
7. **`opts` are hints.** An adapter ignores hints it can't use.
8. **The adapter serialises its own device** (the Bloc hub lock and its 250 ms gap between writes; one IP Control connection per TV). It keeps no coordinator state.
9. **Device writes follow §11 of the programme:** only through room_service, only with the owner's approval. A writer that isn't approved yet runs as `dry_run`.
10. **State that lives in room_service's memory is persisted by its adapter** (R11), in its own file under `~/.local/state/screen-machine/room/` (as `screens.json` already is), and restored on start, so `read()` returns what was applied, even after a restart. Today that means the sensor mask's `suspended` flag and the mask value it holds, and the page policy per screen.
11. **`normalize()` is pure and offline** (R16). It checks shape and enums. Anything that needs the device (a Sonos favourite's URI) is resolved in `apply()` and `read()`.

### 5.3 The adapters

| Subsystem | Members | Kind | Values (canonical) | Readback | `change_time_s` | `poll_s` | State now |
|---|---|---|---|---|---|---|---|
| `screens` | `north-screen`, `south-screen`, `devtest` | level | `"art"` or `{"live": {scene, transitions:{in,out}, params?, profile?}}`; read-only `"failed"` | ScreenController: layer, session spec, phase | 40 | 1 (in memory) | built (shim needed) |
| `tv` | `north-screen`, `south-screen` | level | `"standby"`, `"art_mode"`, `"hdmi"` | Samsung IP Control (power, Art Mode, input), ~0.7 s per TV, serialised | 90 (a wake from Art Mode takes 60–90 s) | 30 | read proven; writes need owner approval |
| `sensor_mask` | `main` | level | `"follow"`, `"suspended"` | MaskPusher, persisted (§5.2 rule 10) | 0 | 1 | built (persistence needed) |
| `page` | `<screen>.timed_reload` | level | `true`, `false` | `kiosk.state.policy`; the applied policy persisted | 10 | 5 | small addition |
| `blinds` | `ch02` … `ch14` (13) | level | int 0 (raised) to 100 (lowered); tolerance 3 | Bloc hub cache (GetDeviceList / ReadDevice; no RF); the cache updates when a motor stops | 75 | 15 (2 Hz in flight) | reads proven; writes gated |
| `mirror_ball` | `main` | edge_discrete (latched relay), else edge_toggle | `"spinning"`, `"stopped"` | none | 1 | — | placeholder (RM4 Pro not yet on the network) |
| `plug` | `dmx_power`, … | level if the plug reports its state, else edge_discrete | `"on"`, `"off"` | the plug's API | 5 | 30 | placeholder (brand to choose) |
| `sonos` | `lounge.volume`, `lounge.playback` | level | volume 0–100 (tolerance 1); playback `"paused"`, `"stopped"`, `{"favourite": name}`, `{"uri": …}` | SoCo | 10 | 10 | placeholder (later) |
| `log` | — | action | signals `log.info`, `log.warning` with `{"msg"}` | — | — | — | built (v1 LogAction) |
| `hue` (later) | per area | level | a scene snapshot, or `"off"` | REST | 5 | 10 | later |
| `maestro` (later) | `main` | edge_discrete (OSC never replies) | show name, or `"off"` | none | 1 | — | later |

A placeholder takes its `members` and, optionally, `values` (an enum per member) from its device config (`mirror_ball: {members: [main], values: ["spinning", "stopped"]}`), so `normalize()` can still reject a typo (R16). Without `values` it accepts anything and the loader warns.

### 5.4 Per-adapter notes

- **screens** (a thin shim around `room_service/screens.py` `ScreenController`; G14, I-F12).
  - `normalize` is `normalize_desired`, which accepts the three spellings of live.
  - **`apply`** gathers `set_desired(value, source=f"mode {tid}")` and then `wait_settled(value, change_time_s)` for each member. It maps:
    - `wait_settled`'s `cancelled` to `unconfirmed` ("desired changed meanwhile");
    - `wait_settled`'s `failed` (the controller has given up, e.g. 3 fallbacks in 10 min) to `failed` with `retry=False` (R12): reconcile must not retry it, because every `set_desired` clears the controller's failed phase, its fallbacks and its entry failures, which would defeat its own guard;
    - `LiveDisabled` to `failed`, "live_disabled", `retry=False`;
    - `ValueError` to `failed`.
  - **`read`** returns what is on glass, not `believed()` (which is the desired value):
    - `"art"` when the layer is art, there is no session, and the controller isn't holding a live request;
    - `{"live": <the session's spec>}` when live;
    - `moving=True` during a handshake or adoption;
    - **`"failed"`** when the controller is in its `failed` phase while still holding a live request (R12). On glass that is art, but the value differs from both `"art"` and any live spec, so: during a live mode it shows as drift (respect); on leaving, the mode's `"art"` is sent, which resets the controller's desired value; a re-apply of the live mode sends the live spec again, which is a deliberate new attempt.
  - **live → live:** the controller already PATCHes in-stream (scene or parameters, same profile) or runs exit plus entry (profile change). The shim adds nothing.
  - **Interrupt rule (S4):** a cancel stops the wait only. The next plan's value decides what happens:
    - before `go`, `"art"` aborts the entry invisibly: the page still shows art, or the anchor that matches it;
    - after `go`, the entry completes and then exits through the normal exit, a matched cut.

    This is what `ScreenController` does today (`screens.py:411-419`). The alternative, hard-cutting mid-crossfade, is the visible jump the owner has ruled out.
  - The default policy is `respect`, because the controller recovers its own fallbacks (stall → re-entry with backoff).
  - The manual `PUT /api/v1/screens/{id}` goes through `coordinator.hand_set` (§4.11).
- **tv.**
  - Reads use IP Control (`experiments/ipcontrol/ipctl.py`; tokens in `~/.config/screen-machine/ipcontrol/`): `powerControl`, `artModeControl`, input. `standby` is reported only when IP Control positively says the TV is off. An unreachable TV raises, so its belief goes `stale`.
  - **v0 (until the owner approves writes): verify only.** `apply` reads, and returns `ok` if the TV already has the value. Otherwise it returns `failed`, "IP Control writes not approved: switch to HDMI by hand", with `retry=False`, so reconcile doesn't repeat it every backoff step.
  - **v1 (after an owner-present test):** power and Art Mode through the fixed samsungtvws path or the IP Control setters, then wait for the readback. A wake from Art Mode can take 60–90 s (v1 §3.7), so `change_time_s` is 90 and the privacy step can take up to about 105 s with a slow TV.
  - It never touches picture settings or Game Mode (standing owner rule).
- **sensor_mask.** One member, `main`, because `MaskPusher.suspended` is global. `apply` calls `set_suspended`. **It persists `suspended` and the mask value it holds** (§5.2 rule 10): `MaskPusher.suspended` starts as `False` today (`room_service/mask.py`, `__init__`), so without this a restart mid-show would let the art mask follow the show-lit sensor.
- **page.** `apply` sends `policy{timed_reload}` on that screen's control socket (LIVE_V1 §2.1), then waits up to 10 s for `kiosk.state` to report it. With no page connected the result is `unconfirmed`: the next `welcome` carries the value, which comes from the persisted policy, not the default. The page's own backstop (never reload mid-live) applies whatever the policy.
- **blinds** (BlocBlinds v1.0, on the spike's `BlocHub` client; I-F11).
  - `read(members)` returns `Reading(position, moving=operation in {opening, closing})`. aa saw the cache keep the old position, as `stopped`, during a hand move; whether it shows motion for a move *we* command is untested. **The in-flight mark and the proof-of-arrival rule (§4.4) cover either case, including a blind sent back to where it started** (R1).
  - `apply` sends `WriteDevice {targetPosition: n}` per member, serialised with the hub lock. It waits for each blind to read `stopped` within tolerance of its target **with proof** (honouring `opts.pending`), and returns a per-member Outcome. If a blind is seen stopped somewhere else after the command, it re-sends once.
  - A cancel sends no STOP.
  - Writes are gated by `writes_enabled` in `config/room/rf/blinds.json`. Until the owner-present `targetPosition` test (aa NOTES §4) the adapter runs `dry_run`.
  - **The same test must check re-targeting mid-travel,** which S4 needs. If the hub can't do it, the adapter sends STOP, waits for the stop to show in the cache, then sends the new position.
- **mirror_ball.** The RM4 Pro, through `python-broadlink`. Prefer a latched relay with separate ON and OFF codes (edge_discrete). With a toggle relay the owner confirms its state once (aa's `assume`, §4.11).
- **plug.** A placeholder until a plug is chosen. Prefer one that reports its state (level), so DMX power drift is visible and the exit gate (rule 2) can be confirmed.
- **sonos.** SoCo, resolving the group coordinator on every call (v1 §3.7).
  - `normalize` checks shape only (R16): `"paused"`, `"stopped"`, `{"favourite": name}`, `{"uri": …}`. A favourite name is resolved to its URI in `apply`, never at load, so an unreachable speaker can't stop the config loading.
  - `read` returns the playback **context**, not the track: `"paused"` / `"stopped"`, or `{"favourite": name}` when the current context (station or container) is one of the favourites, else `{"uri": <context uri>}`. So a playing favourite compares equal to the mode's value, and a store captures the user's radio station or queue context rather than one track. Restoring a played queue exactly would need SoCo's `Snapshot`, inside the adapter; that waits for the real adapter.
  - `opts.over_s` ramps the volume.
  - Signals: `pause`, `play`, with the zone as the member.
- **hue** (later; v1 §3.7). The value is a scene snapshot.
  - `store` / `retrieve` give "snapshot before the show, restore afterwards" for free.
  - `apply` verifies at least 1 s after a restore and re-asserts once internally, because the bridge writes its own post-entertainment state 300–600 ms after an app restores.
  - **When another app holds the stream,** `apply` returns `failed`, "taken_over", `retry=False` (R15): it is never retried, and the closed slot is kept (`final`). **Reclaim** is a re-request of the mode, which uses the kept snapshot. While taken over, `read` reports a value the mode didn't ask for, so it shows as drift; the policy is `respect`, so it is never fought.
- **maestro** (later). OSC `/show/name`, `/show/play`, and `/global/brightness 0` for off. Never `/live/*`. Cues are signals. DMX power comes first by step order. Maestro carries on its show if room_service is down (v1 §3.7), which is why the exit gate checks DMX power, not Maestro.

---

## 6. Scenario walkthroughs

**The common starting point.** The room has been in `normal` since the afternoon.

| Target | Value |
|---|---|
| `blinds.ch02` / `ch03` / `ch14` | 0 / 80 / 100 (raised / mostly down / down); the other ten are unmanaged |
| `tv.north-screen` / `tv.south-screen` | `hdmi` / `art_mode` |
| `screens.*` | `"art"` |
| `sensor_mask.main` | `follow` |
| `page.*.timed_reload` | `true` |
| `plug.dmx_power` | `off` |
| `mirror_ball.main` | `stopped` (unconfirmed) |
| `sonos.lounge.volume` / `.playback` | 25 / `{"uri": <radio>}` |
| `mode` | `{current: normal, target: null, via: [], entered: [house, normal], leaving: []}` |
| `stored` | `{}` |

Unless a scenario says otherwise, every adapter is real and enabled.

### S1. normal → disco

- **Request:** `PUT /mode {mode: disco}` with source `web`. `from` is normal and `via` is empty. Rule 1 (`* → @live`) applies.
- **Store** (saved before any command): `pre_live` = {ch02: 0, ch03: 80, ch14: 100, tv.north-screen: hdmi, tv.south-screen: art_mode, sonos volume: 25, sonos playback: the radio}.
- **Signals:** exits [normal] (it has none; `leaving` = [normal]). Enters [live, disco] at the end: live's `log.info` "show starting".

| t (s) | Step | What happens |
|---|---|---|
| 0.0 | read | blinds (~0.1 s), both TVs (~0.7 s, in parallel), Sonos; screens, mask and page from memory |
| 0.8 | store | 7 slot entries saved to disk |
| 0.9 | 1 | `sensor_mask` → suspended (it holds the pre-show mask, before the room darkens); `page` → false ×3 |
| 1.0 | 2 | ch02, ch03 → 100 (ch14 is already there: settled, not sent); tv.south-screen → hdmi. In-flight marks are saved first. The step waits for both blinds to prove 100 ± 3 (a reading of 100 differs from where they started, so it is proof). |
| ~60 | 3 | `plug.dmx_power` → on. This is the first thing that can light up, and the blinds are already down. (With a TV that wakes slowly, step 2 can last up to ~105 s, because it waits for the TV too.) |
| ~61 | delay | 2 s for DMX boot (the step before it sent something) |
| ~63 | 4 | `frames` → live: both screens run the entry handshake in parallel. Each is `ok` once it is entering (~10–15 s). |
| ~77 | 5 | `mirror_ball` → spinning (RF; belief `unconfirmed`) |
| ~77 | rest | Sonos volume 25 → 60 over 5 s; playback → the Disco favourite |
| ~83 | done | live's `log.info` fires; `entered` = [house, live, disco]; `leaving` = []; `current` = disco; slots stay open; status `ok` |

- **Nothing lights up before the blinds are down:** DMX power at ~60 s, screens at ~63 s, the ball at ~77 s, all after step 2.
- **"Sonos signal":** the reference fired `sonos.play` on entry. Here playback is state (stored, then set), so leaving puts back what was playing. A one-shot is still available if the owner prefers one (Q5).
- **With v0 TVs** (writes not approved): tv.south-screen's row is `failed`, "IP Control writes not approved", `retry: false`. It isn't guarded, so the show goes on and the status is `partial`. Reconcile doesn't repeat it. The south screen's stream appears as soon as someone switches the TV to HDMI.
- **In the slice** (blinds dry run, plug and ball placeholders, `allow_skipped: true` in the shipped config): the blinds' rows are `skipped`, "dry run", the transition goes on and ends `partial`, "guard not enforced: blinds.ch02, blinds.ch03 skipped". Without `allow_skipped` it would abort at step 2 (R8).

### S2. disco → ambient (live → live)

- `from` is disco and rule 1 applies.
- **Store:** every `pre_live` entry is open, so nothing is stored again.
- **Wanted:** ambient inherits `live` (blinds 100, TVs hdmi, default zoom) and `house` (ball stopped, DMX off, Sonos retrieved).
- **Plan:** steps 1–2 have nothing to do. Step 3 turns DMX off, then the 2 s delay. Step 4 changes the frames' spec (`zoom_rate` 0.6 → the default): the controller PATCHes the session **in-stream** with no exit or re-entry. Step 5 stops the ball. Rest retrieves the Sonos volume (25) and playback (the radio) and clears those two slots.
- **If a privacy blind fails here** (a hub read blip leaves the blinds `stale` at 100, so they are re-sent, and the write fails): disco already set them to 100, so the failure doesn't abort the hop (§4.7). The status is `partial`, the blinds are retried, and the scene still changes (R7).
- **Signals:** exits [disco], enters [ambient]. Disco has no `on_exit`. Live's `on_exit` doesn't fire: the room stays in the live family.
- **After:** `current` = ambient; `pre_live` still holds the blinds and TVs (ambient stores into `pre_live`, so the episode is still open). Status `ok`.

### S3. ambient → normal

- From the live family to a non-live mode, so rule 2 (`@live → *`) applies.
- **Signals:** exits [ambient, live] at the start: live's `log.info` "show over". Enters [normal] at the end.
- **Plan:** the ball and DMX are already as wanted, so those steps send nothing (and the gate has nothing to check). `frames` → art runs the exit handshake: freeze, screenshot, a 3 s crossfade out, a matched cut, `art_visible`. Then **rest**, in parallel:
  - ch02 → 0 and ch03 → 80 (each blind to its own value);
  - ch14 is already at 100, so its slot is cleared without a command;
  - tv.north-screen is already hdmi, so it is cleared; tv.south-screen → art_mode, after the screens have cut back;
  - `sensor_mask` → follow, after `art_visible`, so the art mask moves from the held value to the current one;
  - `page` → true;
  - Sonos has nothing stored, so it is left alone.
- **After:** `stored` = {}; `current` = normal; `entered` = [house, normal]. A blind that failed to arrive keeps a closed slot and is retried (S9).

### S4. Interrupted: normal → disco, then normal 3 s later

- **At 3 s:** step 2 is running. ch02 and ch03 were commanded to 100 at 1 s and are travelling; their marks are {value 100, from 0 / 80, until 76 s}. tv.south-screen is commanded to hdmi. The mask is suspended and the page's reloads are off. `leaving` = [normal]; normal is still in `entered`.
- **The request:** `_stop_tasks` cancels the transition. The blinds adapter stops waiting, but the blinds keep moving, and their in-flight marks stay on disk. Record A becomes `cancelled`, and `via` = [disco].
- **The new transition B:** `from` is normal, `via` is [disco], `to` is normal. disco ∈ `via` matches `@live`, so **rule 2, the exit order, applies.**
- **Read:** the hub cache still shows ch02 = 0 and ch03 = 80, `stopped`, because it updates only when a motor stops. That is not arrival anywhere: a reading of 0 isn't the commanded 100. The marks stay (F1).
- **Wanted:** retrieve `pre_live`: ch02 0, ch03 80, ch14 100, tv.south art_mode, and so on.
  - ch02, ch03 and tv.south are in flight, so they are **sent**.
  - ch14 and tv.north were never commanded by A, and their entries are `ignore` retrieves in the mode the room last completed, so they are left alone and their slots are cleared (§4.4).
  - The ball, DMX and frames are already as wanted (the screens step never started), so nothing is sent.
- **Signals:** none. Normal never left `entered` (A didn't complete), so nothing is entered; live was never entered, so **no live `on_exit` fires**; normal's `on_exit` already fired in A (it has none) and isn't repeated (F5, R18).
- **Steps:** the ball and plug steps have nothing to do (the gate has nothing to check), nor does frames. Rest re-targets ch02 → 0 and ch03 → 80: each mark keeps its `from`, adds 100 to `earlier`, and sets `earlier_until` to 76 s. It also sends tv.south → art_mode, turns the mask back to follow and the reloads back on.
- **Arrival (R1):** both blinds are going back to where they started, so a reading of 0 / 80 only proves anything once A's commands must have finished, at 76 s. With a hub that re-targets mid-travel, the blinds are back within seconds and the 76 s reading proves it; the slots are cleared then, not earlier. With one that doesn't (Q11), the cache shows 100 at ~50 s (stopped somewhere else), the adapter re-sends, and if that runs past the apply deadline the row is `unconfirmed`: the slot stays closed with `explained` = {0, 100}, and reconcile's retry finishes the job. The original values are never lost.
- **After:** each blind is proven at its **original** position, `stored` = {}, `current` = normal. The ball was never sent.
- **Screens, had the interrupt come during step 4:** the cancel stops the wait. The next plan's `"art"` then either aborts the entry (before `go`) or completes it and exits (after `go`). Both are matched cuts (§5.4). The ball step, which comes after the screens, can't start, because `gather` re-raises the cancel even if an adapter swallows it (aa's code; shown by the tracers' experiment E14).
- **The mirror case** (disco → normal, then disco 3 s later): ch02's retrieve to 0 is in flight when disco sends it back to 100. The cache still shows 100, `stopped`, but 100 is the mark's `from`, so it isn't proof until the first command's `until`. The privacy step therefore waits (up to ~75 s) before DMX power comes on, instead of lighting the room with a blind on its way up. If it can't prove arrival, the step aborts; there is no rollback (the last completed mode is disco itself), so the room stays with the lights off and status `failed` until the owner chooses (§4.7).
- **Hardware dependency:** re-targeting mid-travel is untested and is in the owner-present test (§5.4, Q11).

### S5. room_service crashes mid-S1 and restarts

- **A hard kill at ~30 s, in step 2.** On disk:
  - `mode` = {current normal, target disco, via [], entered [house, normal], leaving [normal]};
  - transition A `running`;
  - 7 slots;
  - in-flight marks on ch02, ch03 and tv.south.
- **`start()`:** the target is disco, so `request_mode(disco, source=resume)`.
  - A is finalised as cancelled, and transition B carries it on under a **new id** with `resumes` = A's id and A's `fired` list. `via` = [disco].
  - From {normal, disco} to disco, rule 1 applies, so the entry order is kept.
- **The resumed plan:**
  - Read: the marks hold (the stale readings 0 / 80 aren't 100), so the blinds are re-sent 100. Re-sending the same value doesn't add to `earlier`, so a reading of 100 still proves arrival as soon as it appears.
  - Store: the slots are open, so they are kept.
  - The mask adapter restored `suspended` and its held value from its own state file (§5.2 rule 10), so nothing is re-sent. Without that rule it would read `follow` and be re-sent `suspended`, with the held value lost.
  - The page still reports false, so nothing is sent to it.
  - Then the rest of S1. No signal repeats: normal is in `leaving`, so its `on_exit` doesn't fire again.
- **SIGTERM** (a deploy or `systemctl restart`): the cancel handler writes `cancelled` and keeps `target` and `via`, so the same resume happens (F3).
- **A crash between saving an extended mark and sending it** (in S4, after B marked ch02 → 0 but before the write): the resumed plan reads 0, but 0 is the mark's `from` and A's command may still be running, so it isn't proof. The command is sent and the slot is kept until arrival is proven (R1).
- **A crash while the ball code was being sent:** the ball's belief was saved as `unknown` (value kept) before the send (F9).
  - Discrete: it is re-sent `spinning`, which is safe.
  - Toggle: it gets an `unconfirmed` row, "toggle state unknown; not sent", and the transition is `partial`. Status asks the owner to confirm the ball's state (S10).
- **A crash during the screens step:** the controller adopts the live sessions, and `read()` reports `moving`. The same live spec is re-sent, and the controller, already holding a matching session, starts no second entry.
- **A restart while settled in disco** (a deploy after the show started): nothing is resumed and nothing is sent. Every target reads what it read before: the mask and page from their persisted state, the devices from themselves (R11).

### S6. During disco someone raises ch03 by hand

- About 50–60 s later the motor stops and the hub cache shows ch03 = 0.
- **Reconcile** (blinds, every 15 s) sees drift from desired 100 under `enforce`, with `reasserted` false. It re-lowers ch03 once, as a cancellable `_rtask`.
- If someone raises it again, or the re-assert doesn't arrive (an obstacle, a weak battery), ch03 is reported `overridden` and left alone. That is one 433 MHz frame, not one per poll (F8).
- **From the Room page** (`PUT /room/targets/blinds.ch03 {value: 0}`): refused with 409 `enforced`; with `hold` (admin) it is applied and recorded as `overridden`, `manual: true`, so enforce doesn't re-lower it at the next tick (R14).
- **Respect or enforce?** The recommendation is enforce-once for the privacy blinds (Q4): a person who raises a blind twice means it. **Re-apply** (re-requesting disco) re-lowers it on demand. If that re-lower fails, the re-apply can't abort, because disco already held 100: it is `partial`, and the blind is retried (R7).
- **On leaving** (→ normal): the retrieve sends ch03 to **80**, its pre-episode value, not the hand position (D5).

### S7. A hand move in normal; stale slots

- **In normal,** the blinds' entries are `retrieve` with policy `ignore`, so a move to ch02 = 30 is neither watched nor reported.
- **The next normal → disco** reads 30 and stores 30. The previous episode's slots were cleared when its retrieves succeeded, or dropped at completion (§4.6).
- **A retrieve that failed** (ch03 never arrived at 80, so it is still at 100) leaves a **closed** slot with `explained` = {100, 80}, and reconcile retries it.
  - If the owner moves ch03 to 20 by hand in the meantime, the next reading (20) is outside `explained`, so the retry accepts it and drops the slot. The owner's manual position is never lost (F13).
  - If nobody touches it and the hub is offline long enough for the retries to give up, the slot stays closed and valid. When the hub comes back, the retries start again (R6).
  - If the slot is still there when the next live episode starts, and ch03 still reads 100, the store **keeps** the original 80 and reopens the slot, because 100 is only where the last episode left it. If ch03 reads anything else, the store takes that new position.
  - A house → house transition (normal → piano) that finds the slot still valid retrieves it, finishing the job. One that finds ch03 moved drops it and leaves ch03 alone.
- **`max_age_s` (12 h on the privacy blinds)** is checked only at retrieve, against the stored value's `at`. For a show that started at 18:00 and ends at 07:00, the slot is 13 h old, so `else: leave` applies and the blinds stay down. A store never refreshes an open slot mid-episode, so a long disco followed by an ambient hop can't capture "lowered" as the original (F13, G4).

### S8. The TV is in standby or Art Mode when disco is requested; and on return

- **Store:** IP Control reads `standby` (power off) or `art_mode`, and `pre_live` stores it. If IP Control is unreachable, the belief is `stale`: the slot stores the last good value with `confidence: stale` and its old `at`, and status shows it (F7).
- **Step 2:** tv.* → hdmi runs **in parallel with the blinds**. A 15–90 s wake overlaps the ~60 s of travel, and `change_time_s` is 90. The TVs aren't guarded, so a slow or failed TV can't abort the show.
  - A readback that doesn't arrive within `change_time_s + 15` gives `unconfirmed`. The belief keeps the last reading (`art_mode`), so the retry's `explained` is {art_mode, hdmi}, and reconcile retries with backoff while the TV still reads `art_mode` (R2).
  - With v0 TVs, the result is `failed`, "writes not approved", `retry: false`: shown, not repeated, and the show goes on.
- **Dependency:** "screens live needs the TV on HDMI first" is expressed by order: TVs in step 2, screens in step 4. If a TV isn't on HDMI, its screen still goes live on the kiosk, and the stream shows up when the TV does. A per-member `requires` would hold that screen back; it is deferred (§10).
- **Return** (→ normal): in rest, after the screens' exit, the TV is retrieved to `standby` or `art_mode`. If nothing usable was stored, `else: leave` keeps it on HDMI. To get standby instead, write `"else": "standby"` on `frame_tvs` in `house` (Q8). With v0 TVs the retrieve is refused (`final`), and the slot is kept for the next request.

### S9. A device is unavailable

| Case | What happens | Stored and retrieved |
|---|---|---|
| **Bloc hub offline at the disco request** | The read fails, so the blinds go `stale`, the subsystem reports `available: false`, and `last_error` is logged. Step 1 runs. Step 2's blinds apply fails; the blinds are guarded and normal didn't hold 100, so the transition is **failed**: "stopped at step 2: blinds.ch02, blinds.ch03 not ok". **Rollback** to normal (exit order): mask follow, reloads on, tv.south retrieved, blinds retrieve fails (offline). DMX, screens and ball never started. | The slots were stored from stale beliefs, marked stale, with their old `at`. If that `at` is older than `max_age_s` (the blinds aren't polled in normal), the rollback's retrieve uses `else: leave` and the slots are dropped. Otherwise the failed retrieves leave them closed, and when the hub returns, reconcile's retry finds the blinds where they were (they never moved), settles them and drops the slots. Either way the blinds never moved. (Corrected, R22.) |
| **RM4 Pro offline (ball)** | The ball's row is `failed`. It isn't guarded, so disco proceeds and is `partial`. The belief is `unknown`, value unchanged, so reconcile doesn't mistake it for "there" (R3) and retries with backoff (30 s … 10 min, 5 tries, restarted if the RM4 comes back) while disco lasts, which is safe for a discrete relay. A toggle is never retried. | — |
| **DMX plug offline, on entry** | The same as the ball: `partial`, and retried. | — |
| **DMX plug offline, on exit** | Rule 2's gate: the ball has stopped; DMX power can't be confirmed off, so the transition **stops at step 2** with no rollback (R4). The screens stay live, the blinds stay down, status is `failed`, "stopped at step 2: plug.dmx_power not ok", and reconcile only observes. Retry repeats the exit (the gate first); Back returns to disco. An edge plug with no readback passes the gate once its send succeeds. | Slots stay open: the episode isn't over. |
| **No adapter at all** (a placeholder) | Rows are `skipped`, "no adapter", and status reports `available: false`. A skipped row doesn't make a transition partial, unless the target is guarded: then it aborts, or with `allow_skipped` makes the transition `partial`, "guard not enforced" (R8). | — |
| **Hub returns mid-episode** after a retrieve failed in normal | The closed slot is retried until the blinds arrive, unless a reading shows someone moved them. Retries that had given up start again when the hub comes back (R6). | The slot is cleared on success. |

The Room page shows the failure as `mode.last_transition`, with **Retry** and **Back**. After a rollback, the rollback's record carries `rollback_of` with the failed transition's id, destination and detail, so the failure stays visible and Retry knows where to go (R22).

### S10. The edge-only ball after a restart

- The belief is kept as saved (`spinning`, `unconfirmed`), so nothing is sent at restart or by reconcile. Edges are trusted as saved (programme §2.2).
- **`unknown`** (a crash mid-send, or a failed send; the value is kept):
  - **edge_discrete** is re-sent at the next transition, or by reconcile's retry, which `start()` schedules at once (R3);
  - **edge_toggle** is **never** sent. Its row is `unconfirmed`, "toggle state unknown; not sent", and the transition is `partial`. The owner confirms the real state with aa's `assume` (admin, §7.3), which sets the belief to `unconfirmed`, and the next transition flips it correctly. A wrong `assume` makes the next flip go the wrong way, which is why it is admin-gated.

### S11. One-shot signals

- **normal → disco:** live's `on_enter` (`log.info` "show starting") is recorded in `entered` and saved, then sent.
  - A restart afterwards: `live` is still in `entered`, so it is not re-sent.
  - A crash between the save and the send: the signal is lost and its row becomes `unconfirmed`. At most once (F9).
- **disco → disco, or piano → piano (a re-apply):** **no signals fire**, neither mode signals nor rule signals. The request re-applies state only.
- **disco → ambient:** live's signals don't fire (same family). A rule signal in rule 1 would fire, because rule 1 runs for hops; put one-shots tied to a mode in its `on_enter`/`on_exit` (R10).
- **Disco's music is state**, so disco → disco sends nothing to Sonos, because playback already matches.
- **Piano's pause is state too** (`{"set": "paused", "policy": "ignore"}`), so disco → piano pauses Sonos in the same step as everything else, and the radio never plays while the blinds travel (R13).
- **Rule signals** (`{"signal": …}` inside steps) are keyed `step:<action>:<n>` in `fired`. A resumed transition skips those already fired, even after a config edit that moves them.
- **A return before completion** (piano → disco, back to piano 3 s later): piano never left `entered`, so its `on_enter` doesn't fire again (R18).

### S12. Partial ownership

- **Sonos in normal:** house's `"sonos.lounge": {"retrieve": "pre_live"}` has the default policy `ignore`. The user changes music and volume freely, with no drift and no enforce.
  - At the end of a disco episode the pre-disco volume and playback are retrieved **once**.
  - normal → ambient → normal stores nothing for Sonos, so it touches nothing.
  - An interrupted disco entry fires no signals (S4).
- **Sonos in piano:** paused on arrival, then the user's (`ignore`). If the user plays tracks and then changes mind mid-way to disco and back, the return leaves the tracks playing, because nothing in the interrupted chain touched Sonos (§4.4). If the chain had already switched Sonos to the Disco favourite, the return pauses it.
- **Blinds in normal:** the same pattern. They are unmanaged in normal (`ignore`; the Bloc app's timers and hand moves are the house's business) and are still retrieved at the end of each live episode.

### S13. Competing requesters

- **The rule: the last request wins,** serialised by `_req`. Every transition records its `source`, and status shows it.
- **Example:** Alexa sends "disco" at 22:00:00.0 and the web app sends "normal" 0.2 s later.
  - The second request waits for the first to *start*. It then cancels it (cleanup ≤ 3 s) and starts normal with `via` [disco], so rule 2 applies and only what disco had begun is undone.
  - Both records are kept: `last_transition` shows disco `cancelled` (source alexa) and the transition shows normal (source web). Before the F2 fix, both ran in full, one after the other.
- **Changes by hand to one target** (`PUT /api/v1/screens/{id}`, `PUT /api/v1/room/targets/{t}`): they go through `coordinator.hand_set` (§4.11).
  - They return **409 `transition_running`** while a mode transition runs.
  - Otherwise they are applied and recorded as drift (`manual: true`) under `respect`, until the next mode request re-applies the mode. An enforced target needs `hold`.
  - A hand set supersedes a reconcile apply or an earlier hand set, and a mode request supersedes it.
- **The scheduler** (later) uses `PUT /mode` with `source: scheduler`. It can override a manual show.
- Precedence, such as "the scheduler doesn't override a manual request made within N minutes", or v1's 4 h lease, would be a new concept. It is **deferred** (§10).

### S14. Cross-cutting overlays (photosafe, guests)

**Deferred explicitly.**
- For now, write the few variants needed as modes using `extends`, for example `disco-photosafe` (§2.6).
- If the combinations multiply, an overlay would be a named partial state map using the same verbs, merged above the mode. `_desired()` would take a list of entry maps, and nothing in this model blocks that.

---

## 7. API and events (additive to LIVE_V1 §3)

Nothing in LIVE_V1 §3 is removed or renamed, and the keys the web Room page already reads from v1 room_service are kept: `mode.last_transition`, row `id`, transition `phase` (R19). The new fields are read when present. The editor endpoints follow aa's `EDIT_SURFACES_PROPOSAL.md` §3, which has their full request and response shapes; this section lists what the coordinator needs.

### 7.1 `GET /api/v1/status`

```json
{"mode": {"current": "normal", "target": "disco", "via": [], "entered": ["house", "normal"], "leaving": ["normal"],
          "since_age_s": 3600.0, "default": "normal",
          "transition": {"id": "t-3f9a12c4", "phase": "running", "status": "running", "resumes": null,
                         "from": "normal", "via": [], "to": "disco", "source": "web", "reapply": false,
                         "started_age_s": 31.2, "steps": ["…rows, §7.2…"]},
          "last_transition": {"id": "t-1b2c3d4e", "phase": "done", "status": "ok", "from": "disco", "to": "normal",
                              "source": "web", "detail": "", "finished_age_s": 7205.0}},
 "screens": {"…": "unchanged LIVE_V1 shape"},
 "stream_service": {"reachable": true, "url": "http://127.0.0.1:8082"},
 "subsystems": {
   "blinds": {"kind": "level", "available": true, "dry_run": false, "last_error": null, "last_ok_age_s": 0.4,
              "believed": {"ch02": 0, "ch03": 80, "ch14": 100},
              "members": {"ch02": {"believed": 0, "confidence": "confirmed", "age_s": 30.1, "moving": false,
                                   "inflight": {"value": 100, "until_s": 44.8},
                                   "desired": 100, "policy": "enforce", "settled": false, "retry": null,
                                   "drift": null}}},
   "mirror_ball": {"kind": "edge_discrete", "available": false, "believed": "stopped",
                   "members": {"main": {"believed": "stopped", "confidence": "unconfirmed"}}}},
 "stored": {"pre_live": {"blinds.ch02": {"value": 0, "age_s": 31.0, "expires_in_s": 43169, "confidence": "confirmed",
                                         "from_mode": "normal", "closed": false}}},
 "config": {"revision": "a1b2c3d4e5f6", "error": null, "pending_targets": [], "warnings": []}}
```

- **`mode.transition`** is the **running** transition, or `null`, as in LIVE_V1. A finished, failed or cancelled one is **`mode.last_transition`**, as v1 room_service already emits and the Room page reads (`useRoomConnection.ts`, `applyStatus`). That includes a cancelled record still held in the state file's `transition` while it waits for a resume. There is no `mode.last` (R19).
- **`phase`** (`started`, `running`, `done`, `cancelled`) is kept on both, because the page decides "finished" from it; **`status`** is the outcome (`ok`, `partial`, `failed`, `cancelled`). aa's proposal calls the outcome `outcome`; it is the same field.
- **`subsystems.<s>.believed`** keeps the LIVE_V1 summary: a single value for a single-member subsystem, otherwise a member → value map. `members` (new) has the detail. aa's proposal adds the same data keyed by target as `status.targets`, with `manual` and `last`; the editors can use either.
- **`available`** comes from adapter health: `false` for placeholders, and after a failed read or apply until the next success.
- **Times are ages** (`age_s`, `since_age_s`, `started_age_s`, `finished_age_s`, `expires_in_s`, `until_s`), never clock times, because the Room page runs on another device (LIVE_V1 §0; aa C12). The state file keeps epoch seconds. v1's `started_at`/`finished_at` strings aren't in LIVE_V1; the page already falls back to its own clock without them.
- `stored` entries add `closed`, `orphan` and `edited_age_s` when they apply. A subsystem whose guard is inactive (placeholder or dry run under an `abort_on`) shows `"guard": "inactive"`.
- The subsystem name `mirror_ball` matches LIVE_V1's.

### 7.2 Rows (`transition.steps`)

At the start, there is one row per target command, signal and delay, all `pending` (or already `skipped`/`unconfirmed` for targets that can't be sent). Each row then moves to `running` and then to its outcome. The LIVE_V1 keys are unchanged; the others are additive.

```json
{"id": "i4", "subsystem": "blinds.ch02", "action": "store pre_live, set 100", "status": "running", "detail": "",
 "step": 2, "kind": "set", "value": 100, "stored": 0, "guarded": true}
```

| Key | Values |
|---|---|
| `id` | `i<k>`, stable for the life of the transition and the same as in aa's `plan()` preview. The Room page patches rows by `id` first (R19). |
| `subsystem` (LIVE_V1) | The target (`blinds.ch02`), the signal's `subsystem.member` (`sonos.lounge`), or `delay` |
| `action` (LIVE_V1) | Fixed for the life of the row (the page's fallback match is (subsystem, action)). Examples: `set 100`, `store pre_live, set 100`, `retrieve pre_live → 80`, `retrieve pre_live (empty) → else standby`, `signal pause`, `wait 2 s`, `live:fractal-zoom` (from the adapter's `show()`). aa's plan items use `verb:value` codes; the port keeps this text and adds aa's codes as `reason`. |
| `status` (LIVE_V1) | `pending`, `running`, `ok`, `failed`, `unconfirmed`, `cancelled`, plus **`skipped`** (new) |
| `detail` (LIVE_V1) | Free text: `timeout`, `no adapter`, `dry run`, `toggle state unknown; not sent`, `arrival not proven`, `IP Control writes not approved`, `taken_over`, `re-apply`, `aborted: an earlier step failed` |
| `step` | The position in the plan: 0 for exit signals, 1…N for the rule's steps, N+1 for enter signals (§4.12) |
| `kind` | `set`, `retrieve`, `signal`, `delay` |
| `value`, `stored` | The value sent, and the value stored before it (if any) |
| `guarded` | Present on a target a guarded step names (§2.4) |
| `reason` | aa's code for a skipped or cancelled row: `already`, `kept`, `nothing_stored`, `stale`, `unknown_state`, `fired`, `quiet_before`, `aborted`, `unavailable` |

Targets that are already as wanted get no row. The web app should give `skipped` a neutral tone, except a `guarded` one.

### 7.3 Calls

Auth follows LIVE_V1 §3: operating is open on the LAN; editing config and stored values needs the admin token.

| Call | Auth | Change |
|---|---|---|
| `GET /api/v1/modes` | open | Additive. `modes` lists **concrete** modes only, so the web app never draws a button for an abstract base. Each mode keeps `label`, `state` (**as written in `modes.json`**, so the page's `summariseState` keeps working), `on_enter`, `on_exit` and `sequence: null` (v1 key, kept), and gains `extends`, `family`, `targets` (resolved per target: `{verb, value?, slot?, else?, policy, max_age_s?, opts?, from}`, aa's naming) and `steps_from: {<from mode>: [step summaries]}`, the resolved plan for arriving from each other mode. Top level gains `default`, `bases`, `groups`, `labels`, `transitions`. (R19: the earlier draft changed what `state` meant.) |
| `PUT /api/v1/mode` `{mode, source?}` | open | `202 {transition_id, reapply}` (aa). Additive `source` (default `web`); `boot`, `resume` and `rollback` → **400 `bad_source`**. An unknown or abstract mode → **404 `unknown_mode`**. Never 409: the last request wins. |
| `PUT /api/v1/screens/{id}` | open | Unchanged body; `hand_set` (§4.11). **409 `transition_running`** while a mode transition runs. Otherwise `202`, a change by hand under `respect`, recorded as drift with `manual: true`. |
| `PATCH /api/v1/screens/{id}/live` | open | Unchanged. The changed spec shows as drift (respect) until the next mode request. |
| `PUT /api/v1/room/targets/{t}` `{value, hold?, send?, cause?}` (aa) | open; **`hold` and `send: false` need admin** | `hand_set` (§4.11): `202 {accepted, effects}`. 400 `bad_value`; 404 `unknown_target`; 409 `transition_running`, `device_busy`, `enforced`, `toggle_state_unknown`. `send: false` is aa's `assume`: it sets an edge target's belief to `unconfirmed` without sending (the toggle ball, S10); 409 for a level target. |
| `POST /api/v1/room/plan` `{mode, config?}` (aa) | open; admin with `config` | A dry run that changes nothing; the same rows a real run would write. |
| `GET /api/v1/room/describe` (aa) | open | Targets with value schemas, labels, kinds and groups; subsystems with members and actions; modes resolved; slots; rules; warnings. |
| `PUT /api/v1/room/stored/{slot}/{t}` `{value}` (aa) | admin | Correct an existing entry; keeps its `at`. Never creates one (404). |
| `DELETE /api/v1/room/stored/{slot}[/{t}]` (aa) | admin | Forget an entry or a slot: the next retrieve uses `else`. The way to clear an `orphan`. |
| `POST /api/v1/room/stored/{slot}/{t}/restore` (aa) | admin | Put one stored value back now (`hand_set` with `hold`); cleared when it arrives. The one-tap Restore for a closed slot. |
| `GET/PUT /api/v1/room/config`, `POST …/config/validate`, `GET …/config/history` (aa) | admin | The config editor: validate with JSON-pointer errors, save with `base_revision` (409 `revision_conflict`), history. A save is a reload (§2.1): it moves nothing. |

### 7.4 Events

| Event | Data (additive keys in bold) |
|---|---|
| `mode.transition` `started` | `{id, phase, **from, via, to, source, resumes?, rollback_of?, reapply, plan: [rows]**}` |
| `mode.transition` `step` | `{id, phase, step: row}` (the whole row, with its `id`; v1's shape) |
| `mode.transition` `done` | `{id, phase, **status: ok\|partial\|failed, detail**}` |
| `mode.transition` `cancelled` | `{id, phase}` |
| `status` | Unchanged. Drift, `available`, `stored` and `config` travel in status. |
| `log` | Read failures (once per outage), re-asserts, `overridden`, retries, give-ups and restarts, hand sets, owner corrections, config reloads |
| `config` (aa) | `{revision, previous, source, by, note, error}` when the config changes |

A rollback appears as a new `started` with `source: rollback` and `rollback_of`. A resumed transition appears as a new `started` with a new id and `resumes`.

---

## 8. Test plan

Pure asyncio with fakes: no I/O, no RF, no hub, no TVs. aa's 50 tests (`test_scenarios.py`, 29; `test_describe.py`, 21) are ported first, unchanged except for names: `ball` → `mirror_ball`, `tv.north.display` → `tv.north-screen`, `class` removed. Tests that assert behaviour this spec changes are updated and marked as such; the known ones are listed at the end of this section.

**Fakes** (aa's `fakes.py`, extended):

| Fake | New options |
|---|---|
| `FakeLevel` | `cancel="stop_halfway" \| "keep_moving"` (the default becomes `keep_moving`, like Bloc and the screens controller); **`stale_until_stop`** (reads return the pre-move value with `moving=False` until the motor stops: the Bloc cache, aa NOTES §4); **`retarget`** (False: a command sent mid-travel is ignored until the motor stops); `readback_lag_s`; `reports_moving`; `hang_apply`, `hang_read`; `cancel_cleanup_s`; `swallow_cancel`; `offline`; `fail` (members that never move); `persist` (state survives a simulated restart, for the mask and page fakes) |
| `FakeEdge` | as aa's: discrete or toggle, with a `physical` state; `offline` (raises on apply) |
| `FakeScreens` | a level fake with an entry handshake: `go_after_s`; `"art"` before `go` aborts; after `go` the entry completes and then exits; `moving` during a handshake; `adopt_wait_s`; `give_up` (enters the controller's failed phase: `read()` gives `"failed"`, `apply` gives `failed` with `retry=False`) |
| `FakeSignals` | records `(member, action, args)`; `delay_s`, used to crash mid-send |
| `Clock` | aa's injectable clock |
| `hard_kill(room)` | copies `state.json` mid-run, cancels the task, and restores the copy: a SIGKILL. aa's `restart_mid_transition` test does this. |

**Scenario tests** (named for `room_service/tests/roomstate/test_scenarios.py`):

| Test | Scenario | Checks |
|---|---|---|
| `test_s1_entry_order_nothing_lights_before_blinds` | S1 | command order: mask and page < blinds and TV < plug < screens < ball < Sonos |
| `test_s1_slots_on_disk_before_first_command` | S1 | `state.json` read inside the first `apply` already holds `pre_live` |
| `test_s1_tv_failure_is_partial_not_abort` | S1, S8 | the TV fails, the show goes on, status `partial` |
| `test_s2_live_to_live_no_restore_in_stream_change` | S2 | slots unchanged; one screens apply with the new spec; no exit or entry |
| `test_s3_each_member_back_to_its_own_value` | S3 | ch02 0, ch03 80, ch14 untouched; TV after the screens; `stored` empty |
| `test_s4_interrupt_real_hardware` | S4 | `keep_moving` + `stale_until_stop`: originals restored; slots cleared only after proof; no signals; ball never sent |
| `test_s4_interrupt_before_go_aborts_entry` / `_after_go_completes_then_exits` | S4 | FakeScreens: matched path both ways; the ball never starts |
| `test_s4_redirect_to_other_live_uses_entry_order` | S4, F4 | normal → disco, interrupted → ambient: blinds before screens |
| `test_s4_interrupted_exit_redirect_keeps_exit_order` | S4, F4 | disco → normal, interrupted → piano: ball and DMX before blinds |
| `test_s5_sigkill_resume` / `test_s5_sigterm_resume` | S5 | both resume toward disco under rule 1, under a new id with `resumes`; no store again; nothing fired twice |
| `test_s5_crash_mid_edge_send` | S5, S10 | discrete re-sent once; toggle `unconfirmed` row and `partial` |
| `test_s5_crash_mid_signal_is_at_most_once` | S11 | `delay_s` signal + hard kill: never sent twice; row `unconfirmed` |
| `test_s6_enforce_reasserts_once_then_overridden` | S6 | exactly one re-assert; `overridden` drift; exit retrieves 80 |
| `test_s6_reconcile_apply_is_cancelled_by_request` | S6, F8 | a request during an enforce apply starts within 50 ms |
| `test_s7_hand_move_in_normal_is_stored_next_time` | S7 | aa's test, kept |
| `test_s7_closed_slot_kept_by_next_store_when_untouched` | S7, R6 | closed slot, target unmoved → the next store keeps the original and reopens it |
| `test_s7_closed_slot_replaced_when_moved_by_hand` | S7, F13 | closed slot, target moved → the next store takes the hand position |
| `test_s7_retry_dropped_when_moved_by_hand` | S7, S9 | a reading outside `explained` is accepted; the slot is dropped |
| `test_s7_lingering_slot_dropped_after_off` | S7, G4 | normal → disco → off: TV slot dropped |
| `test_config_rejects_max_age_on_store` | S7 | ConfigError |
| `test_s8_store_from_failed_read_is_stale` | S8 | slot `confidence: stale`, old `at`; subsystem `available: false` |
| `test_s8_slow_tv_runs_parallel_with_blinds` | S8 | TV `change_time_s` within the step; total time ≈ max, not sum |
| `test_s9_hub_offline_aborts_and_rolls_back` | S9 | `failed` + rollback with the exit order; nothing after step 2 ran; reconcile never enforces disco; `rollback_of` kept |
| `test_s9_ball_offline_partial_and_retried` | S9 | backoff schedule; stops after 5 tries; restarts when the RM4 comes back |
| `test_s9_hub_returns_retrieve_retried` | S9 | closed slot cleared on success |
| `test_s9_placeholder_rows_skipped` | S9 | `skipped`, no abort and not partial when unguarded |
| `test_s9_hung_apply_times_out_unconfirmed` / `test_hung_read_marks_stale` | S9, F11 | timeouts |
| `test_s10_toggle_unknown_row_and_assume` | S10 | `unconfirmed` row; `assume` then a correct flip |
| `test_s10_restart_does_not_resend_edges` | S10 | aa's test, kept |
| `test_s11_same_mode_request_fires_nothing` | S11 | disco → disco: no mode or rule signals, no store |
| `test_s11_base_mode_signals_fire_at_family_edge` | S11, G1 | `live.on_enter` once on normal → disco; not on disco → ambient |
| `test_s12_sonos_unmanaged_in_normal_retrieved_once` | S12 | no drift in normal; one retrieve at the end of the episode |
| `test_s12_interrupted_entry_no_exit_signal` | S12, F5 | no `live.on_exit` |
| `test_s13_concurrent_requests_last_wins` | S13, F2 | `gather(request ambient, request normal)` with a 100 ms cancel cleanup: only normal runs; ambient `cancelled` |
| `test_s13_source_recorded` / `test_s13_manual_screen_409_during_transition` / `test_s13_reserved_source_rejected` | S13 | |
| `test_exception_in_run_fails_cleanly` | F10 | mode removed mid-episode; reconcile goes on; `start()` falls back to the default |
| `test_boot_request_before_boot_ran` | F19 | the cancelled record is finalised and emitted |

**Review regressions** (R numbers are §11's):

| Test | Hole | Checks |
|---|---|---|
| `test_r1_return_to_origin_both_directions` | R1 | `stale_until_stop`: normal → disco → normal, and disco → normal → disco, 3 s apart. No `ok` before `earlier_until`; in the second, DMX power is not sent until ch02 is proven at 100; with `retarget=False` the originals are still restored (re-send or retry) |
| `test_r1_restart_between_mark_and_send` | R1 | hard kill after the extended mark is saved: the resumed plan sends, and the slot survives until proof |
| `test_r1_resend_same_value_not_delayed` | R1 | a resume re-sending 100 proves on the first fresh reading |
| `test_r2_unconfirmed_retrieve_is_retried` | R2 | `fail={"ch03"}` (never moves): after `until`, retried with backoff, not accepted; closed slot kept; aa's `a_blind_that_never_arrives_keeps_its_slot_for_a_retry` passes with reconcile running |
| `test_r2_slow_tv_unconfirmed_retried` | R2 | a wake past `change_time_s + 15`: retried while it still reads `art_mode` |
| `test_r3_failed_edge_send_retried` | R3 | RM4 offline: belief `unknown`, value unchanged; not settled by reconcile; settled after it comes back |
| `test_r4_exit_gate_stops_with_blinds_down` | R4 | DMX plug offline on disco → normal: no blind command; `target` set; observe-only; Retry runs the gate first |
| `test_r5_tick_overlapping_request_drops_its_view` | R5 | `hang_read` on blinds during a tick, then request normal: no re-lower after normal completes |
| `test_r5_two_ticks_one_apply` | R5 | two subsystem ticks with work: one `_rtask` at a time; a request cancels all |
| `test_r6_closed_slot_survives_give_up_and_hub_return` | R6 | offline 30 min: 5 tries, `gave_up`; hub back → retried → restored; next disco stores the true original |
| `test_r6_hand_move_after_give_up_is_accepted` | R6 | after give-up the user sets 50; normal → piano leaves 50 |
| `test_r7_hop_privacy_failure_is_partial` | R7 | disco → ambient with a hub write failing: `partial`, scene changed, blinds retried; no rollback |
| `test_r7_reapply_failure_not_observe_only` | R7 | disco re-apply with ch03 failing: `partial`, `target` null, enforce and retries go on |
| `test_r8_skipped_guard_aborts_unless_allowed` | R8 | dry-run blinds: abort; with `allow_skipped`: goes on, `partial`, "guard not enforced" |
| `test_r9_reload_moves_nothing_and_reapply_stores_first` | R9 | aa's reload test, extended: ch05 added to `privacy_blinds` mid-disco → `pending_targets`; re-apply stores then lowers it; normal restores it |
| `test_r9_renamed_slot_kept_as_orphan` / `test_r9_renamed_current_mode` | R9 | entries kept and flagged; a request after renaming the current mode completes; the task's own config is used for its rollback |
| `test_r10_reapply_skips_rule_signals` | R10 | a `{"signal"}` in rule 1: fires on normal → disco and on a hop, not on disco → disco |
| `test_r11_restart_settled_keeps_mask_suspended` | R11 | restart while in disco: the mask reads `suspended`; nothing sent; no drift |
| `test_r12_failed_screen_not_retried_and_reset_on_exit` | R12 | `give_up`: no `set_desired` from reconcile; disco → normal sends `"art"` to it |
| `test_r13_disco_to_piano_pauses_with_rest` | R13 | Sonos paused in the rest step, never playing the radio |
| `test_r13_piano_return_leaves_user_tracks` | R13 | user plays in piano; piano → disco → piano (3 s): no pause sent |
| `test_r14_hand_set_enforced_needs_hold` | R14 | 409 `enforced`; with `hold`: applied, `overridden`, not re-lowered at the next tick |
| `test_r15_taken_over_not_retried_reclaim_restores` | R15 | `retry=False`: no retries; slot kept; a re-request restores |
| `test_r16_normalize_offline` | R16 | Sonos adapter offline at load: config loads; favourite resolved at apply |
| `test_r17_set_inside_family_keeps_slot` | R17 | disco → karaoke (sets volume) → normal: pre-disco volume restored; `set_over_retrieve` warning for a karaoke without store |
| `test_r17_bare_abstract_name_rejected` | R17 | `"to": "live"` → ConfigError |
| `test_r18_return_before_completion_no_on_enter` | R18 | a mode with `on_enter`: X → disco → X within 3 s fires X's `on_enter` once in total |
| `test_r19_room_page_compat` | R19 | `last_transition`, row ids, `phase`; a resumed transition's `started` has a new id |
| `test_r22_rollback_keeps_failure` | R22 | after the rollback: `last_transition.rollback_of` has the failed id, destination and detail; the aborted rows are `cancelled` |

**aa tests that change** (marked in the port): `a_blind_that_never_arrives_keeps_its_slot_for_a_retry` (the belief keeps the reading, so its confidence stays `confirmed` and the mark carries the command); `unreachable_hub_aborts_before_anything_flashes` (a rollback runs, instead of aa's restore of the old desired values); `edge_toggle_ball_never_sends_blind` (the row is `unconfirmed`, not `skipped`); `set_target_validates_and_follows_the_mode_policy` (an enforced target needs `hold`, as aa's code now does).

**Config tests:** the four extends merge rules (rule 1 over a retrieve and over a store); specificity and conflicts; prefix expansion; unknown `@name`; a bare abstract name; a missing `* → *`; `normalize` rejects a typo, including a placeholder with `values`; a retrieve slot that no mode stores; placeholders accepted; `sequence` rejected; the `on_fail` options; abstract modes absent from `GET /modes`; aa's integer-like key and unknown-key checks.

**Status tests:** LIVE_V1 keys present; `last_transition` and `phase` present; rows created at the start with ids and patched in place; ages, never clock times; `json.dumps` round trip.

**Integration (by the integration agent, not unit tests):** gallery ↔ live-test on devtest through the new coordinator; normal ↔ disco with the blinds read for real and run as `dry_run`, under the slice config's `allow_skipped`.

---

## 9. Implementation slice, migration and changes to the reference

### 9.1 The slice (about one day, one builder)

| # | Work | Hours |
|---|---|---|
| 1 | **Port** aa's `contract.py`, `model.py`, `state.py`, `coordinator.py`, `schema.py` and `fakes.py` into `room_service/roomstate/` (a copy; room_service never imports from `experiments/`). Apply §9.3. Port aa's 50 tests plus §8's scenario and review tests into `room_service/tests/roomstate/`. | 4.5–5 |
| 2 | **Adapters** in `room_service/adapters/`: `screens.py` (the shim, 1 h); `sensor_mask.py` with persistence (0.5 h); `page.py` with persistence (0.5 h, if the page track has landed `policy.timed_reload`, otherwise a placeholder); `placeholder.py` for `mirror_ball`, `plug` and `sonos`, with `members` and `values` from device config (0.3 h); `blinds.py`, reading for real with writes in `dry_run` (1 h); `tv.py`, verify-only over IP Control (1–1.5 h); `log.py` from v1 `LogAction` | 4.5–5 |
| 3 | **API and events** (§7): rows with ids, `last_transition` and `phase`, `members`, `stored`, `config`, `GET /modes` additions, `source`, the request lock, the 409s, `PUT /room/targets`. Wire into `service.py` and `api.py`. aa's editor endpoints can follow. | 2 |

**The shipped `config/room/modes.json`** is §2.2's example plus `live-test` (§9.2), with `"allow_skipped": true` on both guarded steps until the blinds' writes and the DMX plug exist. Status then shows "guard not enforced" on every live transition, so nobody mistakes the demo for the real guard.

**The demo at the end of the day:**
- gallery (now `normal`) ↔ `live-test` on devtest, driven from the Room page through the new coordinator;
- normal ↔ disco with the privacy blinds **read** live from the hub, commands shown as `skipped (dry run)`, stored slots visible in status, and placeholders skipped.

**Not in the slice:**
- real blind writes (after the owner-present `targetPosition` test);
- TV writes (owner approval);
- the RM4 Pro ball, Sonos/SoCo, the DMX plug, Hue, Maestro;
- aa's config editor endpoints and history;
- overlays and precedence.

### 9.2 Migration from room_service's v1 coordinator

| v1 (`room_service/coordinator.py`, `subsystems.py`) | New |
|---|---|
| `parse_modes`, `sequence`, `after:`, the `fired` dict, the plan by sequence groups | `roomstate.model` (transition rules) and `roomstate.Coordinator`. `sequence`/`after` are rejected at load with a pointer to `transitions`. |
| `config/room/modes.yaml` (`gallery`, `live-test`) | `config/room/modes.json`, shipped in the slice, with §2.2's modes plus `"live-test": {"label": "Live test (devtest)", "extends": "house", "state": {"screens.devtest": {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}, "profile": "h264-720p30"}}}}`. `gallery` becomes `normal`. **There is no fallback to `modes.yaml`** (R23): it has no `transitions`, which §2.5 requires, and its `unmanaged` keyword is `null` here. `modes.yaml` is deleted when `modes.json` lands. |
| `ScreenSubsystem` (one key per screen; `read()` = desired) | `ScreensAdapter`: members = screen ids; `read()` = what is on glass, or `"failed"` (§5.4) |
| `SensorMaskSubsystem` (`sensor_mask`) | `SensorMaskAdapter`, member `main`, persisted. The selector `sensor_mask` still works, by prefix expansion. |
| `PlaceholderSubsystem`, `KNOWN_PLACEHOLDERS` | `Placeholder` adapters with members and values from device config (`mirror_ball: [main]`, `plug: [dmx_power]`, `sonos: [lounge.volume, lounge.playback]`, `blinds` from `rf/blinds.json` when the adapter is off) |
| `LogAction` (`{log: msg}` actions) | `log` action adapter: `{"signal": "log.info", "args": {"msg": "…"}}` |
| state `coordinator.json` | `state.json` v2 (§3.5); `coordinator.json` is not read. `screens.json` (the controller's own state) is unchanged. |
| `PUT /screens/{id}` straight to the controller | `coordinator.hand_set` under the request lock (§4.11) |
| status `last_transition`, `phase`, step `id` | Kept (§7.1). `started_at`/`finished_at` become ages. |

**LIVE_V1 notes to add (additive, by 2f):**
- §0 Modes row → `config/room/modes.json`;
- §3 the status and row additions in §7, and the `/room/*` routes with their auth labels;
- §3.2 a pointer to this spec.

**Room page** (`src/pages/Room`):
- show `last_transition` with Retry and Back, using `rollback_of` after a rollback;
- a tone for `skipped`, and a warning tone for a `guarded` skipped row;
- members and stored slots under subsystems, with Restore and Forget on a closed slot (admin);
- the `enforced` refusal with a "hold until the next mode change" choice (admin);
- `steps_from` as a preview of the plan.

All of it is optional reading of additive fields.

### 9.3 Changes needed in aa's reference code

For aa, or for the implementer porting it. Line numbers refer to `coordinator.py` and `model.py` at 23:23 / 23:17 on 13 Oct; aa has edited both since (00:21 on 14 Oct), so look for the named symbols. Items marked **R** are from the review.

1. **Request lock** around all of `request_mode`, and pass `tr` into `_run` instead of reading `self.s["transition"]` (`request_mode`, `_run`). F2.
2. **Cancel handler** keeps the record in `transition`, with status `cancelled` and `mode.target` unchanged. `start()` resumes whenever `mode.target` is set, under a new id with `resumes` (R19). Reconcile's guard checks the task, not the presence of a record. F3.
3. **`via`** and from-set rule matching (`plan_steps(from_set, to)`), replacing `from_mode = prev['to']` (`request_mode`; `model.py` `transition_for`). F4.
4. **Abort → `_rollback`**, run in the same task and config; `rollback_of {id, to, detail}` on its record (R22). aa now restores the old desired values and clears `target` instead; the rollback does that and also moves the devices back. F4.
5. **`mode.entered`, `mode.leaving`** and `_edges()`, replacing the unconditional `on_exit(from)` / `on_enter(to)` in `_steps`. A mode leaves `entered` only when the transition completes (R18). Signals then work through families; `on_enter`/`on_exit` are not inherited (aa A6 inherits them; family edges make that unnecessary). F5, G1, F23.
6. **In-flight marks** with `from`, `earlier`, `earlier_until`, written before each level send and extended, never replaced, on a re-target; `_arrived()` is the only arrival test, used by `_refresh` and to check an adapter's `ok` (`_prove`); `_needs_send` returns True for in-flight or moving targets; clear-on-match skips them (`_refresh`, `_apply_sub`, `_needs_send`). F1, **R1**.
7. **Save before each send:** after the edge `unknown` marks, and the `fired` key before `_signal`. F9.
8. **`except Exception`** in `_run`, with `cfg.mode()` inside the try; the task keeps its config; `cfg.modes.get()` in `_matches` and `_edges`; reconcile skips an unknown current mode (aa's `_current_mode()` does); `start()` falls back to the default. F10, **R9**.
9. **`asyncio.wait_for`** on every adapter call (`_refresh`, `_apply_sub`, `_signal`). F11.
10. **`desired.settled` and `desired.retry`** (`explained`, `final`), with retries in reconcile; the belief keeps the last reading on `unconfirmed` and `failed`, and `explained` is taken before the send (**R2**); edges keep their value while `unknown` and count as "there" only when `confirmed`/`unconfirmed` (**R3**); the moved-by-hand check runs before `gave_up`; health recovery restarts give-ups (**R6**). F12.
11. **`_close_slots()` on completion** with the slot-level lifetime (**R17**), the `closed` flag with `explained` and `final`, and orphans (**R9**). `_store` reopens a closed entry whose target hasn't moved and overwrites it otherwise (**R6**); `_desired` drops a closed entry whose target has moved. `max_age_s` is rejected on store entries, and `_store` no longer checks expiry (`_store`; `model.py` `_entry`). F13, G4.
12. **`on_fail: {"abort_on": sel, "rollback"?, "allow_skipped"?}`** in `_parse_step`; abort only when a guarded target isn't `ok`, a skipped guarded target counts (**R8**), and a value the room already held never aborts (**R7**) (`model.py` `_parse_step`; `_run`). F14, **R4**.
13. **`normalize(member, value)` at load**, pure and offline (**R16**), for every set/else value; placeholders check `values` from device config. Unknown subsystems become placeholders instead of ConfigErrors (`model.py` `_check_values`, `_expand`). G5, I-F4. (The per-member map sugar is not needed: **R23**.)
14. **`@name` means a mode and its descendants;** `class` dropped; `@` names validated; a bare abstract name in a rule is an error (aa's `abstract_endpoint` warning becomes a ConfigError, **R17**); a `* → *` rule required (`model.py` `_matches`, `_parse_transition`). F16, G11.
15. **The four extends merge rules,** replacing `state[t] = entry`, with rule 1 as reworded in §2.4 (`model.py` `_resolve`). G2, **R17**.
16. **Specificity precedence;** prefix expansion at dot boundaries; `rest` reserved; `rest` excludes targets named elsewhere in the rule (`model.py` `_expand`; `_plan`). G10.
17. **`signal(member, action, args)`,** parsed as `subsystem[.member].action` (`model.py` `_parse_step`; `_signal`). G11.
18. **Read failures** make beliefs `stale` and mark the subsystem unavailable. `_store` copies the belief's `at` and confidence. Reads run in parallel (`_refresh`, `_current`, `_store`). F7, G9.
19. **Enforce once, then `overridden`.** Reconcile's applies run in a cancellable `_rtasks` set, with the request generation checked after the read and under `_work` (**R5**); aa's `_manual` set is the same idea for hand sets (`reconcile_once`). F8, G13.
20. **An `unconfirmed` row**, "toggle state unknown; not sent", for an unknown toggle; the transition becomes `partial` (`_why_quiet`, `_plan`). F17, **R21**.
21. **Rows in the LIVE_V1 shape** with aa's `id`, written at the start; `last_transition` and `phase` in status; ages; `available` from health (`status`, `_plan`). G6, I-F6, **R19**.
22. **Signal keys** `step:<action>:<n>`; a re-apply skips rule signals (**R10**) (`_plan`). F10, I-F16.
23. **State file v2,** 0600 through `atomic_write_json`, saved on change only, unknown versions moved aside (`state.py` `save`). I-F13.
24. **Fakes:** `FakeLevel(cancel="keep_moving", stale_until_stop, retarget, readback_lag_s, reports_moving, hang_*, cancel_cleanup_s, swallow_cancel, persist)`, `FakeScreens(give_up)` (`fakes.py`). F1, **R1**.
25. **Names:** `ball` → `mirror_ball`, `tv.north.display` → `tv.north-screen`, `sensor_mask.main`.
26. **`example_room.json`** becomes §2.2: the mask first, DMX after the blinds, `abort_on` for the privacy blinds only, the exit gate on DMX power (**R4**), Sonos playback as state, paused as state in piano and off (**R13**). F6, F18.
27. **`Outcome.retry`** in `contract.py`, and `final` handling in reconcile (**R12**, **R15**).
28. **The `ignore` rule** in `_needs_send` for a re-apply or a return (**R13**, **R18**), using `believed.sent_at` and `mode.since`.
29. **`set_target` becomes `hand_set`** (§4.11): aa's refusal codes and `hold` are kept; a hand set settles the target, abandons a closed retrieve, and marks an enforced target `overridden` (**R14**).

aa's suite passes today (50 of 50, at 00:23 on 14 Oct). The tracers' probe scripts reproduce F1–F23 and G1–G14. They are in this session's scratchpad (`trace/`, `tracer-generality/probes/`, `spike/roomstate/tests/test_tracer_probes.py`) and are worth porting as regression tests.

---

## 10. Deferred, and open owner questions

### 10.1 Deferred (each would add a concept, so each waits for a real need)

| Item | Why it waits | Interim |
|---|---|---|
| Overlays (photosafe, guests) | S14: composing them with modes is a new concept | Variants as modes via `extends` (§2.6) |
| Requester precedence and leases (v1's 4 h lapse to off; "the scheduler doesn't override a manual show") | S13: a new concept | The last request wins; `source` is shown |
| Per-member `requires` (a screen live only if its TV is on HDMI; blinds raised only after the lights are off) | S8, R4: step order and the exit gate cover it | Step order; `rollback: false` |
| Quiet hours on retrieve ("never raise the blinds 23:00–07:00") | Q1 | `max_age_s` |
| Aliases for blind labels | Groups and `labels` do the job | Groups, `labels` |
| Pruning slot entries no mode uses any more | Rare, and a rename shouldn't lose values | Kept as `orphan`; `DELETE /room/stored/…` (R9) |
| Restoring a Sonos queue exactly | Needs SoCo's `Snapshot` inside the adapter | The playback context is restored |
| Hue, Maestro, Sonos, plug and ball adapters | Hardware not configured | Placeholders |

### 10.2 Owner questions (each has a default)

| # | Question | Default until answered |
|---|---|---|
| Q1 | A store at 18:00 with the blinds raised, and a show ending at 01:00: raise the blinds at 1 am? | Yes, with `max_age_s: 43200` as the only guard (aa's Q1) |
| Q2 | If a privacy blind fails to lower, abort the show and roll back, or carry on? | Abort and roll back (`abort_on: privacy_blinds`) |
| Q3 | The ball's relay: latched ON/OFF codes, or one toggle code? Its state at first boot? | Latched (edge_discrete). With a toggle, you confirm its state once in the Room page. |
| Q4 | Someone raises a privacy blind during a show: re-lower once (enforce), or just report it (respect)? | Enforce once, then leave it |
| Q5 | After a show, restore what Sonos was playing (state), or only pause it (a signal)? | Restore volume and playback |
| Q6 | Approve an owner-present test of IP Control (or samsungtvws) writes for TV power, Art Mode and input? | Until then the TVs are verify-only: switch to HDMI by hand |
| Q7 | What should Off mean? | TVs to standby, Sonos paused, house values otherwise |
| Q8 | When nothing usable was stored for a TV, leave it on HDMI or put it in standby? | Leave it |
| Q9 | Should any of the other ten blinds come down in live modes? | No: only ch02, ch03 and ch14 are managed |
| Q10 | When a mode aborts, go back automatically, or stay half-way and ask? Now a per-rule choice (`rollback`). | Entering a show: go back automatically. Leaving one: Q12. |
| Q11 | An owner-present Bloc test: `targetPosition` writes, re-targeting mid-travel, STOP mid-travel, real travel times | Needed before blind writes leave `dry_run` |
| Q12 | Leaving a show when DMX power can't be confirmed off: keep the blinds down and wait for you (Retry or Back), or raise them anyway? | Keep them down and wait (`rollback: false` on the exit gate) |
| Q13 | Someone on the Wi-Fi tries to raise a privacy blind from the Room page during a show: allow it, or require the admin "hold"? | Require the admin hold (aa's `enforced` refusal) |

---

## 11. Review log

A review pass traced v1.0 against S1–S14 and reported 31 holes. Several were reported twice by different reviewers; they are merged below (R1–R23). Each was checked against the spec text, aa's code and the code it touches before being fixed or rejected. aa's reference changed while this ran (`coordinator.py`, `model.py`, `NOTES.md` §4b and `EDIT_SURFACES_PROPOSAL.md`, 00:18–00:23 on 14 Oct); the last part of this section reconciles with it.

### 11.1 Holes

| # | Hole (severity) | Verdict | What changed |
|---|---|---|---|
| R1 | **A blind re-targeted to where it started looks arrived at once** (high; two reports). The Bloc cache shows the pre-move value as `stopped` until the motor stops, so the adapter's arrival test and `_refresh`'s mark deletion both pass immediately; the new mark also overwrote the old one, and a restart between mark and send deleted it. Lights on with a blind still rising, or a lost stored value. | **Fixed.** Verified against aa NOTES §4 (cache updates only on stop) and §4.4's code. | Marks are extended, never replaced (`from`, `earlier`, `earlier_until`); one arrival test, `_arrived`, used by `_refresh` and to check every adapter `ok` (`_prove`); `opts.pending` lets an adapter do the same; re-sending the same value isn't delayed. §3.3, §4.4, §5.2 rule 3, §5.4 blinds, S4 (both directions), S5, §8 (`stale_until_stop`, `retarget`). |
| R2 | **`retry.observed` recorded the commanded value**, so a command that timed out was "accepted as moved by someone else" at the first tick: no retries for a blind that never moved or a slow TV, and its closed slot was dropped (high; two reports). | **Fixed.** Verified: `_believe` updated in place before `observed = b.value`. | A level belief never takes a commanded value; only a proof or a reading writes it. `retry.explained` = the pre-move reading, earlier commands and the commanded value; a reading outside it is someone else. `tv.change_time_s` 90. **Not adopted:** "stop retrying and offer Restore" for the case where a person sends a moving blind back to its start: it looks like "never moved", the most common RF failure, which S9 needs retried; a retry that lands costs one re-send, after which changes are drift under the policy. aa's Restore is offered for closed slots anyway. §3.3, §4.4, §4.8, S8. |
| R3 | **Edge targets were never retried**: a failed send wrote the desired value with `unknown`, and reconcile's "got there after all" ignored confidence; `_mark_unsettled` set no retry (medium/high; two reports). | **Fixed.** Verified. | An edge marked `unknown` keeps its old value; "already there" needs `confirmed`/`unconfirmed`; `_mark_unsettled` creates a retry due now. §4.4, §4.8, §4.9, §4.12, S9, S10. |
| R4 | **The exit order ordered but didn't gate**: a failed DMX-off step continued, and the blinds rose with the heads lit (Maestro runs on, v1 §3.7) (medium). | **Fixed** with an option on the existing key: rule 2 guards `plug.dmx_power` with `rollback: false`. Q10 already offered "stay half-way and ask"; it is now per rule. **Not adopted:** `{"hold": sel}` (it needs dependency-ordered retries of the held blinds, which is the deferred `requires`); "an abort in a rule whose from-set is live doesn't roll back" (a direction-specific special case, against steer e). | §2.2, §2.3, §2.4, §4.7, S9, Q12. |
| R5 | **A reconcile tick could overlap a request**: the tick passed its guard, awaited the hub read, then queued a re-assert from the old mode's desired values that ran after the new transition; per-subsystem loops orphaned each other's `_rtask` (medium; two reports). | **Fixed.** Verified. | `_gen` bumped by requests and hand sets; the tick drops its view if the generation changed or anything is busy; `_reconcile_apply` re-checks under `_work`; `_rtasks` is a set, all cancelled. **Not adopted:** holding `_work` across the whole tick on its own, because a tick can still start during `_stop_tasks`' awaits; the counter closes that. §4.1, §4.8. |
| R6 | **Closed slots lost the original, or came back stale** (medium; two reports with opposite fixes): after give-up nothing retried when the hub returned, and the next store overwrote the closed entry with the unmoved, lowered position; and, conversely, `gave_up` was checked before "moved since", so a user's later change was missed and a house → house transition re-retrieved the old value. | **Fixed** by one rule that satisfies both: a closed entry keeps `explained` and is valid while the target reads within it. Valid: retried (restarted on health recovery), used by the next retrieve, kept and reopened by the next store. A reading outside it drops it. The hand-move check runs before `gave_up`. **Not adopted:** "drop on give-up" and "`_desired` treats closed as empty" (they lose the original, which is the first report's failure); "a default `max_age_s` on every retrieve" (validity by reading already covers the staleness that matters, and an age cut would drop valid jobs). | §3.3, §4.4, §4.6, §4.8, S7, S9. |
| R7 | **A privacy failure on a hop or a re-apply aborted, the rollback re-ran rule 1 and failed again**, leaving the room observe-only with enforce and retries stopped; a stale-but-matching blind was also re-sent during a read blip (medium). | **Fixed** by `_held_already`: a guarded value that the last completed mode and every `via` mode already set doesn't abort; it is reported and retried. Re-sending a stale value stays (it is harmless, and trusting a stale "down" on entry would weaken the guard). **Not adopted:** "abort only when from contains a non-live mode" (names a family; `_held_already` is the general form); "clear the target when the failed destination is the current mode" (a re-apply can no longer abort, and a turned-back exit, where it would apply, should stay observe-only with the lights off: R1's mirror case). | §2.3, §2.4, §4.7, S2, S6. |
| R8 | **A skipped guarded target bypassed the guard**: with the blinds `dry_run` or a placeholder, disco ran DMX, screens and the ball with the blinds up, and said `ok` (medium; two reports). | **Fixed.** Verified: skipped targets never entered the batch. | A skipped guarded target counts as not ok; `allow_skipped` lets it go on as `partial`, "guard not enforced"; `guard_inactive` load warning; the shipped slice config sets `allow_skipped` on purpose. `dry_run` is checked after "already there". §2.4, §2.5, §4.3, §4.4, §9.1, S1, S9. |
| R9 | **Config edited mid-episode**: new targets' settled state unspecified; a renamed slot's entries deleted at the next completion; a renamed current mode raised `KeyError` in step 5 and wedged the room; `cfg.mode()` outside the try; the task read the live config (low; two reports). | **Fixed.** | aa's reload semantics (moves nothing; `pending_targets`; a re-apply stores and then sets the new targets); a reload is applied when a running transition ends; the task keeps its config, rollback included; orphans kept; `mode_signals`/`cfg.modes.get`; `cfg.mode()` inside the try; rollback falls back to the default. §2.1, §4.3, §4.6, §4.7. |
| R10 | **Rule signals fired again on a re-apply** (and on hops) (low). | **Fixed** for re-applies (skipped, row "re-apply"); **documented** for hops, which run rule 1 by design. | §4.3, §4.5, S11. |
| R11 | **A restart while settled lost room_service's own in-memory state**: `MaskPusher.suspended` comes back `False` (`room_service/mask.py`), reconcile only reports it under respect, and the held mask is lost; the page policy too (low/medium; two reports). | **Fixed** with a contract rule: an adapter whose state lives in room_service's memory persists it, so `read()` stays truthful. **Not adopted:** "mark those targets unsettled at start" (a new adapter flag, and the first reading would look like a hand move); "re-apply the current mode at every boot" (it would revert real hand changes made while room_service was down: only the adapter can tell amnesia from a hand change). | §2.1, §4.9, §5.2 rule 10, §5.4, S5. |
| R12 | **A screen that failed during a show was retried** (each `set_desired` clears the controller's failed phase and fallbacks, defeating its 3-in-10-min guard), and on exit nothing was sent because `read()` said `"art"` (low). | **Fixed.** Verified in `screens.py` `set_desired`/`believed`. | `Outcome.retry = false` for the controller's failed phase; `read()` returns `"failed"` while it holds an abandoned live request, so the exit sends `"art"`. §5.1, §5.2 rule 4, §5.4. |
| R13 | **disco → piano played the radio for ~60 s** before piano's `on_enter` pause, because house's retrieve runs in rest with the blinds (low). | **Fixed in config** (piano and off hold playback `paused` as state, `ignore`), plus the `ignore` rule so that a return to piano doesn't re-pause the user's music (otherwise this fix would reintroduce R18's complaint as state). | §2.2, §2.3, §4.4, S11, S12. |
| R14 | **A deliberate hand set from the Room page was fought once** by enforce (low). | **Fixed** as aa's code now does: an enforced target is refused with `enforced` unless `hold`, which makes it `overridden` until the next transition. One path (`hand_set`) for every target. | §4.11, §7.3, S6, S13, Q13. |
| R15 | **A Hue retrieve refused by a takeover was retried 5 times**, which is fighting it (low). | **Fixed** by `Outcome.retry = false` ("taken_over"): never retried, slot kept for Reclaim. | §5.2, §5.4 hue. |
| R16 | **`normalize()` needed the network** (a Sonos favourite's URI), so an offline speaker at boot rejected the whole config; `read()` returned a track URI that never matched a playlist favourite; placeholder values were unchecked (low; two reports). | **Fixed.** | `normalize` pure and offline; `{"favourite": name}` canonical, resolved in `apply`; `read` maps the context back; placeholders take `values` from device config. §2.5, §5.2 rule 11, §5.3, §5.4 sonos. |
| R17 | **A live mode that sets a target another live mode stored lost the stored value** (merge rule 1 turned the inherited retrieve into a set, and `_close_slots` deleted the entry); rule 1 contradicted itself about options; a bare abstract name in a rule only warned (medium). | **Fixed**: slot lifetime is per slot (an entry lives while the destination stores into its slot or retrieves it); rule 1 reworded; `set_over_retrieve` warning; bare abstract names are ConfigErrors. **Not adopted:** an implicit store whenever a child sets over an inherited retrieve (magic, and it would make `off` store the TVs and wake them a week later). | §2.4, §2.5, §2.6, §4.6. |
| R18 | **piano → disco → piano re-fired piano's `on_enter`**, because exits were applied to `entered` before the steps (medium). | **Fixed**: a mode stays in `entered` until a transition that leaves it completes; `leaving` stops a second `on_exit`. | §3.3, §4.3, §4.5, S4, S11. |
| R19 | **The Room page wouldn't keep working unchanged**: it reads `mode.last_transition`, row `id` and `phase` (verified in `useRoomConnection.ts` and v1 `coordinator.py` `_public`); a resume under the same id stayed "cancelled" on the page; `GET /modes` changed what `state` meant (medium). | **Fixed.** | `last_transition` kept (no `mode.last`), row `id` (`i<k>`), `phase`; a resumed transition gets a new id with `resumes`; `GET /modes` `state` as written plus `targets`. §3.3, §4.2, §7. |
| R20 | **aa's editor contract and API were missing**: `value_schema`, `actions`, `label(member)`, the `labels` key (rejected as unknown), `describe`/`plan`/`revision`, aa's 21 editor tests; no auth class for the new endpoints; reserved `source` values accepted from the LAN (medium). | **Fixed.** | §2.4 top-level keys; §5.1 (`show()` for row text, so `label(member)` keeps aa's meaning); §7.3 with aa's `/room/*` routes and auth; `assume` admin-gated; 400 `bad_source`; 50 tests ported. |
| R21 | **Load-bearing helpers were undefined or contradictory**: `hand_set` locking and return, `_reconcile_apply`, `_any_not_ok` vs the toggle-unknown row, `_belief`/`_believe`, `_settle(accept)`, row numbering, `occurrence`, `_mark_unsettled`, the 3 s bound (medium). | **Fixed.** | §4.11, §4.12; the toggle-unknown row is `unconfirmed`, so "skipped never counts" holds except for guarded targets (R8). |
| R22 | **A rollback overwrote the failure** in `last_transition`; aborted rows stayed `pending`; S9 claimed closed slots where a stale `at` past `max_age_s` gives `else: leave` (low). | **Fixed.** | `rollback_of {id, to, detail}`; rows `cancelled`; S9 corrected. §4.3, §4.7, S9. |
| R23 | **The `modes.yaml` fallback couldn't load** (no `transitions`; `unmanaged` isn't a value), and the per-member map sugar existed only for it (low). | **Fixed** by dropping both; `modes.json` ships in the slice. | §2.1, §2.4, §9.2. |

### 11.2 Reconciliation with aa's reference (14 Oct, 00:18–00:23)

aa's suite is at 50 tests and passing (29 scenario, 21 editor). Differences, and what this spec does:

| aa | This spec | Why |
|---|---|---|
| An aborted switch restores the old mode's desired values and clears `target` (C1); devices stay where the aborted steps left them | Rollback: a transition back to the old mode, with the exit order; `rollback: false` per rule | The aborted steps may have switched TVs and suspended the mask; the rollback puts them back in a safe order. aa's C1 is the desired-values half of it. |
| `on_enter`/`on_exit` inherit through `extends` (A6); a re-apply fires none (A5) | Not merged; fired at family edges by `entered`; a re-apply fires none | Family edges give base signals to every child without copying, and stop live's signals firing on a hop (S11). A5 agrees. |
| `class` and `@class` | `@name` = a mode and its descendants | §1.3 #14 (removes a concept). |
| `max_age_s` allowed on store | Retrieve only | F13/G4: a store never overwrites an open slot. |
| `set_target(hold)` relaxes enforce to respect and flags `held` | `hand_set(hold)` marks the target `overridden`, `manual: true` | Same effect, existing vocabulary. aa's refusal codes are kept unchanged. |
| `assume`, `stored_edit` (keeps `at`), `stored_forget`, `stored_restore`, `Refused` codes, `_manual` task set | Adopted (§4.11, §7.3); `_manual` merged into `_rtasks` | |
| `reload()` moves nothing; `pending_targets` (F4) | Adopted (§2.1); also applied when a running transition ends, and with orphans kept | R9 |
| Status gives ages, never clock times (C12) | Adopted (§7.1) | LIVE_V1 §0. The Room page falls back to its own clock. |
| Item `id` `i<n>`, `kind`, `reason`, `step` event carries the item (F9) | Adopted (§7.2), alongside the spec's readable `action` text | R19 |
| `/api/v1/room/*` endpoints and auth labels (`EDIT_SURFACES_PROPOSAL.md` §3) | Adopted for the editor calls (§7.3); `PUT /room/targets` replaces this spec's earlier `PUT /targets/{t}/believed` and `DELETE /stored/…` | One set of routes. |
| `status.targets` view | `subsystems.<s>.members` in the slice; either for the editors | The same data. |
| Tombstones on forget rejected (C4) | Agreed: forgetting means `else` | |
| NOTES §4: "a late cache update shows the old position, which doesn't match, so it can't be mistaken for arrival" | True only when the target differs from the old position | R1: a blind sent back to where it started is the exception. |
| `retry` and closed-slot handling, edge `unknown` value, `Outcome.retry`, adapter persistence | Not in aa's code yet | §9.3 items 10, 11, 27–29. |
