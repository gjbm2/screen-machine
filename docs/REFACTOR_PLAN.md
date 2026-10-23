# Screen Machine: code-health list

23 Oct 2026. This one page replaces the 37,000-word refactor plan of 11–14 Oct, kept for reference in
[archive/REFACTOR_PLAN_2026-10-14.md](archive/REFACTOR_PLAN_2026-10-14.md).

**Why the old plan was retired.** Its method (staging copies, week-long soaks, shadow phases, auth layers, review
loops, a multi-week phased programme) is ruled out by the owner's 14 and 18 Oct rules in `CLAUDE.md`. Most of its
display goals were then built a different way: one display page with live cutover, instant light masks, 4K60 live.
And it predates the new services (`room_service`, `show_service`, `stream_service`, `piano_render`), which now hold
most of the code. Treat the archive as background only; where it disagrees with `CLAUDE.md`, `CLAUDE.md` wins.

## Done (23 Oct)

1. **Live scheduler state is no longer tracked in git.** `routes/scheduler/*.json` (and `.bak`) and
   `scheduler/_vars.json` are untracked and ignored; the files stay on disk and their history stays in git. Schedule
   scripts (`routes/scheduler/scripts/`) and `schedule.schema.json.j2` stay tracked.
2. **Safe writes.** `bucket.json` (`routes/bucketer.py` `save_meta`) and the published sidecars
   (`routes/publisher.py`) are written to a hidden temp file and renamed into place, so a crash or a reader never sees
   half a file. Temp names carry the process and thread, so two web threads can't share one. The TVs' picture files
   were already placed this way (`_place_atomically`).
3. **Flask stops by PID.** `start.sh` records the server's own PID in `logs/flask.pid` and `stop_flask` kills exactly
   that process (with the exact-match `pgrep -fx` as the fallback for a server started before the pidfile existed).
4. **Tests can't start the live app.** Importing `app.py` under pytest (`PYTEST_VERSION` set) skips
   `initialize_schedulers_from_disk()`, which reads the live `routes/scheduler/*.json` and would otherwise drive the
   real screens. Room, show bus and the overlay socket only start under `__main__`.

## Still worth doing (small, in order)

1. **`start.sh` stop_vite and stop_ngrok still use loose `pkill -f` patterns** (`npm run dev`, `ngrok http`), which
   can hit other projects on this box. Give them the same pidfile treatment.
2. **Samsung TV pairing token files are still tracked**, in a public repo (`north-screen-token*.txt`,
   `south-screen-token*.txt`, and a copy under `samsungtvws/example/`). Untrack them; re-pairing the TVs is the owner's
   call (it invalidates the published ones).
3. **`routes/display.py` `send_overlay` still falls back to `get_event_loop`/`new_event_loop`** when called off the
   socket server's loop. No "different loop" errors are in the current logs, so leave it unless they come back.

## Not doing

Staging, labs, soaks, shadow weeks, release directories, auth layers, reviewer loops and phased programmes, per
`CLAUDE.md`. New code-health items go on this page as one-line entries, not as a new plan.
