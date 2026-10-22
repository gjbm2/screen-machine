# Room-state edit surfaces: proposal

**For:** screen-machine-2f, who owns the room_service endpoints and the Room page editors. **From:** screen-machine-aa, 14 Oct 2026, about 01:00.
**Status:** this is input to 2f's design, not a spec. It came from a four-agent pass (a survey of the existing Room page and room_service, a design, a critique and a revision), and screen-machine-aa reviewed it against the reference model.

## How it was checked, and what has changed in the reference since

- **Citations.** The survey's file and line citations point at today's `src/pages/Room/*` and `room_service/*`; spot-checks matched. The auth model in §1 is the owner's ruling in `LIVE_V1.md` §3: operating is open, editing needs the admin token.
- **Real bugs fixed.** The critique found real bugs in the reference model. These are now fixed in `experiments/rf-spike/roomstate/` and covered by tests (56 pass, pure asyncio with fakes):

  | Ref | Fix |
  |---|---|
  | C1 | An **aborted switch now puts the old mode's desired values back** and clears `mode.target`. Before, Disco's "blinds lowered, enforce" stayed in charge while the room was still in Normal, so the next reconcile would have lowered the blinds. A regression test covers it. |
  | F3 | **The revision now includes key order.** Later entries win, so a reordering is a real change; before, a conflicting save went undetected. |
  | C12 | **`status()` gives ages, not clock times:** `age_s`, `edited_age_s`, `expires_in_s` (LIVE_V1 §0). |
  | F1, F2 | **`room_config_schema()` had a `oneOf` that rejected every verb entry.** It is fixed, and `opts` is allowed on retrieve. jsonschema checks it against the example. |
  | A5 | **Re-applying the current mode fires no on_exit or on_enter signals.** |
  | F4 | **`reload()` moves nothing.** Desired values stay, policies only relax, targets the new config no longer manages are dropped, and `pending_targets` lists what the next switch or a re-apply would change (`status.config`). |
  | F5 | **The current mode is guarded** in `set_target` and reconcile (`_current_mode()`). |
  | F6 | **A mode switch cancels a `set_target` still running**, which returns `failed: superseded by a mode switch`. |
  | F7 | **A toggle relay in an unknown state** is refused with `toggle_state_unknown`. `assume(target, value, cause)` sets its state without sending: "Tell the room what it's doing". |
  | — | **An enforced target is refused with `enforced`** unless `hold=True`, which relaxes it to respect until the next switch (`held` in status). |
  | — | **Refusals carry a code:** `Refused.code` is `transition_running`, `device_busy`, `enforced`, `toggle_state_unknown` or `unknown_stored`. |
  | C5 | **`stored_edit` edits existing entries only.** It keeps `at`, so the expiry doesn't move, and adds `edited_at`. `stored_restore(slot, target, cause)` puts one value back now. |
  | F9 | **Plan and transition items** gain `id` (`i<n>`, identical in a plan and in the run it previews), `kind`, `reason`, and `on_fail` on target items. The `step` event carries the item. `plan(mode, config=draft)` previews a draft without touching the live config. |
  | A6 | **`on_enter` and `on_exit` inherit through `extends`** unless the mode has its own. |
  | C13 | **Integer-like mode, group and selector keys are rejected,** because JavaScript reorders them. |
  | — | **Unknown keys** are reported with a pointer at the top level, per mode and per rule. |

- **New in `describe()`:**
  - `default` and `classes`;
  - per subsystem: `available`, `poll_s`, `change_time_s`;
  - per entry: `selector`; per mode: `order`, `on_enter` and `on_exit`;
  - `slots {stored_by, retrieved_by}`, `rules`, and `warnings`, from the new `RoomConfig.lint()`.
- **`RoomConfig.lint()`** returns warnings with codes: `slot_never_retrieved`, `slot_never_stored`, `rule_unused` and `abstract_endpoint`. **`RoomConfig.rules()`** gives the from → to matrix, including first boot (`from: null`).
- **`x-ui.ends`** is added: `range_schema(..., ends=("Raised", "Lowered"))`. `BlocBlinds` uses it.

**Not done in the reference.** Some of these are yours (room_service or page), some are later, and some I disagree with:

- **Yours:**
  - auth tagging, and Host/Origin checks on the new routes;
  - the config file store: order-preserving write, backups and history, the mtime watch, last-good on a bad file;
  - the `config` event;
  - `status.targets` with `manual` and `last`;
  - catalog checks (`scene_unknown`) and `placeholder_target`/`device_offline` warnings;
  - migrating `modes.yaml`.
- **Later:**
  - the delay `run` option and an `after` step;
  - a snapshot of each mode at entry. A deleted or renamed current mode is guarded rather than snapshotted;
  - a per-target reconcile lease after a manual move. Today the device lock covers the move itself.
- **Disagree: tombstones on forget (C4).** If a value is forgotten during a live mode, the next live→live hop stores the current, lowered position, and Normal then restores "lowered". For the blinds that is the same as `else: leave`, which is what forgetting means. One mechanism fewer.

Everything else below is as the revision produced it.

---

Line numbers were taken from the tree on 14 Oct at about 00:06. The reference files (`experiments/rf-spike/roomstate/*`) and `room_service/*` are still being edited, so each citation gives the symbol name as well as the line. "Ref" means the reference model. "v1" means what room_service runs today.

## 1. Summary

- **Operating stays open and editing needs the admin token.** This follows the owner's ruling in `docs/av/contracts/LIVE_V1.md:335-355`.
  - The Control tab works with no token: switching modes, setting devices by hand, putting things back, and previewing a plan.
  - A **🔒 Unlock editing** button opens `TokenPrompt` only when someone taps an admin control. Admin controls are:
    - the Setup tab;
    - editing remembered values;
    - "Hold until the next switch";
    - "Tell the room what it's doing";
    - Save, with or without "apply now".
- **`/room` gets two tabs: Control and Setup.**
  - **Control** acts on the live room. It holds the ModeSwitcher, a new Room now panel, Remembered values, Last switch and the screen cards.
  - **Setup** edits a draft of `config/room/room_state.json`. Nothing moves until Save. Save moves nothing unless the user picks "now".
- **Every value widget comes from adapter schemas.** Values use `Adapter.value_schema` and signal arguments use `Adapter.actions[*].schema`, both rendered by one `ValueField` built from ParamForm's `Field`.
  - Every edit addresses one draft by JSON pointer.
  - The server's `validate` is the authority. It returns the resolved modes, which rule wins for each pair of modes, coded warnings, and what a save would change in the room now.
- **Every contract change is additive.** These v1 fields keep their shape: `status.subsystems`, `phase`, `last_transition`, the `/modes` shape and the v1 drift fields. New data goes in:
  - `status.targets`, `status.stored` and `status.config`;
  - `outcome` and step `id`s;
  - a `config` event;
  - `/api/v1/room/*` routes.
- **Phase 1 surfaces:**
  - Room now, Remembered values, Last switch, the abort banner, PlanView and ModeSwitcher fixes.
  - A mode editor that edits state rows.
  - A whole-config JSON tab with problems placed by pointer.
  - A Save dialog with a diff and conflict handling.

  Transitions are shown read-only, with a rule tester, and edited in the JSON tab. **Prerequisite:** room_service has adopted the reference coordinator with v1-compatible status (about a day for 2f; see A8).

---

## 2. Surfaces

### 2.0 Shell and auth

```
┌ Room · 192.168.1.205:3001 ──────── ● events live ── Refresh  Server   [🔒 Unlock editing] ┐
│ [ Control ]  [ Setup 🔒 ]                                                                 │
└───────────────────────────────────────────────────────────────────────────────────────────┘
 unlocked:   [ Control ]  [ Setup ● 3 unsaved ]                         🔓 editing · [Lock]
```

- **Page gate.** `connected` (`index.tsx:58-59`) stops depending on `authRequired`.
  - `useRoomConnection.ts:157` and `:241` set `authRequired` only when an admin call gets a 401, or when `/status` itself returns 401. That second case means an old server, which keeps today's prompt.
  - The token stays under `sm_room_token`. `isAdmin` is true when a token is present and no admin call has returned 401.
- **Tabs.** Use `src/components/ui/tabs.tsx` and keep the chosen tab in `?tab=setup`.
- **Phone.** Each tab has one sticky action bar. Setup's Save bar never stacks on the sticky bar in `LiveControls.tsx:463`.
- **Reused:** `ConnectionBar.tsx`, which gains a lock slot; `TokenPrompt.tsx`, opened on demand from `index.tsx:102`; `bits.tsx` `Pill` (:7).

### 2.1 Control › Room now (open)

```
┌ ROOM NOW ───────────────────── LIVE ROOM · changes here move things ─ 1 drifted [Put all back] ┐
│ Disco (disco) · entered 3 min ago · settings rev a1b2                                          │
│ ▾ Privacy blinds (3)   ✓ Lowered          Disco keeps these lowered       [Raise until next switch 🔒]│
│     Lounge left   blinds.ch02  ✓ 100 %                                                         │
│     Lounge right  blinds.ch03  ✓  40 %  ⚠ moved by hand 3 min ago · lowered again at next check │
│     Bay           blinds.ch14  sent 12 s ago · the hub reports when it stops (~50–60 s)        │
│ ▾ TV display   North ✓ Kiosk (HDMI)  want Kiosk · respect · from live         [Art|Kiosk|Off]  │
│                South ~ Off (sent, no readback) · not managed by Disco         [Art|Kiosk|Off]  │
│ ▾ Sonos lounge Volume ✓ 30 · Disco: 60 (respect: left alone)   quiet ━━●━━━━━ loud  30         │
│                [Put back 60]   [Make 30 Disco's volume 🔒]                                      │
│ ▾ Plugs        DMX power ✓ On · want On                                         [Off|On]       │
│ ▾ Mirror ball  ? unknown · toggle relay: the room won't send without knowing                    │
│                Tell the room what it's doing:  [It's stopped 🔒]  [It's spinning 🔒]             │
│ ▾ Screens      devtest ✓ live fractal-zoom · north ✓ art · south ✓ art          ↓ screen cards │
│ ▸ Other blinds (10) · all where they should be                                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
 390 px: two-line rows      │ Sonos volume            │
                            │  ✓ 30 · Disco wants 60  │
                            │  quiet ━━●━━━━ loud     │
                            │  [Put back 60]          │
```

**Data.** Rows come from `status.targets.<t>` (new) and `describe.targets.<t>` (label, schema, groups).
- Groups come first, and a member in a group appears only there.
- Other members are listed by subsystem. A subsystem with more than 6 members, all where they should be, starts collapsed.
- `screens.*` is a one-line summary that links to the ScreenCards.

**Formatting.** Values are formatted from the schema: `x-ui.labels`, `unit`, and `x-ui.ends` for blinds, where 0 is raised. Confidence shows as ✓ confirmed, ~ unconfirmed or ? unknown. Times are relative only, from `age_s`; server clock times never cross hosts (C12).

**The active mode's policy decides which control appears:**
- **Not managed, or `ignore`:** a ValueField that sends.
- **`respect`:** a ValueField that sends, with the line "kept; shown as drift until the next switch".
- **`enforce`:** no Set control. The row says "Disco keeps these lowered". An admin gets **[Raise until next switch 🔒]**, which sends `hold: true`. The server also refuses an open set on an enforced target with 409 `enforced`, which removes the "Set anyway" trap.

**Sending:**
- Discrete controls send on tap: preset chips, and a toggle group for enums of four options or fewer.
- A slider or number box shows a pending value ("40 % · not sent"). It sends on release (`onValueCommit`) or on Enter.
- A group's "all" chips appear only when every member has the same schema. One `PUT /room/targets {values:{…}}` carries the whole group.
- **Put back** and **Put all back** send the desired values through the same endpoint. They are open.

**Feedback while a device moves** comes from elapsed time in `targets.<t>.manual`: "sent 12 s ago, ~50–60 s". It never claims "moving" without a readback.

**When controls are disabled.** All controls are disabled during a transition, with the reason "Transition running". If the device lock is held, the reason is "device busy", from 409 `device_busy`.

**Toggle relay in an unknown state** (F7). The Set controls are replaced by "Tell the room what it's doing", which sends `send: false`. This is admin.

**"Make 30 Disco's volume 🔒"** appears after a manual change to a target that the current mode sets.
- It does `GET /room/config`, then `setAt(/modes/<current>/state/<target>)`, keeping the verb. A store+set stays a store+set.
- It saves with `apply: "now"`. The room is already at that value, so nothing moves.

**Reused:**
- `ParamForm.tsx` `Field` (:86), to be exported.
- `SliderControl` (:172), which needs `onValueCommit` next to `onValueChange` at :187.
- `NumberControl` (:202), which already commits on Enter or blur.
- `SelectControl` (:290). Its option text at :306 becomes `x-ui.labels[o] ?? String(o)`.
- `ui/toggle-group.tsx`.
- `bits.tsx`: `SectionTitle` (:73), `Notice` (:82), `Pill` (:7).
- `tones.ts`.

### 2.2 Control › Remembered values, Last switch, abort banner

```
┌ REMEMBERED · pre_live ───────────── kept from before the first live mode ───── 🔒 to edit ┐
│ from Normal · 3 h 38 min ago · Normal puts these back · expires in 8 h 22 min              │
│   Lounge left   blinds.ch02   0 %  confirmed                         [Edit 🔒] [Forget 🔒] │
│   Lounge right  blinds.ch03  80 %  confirmed · edited 2 min ago      [Edit 🔒] [Forget 🔒] │
│   Bay           blinds.ch14   —   not remembered: position unknown when Disco started      │
│   Sonos volume  25           confirmed                               [Edit 🔒] [Forget 🔒] │
│ ⚠ old_slot · 1 value · no mode puts it back (orphaned by rev c3d4)              [Forget 🔒] │
└────────────────────────────────────────────────────────────────────────────────────────────┘
┌ LAST SWITCH · Disco → Normal · partial · 4 min ago ────────────────────────────────────────┐
│ NOT DONE (2)                                                                                │
│   Lounge right ch03  put back 80 % failed: no arrival · still remembered  [Put back now 🔒] │
│   Bay ch14           nothing remembered → left where it is                                  │
│ DONE (5)  ▸ 6 already right                                                                 │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
⛔ Disco stopped 21:40: Privacy blinds failed (hub unreachable). The room is still in Normal;
   nothing after the blinds ran.                      [Retry Disco]  [Put the room back to Normal]
```

**Remembered values** (`status.stored.<slot>.<target>`, which carries `age_s` and `expires_in_s`):
- **Edit.** A popover with a ValueField, then `PUT /room/stored/{slot}/{target}`. Only existing entries can be edited. The edit keeps `at` and `from_mode` and shows "edited N ago".
- **Forget.** The confirm text says what happens next:
  - outside a live mode: "Normal will leave this blind where it is";
  - in a live mode: "The room won't remember a new position for this blind until Normal". In this case the server writes a tombstone.
- **Wording.** "Remember", "Put back" and "Remembered values" everywhere. "Slot" always comes with a gloss.

**Last switch** reads `status.mode.last_transition` (kept, per A3).
- "Not done" lists failed items, plus skipped items with reason codes `nothing_stored`, `stale`, `unknown_state` or `aborted`.
- Items with reason `already` fold under "N already right".
- **Put back now** sends `POST /room/stored/{slot}/{target}/restore`.

**Abort banner.** It shows when `last_transition.outcome == "failed"` and `mode.current` is still the old mode.
- **Retry** sends `PUT /mode {mode: disco}`.
- **Put the room back to Normal** sends `PUT /mode {mode: normal}`. This is a same-mode re-apply with no signals. It reverses what step 1 changed by putting back the remembered values.
- Both buttons are open.

**Reused:** PlanView (§2.3), `bits.tsx` `Notice` (:82), `ui/popover.tsx`, `ui/alert-dialog.tsx`.

### 2.3 Control › ModeSwitcher and PlanView

```
┌ MODE ───────────────────────────────────────────────────────────────── current Normal ┐
│ [ Normal ✓ ]          [ Live 👁 ]                    [ Disco 👁 ]                      │
│  blinds put back·TV Art   blinds ↓·TV Kiosk·screens live   blinds ↓·TV Kiosk·ball spinning·DMX on│
│ ⓘ Disco runs on rev a1b2 settings; saved rev c3d4 changes DMX power  [Preview re-apply]   │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌ If you switch to Disco now ─ from Normal · rule 1 (* → @live) · rev a1b2 ─ [Switch to Disco] ┐
│ First · remember as pre_live: Lounge left 0 % · Bay 100 % · TV North Art · volume 25          │
│         kept: Lounge right already remembered (80 %, 12 min ago)                              │
│ 1 ⛔ stops the switch if any fail                                                              │
│      Lounge left → Lowered · Lounge right → Lowered · TV North → Kiosk (HDMI) · DMX → On       │
│ 2    wait 2 s  (skipped if step 1 changed nothing)                                            │
│ 3    screens.devtest → live fractal-zoom   ⚠ north-screen: live disabled in room.yaml         │
│ 4    rest · Sonos volume → 60 (ramp 5 s)                                                      │
│ 5    on entering Disco · Sonos · Play {playlist: disco}                                       │
│ NOT DONE (1)  Mirror ball → Spinning: toggle relay in an unknown state     ▸ 3 already right  │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**ModeSwitcher:**
- **👁** sends `POST /room/plan {mode}` against the saved config, with a refresh. It is open. Tapping the tile itself still switches in one tap.
- **Tap the current mode** to re-apply it. On_exit and on_enter don't fire (A5).
- **Mode list.** Read from `describe.modes`. Abstract modes are hidden; `ModeSwitcher.tsx:126` lists them today. Tiles keep the order of the `modes` keys.
- **Tile summaries** come from the resolved `entries`, replacing `summariseState` (`ModeSwitcher.tsx:15`).
- **"No adapter yet"** still reads the v1 `subsystems` (:91, :211), which is unchanged.
- **Re-apply notice.** It shows when `status.config.pending_targets` is non-empty, i.e. the active mode was entered under an older rev.

**PlanView:**
- It renders the 👁 preview, the running transition and Last switch.
- Items are grouped by `step` number. Section names come from `origin` (on_exit, rule step, rest, on_enter). ⛔ comes from `on_fail`.
- Skipped items split into "Not done" and "Already right" by reason code.
- **Live patching** keeps matching by `id` (`useRoomConnection.ts:362`), so ref plan items need `id`s.

**Reused:** the step-list markup in `ModeSwitcher.tsx:167-205`, `tones.ts` `stepTone`, and `patchSteps` (`useRoomConnection.ts:362`).

### 2.4 Setup › Modes (admin)

```
┌ Setup · DRAFT · nothing moves until you Save ─── base rev a1b2 · 3 changes · ✓ valid [Discard][Save…] ┐
├─────────────────────────────┬─────────────────────────────────────────────────────────────────────────┤
│ MODES  (= switcher order)   │ Disco (disco) · extends live · class live (inherited)  [👁 Preview] [⋯]   │
│  ★ Normal (normal)      ↑↓  │ Label [Disco    ]  Extends [live ▾]  Class [+ ]  ☐ Base only (abstract) │
│  All live modes (live)      │ Entering uses rule 1 (* → @live), shared with Live (ambient) ↗           │
│   ├ Live (ambient)      ↑↓  │ ── STATE · later rows win for targets they share ─────── [+ Add target ▾]│
│   └ Disco (disco) ●     ↑↓  │   Privacy blinds (3)  from live · remember as pre_live, then Lowered ·   │
│ [+ New mode ▾]              │                       keeps it there (enforce)               [Override] │
│                             │   TV (2)              from live · remember as pre_live, then Kiosk       │
│ TRANSITIONS (2)  read-only  │   Screens (3)         from live · live fractal-zoom          [Override] │
│ JSON  ⚠ 1                   │ ● DMX power           Set [Off|On]                        ↑↓ [↺ Revert] │
│                             │ ● Mirror ball         Set [Stopped|Spinning]  ⓘ adapter not installed yet│
│                             │ ● Sonos volume        remember as pre_live, then 60 · ramp 5 s   ↑↓ [▾] │
│                             │ ── ON EXIT (runs first) · own ──── Sonos · Pause        [Edit in JSON] │
│                             │ ── ON ENTER (runs last) · own ──── Sonos · Play {playlist: disco} [JSON]│
└─────────────────────────────┴─────────────────────────────────────────────────────────────────────────┘
 Row opened:
 ● Privacy blinds → Lounge left, Lounge right, Bay                own · overrides live  [↺ Revert to inherited]
   Does     ( ) Set  (•) Remember, then set  ( ) Remember only  ( ) Put back  ( ) Not managed
   Remember as [pre_live ▾]   Put back in: [Normal ✓] [+ mode]   keeps the position from before the first live mode
   Value    Raised ━━━━━━━━━━━━● Lowered  100 %     [Raised] [Lowered]
   Policy   [Keep it there (enforce) ▾]   if moved by hand, the room puts it back within ~15 s
   Expires  [     ] h (empty: never)       Options: none for blinds
   ⚠ Lounge right: 120 is above maximum 100                       /modes/disco/state/privacy_blinds/set
```

**The draft** lives in `setup/useConfigDraft.ts` as `{base, baseRevision, draft}`.
- `setAt` and `moveKey` keep key order. Order matters, because later selectors win (F3).
- The draft is backed up to localStorage, keyed by `(baseUrl, baseRevision)`, behind try/catch.
- `POST /room/config/validate` runs 400 ms after the last edit. It fills `errors`, `warnings`, `describe`, `rules` and `affects_now`.

**Mode list:**
- **Tree.** Built from `extends`. Each mode shows its key and label. The abstract base reads "All live modes (base)". ★ marks the default.
- **↑↓** moves keys in `modes`, which sets the switcher order.
- **[+ New mode ▾]** offers three choices:
  - **Live mode:** `{label, extends: "live"}`. The key is a slug of the label, editable until the first save.
  - **Blank.**
  - **Copy of selected.**
- **Delete** is refused while a child extends the mode. It lists the transition references it would leave behind.
- **The extends picker** leaves out the mode itself and its descendants, so there are no cycles.
- **Abstract** is disabled for the default mode.

**State rows:**
- **Own rows** follow file order, with ↑↓.
- **Inherited rows** are grey with "from ‹mode›" and **[Override]**, which copies the resolved entry in as an own row.
- **Partial overrides** show as "ch03 overridden below", using `entries[t].selector` (F11).

**Verb → JSON:**
| UI verb | JSON written |
|---|---|
| Set | `v`, or `{set: v, policy?, max_age_s?, opts?}` |
| Remember, then set | `{store, set, …}` |
| Remember only | `{store}` |
| Put back | `{retrieve, else: "leave"\|v, policy?, max_age_s?}` |
| Not managed | `null`, offered only when an ancestor manages the target. Otherwise the action is "Remove row". |

**Row details:**
- **Policy** always shows the effective value, e.g. "respect (default for Set)" or "ignore (default for Put back)".
- **Value widget.** A ValueField from the schema shared by every expanded target. If the schemas differ, a JSON box appears with the hint "use a narrower selector".
- **Put back in: [Normal ✓]** writes a matching `{retrieve: slot}` row, with the same selector, into the chosen mode. `slot_never_retrieved` and `slot_never_stored` are warnings that must be acknowledged before Save.
- **Errors** appear on the row whose pointer prefix matches `/modes/<m>/state/<sel>`.
- **Add target ▾** opens `SelectorPicker`, which lists:
  - groups;
  - subsystems, e.g. "All TV (2)";
  - targets, by label.

  It also carries a help line: "Sonos playback (held state) vs Sonos · Play (one-shot signal)".
- **Screens value (phase 1):** `[Art | Live]`, a scene-preset select from the catalog, and a JSON box.
- **on_enter / on_exit** are read-only in phase 1, marked own or inherited. "Edit in JSON" jumps to their pointer.

**Reused:**
- `ParamForm.tsx` `Field` (:86). Its blue dot and reset now mean "own row, overrides the parent / revert to inherited".
- `ui/radio-group.tsx`, `ui/select.tsx`.
- `ui/command.tsx` with `ui/popover.tsx` for SelectorPicker.
- `catalog.ts` presets.

### 2.5 Setup › Transitions (read-only in phase 1)

```
┌ TRANSITIONS · first match wins ───────────────────────────────────────────── [Edit in JSON] ┐
│ Which rule runs?  from [Normal ▾]  to [Disco ▾]  →  rule 1 (* → @live)                         │
│   from \ to    Normal     Live       Disco                                                     │
│   first boot   –          1          1           – = no rule: all changes at once              │
│   Normal       re-apply   1          1                                                         │
│   Live         2          re-apply   1                                                         │
│   Disco        2          1          re-apply                                                  │
│ 1  * → @live   6 pairs                                                                         │
│    ⛔ Privacy blinds · TV · DMX power  (stops the switch if any fail)                           │
│    wait 2 s  (skipped if the step above changed nothing)                                       │
│    Screens → Mirror ball → everything else                                                     │
│ 2  @live → *   2 pairs     Mirror ball → DMX power → Screens → everything else                 │
│ ⚠ 3  * → live  never used · 'live' is a base mode; did you mean '@live'?                       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Source.** The matrix comes from `validate().rules`, a list of `{from, to, rule}` that includes `from: null` (first boot).
  - Clicking a cell highlights the winning rule.
  - Below the `md` breakpoint the matrix becomes the tester plus a list.
- **Delay cards** quote the delay's `run` setting.
- **"Edit in JSON"** jumps to `/transitions/<i>`.

### 2.6 Setup › JSON

```
┌ JSON · whole config ──────────────── ✓ parses · ⚠ 1 problem · 1 warning ───── [Format] [Copy] ┐
│ 29    "disco": {                                    │ PROBLEMS                                  │
│ 30      "label": "Disco",                           │ ⚠ Disco · Privacy blinds · value          │
│ 31      "state": {"privacy_blinds": {"store":       │   Lounge right: 120 is above maximum 100  │
│ 32          "pre_live", "set": 120}},    ◀          │   [Select in text]  [Show in form]        │
│                                                     │ WARNINGS (acknowledge before Save)        │
│                                                     │ ☐ Rule 3 · never used                     │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **The text area** is a monospace `ui/textarea.tsx`. It re-parses after a 500 ms pause.
- **Parse errors** show as line:col. While the text doesn't parse, the form tabs are locked.
- **Placing problems.** About 60 lines of token scanner map a pointer to text offsets for `setSelectionRange`. There is no new dependency.
- **Show in form** routes `/modes/<m>/state/<sel>` to the matching row, and `/transitions/<i>` to the rule card.
- **Labels.** `describePointer()` gives the label ("Disco · Privacy blinds · value") for the problem list, the diff and the conflict dialog.
- **No Ajv in the client in phase 1.** The server is the authority.
- **Reused:** the debounce and form/raw sync idea from `src/components/scheduler/SchemaEditModal.tsx:650-698`, and the JSON textarea from `VarsRegistryCard.tsx:521-532`.

### 2.7 Save, conflict, changed elsewhere

```
┌ Save room settings ──────────────────────────────────────────────────────────────────┐
│ Changes (3) since rev a1b2                                                            │
│   Disco · DMX power               On → Off                                            │
│   Disco · Sonos volume · policy   respect → ignore                                    │
│   Normal · Privacy blinds         + Put back from pre_live                            │
│ Disco is running. This save changes:                                                  │
│   now           Sonos volume: stops being watched     (relaxations apply at once)     │
│   next switch   DMX power wants Off                                                   │
│ When should values apply?                                                             │
│   (•) At the next switch: nothing moves now                                           │
│   ( ) Now: re-apply Disco (signals don't fire again)        ▸ Preview what moves      │
│ ☑ I've read 1 warning (rule 3 never used)                                             │
│ Note [ DMX off in Disco                ]                         [Cancel]  [Save]     │
└───────────────────────────────────────────────────────────────────────────────────────┘
 409:     Settings changed elsewhere · rev e5f6 · "ball delay" · 2 min ago
          Their changes: Rule 1 · step 2 · wait 2 s → 3 s     [Discard mine, load theirs] [Overwrite theirs]
 event:   clean draft → reloads quietly, Notice "Updated to rev e5f6"
 file:    room_state.json changed outside the page (rev e5f6)   [Show changes] [Load rev a1b2 into the draft]
```

- **The diff** is a client-side `diffConfig(base, draft)`, labelled with `describePointer`. Arrays are compared whole; a same-length permutation shows as "steps reordered".
- **"Now / next switch"** comes from the server's `affects_now`.
- **Preview** sends `POST /room/plan {mode: current, config: draft}`.
- **Save is disabled** while any of these holds:
  - there are errors;
  - warnings are unacknowledged;
  - a transition is running ("Wait for the switch to finish").
- **Success** shows an inline `Notice`, never a toast.
- **Reused:** `ui/dialog.tsx`, `ui/radio-group.tsx`, `bits.tsx` `Notice` (:82).

---

## 3. Endpoints (additive to LIVE_V1 §3)

**Auth labels.**
- **open:** no token needed. The Host and Origin checks still apply, including on the new routes.
- **admin:** `Authorization: Bearer <room token>`. A missing token gives `401 unauthorized`, as today (`room_service/api.py` `auth_mw`, :83-87).

**Errors** keep the shape `{error, detail}`. Validation errors add `errors: [{path, message, code?}]`.

| Method & path | Auth | Request | 2xx | Errors |
|---|---|---|---|---|
| `GET /api/v1/room/describe[?schema=1]` | open | n/a | describe (below) | none |
| `POST /api/v1/room/plan` | open; **admin if `config` is given** | `{mode, config?}` | `{from, to, rule: {index, from, to}\|null, revision, draft, steps: [item], summary: {stores, sets, retrieves, signals, delays, skipped}}` | 404 `unknown_mode` (also for abstract modes); 400 `invalid_config` + errors |
| `PUT /api/v1/room/targets` (alias `/room/targets/{t} {value}`) | open; **`hold` and `send:false` need admin** | `{values: {t: v}, hold?, send?, cause?}` | 202 `{accepted, effects: {t: "drift"\|"enforced_back"\|"held"\|"none"\|"believed_only"}}` | 400 `bad_value` + errors; 404 `unknown_target`; 409 `transition_running` \| `device_busy` \| `enforced` \| `toggle_state_unknown` |
| `GET /api/v1/room/config` | admin | n/a | `{config, revision, path, saved_age_s, disk_error}` | none |
| `POST /api/v1/room/config/validate` | admin | `{config}` | `{ok, revision, errors, warnings: [{path, code, message}], describe, rules: [{from, to, rule}], affects_now: {now: [...], next_switch: [...]}, orphaned_slots}` | 400 `bad_json` |
| `PUT /api/v1/room/config` | admin | `{config, base_revision, apply: "next_switch"\|"now", acknowledged: [code], note?}` | 200 `{revision, previous, backup, applied: {now, next_switch}, transition_id}` | 400 `invalid_config` + errors; 409 `revision_conflict {current_revision}` \| `warnings_unacknowledged` \| `transition_running`; 428 `revision_required`; 403 `config_edit_disabled` |
| `GET /api/v1/room/config/history[/{rev}]` | admin | n/a | `{entries: [{revision, age_s, note, by}]}` / `{config, revision, note}` | 404 |
| `PUT /api/v1/room/stored/{slot}/{t}` | admin | `{value}` | 200 `{slot, target, value, old}` | 404 `unknown_slot`/`unknown_target` (never creates an entry); 400 `bad_value`; 409 `transition_running`/`device_busy` |
| `DELETE /api/v1/room/stored/{slot}/{t}` | admin | n/a | 200 `{slot, target, old, tombstone}` | same |
| `DELETE /api/v1/room/stored/{slot}` | admin | n/a | 200 `{slot, old}` | same |
| `POST /api/v1/room/stored/{slot}/{t}/restore` | admin | `{}` | 202 `{target, value}` | 404; 409 |
| `PUT /api/v1/mode` (changed) | open | `{mode}` | 202 `{transition_id, reapply}`. A same-mode request is an implicit re-apply with no signals. | as today |
| `GET /api/v1/modes` (changed) | open | n/a | The v1 shape stays, plus per mode `class`, `extends`, `abstract` and flat `targets` | as today |

**Describe** (ref `describe()`, `coordinator.py:160`, plus the additions):
```json
{"revision": "a1b2c3d4e5f6", "default": "normal", "classes": ["live"],
 "targets": {"blinds.ch02": {"subsystem": "blinds", "member": "ch02", "kind": "level", "label": "Lounge left",
     "groups": ["privacy_blinds"], "available": true,
     "schema": {"type": "integer", "minimum": 0, "maximum": 100,
                "x-ui": {"unit": "%", "presets": {"raised": 0, "lowered": 100}, "ends": {"min": "Raised", "max": "Lowered"}}}}},
 "groups": {"privacy_blinds": ["blinds.ch02", "blinds.ch03", "blinds.ch14"]},
 "subsystems": {"sonos": {"kind": "level", "available": true, "poll_s": 15, "change_time_s": 5,
     "actions": {"play": {"label": "Play", "schema": {"type": "object", "properties": {"playlist": {"type": "string"}}}}},
     "opts_schema": {"type": "object", "properties": {"over_s": {"type": "number", "minimum": 0}}}}},
 "modes": {"disco": {"label": "Disco", "class": ["live"], "extends": "live", "abstract": false, "order": 2,
     "entries": {"blinds.ch02": {"verb": "store+set", "value": 100, "slot": "pre_live", "else": "leave",
         "policy": "enforce", "policy_effective": "enforce", "max_age_s": null, "opts": {}, "from": "live", "selector": "privacy_blinds"}},
     "on_enter": {"from": "disco", "steps": []}, "on_exit": {"from": "disco", "steps": []}}},
 "slots": {"pre_live": {"stored_by": ["live"], "retrieved_by": ["normal"]}}}
```

**Plan item.** This is the v1 step shape plus new fields:
`{id, step, group, subsystem, action, status, detail, kind: "store"|"set"|"retrieve"|"delay"|"signal"|"leave", origin: "store"|"on_exit:ambient"|"rule:0/2"|"rest"|"on_enter:disco", on_fail?, reason?: "already"|"kept"|"nothing_stored"|"stale"|"unknown_state"|"fired"|"quiet_before"|"aborted"|"unavailable"}`

**Status additions.** `status.subsystems` is untouched:
```json
{"mode": {"…v1…": "", "desired_revision": "a1b2", "last_transition": {"…": "", "outcome": "ok|partial|failed|cancelled"},
          "transition": {"…": "", "phase": "started|running|done|cancelled", "outcome": null}},
 "targets": {"blinds.ch03": {"believed": 40, "confidence": "confirmed", "desired": 100, "policy": "enforce", "from": "live",
     "drift": {"mode": "disco", "actual": 40, "since": "…", "observed": 40, "desired": 100, "age_s": 180},
     "held": false, "manual": {"value": 40, "status": "running", "age_s": 12, "expected_s": 70},
     "last": {"action": "set:100", "status": "ok", "reason": null, "age_s": 3600}}},
 "stored": {"pre_live": {"blinds.ch03": {"value": 80, "confidence": "confirmed", "from_mode": "normal",
     "age_s": 13080, "expires_in_s": 30120, "edited_age_s": 120, "tombstone": false}}},
 "config": {"revision": "c3d4", "error": null, "pending_targets": ["plug.dmx_power"]}}
```

**Events:**
- **New:** `{"type": "config", "data": {"revision", "previous", "source": "api"|"file", "by", "note", "error"}}`.
- **`mode.transition`:** the `step` event carries the step object. That is v1's `_step_event` shape (`room_service/coordinator.py:574-576`); the ref emits `step=<int>` (`roomstate/coordinator.py:362`). `done` adds `outcome`. `phase` is unchanged.

**`explainError`** (`api.ts:109`) gains plain sentences for these codes:
- `enforced`
- `transition_running`
- `device_busy`
- `toggle_state_unknown`
- `revision_conflict`
- `invalid_config`
- `warnings_unacknowledged`
- `config_edit_disabled`

---

## 4. What the state model must provide (checklist)

Owner key: aa = the reference model, 2f = room_service.

**Schemas, describe, labels**
- [ ] `value_schema(member)` for every adapter, including stub adapters with `available: false` for `KNOWN_PLACEHOLDERS` and for devices that are offline or not yet discovered. These give warnings, never errors (F12, A7; `contract.py:41`).
- [ ] `x-ui.labels`, `x-ui.presets`, `x-ui.ends`, `unit`, through `enum_schema` and `range_schema` (`schema.py:42-56`).
- [ ] `actions {name: {label, schema}}` (`contract.py:39`). `opts_schema` is optional.
- [ ] `label(member)` (`contract.py:48`), overridden by the config's `labels`.
- [ ] `describe()` adds:
  - `available`, `poll_s`, `change_time_s`;
  - `entries[t].selector` (F11) and `policy_effective`;
  - `slots {stored_by, retrieved_by}`, `classes`, `order`;
  - the origin of on_enter and on_exit.
- [ ] Reject integer-like keys for modes, groups and selectors, because JS reorders them (C13).

**Validate**
- [ ] Fixed JSON Schema:
  - the plain-value branch excludes verb objects (F1; `schema.py` `room_config_schema`, entry `oneOf`, the last branch);
  - `opts` is allowed on retrieve (F2);
  - `description` is allowed on modes and rules;
  - `run` is allowed on delay steps;
  - the `after` step is allowed.
- [ ] The schema runs first, then the model checks. Unknown keys are reported at every level with a pointer. There is no `{"path": ""}` catch-all (`model.py:315-319`).
- [ ] Warning codes:
  - `rule_unused`
  - `abstract_endpoint` (F10)
  - `slot_never_stored`, `slot_never_retrieved`
  - `placeholder_target`, `device_offline`
  - `scene_unknown`, checked against the catalog and skipped when stream_service is unreachable (C9)
- [ ] `rules` as a list, including first boot. `affects_now` split into now and next switch.
- [ ] The revision is computed over an order-preserving dump. The file is written without `sort_keys`, mode 0644 (F3; `model.py:57` `revision`; `room_service/util.py` `atomic_write_json`).

**Plan and transitions**
- [ ] `plan(name, cfg=None, refresh=…)` takes the config as a parameter instead of swapping `self.cfg` (A12; `coordinator.py:145`).
- [ ] Every item carries `id`, `step` with a `group` alias, `kind`, `origin`, `on_fail` and `reason` (F9, fix 8; `_plan` :277).
- [ ] A same-mode request re-applies without on_exit or on_enter. The ref adds them always (`_steps` :398-406); v1 skips them (`room_service/coordinator.py:355`).
- [ ] Delay `run: "always" | "if_prev_changed"`, defaulting to today's behaviour (`:294-297`, `worked = bool(batch)` :317).
- [ ] **Abort:** restore the old mode's desired values, clear `mode.target` and set `outcome: failed`. Today desired is set at :344 before any step runs, and `_finish(completed=False)` (:408-414) leaves `target` set (C1).
- [ ] Keep `phase` and `last_transition`, and add `outcome` (A3). The `step` event carries the object (F8).
- [ ] Take a snapshot of the mode at entry: `{name, label, classes, on_exit, revision}`. Leaving a mode that was deleted or renamed then uses it (`:401`; `model.py` `_matches` :293-294; C3). A rename rewrites `mode.current` in the same save.
- [ ] **Decide A6.** Default: on_enter, on_exit and label inherit through `extends` as in v1 (`room_service/coordinator.py:95-96`), and `{"after": "screens.live", "timeout_s": 20}` replaces `sequence.after`.

**Manual set**
- [ ] `set_target` returns 202 and runs as a task that `request_mode` can cancel (F6; `:186-200`).
- [ ] Per-target lease: reconcile skips the target until it arrives, plus about 15 s (C7).
- [ ] `hold`, and `send:false` (believed and confirmed, nothing sent, logged with its cause).
- [ ] An edge toggle in an unknown state returns 409 `toggle_state_unknown` (F7).
- [ ] An open caller on an enforced target gets 409 `enforced`.
- [ ] Guard `cfg.mode(current)` in `set_target` and `reconcile_once` (F5; :193, :565).
- [ ] Split `busy()` (:94) into `transition_running` and `device_busy`. Reload and stored edits wait up to 5 s for `_work`.
- [ ] Status carries `manual {value, status, age_s, expected_s}` and `last {action, status, reason, age_s}`.

**Stored values**
- [ ] `stored_edit` changes existing entries only, with no `setdefault` (:207). It keeps `at` and `from_mode` and adds `edited_at` and `edited_by` (:209; C5).
- [ ] Forget during a live mode writes a tombstone that blocks re-storing until the next retrieve, which clears it and uses `else` (C4; `_store` :417-429).
- [ ] Restore one value now. Status carries `age_s` and `expires_in_s`.

**Config lifecycle**
- [ ] `reload(cfg)` keeps desired **values** and applies policy relaxations at once: enforce → respect or ignore, and managed → unmanaged. Today `reload` recomputes all desired values (`:224-237`; F4, fix 6).
- [ ] Backups (last 50) in `~/.local/state/screen-machine/room/config-history/`.
- [ ] mtime watch on the file:
  - an external change emits `config` with `source: "file"`;
  - an invalid file keeps the running config and sets `status.config.error`;
  - an invalid file at cold start loads the last good version from history (C10).
- [ ] Migrate `modes.yaml` (`gallery`, `live-test`) to `room_state.json`, keeping the old file's comments as `description` fields.
- [ ] Update LIVE_V1 §0 and §3.2, and write `docs/av/ROOM_STATE_MODEL.md`.
- [ ] `status.subsystems` stays exactly as in v1 (A2). New data goes in `targets`, `stored` and `config`. Drift carries both the v1 and the reference fields (A4).

---

## 5. Build order (one agent, one day)

The day assumes room_service already runs the reference coordinator with v1-compatible status (A8). If it doesn't, that adoption is the day. Each step ships on its own.

1. **Auth split (0.5 h).**
   - Tag routes `open` or `admin` in `room_service/api.py`, and apply Host and Origin checks to every route.
   - Page: `index.tsx:58-59`, `useRoomConnection.ts:157/241`, plus the Unlock button.
   - *Ships: guests can operate.*
2. **Model fixes (2 h)** from §4:
   - abort, same-mode re-apply, plan ids/origin/reason, `plan(cfg=)`;
   - manual-set task, lease, hold, `send:false`, `enforced`;
   - stored edit and tombstone, busy split;
   - status additions.

   Add the `describe`, `plan` (saved config), `targets` and `stored` routes. pytest with `roomstate/fakes.py` covers:
   - an abort restores desired and clears target;
   - a same-mode request fires no signals;
   - hold;
   - an edit can't create an entry;
   - a tombstone blocks re-storing.
3. **Web operating surfaces (1.5 h).**
   - Move `mock_room.py` and `test_room.py` into the repo, with `/room/*` served by the reference Coordinator and fakes.
   - `types.ts` (XUi `labels`/`presets`/`ends` at :14; `RoomDescribe`, `TargetStatus`, `PlanItem`; ModeDef gains class, extends and abstract at :216).
   - `api.ts`: new methods and `errors` on `RoomApiError` (:93).
   - `ValueField`, `RoomNow`, `Remembered`, `LastSwitch`, the abort banner, `PlanView`, and the ModeSwitcher fixes.
   - *Ships.*
4. **Config store (1 h).**
   - `room_service/room_config.py`: load, validate, order-preserving write, backup, history, mtime watch, the `config` event.
   - Routes: validate, PUT and history.
   - pytest covers:
     - key order is kept;
     - a backup is written;
     - a stale `base_revision` gives 409;
     - a save under enforce sends nothing, and one reconcile afterwards still sends nothing;
     - relaxations apply at once;
     - a plan on a draft changes nothing.
5. **Setup (2 h).** `useConfigDraft`, `pointer.ts`, `configDiff.ts`, the mode list, `EntryRow` with `SelectorPicker`, `RawJson` with pointer routing, `SaveDialog`, and a conflict dialog with overwrite or discard only. *Ships.*
6. **Transitions view (0.5 h).** The read-only rule list and the tester.
7. **Playwright (0.5 h).** Cases:
   - the Setup tab sends zero requests to `/mode` and `/room/targets`;
   - Control works with no token;
   - an enforced blind shows no Set for a guest;
   - an invalid value blocks Save at the right row;
   - a "next switch" save leaves desired unchanged;
   - a stale revision opens the conflict dialog;
   - the abort banner appears;
   - the 390×844 layout has no overflow.

**Cut line.** If time runs out, stop after step 3: the operating surfaces work and Setup moves to the next day. Inside step 5, these drop to warnings or JSON-only: [Override], "Put back in" pairing, and "Make X ‹Mode›'s setting".

## 6. Later

- **Transition editing with `StepList`:**
  - dnd-kit with `PointerSensor` (`ViewModeContent.tsx:56`) and `TouchSensor` with a delay (`src/pages/Index.tsx:1184`), plus ↑↓ buttons;
  - insert points between steps;
  - "Make a Disco-only copy of rule 1";
  - signal args through ParamForm;
  - editing on_enter and on_exit.
- The groups and labels editor.
- Renaming modes, groups and slots with every reference rewritten, plus `migrate_slots`.
- The history browser and restore; three-way rebase; undo.
- Taking `LiveSpecForm` out of `LiveControls.tsx`: `TransitionPicker` :513, `TOUCH_GRACE_MS` :41, PATCH batching :163-204.
- Drag reorder for state rows.
- CodeMirror or `jsonc-parser`.
- Forms built from `opts_schema`.
- The separate panel build served by room_service on :8700 with a `location.origin` base. `api.ts:29-31` hard-codes :3001 today, and `App.tsx:18` imports RoomPage eagerly.
- Scoping fired signals per departure (C14), and warnings for duplicate JSON keys.

---

I wrote nothing and changed no files, git state, devices or services.