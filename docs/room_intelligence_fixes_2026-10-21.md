# Room page intelligence: fix specs (09's audit, 21 Oct 23:10)

Owner complaint, 21 Oct: "generally need to inspect the 'intelligence' in this page as too much of it is flakey and gets in the way". Each section is one owner session's batch with its restart class.

## Fix specs

Paths are relative to `/home/gjbm2/dev/screen-machine`. Line numbers were checked against the tree at 23:06.

### Already done (no action)
- b097e43d, 7521bd87, d3d74002 and 54e29d53 are live. show_service restarted at 22:46:21, 22:54:19 and 23:05:31 (pid 802709).
  - The 22:57:00 Auto→Piano switch logged kind `partial`, and Auto reads `watching`. That closes auto-1, mo-1 and ai-F1.
  - `GET :8703/api/v1/autopilot` still shows `backoff_s 1800 (default)`, so auto-2 is still needed.
- 09's pc22 `room_sound: false` line is already gone from show.yaml (the first half of ai-F8).
- Already in config: `heard_by: null`, `listener.requires_mode: false`, Piano `show.ai: true`.

### Restart classes
- **[hot]** No restart. `config/room/*.yaml` and `*.json` hot-reload.
- **[show]** One `scripts/ctl restart show` for the whole batch.
  - Check holds with 26 (calibration), e9 and 3a first.
  - The restart also loads the uncommitted `show_service/clock.py` and `mllink.py` (plus their tests). 3a must OK that.
- **[flask]** One Flask restart for the whole room_service batch.
  - Tell the sessions that edit room_service first.
  - `POST http://192.168.1.205:4317/api/control/screen-machine-flask/restart`.
  - Within 30 s, check `:5000/api/health` and `:8700/api/v1/status`. If Flask is down, start it with start.sh's screen command.
  - Do it while the room is in Normal or Off (needed for LM-1).
- **[stream]** One `scripts/ctl restart stream`, only when `GET :8702/api/sessions` shows nothing live, entering or holding (devtest included). Then check that it returns 200.
- **[fe]** One frontend deploy.
  - Run `git status src/` first: `MusicCard.tsx` has uncommitted hunks and most of `src/pages/Room/` is untracked, and all of it ships.
  - Then `npx vite build --outDir dist_next --emptyOutDir && rm -rf dist_prev && mv dist dist_prev && mv dist_next dist`.
- **All builders:** commit only your own hunks (`git apply --cached` where peers have edits). Run crash-level tests under `flock` + `nice 19`.

### screen-machine-09: hot config [hot], apply now and tell 3a
- **H1:** in `config/room/show.yaml`, delete the 3 `stream: room-mic` lines at :96, :106 and :111 (situations music, quiet and talking). Then check that `POST :8703/api/v1/autopilot/check` gives music_audible true while music plays.
- **H10:** `show.yaml:97`, `situations.music.min_prob` 0.5 → 0.4. Comment: "8f's 20 Oct measurement, d94a58f2".
- **H12 (config half):** delete the `heard_by` and `heard_min_lufs` lines at `show.yaml:85-93`. This can land in either order with 3a's code half.
- **ai-F8 (rest):**
  - In show.yaml `listener`, delete the dead keys: `requires_auto` (:371), `requires_mic` (:372), `requires_mode` (:374), `music_gate`, `ced.music_min` (:392) and `ced.speaks_s` (:398). No non-test show_service code reads them (grep checked).
  - Fix the stale comments at :31-37: AI listening now claims the mic itself.
  - KEEP `quiet_gate: 0.001`, `ced.speech_max` and `ced.speech_words` until Q3 is answered.
- **ai-F6 step 1:** in `config/room/vibes.yaml`, delete the 6 `listener:` lines (:65, :105, :144, :184, :223, :263). The old code defaults the field to true, so this is safe now.

### screen-machine-09: Room page [fe], one deploy (ship H6 first if split)

**Auto and mode lines**
- **H2:** `PianoRow.tsx:224` becomes `else if (auto.state && auto.state !== "watching") autoLine = null;`. Fix the comment at :223. This supersedes auto-6.
- **H3:** `PianoRow.tsx:229` becomes `else if (played && !last?.test && reading?.value === false && reading.evidence)`. Delete the heard_by branch at :54-56 and the header line at :12.
- **auto-8:** in `ModeSwitcher.tsx:796` (plainReason), use `const s = r.replace(/\s*\([^()\s]+\/[a-z_]+\)\s*$/, "").trim();`. This supersedes mo-3.
- **auto-11:** in `MusicCard.tsx:443`, drop the never-sent `backoff` chip tone and keep `failed` as warn.
- **mo-4:**
  - `ModeSwitcher.tsx:932-936` stepStatusWords:
    - reason `unavailable` → "The room can't switch it yet";
    - `unknown_state` → "Not sent: on or off not known";
    - detail `dry run` → "Not wired up yet";
    - reason `hand` → "Left as you set it";
    - reason `quiet_before` or detail `re-apply` → "Not needed".
  - `words.ts:94`: unconfirmed becomes "Sent, not confirmed".
  - `ModeSwitcher.tsx:871` becomes `${unchecked} not confirmed`.
- **mo-5 (words only, keep the items on the tiles):**
  - `modeWords.ts:226` becomes "(the room can't switch it yet)".
  - `ModeTargets.tsx:695` becomes "The room can't switch it yet".
  - In notConnectedWords (`modeWords.ts:265-296`), build the sentence: `The room can't switch ${theList(names)} yet, so modes leave ${p?'them':'it'} alone.`
  - For a device that is only unreachable: "The room can't reach  right now".
- **mo-6:**
  - `useRoomConnection.ts` (~:411): keep `status` and `detail` on done and cancelled.
  - `types.ts`: add `status?: string|null; detail?: string|null` to ModeTransition and ModeTransitionEventData.
  - ModeProgress: set `outcome = transition.status ?? st?.status`. Add a `failed` branch before the failed-rows branch: tone bad, "Couldn't switch to ${to}", sub "still <current label>", and the CircleAlert icon. Never show the raw detail.
- **mo-8 (ship after the [flask] restart):** in `Effects.tsx:101`, drop `|| toggleUnknown` from `disabled`. Keep the question row.
- **mo-10:**
  - `ModeTargets.tsx:555-557`: `storeIfWords(t)` uses the target's own `entry_schema.properties.store_if['x-ui'].enum_labels` when it has exactly one item, else "Only if it was in use". Widen the type at :30-31.
  - Do the same in `ModeEditor.tsx:86-88`.

**What it hears and the mic**
- **H4:** in `MusicCard.tsx:690`, delete "following loosely", and remove it from the comment at :10.
- **H5:**
  - `MusicCard.tsx:697` becomes `{card ? (!hearing?.text || hearing.text === "Listening" ? (hearing?.test && level > 0 ? "Test music" : "Not sure yet") : hearing.text) : "Nothing yet"}`.
  - At :718, show "From the test music." only when `!!hearing.text && hearing.text !== "Listening"`.
- **H6 (live already, ship first):**
  - `MusicCard.tsx:506` becomes `c.by === "auto" ? "for Auto" : c.by === "listening" ? "for AI listening" : ...`.
  - `types.ts:889`: add `"listening"` to the claims `by` union.
- **H11:**
  - `MicPicker.tsx:79` group labels: off → " (not in use)", no_device → " (no mic plugged in)", any other state that isn't streaming or quiet → " (not sending)".
  - After :97, use `const using = inputs?.active && choice && inputs.active !== choice.stream ? list.find(i => i.stream === inputs.active) ?? null : null`. When it's set, show "<picked> isn't sending; the room listens through <using.label> until it's back."

**Music card and lights**
- **nudges-F6:**
  - `api.ts:508-510`: `showAction(action, on?)` posts `{do, on}` only when `on` is given.
  - `MusicCard.tsx:575`: House lights sends `on = !card?.house`.
  - `MusicCard.tsx:578`: Lights low sends `on`.
- **nudges-F9:**
  - `Fixtures.tsx:593`: drop ` N/s`.
  - Keep :594 as a warn.
  - `:202` becomes "It follows as soon as the show sends colours."
- **nudges-F10:** `MusicCard.tsx:821` "Back to auto" → "Clear all", and change the log word at :587 and the comment at :13.
- **nudges-F11:** `MusicCard.tsx:572` becomes "No colours are playing yet, so there's nothing to change."
- **nudges-F12:** in `MusicCard.tsx:565`, the limit lines become "That's as calm as it goes." and "That's as wild as it goes."
- **nudges-F13:** in `MusicCard.tsx:574`, the log word is "Keep this look" / "stop keeping this look".

**Live screens**
- **live-F3:** `api.ts:168-169` no_effect becomes "There's no other effect to bring in."
- **live-F4:** delete the "didn't fit; it brought in" note at `UpNext.tsx:243-247`.
- **live-F5:**
  - Delete `liveLib.ts:245-249` mismatchWords and its callbacks at `LivePanel.tsx:874` and `RoomLive.tsx:243`.
  - `UpNext.tsx:415` becomes "Choosing the next ones".
  - `LivePanel.tsx:275` becomes "none on yet; one comes in at its next change". Ship this one only after the [stream] restart.
  - Keep UpNext's "if it fits" lines.
- **live-F11:**
  - `LivePanel.tsx:1103`: the Any chip is active only when `pool === "all"`. For a list pool, add the line "This mode picks from its own N scenes; Any widens it."
  - Show the "How often it changes scene" slider (:1119-1124) only while the "Follow the feel" dwell is chosen.
  - At :1058-1078, re-read `api.inputs` every 5 s while the section is mounted and the page is visible.
- **live-F13:**
  - `liveLib.ts:243` becomes `${label} came off to make room: a screen runs ${max} effects at most.`
  - `api.ts:158-159` not_live: if the detail contains "cut is under way", show "The screen is changing over. Try again in a moment."; otherwise "The screen isn't live just now."
- **live-F9 (after [flask]):** remove the too_fast words at `api.ts:160-161` and `UpNext.tsx:75`.

**Screens**
- **S8:**
  - `ScreenCard.tsx:202-207` becomes "The TV page isn't open right now. Live still starts; the TV joins it when its page opens."
  - At `words.ts:63`, drop "Can't go live yet". Build the hint from `observed.retry_in_s` ("Trying again in a moment") and from `streamReachable === false` ("Waiting for the live pictures to come back").
  - Use a detail prefix only for "the renderer is restarting", which becomes "Live pictures are restarting  back in a moment".
- **S9:**
  - `LiveControls.tsx:349` becomes "The live pictures aren't answering right now. Going live starts by itself as soon as they're back."
  - `ScreenCard.tsx:215` becomes "The live pictures aren't answering right now. Back to art still works."
  - `MenuSheet.tsx:187` becomes "Not answering: going live waits until it's back."
- **S10:**
  - `words.ts:50` becomes "Live didn't start  showing art".
  - At `ScreenCard.tsx:194-201`, choose one sentence by the last_error prefix:
    - `fallback:` → "Live stopped by itself."
    - `N fallbacks` → "Live keeps stopping."
    - `entry` → "Live didn't start at first."
    - `exit` → "The cut back to art wasn't smooth."
    - `live change refused` → "That change didn't take; the screen kept what it had."
    - `internal:` → "Something went wrong inside the room."
  - Add " It's trying again." only while the screen's desired value is live.
  - The raw text appears only in Technical details.
- **S11:** in `words.ts:71`, drop the "Last try had a problem" hint from "Showing art".
- **S12 (after ff's coordinator part, [flask]):** `ScreenCard.tsx:133` becomes `showCore = sc.liveNow || (canStop && (phase === "failed" || !!desiredLive))`. Fix the comment at :129-131.
- **S16:** in `useRoomConnection.ts`, on visibilitychange (to visible) and `online`: call refreshStatus() at once, and if the socket isn't OPEN, clear reconnectTimer and call connect().
- **S15 (UI, later):** remove the key UI in `index.tsx:263-278`, `TokenPrompt.tsx`, `connection.ts:20-22` and the MenuSheet key row.

**Light mask**
- **LM-2:**
  - In `ScreenDetails.tsx:94-141`, the title becomes "Art brightness". Pass `obs.layer` in.
  - The pill shows the first of these that applies:
    - live → muted "not used while live", no bar;
    - source `fixed` → "off for this screen";
    - `manual_override` or `maskoff` → "set by hand";
    - suspended → muted "stays as it was (this mode)";
    - otherwise → "follows the room's light".
  - Show "The page draws" only when it differs by more than 0.01.
  - Add `source?: string` to ScreenMask in `types.ts:200-202`.
- **LM-3 (page half):** `ModeTargets.tsx:52` BEHIND.sensor_mask becomes "Art brightness".

### screen-machine-3a: show_service [show], one batch
e9 rewrote `listener.py` and `mic.py` tonight, so 3a and e9 agree who lands those two files.

**Auto (`show_service/autopilot.py`)**
- **auto-2: remove the backoff.**
  - `_fail` (:1256-1262) stops setting backoff_until. It adds `a['rule']` to a new `self._failed: set[str]` and announces `f"Auto couldn't switch to {label}"`. The room's detail goes in the reason and the log only.
  - In `_evaluate` (:936-972), right after the problems and refused checks, any enter, suggest OR leave candidate whose rule is in `_failed` gets blocked: `f"the last try didn't work; it tries again next time {words} starts"`.
  - `_expire` (:1031): `self._failed = {r for r in self._failed if r in self._infos and self._infos[r].get('value') is not False}`.
  - `resume()` (:1373-1376) and the Auto-on path in tick (:781-783) clear `_failed`.
  - Delete backoff_until at :632, :701, :718-719, :783, :878-879, :1002-1004 and :1376, the line at :1610-1612, `AP_DEFAULTS['backoff_s']` (:72) and the `_valid` branch at :761.
  - `contract.py`: remove 'backoff' from AUTOPILOT_STATES and AutopilotStateName (:839-840), 'backoff_s' from AUTOPILOT_GLOBAL_KEYS (:844), and the field at :877.
  - Tests: fix the fixture at :29. Replace `test_failed_switch_backs_off_until_resume` (:445) with: a failed switch isn't retried until its rule goes false, and leave rules still run.
- **auto-3: remove the flap guard.**
  - Delete `_flap` (:1080-1090) and call `_request` directly at :1061 and :1069.
  - Delete `max_switches` (:71, `_valid` :766) and `self.pause` with its uses (:631, :701, :717, :782, :876-877, :999-1001, :1375, line :1607-1609).
  - KEEP the `paused` state: quiet hours use it.
  - `contract.py`: remove `max_switches` from :844, plus MaxSwitches (:852) and its field (:876).
  - Tests: delete `test_flap_guard_pauses_auto` (:569), fix the fixture at :30, and point the :654 assertion at another key.
- **auto-4 (= nudges-F1): remove the "engaged" block on leaving.**
  - Delete :912 (`engaged = `) and the `elif engaged` at :971-972.
  - `test_autopilot.py:365-379` now expects the leave.
  - Reword `docs/SHOW_ENGINE.md:1099` and `show_service/README.md:1971`.
- **auto-7: blocked reasons in Room-page words.**
  - :944 becomes `f"only from {' or '.join(self._label(view, m) for m in allowed) or 'no mode'}"`.
  - :946 and :970 become "you changed it back right after Auto switched; it'll switch again next time".
  - :951 becomes `f"the room stays in {self._label(view, current)} at least {dwell:.0f} s ({dwell - since:.0f} s to go)"`.
  - :937 becomes "its rule has a problem: open the mode's ".
  - :939 becomes "the room said no to this switch: open the mode's ".
  - :1614 becomes "Auto can't reach the room right now".
  - No new line may end in ` Resume` (MusicCard.tsx:534 keys on that).
  - Update tests :438 and :611, `README.md:1961` and `SHOW_ENGINE.md:1113`.
- **auto-10:** `:70` min_dwell_s 300.0 → 60.0 (test :653). In `SHOW_ENGINE.md:1074-1079`: quiet hours "none unless set", minimum stay 60 s, and drop the flap and backoff lines.
- **auto-11:** in `_person` (:1295-1297), record the hold with `self._note('hold', )` instead of `_announce`.

**Listener (`listener.py`, current lines)**
- **ai-F2: delete the fail-closed "can't check" pause.** Remove `_decide` :1304-1306, `WHY_CANT_CHECK` (:172 and in STATUS_WORDS :173), docstring (c) and its tests. Keep the talking branch at :1301-1303 until Q3 is answered.
- **ai-F3: one word per pause, in STATUS_WORDS.**
  - No usable model while the switch is on (`_gate` :1246-1251, which returns WHY_OFF today) → "Paused: no AI model set up".
  - Mic off (`_gate` ~:1252-1255, and the mid-clip check in `_call`) → "Paused: Room mic is off".
  - Stream and features present but the ring under `min(clip_s, min_clip_s)` → "Starting".
  - No stream or features, or a clip under 1 s → "Paused: no sound from the mic".
  - "Paused: quiet" only for the quiet_gate branch.
  - Update the tests.
- **ai-F7:** in tick (~:1020-1035), while `not self.on`, set `_section_due = None` and log nothing; or guard the arming at :1329 with `self.on`.
- **ai-F6 (code):**
  - `vibes.py`: drop `listener` at :76, :104 and :166. It must ignore a leftover `listener` field in stored overrides.
  - `director.py:4101-4104`: drop the "this feel doesn't listen" readback.
  - Keep listening_effective and listening_why, filled from the listener (`api.py:1353-1366`).

**Hearing**
- **H7:**
  - `api.py:336` `MIC_MUTED = "Room mic switched off"`.
  - `mic.py:62-81` why_words: "Muted" → "Switched off".
  - Tests: `test_mic.py:109,112,114,130,132,226,261` and `test_api.py:1618`.
  - Comments: `mic.py:29`, `api.py:413`.
- **H9:** in `situations.py`, delete `own = self._own_change(p)` and the early return at :1204-1206 in `_r_music_audible`, and the own-change return at :1437-1439 in `_r_quiet`. Update the own-change tests.
- **H12 (code):** remove the heard_by block in `situations.py:1163-1175` and its entries in PARAM_SCHEMAS (:115-118), DESIGN_DEFAULTS (:160) and WORDS (:180), plus the docstring at :20. Update test_situations, test_autopilot, test_e2e and test_structure.

**Lights and nudges**
- **nudges-F3:** delete `director.py:3782-3783` (`if not self.known and self._relaxed: return None`).
- **nudges-F4:** in `artnet_out.py`, delete the pps branch at :354-356, `own_hz` at :985 and `FOREIGN_PPS_MARGIN` (:183). Update `test_artnet_out.py:660-663`.
- **nudges-F5:**
  - `director.py`: delete :4172-4173.
  - Move `if self._vibe_name is None: self._pick_light_scene("track", now_s)` from :1951-1952 into the early-return branch at :1956.
- **nudges-F6 (server half):**
  - `api.py` `_action` (:1310): pass body `on` only when it is present.
  - `director.py:4177/:4182`: `self.house = (not self.house) if on is None else bool(on)`, and the same for `low`.
- **nudges-F7:** in the card at `api.py:1173` and `:1229`, send `describe.suggest = null` when suggest.mode equals the current mode.
- **nudges-F10:** `director.py:4368` label "Auto" → "Let it choose" (`test_api.py:1084`).
- **nudges-F13:** `director.py:4175` (action 'keep' only): `self.keep_until = None if self.keep_until is not None else now + float(self.cfg["keep_s"])`.
- **live-F8 (song ends, show only):**
  - In `cues.py`, keep a set of track ids that already had a `sound_back` (add to it in `_dark_tick`).
  - `_stop_check` (before the publish at :489-500) and `_fade_fire` (:523-534) skip those ids and log `repeat`.
  - Clear the set on a track change.
  - Tell e9: the lamps hear the same cues.

### screen-machine-ff: room_service [flask], one restart
- **mo-2 (Sonos paused vs stopped):**
  - `roomstate/contract.py` class Adapter: `def equivalent(self, x, y) -> bool: return False`.
  - `adapters/sonos.py` SonosAdapter (:237): `def equivalent(self, x, y): return isinstance(x, str) and isinstance(y, str) and {x, y} == {"paused", "stopped"}`.
  - `coordinator.py:72` becomes `return x == y or (a is not None and a.equivalent(x, y))`.
  - Add a test in `tests/roomstate/test_sonos.py`: entering Piano with the player stopped sends no playback row, starts no reconcile retry and doesn't make the switch partial. This supersedes auto-5.
- **mo-7:** delete the enforce refusal at `coordinator.py:1088-1090`. Update the docstring at :1079 and the tests at `test_review.py:572` (accepted, drift overridden) and `test_describe.py:203-205`.
- **mo-8:** delete `Refused('toggle_state_unknown')` at `coordinator.py:1091-1092`, so one toggle is sent and the tapped value is believed, unconfirmed. Update `test_describe.py:293-299` (expect `sent == ['toggle']`).
- **mo-11:** in `_finish` (:440-448), log one INFO line to logger `room_service.mode`: id, from→to, status, source, and the rows that weren't ok (or "all ok").
- **S12 (backend):** after a batch's `_apply` (~:362), a target in `self._hand_wins` gets the row `skipped`, "set by hand during the switch".
- **auto-3 tidy:** drop `max_switches` and `backoff_s` from `roomstate/schema.py:75-78`.
- **S1: never park after failed go-lives.**
  - `screens.py:1802-1805` and `:1951-1954` become a plain `self._schedule_retry(f"entry failure {self._entry_failures}")`.
  - Stop restoring and persisting `failed` (:283-284, :308). Delete `config.py:118` max_entry_failures.
  - `adapters/screens.py:104` becomes "Live is off for this screen (showing art)".
  - Rework the `test_screens.py:99-187` cases that check for `failed`.
- **S2:**
  - `screens.py:1663-1670`: always call `self._schedule_retry(f"fallback {n}")`.
  - `config.py:115` `backoff_s = [2.0, 5.0, 15.0, 30.0, 60.0]`.
  - Delete `config.py:117` fallback_limit and `screens.py:62` SLOW_RETRY_S.
  - Keep the "not counted" path for renderer restarts and the fallbacks deque.
- **S3:** `screens.py:1511` becomes `if not self.detail.startswith(("retry in", "the renderer is restarting", "live keeps stopping")):`.
- **S4:** `screens.py:1583` cue `"source": "room"` → `"source": "mode"`.
- **S5:** in `adapters/room.py:131-135`, no page connected → `Outcome("ok", "kept; the TV page gets it when it opens")`, and a page that doesn't report policy → `Outcome("ok", "sent")`.
- **S7:** in `config/room/room.prod.yaml:23` timeouts, add `session_ready_s: 15` (read at start). Update the README timeout table.
- **S11 (backend):** `set_desired` (:857-861) also sets `self.last_error = None`. Keep it persisted.
- **S13:**
  - `on_page_closed` (:1215-1224): when the leaving page is this screen's own glass, set `_page_lost_at` and don't promote.
  - `_supervise` (:2458): skip promotion while `_page_lost_at` is within adopt_wait_s.
  - `:1773`: set glass_device from the page only if it is unset or `_own(page)`.
  - `:1104-1106`: log the own glass coming back at info level.
- **S14:** remove `or client.rejoins >= self.REJOIN_MAX` (:801) and the constant at :778.
- **S15:** delete the "no control key configured" warning at `pages.py:245-249`. The rest of the key plumbing can go later.
- **S17 (docs):** update `room_service/README.md:184-193` (and :148, and the timeout table) and `docs/AV_PROGRAMME.md:288`: retries back off 2/5/15/30/60 s and never park.
- **LM-1:**
  - `adapters/room.py` `apply` (:76-80) saves `{"suspended": s, "held": {sid: {**v.as_dict(), "source": src.get(sid)}}}`, using getattr on `pushed` and `pushed_source`.
  - `restore()` (:58-61) seeds `mask.pushed` / `pushed_source` only when the saved suspended is True and the screen is still configured with mask_push.
  - Add a test that welcome_mask returns the held value.
- **LM-3 (backend):** `adapters/room.py:47` subsystem_label "Art brightness". At `:64`, use `enum_schema(..., 'Art brightness', {'follow': "Follows the room's light", 'suspended': 'Stays as it was'})`.
- **LM-4:** delete the `[mask_state]` debug line at `routes/display.py:76`.
- **LM-5:**
  - Fix the key wording in the `mask_arbiter.py` docstrings at :15-17, :133 and :144.
  - Drop `keys.refresh()` and the armed/off log at :466-471, and `sensors_file` / `sensors_with_keys` from `overview()` (:547-548).
  - Fix the docstrings at `ingest_config.py:3-4` and `ingest.py:11-16`.
- **nudges-F8:** `dmx/api.py:142` "Following Maestro" → "Following the music"; `:146` → "Ready: waiting for the music".
- **live-F9:** in `live_api.py`, delete `CUE_MIN_INTERVAL_S` (:54), `last_cue` (:237), the check at :503-506 and the docstring lines :15 and :21. In `tests/test_live_api.py`, drop :118 and :185-190.
- **ai-F6 step 3:** drop `"listener"` from BASIC_FIELDS in `adapters/show.py:93`.

### screen-machine-ff: stream_service [stream], one restart
- **live-F1: remove the "fewer effects" step.**
  - `degrade.py`: STEPS becomes `("scene_rate","fps")` (:36); drop `fx` from UP_WAIT_S (:44); delete :240-241.
  - The end of `project()` becomes `return max(ld.values())` (:258-260).
  - Delete `fx_at` (:164, :272-273). Keep the `fx_trim: False` key in state().
  - `sessions.py:2231-2232`: drop the set_trim call.
  - `fx_director.py`: delete trim_max (:115), set_trim (:214-223), the trim lines at :427-428 and :436-437, FX_TRIM_BUDGET (:69), status `trimmed` (:474) and the docstring at :31-34.
  - Rewrite `test_degrade.py` (:87, :92, :212, :411-460) for a two-step ladder.
- **live-F2: remove the cost budget.** In `fx_director.py`, delete :269-270 and :282-283, FX_BUS_BUDGET_MS and FX_UNKNOWN_COST_MS (:52), `_cost` (:301-307) and the docstring lines at :8-9 and :32-33. Update `test_fx_director.py:9,125` and `test_degrade.py:412,421`.
- **live-F3:**
  - `fx_director.py` `next_move` (:452-465): when `_choose` returns None, try again with the stage rules relaxed. Still exclude quarantined effects, failed vetting, `auto:false` and effects already on.
  - `sessions.py:1951-1952`: return `no_effect` only when that is empty too, and `too_many_effects` at MAX_BUS_SLOTS.
- **live-F4:** at `sessions.py:1333-1335`, for an effect, after `q.move(qid, 0)` use `result['fx'] = await self.fx_next({'effect': q.effects[0]['effect']})`.
- **live-F6:** in `music_direction.py` `effective_direction` (:99-100), skip the fx.density trim when the panel's fx.density is 1.0, and leave it out of `music_fields` (:126) then. Add a docstring note at :17 and a test.
- **live-F7:** in `sessions.py` `_apply_direction` (:1536-1539), when the cause is `music`, keep `_drop_prepared()` and replace `_replan()` with a `_replan_head()`. It re-picks q.scenes[0] and q.effects[0] in place, and only when they are auto and not held (`q.add(kind, entry, at=0)`).
- **live-F12:** in `sessions.py` `auto_ok` (:2507-2515), a calibration entry excludes only `status in ('hung','error')`. With no entry, `return not (origin_of(plugin) == 'imported' and plugin not in sheets)`, with no ms budget test. Update `test_gpu_guard.py:378-392` (st_hung stays out).
- **live-F13:** in `upnext.py:56-58`, use `lead = f"{'an' if stage[:1] in 'aeiou' else 'a'} {stage} effect"`, both at :57 and in the comparison at :58.

### Held for the owner's answers
- **Q1 (02:00):**
  - If yes: 09 removes the `set_auto on:false` block at `routes/scheduler/north-screen.json:458-474` and `south-screen.json:458-474` through the app's scheduler editor (Flask rewrites these files).
  - If no: 3a makes Auto's off line say "Auto is off (the 02:00 schedule turned it off)".
- **Q2 (AI listening per mode):**
  - If no: 09 deletes the `show.ai` row from all 8 modes in `config/room/modes.json` [hot]: gallery :28, live-test :54, piano :106, off :168, lights-only :194, ambient :241, party :313, disco :384.
    - Then ff removes the `ai` member from `adapters/show.py` (:49-58, :100-103, :109-111) [flask].
    - show_service keeps `mode` in `GET /api/v1/listening` until then.
  - If yes: 3a makes those modes switch the AI listening switch off on entry. That is not a veto: the person can switch it back on.
- **Q3 (talking):**
  - If no: 3a deletes the talking branch (`listener.py:1301-1303`, WHY_TALKING), and 09 deletes `ced.speech_max` and `speech_words`.
  - If yes: set speech_max back to 0.3 only after the ML worker's CPU starvation is fixed, and never with a fail-closed pause.
- **mo-9 (privacy-blind abort):** the `on_fail` at `modes.json:422-425` is latent while the blinds are dry run. Ask the owner before blind writes are turned on.

### Superseded or rejected (don't build)
- **Superseded:**
  - auto-6 → H2;
  - mo-3 → auto-8;
  - auto-5 → mo-2;
  - nudges-F1 = auto-4 (build it once).
- **Done:** auto-1, mo-1, ai-F1, and ai-F8's pc22 line.
- **Rejected:** mo-12, H8, nudges-F2, live-F10, S6, LM-6, ai-F9.
