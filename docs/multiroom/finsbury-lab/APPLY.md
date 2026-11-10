# Step 8: the Lab gets its own modes (finsbury, PC22)

Config only, and optional. Today the drawing room's modes also move devtest (Normal sends it to art, Disco dims it,
and so on), and Live test is a site mode. After this, the Lab has its own two modes and the drawing room's modes leave
devtest alone. `apply.py` (this folder) edits PC22's own files in place, so nothing PC22 changed in them is lost:

- `deployments/finsbury/rooms.json`: `lab` gains `"own_modes": true`;
- `deployments/finsbury/room/modes.json`: the `live-test` mode moves out;
- `deployments/finsbury/room/modes.lab.json`: added from this folder (the Lab's Normal and Live test).

The Lab's Live test is the plan's: it drops the old entry's `mirror_ball` (the glitterball is the drawing room's, so the
Lab can't name it), `on_room_page` and `_note`. "Alexa, live test" still resolves: the site has no such mode now, so it
goes to the one zone that has it.

## Before

1. The wave-2 code is deployed on PC22 and its goldens pass there:
   `.venv/bin/python -m pytest room_service/tests/test_golden.py -q -p no:cacheprovider` (4 passed).
   The frontend build (the Lab strip on the Room page) and the dot_service restart are part of that deploy.
2. Tell the other sessions that Flask (and so room_service) restarts.
3. Best with the Lab idle: the site not in Live test. If it is, the switch still works. The Lab starts in Live test
   and holds the mic and AI claims; the site's mode line says Normal, and nothing is sent.

## Apply (repo root on PC22)

```bash
cd ~/dev/screen-machine
.venv/bin/python docs/multiroom/finsbury-lab/apply.py              # dry run; it refuses if anything is unexpected
.venv/bin/python docs/multiroom/finsbury-lab/apply.py --apply      # backs both files up, edits, runs smconfig check
SM_GOLDEN_RECORD=1 .venv/bin/python -m pytest room_service/tests/test_golden.py -q -p no:cacheprovider -k finsbury
.venv/bin/python -m pytest room_service/tests/test_golden.py -q -p no:cacheprovider     # 4 passed (Lab shape)
git diff --stat room_service/tests/golden/      # finsbury_* only
curl -s -X POST http://192.168.1.205:4317/api/control/screen-machine-flask/restart
```

`smconfig check` must come out clean: no problems, and no "share the site's modes" note. Commit the three deployment
files and the re-recorded goldens together.

## Check on PC22 (after the restart)

- `curl -s http://127.0.0.1:8700/api/v1/status | jq '[.zones[].room], .mode.rooms_error, .mode.current'` gives `["lab"]`, `null`
  and the drawing room's mode, not `live-test`.
- The Room page shows a Normal / Live test strip under Lab, and the top switcher is captioned Drawing room.
- Lab Live test: `curl -s -X PUT http://127.0.0.1:8700/api/v1/mode -H 'content-type: application/json' -d '{"mode":"live-test","room":"lab"}'`.
  devtest goes live, the site's mode is unchanged, and the mic and AI listening come on.
- While the Lab is live, drawing-room Normal and then Ambient leave devtest live. The director's palette still reaches
  north, south and devtest.
- Lab Normal (`{"mode":"gallery","room":"lab"}`): devtest goes back to art. The mic goes off unless the drawing
  room's mode claims it (Piano, Ambient, Party and the like keep it on).
- "Alexa, live test" switches the Lab.
- `tail logs/room_service.log` shows "mode zones: Lab (N targets, modes.lab.json)" and no rooms_error.

## Rollback

Put the Lab in Normal first. Otherwise its mic claim stays on show_service until the site next switches. Then:

```bash
cd ~/dev/screen-machine
.venv/bin/python docs/multiroom/finsbury-lab/apply.py --rollback   # restores both files, removes modes.lab.json
curl -s -X POST http://192.168.1.205:4317/api/control/screen-machine-flask/restart
```

Then re-record the finsbury goldens as above, or put `room_service/tests/golden/finsbury_*` back to the committed
ones. The Lab's state (`~/.local/state/screen-machine/room/rooms/lab/`) and `show_claims.json` are left behind and are
unused without zones. Delete both before a later opt-in, so it starts fresh.

## Verified here (on the laptop, against a copy of deployments/finsbury with the change applied; apply.py's result is byte-identical to that copy)

- smconfig check: no problems and no notes. own_mode_rooms = {lab: devtest, show true}.
- room_service booted on the copy (inert devices, fake show_service):
  - zones [lab], with no rooms_error and no config error in either zone;
  - site modes ambient, disco, gallery, lights-only, off, party and piano; Lab modes gallery and live-test;
  - Lab scope: screens.devtest, diag.devtest, page.devtest.timed_reload, sensor_mask.lab, brightness.lab.screens,
    show.lab.mic and show.lab.ai;
  - no site mode's plan names devtest or a Lab member.
- The PC22 checks above, run against the fake show_service:
  - the Lab's Live test keeps the site in gallery and turns the mic and AI on;
  - site Ambient and then Normal leave devtest live;
  - Lab Normal puts devtest back to art and releases both claims;
  - with the site in Piano, a Lab Live test followed by Lab Normal leaves the mic on, and site Normal then releases it.
- The same run, started with the site in Live test before the opt-in, gave the same results. That covers the migration
  path.
- show_service tests/test_room_scope.py (the RoomLink and players goldens) passes on the copy. So does the
  golden-shape test against a probe of the copy.
