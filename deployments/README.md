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
5. On the machine: `SM_DEPLOYMENT=<id>` in `.env`, then `python -m smconfig check`.
6. Start as usual: `./start.sh start flask`, then `scripts/ctl start all`, which starts only the switched-on services.

## Migrating PC22 (finsbury) from the old layout

The old layout kept these files in `config/room/`, `routes/data/` and `deploy/`.
`smconfig/legacy.py` lists every move. The content of each file is byte-for-byte unchanged, except `show.yaml`. Its
site keys moved to `deployments/finsbury/show.yaml`, and the layered result equals the old file exactly.

**Before you start.** Tell the other sessions. Ask them to commit their work, so `git status` is clean for
`config/`, `routes/data/` and `deploy/`, and to stop editing those paths. The pull moves files they edit.

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

**Rollback.** `git checkout <the commit before the pull> -- .` won't do, because the files moved. Use:

```bash
git reset --keep ORIG_HEAD            # back to the pre-pull commit (only if nothing was committed since)
# put the Samsung tokens back in the repo root if TV control is needed:
cp ~/.config/screen-machine/samsung/*-token*.txt .
./start.sh restart flask; scripts/ctl restart all
```

`.env`'s `SM_DEPLOYMENT` line is harmless to the old code.

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
