# Deployments

screen-machine is one codebase that runs as several **deployments**. A deployment is one installation on one
machine:

- **finsbury**: PC22's drawing room, the production site, with two Frames, the kiosk, Hue, DMX, RF, Sonos and the e-ink tags.
- **a laptop rig**: one VJ screen, Art-Net lights on a local LAN, no image generation.
- anything else you set up later.

The code is the same everywhere. What differs between deployments lives in one directory, `deployments/<id>/`. The
machine picks its deployment with one line in its `.env`:

```
SM_DEPLOYMENT=finsbury
```

Nothing has a default. A process that cannot tell which deployment it belongs to refuses to start and says why, because
a wrong guess would drive another site's lights and screens.

## Where things live

| Layer | Where | Same on every deployment? | In git? |
|---|---|---|---|
| Code | the repo | yes | yes |
| Shared behaviour: scenes, effects, vibes, the tuning in `show.yaml`, LLM routing, prompts, refiners, workflows, fixture and RF types | `config/`, `routes/data/` (templates), `src/data/` | yes | yes |
| **The deployment:** screens, rooms, devices, lights, calibration, its service flags, the site layer of `show.yaml` | `deployments/<id>/` | no | yes |
| Machine addresses, API keys, `SM_DEPLOYMENT` | `.env` | no | **no** |
| Secrets: pairing tokens, Hue and Bloc keys, Dot. key | `~/.config/screen-machine/` | no | **no** |
| Runtime state: room state, show state, jobs, logs | `~/.local/state/screen-machine/`, `logs/` | no | **no** |
| Art: buckets and the current picture per screen; schedules | `output/`, `routes/scheduler/*.json` | no | **no** |

Each service reads the deployment through one small library, **`smconfig`** (`smconfig/__init__.py`). Code never
builds a path to a deployment file by hand. It asks `smconfig.path("lights", "patch.yaml")`. Services find each
other with `smconfig.url("room")`. A switchable feature checks `smconfig.enabled("generation")`.

## A deployment directory

```
deployments/<id>/
  deployment.yaml            the manifest (below)
  publish-destinations.json  screens and publish targets: buckets, sizes, Alexa, TV address, `live` block
  rooms.json                 which room holds which screens, lights, blinds, sensors, effects
  intensity.json             light sensor -> screens group, and the lux curve
  show.yaml                  this site's layer over config/room/show.yaml (deep-merged)
  dot.json                   the e-ink tags
  room/
    room.yaml                room_service settings (was config/room/room.prod.yaml)
    modes.json               the room's modes (they name this site's devices)
    auto_hints.json          "turn Auto on" hints
    sonos.json  tv.json      Sonos zones; TV control (placeholder)
    rf/*.json                RF devices: blinds hub, glitterball, pinspots (one file per device; the app adds more)
  lights/
    patch.yaml               DMX layout, Hue bridge and lamps, screen fixtures
    fixtures.yaml            the light registry (hand-written)
    fixtures.added.yaml      lights added on the Lights page (written by the app)
    fixtures.setup.yaml      per-light setup from the Lights page (written by the app)
    space.yaml               the room's geometry (hand-written)
    space.added.yaml         the room map from the Lights page (written by the app)
  calibration/
    latency.json             presented-to-photon latencies per screen and output
    audio_cal.json           mic calibration
  live/
    stream_switches.json     per-screen encoder switches for the renderer
  services/
    sm-stream.env  sm-show.env  sm-dot.env   scripts/ctl's flags per service
```

A file the deployment does not have means the site has none of that thing. For example, a deployment with no
`room/sonos.json` has no Sonos. `smconfig check` says which files a switched-on capability needs.

Files the app writes (the `*.added.yaml` and `fixtures.setup.yaml` files, `rf/*.json` learned codes and new devices,
`intensity.json` from the Light Sense page, `rooms.json` from Settings, `dot.json` from the type lab) are written to the
running deployment's directory. That keeps each site's edits in its own directory, so a pull never mixes them up.

### The manifest: `deployment.yaml`

```yaml
id: finsbury                       # must match the directory name
name: Drawing room (PC22)
location: {lat: 51.5074, lon: -0.1278}   # default for a screen's light curve when its intensity_cfg has no lat/lon
default_room: drawing-room         # the room the art director and auto hints use when none is named
capabilities:                      # on/off per feature; one left out is OFF
  generation: true
  ...
ports: {flask: 5000, overlay_ws: 8765, room: 8700, show_bus: 8701, stream: 8702, show: 8703, dot: 8704}
kiosk:                             # only a site with the kiosk mini-PC
  host: 192.168.1.92
  user: gjbm2
  base_url: http://95.141.21.170:8000
  screens: {north-screen: {name: north, output: HDMI-A-1, cdp_port: 9222}, ...}
host:                              # machine paths some services need
  fonts_dir: /mnt/c/Windows/Fonts
  windows_python: /mnt/c/Windows/py.exe
  piano_audition_dir: "/mnt/c/Users/Greg/Music/Piano audition"
```

### Capabilities

| Capability | When off |
|---|---|
| `generation` | AI image and video generation (RunPod + LLM) is refused: `/api/generate-image`, the scheduler's generate and animate, Alexa's generation. Buckets, publishing, uploads and the scheduler's display from bucket all still work. The generator page says generation is off. |
| `art_director` | Flask's art director (fills thin screen libraries) is not started. Needs `generation`. |
| `art_tags` | AI tagging of bucket files is not started. |
| `alexa` | `/api/alexa` answers 404. |
| `tv_power` | Samsung Frame wake, sleep, standby and sync are logged no-ops. |
| `kiosk_admin` | The admin API's kiosk actions over SSH are refused. |
| `alerts` | No alert e-mails or healthcheck pings. |
| `ngrok` | `start.sh` does not start the ngrok tunnel. |
| `room` | room_service is not started inside Flask (no :8700, no Room page backend). |
| `live` | stream_service is not started by `scripts/ctl start all`; no screen can go live. |
| `show` | show_service is not started by `scripts/ctl start all`. |
| `dot` | dot_service is not started by `scripts/ctl start all`. |
| `dmx` | room_service's DMX stack (Art-Net in, show bus, Hue patch) and show_service's virtual Maestro are off. |
| `hue` | No Hue lamps. |
| `rf` | No RF devices (glitterball, pinspots): they become inert placeholders. |
| `blinds` | No blinds hub: placeholder. |
| `sonos` | No Sonos zones: placeholder. |

## Commands

```
python -m smconfig list                 # deployments in this checkout
python -m smconfig which                # the selected deployment and its directory
python -m smconfig check                # validate: manifest, required files, parsing, cross-references
python -m smconfig dump                 # everything every process resolves, as JSON (diff it across a change)
python -m smconfig get ports.room       # one value, for scripts
python -m smconfig enabled dot && ...   # exit 0 when a capability is on
```

Run `check` after any edit to a deployment. The test suite runs it for every shipped deployment
(`smconfig/tests`).

## Setting up a new deployment

1. Copy a deployment directory as a starting point: `cp -r deployments/example deployments/<id>`. Set `id:` to the
   new name.
2. In `deployment.yaml`, switch on only what this site has.
3. Describe the screens in `publish-destinations.json`. A screen that goes live needs a `live` block. Then fill in
   `rooms.json`.
4. Add device files only for devices you have (lights in `lights/`, RF devices in `room/rf/`, and so on).
5. On the machine, set `SM_DEPLOYMENT=<id>` in `.env`. Also set any `${VAR}` the deployment's `services/*.env`
   use, usually `SM_LAN_HOST=<this machine's LAN address>`. (`${PORT_<SERVICE>}` needs nothing: it is the
   manifest's port.) Then run `python -m smconfig check`. It validates the files, the modes against room_service's
   model, and the `${VAR}`s.
6. Start as usual: `./start.sh start flask`, then `scripts/ctl start all`, which starts only the switched-on services.

## Migrating PC22 (finsbury) from the old layout

The old layout kept these files in `config/room/`, `routes/data/` and `deploy/`.
`smconfig/legacy.py` lists every move. Every moved file holds the same data as before. Some differ in bytes:

- Comments that named old paths now name the new ones. The app-written files (`*.added.yaml`, `fixtures.setup.yaml`)
  carry the app's new header.
- `services/sm-stream.env` says `${SM_LAN_HOST}` where it said `192.168.1.205`. `scripts/ctl` fills that in from
  `.env`.
- One declared data edit (`smconfig/legacy.py` `CONTENT_EDITS`): the `devtest` screen's `live` block gains
  `"fixture": "screen.north"`. This is the fact stream_service used to hard-code.
- `show.yaml`'s site keys moved to `deployments/finsbury/show.yaml`. The layered result equals the old file exactly.

The migration proves all of this file by file. It compares parsed data, expanded env values, the declared edit and
the layered `show.yaml`.

**Before you start.**

- Tell the other sessions. Ask them to commit their work, so `git status` is clean for `config/`, `routes/data/` and
  `deploy/`, and to stop editing those paths. The pull moves files they edit.
- `git status -- '*-screen-token*.txt'`. If a Samsung token shows as modified (a TV re-paired since the last commit),
  copy it first: `mkdir -p -m 700 ~/.config/screen-machine/samsung && cp -p north-screen-token.txt
  ~/.config/screen-machine/samsung/ && chmod 600 ~/.config/screen-machine/samsung/*`. Then delete the repo copy
  (`rm north-screen-token.txt`): the pull deletes it anyway, and a local deletion doesn't block it. The migration keeps a token that is already in place.
- `.env` must have `SM_LAN_HOST=192.168.1.205` (it does today), with each key on one line only. The migration checks
  both.

```bash
cd ~/dev/screen-machine

# 1. Stop the services (Spectre's cards, or:)
scripts/ctl stop all
./start.sh stop flask

# 2. Pull
git pull

# 3. Plan, then apply, the migration
.venv/bin/python scripts/migrate_deployment.py --deployment finsbury           # read the plan
.venv/bin/python scripts/migrate_deployment.py --deployment finsbury --apply   # do it; must end "OK"

# 4. Rebuild the frontend (CLAUDE.md's deploy line)
npx vite build --outDir dist_next --emptyOutDir && rm -rf dist_prev && mv dist dist_prev && mv dist_next dist

# 5. Start
./start.sh start flask
scripts/ctl start all

# 6. Check the running services
.venv/bin/python scripts/migrate_deployment.py --deployment finsbury --verify-live
```

`--apply` does the following:
- adds `SM_DEPLOYMENT=finsbury` to `.env`;
- moves any file the app wrote into an old location since the last commit (for example, a new RF device);
- moves the Samsung pairing tokens out of the repo root into `~/.config/screen-machine/samsung/`;
- proves every moved file matches its pre-pull content (`ORIG_HEAD`);
- runs `smconfig check`, and saves the effective config to `logs/`;
- lists anything in Spectre or the boot script that still names an old path.

It stops on a conflict without overwriting anything.

**Then test** (see "Testing after the migration" below).

**Rollback** (without reset or checkout, per CLAUDE.md rule 9):

```bash
scripts/ctl stop all; ./start.sh stop flask
git add -- deployments/                       # commit what the app wrote since the migration, so the revert carries it
git commit -m "Before rolling back the deployments layout" || true
git revert --no-edit <first deployments commit>^..<last deployments commit>   # git log --oneline -- smconfig/
cp -p ~/.config/screen-machine/samsung/*-token*.txt .                       # the old code reads the tokens here
./start.sh start flask; scripts/ctl start all
```

The revert moves every file back to its old path, edits included. The migration's report in `logs/migration-*.txt`
lists any leftover files it moved (the `do move` lines). Move those back by hand. `SM_DEPLOYMENT` in `.env` is
harmless to the old code.

### Old path → new path

| Old | New (in `deployments/finsbury/`) |
|---|---|
| `routes/data/publish-destinations.json` (and the `src/data/` symlink) | `publish-destinations.json` |
| `routes/data/rooms.json` | `rooms.json` |
| `routes/data/intensity.json` | `intensity.json` |
| `config/room/room.prod.yaml` | `room/room.yaml` |
| `config/room/modes.json`, `auto_hints.json`, `sonos.json`, `tv.json` | `room/…` |
| `config/room/rf/*.json` (not `types/`) | `room/rf/*.json` |
| `config/room/patch.yaml`, `fixtures*.yaml`, `space*.yaml` | `lights/…` |
| `config/room/latency.json`, `audio_cal.json` | `calibration/…` |
| `config/room/dot.json` | `dot.json` |
| `config/live/stream_switches.json` | `live/stream_switches.json` |
| `deploy/sm-*.env` | `services/sm-*.env` |
| site keys of `config/room/show.yaml` | `show.yaml` (the layer) |
| `*-screen-token*.txt` in the repo root | `~/.config/screen-machine/samsung/` |

These stay where they were, shared by every deployment: `config/room/show.yaml` (tuning), `config/room/vibes.yaml`,
`config/room/rf/types/`, `config/room/fixture-types/`, `config/live/*`, `config/llm.yaml`, `config/art_tags.yaml`,
`config/piano/`, and `routes/data/*` templates.

## Testing after the migration

1. **Static.** `python -m smconfig check` is clean, and `scripts/migrate_deployment.py --verify-live` says OK.
2. **The room.** The Room page lists the same rooms, modes and screens. Switching a mode moves the same devices
   (lamps, glitterball, pinspots, blinds).
3. **Screens.** Both Frames show art. A mode with live screens cuts to live and back cleanly, the masks follow the
   light sensor, and the light masks still push instantly.
4. **The show.** The kiosk mic and piano appear as senders. Auto follows the music, and the lights follow the show.
5. **Generation.** A prompt on the generator page generates and publishes. The scheduler's running schedules carry on.
6. **Tags and TVs.** The e-ink tags redraw. The scheduler's TV wake and sleep work.
7. **Performance.** Live sessions start in the same time as before. `logs/room_service.log`, `logs/flask_console.log`
   and the show and stream logs show no new errors, and CPU and GPU use is the same as before for the same mode.
