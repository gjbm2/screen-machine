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
| Machine addresses, API keys, `SM_DEPLOYMENT` (see "Secrets") | `.env` | no | **no** |
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
  rooms.json                 which room holds which screens, lights, blinds, sensors, effects (and, per room,
                             "own_modes" and "show": see "Rooms with their own modes")
  intensity.json             light sensor -> screens group, and the lux curve
  show.yaml                  this site's layer over config/room/show.yaml (deep-merged)
  dot.json                   the e-ink tags
  room/
    room.yaml                room_service settings (was config/room/room.prod.yaml)
    modes.json               the room's modes (they name this site's devices)
    modes.<room>.json        the modes of a room with "own_modes": true (see "Rooms with their own modes")
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
default_room: drawing-room         # the room a caller means when it names none (Alexa's fallback); else rooms.json's first
art_director: {modes: [ambient], rooms: [drawing-room]}   # see "The art director" below
wording: {art_space: a living room, screen_device: TV, ...}  # see "Site wording" below
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

### The art director

Flask's art director (capability `art_director`) tops up thin screen libraries. `art_director.modes` lists the room
modes it works in; with none listed it never makes anything (mode ids are each site's own). `art_director.rooms` lists
the rooms whose screens it looks after (a single id is fine); left out, it means every rooms.json room that has screens.
finsbury sets `{modes: [ambient], rooms: [drawing-room]}`, which is what was hard-coded before. Without `rooms` it
would also make art for the lab's devtest screen.

### Site wording

Prompts and page copy that used to name finsbury facts ("a living room", "two 4K TVs", "PC22") now read a phrase from
the manifest's `wording` map, through `smconfig.deployment().words(key, generic default)`. A deployment without the
key gets the generic phrase. finsbury's values keep PC22's text exactly as it was.

| Key | Used by | Generic default | finsbury |
|---|---|---|---|
| `art_screen` | image generator: "made for ... in ..." | a screen | a 4K TV |
| `art_space` | image generator | a room | a living room |
| `art_library` | art tagger | art library | living-room art library |
| `art_screens` | art tagger rubric (`config/art_tags.yaml`) | the screens | two 4K TVs |
| `listen_space` | show_service's listener prompt | a room | a home's drawing room |
| `screen_device` | room_service page copy ("TV pages") | display | TV |
| `lights_menu` | the app menu's Lights hint | Lights and appliances | Lamps, glitterball, pinspots |
| `windows_host` | show_service piano and Sonos copy | this PC | PC22 |
| `windows_speakers` | show_service piano output | This PC's speakers | PC22 speakers |
| `artnet_no_replies` | show_service's Art-Net hint | (generic hint) | (names PC22's relay) |
| `ad_room` | show_service's treatment prompt: "the art director of ..." | a room | a drawing room |
| `ad_rig` | show_service's treatment prompt: what is in the room | screens showing generative scenes, and lights | two large TVs showing generative scenes, lamps, and on some modes a glitterball and pinspots |
| `site_address` | the Alexa overlays: the address under the QR | (none: the line is left out) | screen.finsburybankforsavings.com |
| `feel_room` | `{feel_room}` in a feel's persona (`config/room/vibes.yaml`) | room | drawing room |

`smconfig check` rejects a key that isn't in this list (`smconfig/check.py` `WORDING_KEYS`), since a typo would silently
fall back to the generic phrase. A new key goes in both places.

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

## Rooms with their own modes

By default every room of a deployment shares the site's modes (`room/modes.json`) and follows the one show. Two
optional keys on a room in `rooms.json` change that:

```json
"back": {"name": "Back room", "screens": ["bs"], "own_modes": true, "show": false}
```

- `"own_modes": true`: the room switches modes on its own. Its modes are in `room/modes.<room>.json`, which has the
  same form as `modes.json`. The room owns what its `rooms.json` entry lists: its screens (their page, diag and TV
  members too), blinds, lights listed by id, effects and its Sonos zone. The site's modes no longer move any of
  them, and the room's switches never touch the site's. In the room's file a bare `screens`, `page`, `sensor_mask`
  or `brightness` means the room's own. `brightness.screens`, `show.mic`, `show.ai` and `sensor_mask.main` are
  aliases for the room's own members, so a copy of another site's `modes.json` works there (`show.mic` and `show.ai`
  only in a room that follows the show). `show.main`, `autopilot`,
  `brightness.lamps` and anything another room lists are refused, with the owner named.
- `"show": false`: the room's screens are outside the show. show_service sends them no palette, composition or
  ensemble part, and a palette picked on one of them never holds the show's. stream_service gives them no direction,
  room events, hold, ensemble, beat clock, gestures or music feed. Lights the room lists still play the show's look,
  because there is one look engine.

The two keys are independent: a room can have its own modes and still follow the show. What stays site-wide whatever
the keys say: Auto, the feel (`show.main`), the lamps' brightness (`brightness.lamps`, one Hue stream), the mic and
AI listening (a room that follows the show claims them through its own members, and the claims are combined), and
the art director and hints, which serve the default room. The ensemble is the default room's too: a screen fixture
whose screen is in another room is never one of its players. The default room (`deployment.yaml` `default_room`, else
the first room) can have neither key.

room_service reads the rooms when it starts. After changing `own_modes`, restart room_service, which means
restarting Flask (tell the other sessions first). Until then its status says so in `mode.rooms_error`. show_service
and stream_service pick up `show` on their own.

In room_service an `own_modes` room is a mode zone (`room_service/zones.py`, described in `room_service/README.md`).
It keeps its state in `<state_dir>/rooms/<room>/`. Its switches are `room_mode.transition` events, and status lists it
under `zones`. `PUT /api/v1/mode` and the mode editor's routes take a `room`. At opt-in, the site's stored values for
the room's targets are dropped. If the site was in a mode that only the room now has (Live test, say), the room starts
in it and the site is relabelled to its default. Nothing is sent either way.

Flask follows the zones from room_service's status (`routes/room_client.py`). Alexa looks for a spoken mode in the
asker's room first (if it has its own modes), then in the site, then in the one other room that has that name, so
"Alexa, live test" still works after a room owns Live test. A schedule's `set_mode` and `room_state` use its screen's
room. The scheduler's `_room_mode` event gives a room's screens their room's mode (with `room` in the payload). The
art director checks each of its rooms by that room's own mode. The "turn Auto on" hints never show in such a room.
With no such room, no request carries a `room`.

On the Room page the site's modes get a caption naming the rooms they cover ("Drawing room"), and under them each
such room has its own mode strip under its name: the same tiles, progress line and mode editor, on its own modes
file, with no Auto row. Look says which rooms the show plays in. Settings › Room & modes gets a room picker. A
room's e-ink tag (dot_service) shows that room's mode, and no Auto. Without such a room, none of this shows and the
pages send exactly what they did.

`smconfig check` validates the keys. These are problems:

- a key that isn't `true` or `false`;
- an `own_modes` room without its `modes.<room>.json`, or with one room_service would refuse;
- either key on the default room;
- an `own_modes` room that shares a screen, blind, light or effect with another room, or lists a whole subsystem
  such as `"light"` (list its lights by id).

These are notes, not problems:

- two or more rooms with screens share the site's modes;
- a room with `"show": false` lists lights.

`smconfig` gives code the facts: `own_mode_rooms()`, `out_of_show_rooms()`, `show_screens()` (None when every
screen is in the show), `room_of_screen(dep, screen)` and `room_modes_path(dep, room)`.

Status (10 Nov 2026): no shipped deployment sets either key. `"show": false` works in show_service and
stream_service (room_service's status passes it on in `rooms[]`). `"own_modes"` works in room_service and in
Flask's callers (Alexa, the scheduler, the events bridge, the art director, the hints), the Room page, Settings and
the e-ink tags.

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
(`smconfig/tests`). The same tests fail when code names one of finsbury's screens, rooms, lamps or addresses, or reads
`config/room/show.yaml` without the site's layer (`test_no_site_values.py`): read such things through `smconfig`.

## Setting up a new deployment

1. Copy a deployment directory as a starting point: `cp -r deployments/example deployments/<id>`. Set `id:` to the
   new name.
2. In `deployment.yaml`, switch on only what this site has.
3. Describe the screens in `publish-destinations.json`. A screen that goes live needs a `live` block. Then fill in
   `rooms.json`.
4. Give each screen a screen fixture in `lights/fixtures.yaml` (`type: sm-screen`, `screen: <its id>`), as
   example's `screen.vj`. A screen without one plays on its own and never joins the ensemble (the screens playing
   together, and scenes that span them). Then add device files only for devices you have (lights in `lights/`, RF
   devices in `room/rf/`, and so on).
5. On the machine, set `SM_DEPLOYMENT=<id>` in `.env`. Also set any `${VAR}` the deployment's `services/*.env`
   use, usually `SM_LAN_HOST=<this machine's LAN address>`. (`${PORT_<SERVICE>}` needs nothing: it is the
   manifest's port.) Then set the app's addresses. `npx vite build` bakes them into the TV pages and the Room page,
   so rebuild after changing one. `<host>` is the address the TVs and browsers reach this machine on, and each port
   is the manifest's:
   - `VITE_API_URL=http://<host>:<ports.flask>/api`: the app's API (the TV pages' masks and pictures too);
   - `VITE_WS_HOST=ws://<host>:<ports.overlay_ws>`: the TV pages' overlay socket;
   - `VITE_ROOM_URL=http://<host>:<ports.room>`: room_service, for the TV pages' control socket and the Room page
     (only with `room` on).

   `.env.example` has the other settings. Then run `python -m smconfig check`. It validates the files, the modes
   against room_service's model, and the `${VAR}`s. It also notes any of these addresses that is missing or on
   another port.
6. Run `python -m smconfig.secrets check` and add what it says is missing (next section).
7. Start as usual: `./start.sh start flask`, then `scripts/ctl start all`, which starts only the switched-on services.

## Secrets

The repo is public. No key, token, password or pairing goes in git, and that includes `deployments/`. There are two
kinds of secret:

- **Keys in `.env`**: the API keys (OpenAI, Gemini, RunPod, AssemblyAI) and the Gmail alert sender. These work on any
  machine. Every process reads them from the repo's untracked `.env`, or from its environment.
- **Files under `~/.config/screen-machine/`** (`smconfig.secret(...)`): the Hue bridge pairing, the Samsung TV tokens,
  the Dot. key, the Bloc hub key, the Cloudflare token and room_service's tokens. Each was made on one machine for
  its own devices, so it stays on that machine. Pair again on a new machine; don't copy these.

The kiosk's SSH login (`MEDIA_SERVER_HOST`, `_USER`, `_PASSWORD`) is in a third place,
`media-server/local-scripts/.env`, which the admin page's kiosk actions and the `media-server/` scripts read.

All of these are this user's only: `.env` and `media-server/local-scripts/.env` 0600, `~/.config/screen-machine/`
0700, the files in it 0600. Nothing that reads them runs as another user. `scripts/migrate_deployment.py --apply`
sets the first three.

`python -m smconfig.secrets check` lists every secret the deployment's switched-on capabilities use, each as set or
missing. It never prints a value. It also warns, naming the path, about any of those files or the folder that other
users on the machine can open. The list of what each capability uses is `NEEDS` in `smconfig/secrets.py`.

**Sharing keys between machines.** Copy them once through a private file, never through git. A synced folder
(OneDrive) keeps a copy in the cloud, and 0600 means nothing on a Windows drive, so only an encrypted copy goes
there. Encrypt it with `gpg -c` (PC22 has gpg; on another Ubuntu, `sudo apt install gnupg`):

```
# on the machine that has them (PC22): export locally, encrypt into the synced folder, delete the plain file
python -m smconfig.secrets export ~/screen-machine-keys.env
gpg -c -o /mnt/c/Users/Greg/OneDrive/Private/screen-machine-keys.env.gpg ~/screen-machine-keys.env   # a passphrase
rm ~/screen-machine-keys.env
# on the other machine, once the file has synced there:
(umask 077; gpg -d -o ~/screen-machine-keys.env /mnt/c/Users/<you>/OneDrive/Private/screen-machine-keys.env.gpg)
python -m smconfig.secrets import ~/screen-machine-keys.env
rm ~/screen-machine-keys.env /mnt/c/Users/<you>/OneDrive/Private/screen-machine-keys.env.gpg
```

- `export` writes only the shareable keys this machine has, mode 0600, and refuses any path inside the repo. Pointed
  at a Windows drive (`/mnt/c/...`), it still writes, but warns that 0600 isn't enforced there and the folder may sync
  to the cloud.
- `import` adds only the keys `.env` lacks; `--overwrite` replaces ones it has. It never prints values and leaves `.env`
  at 0600.
- Restart the services that use the keys afterwards.
- Each machine keeps its own copy, so nothing at run time depends on OneDrive.
- Delete both copies (plain and encrypted) when you're done.

The laptop rig has generation off, so it needs none of these today. The listener's "Describe it" uses an LLM key if
one is set and works without one.

## Room audio on a WSL machine

show_service runs in WSL and never opens a Windows sound device. A small agent on the Windows side
(`tools/show_sender/pc22_agent.py`) captures the Windows default input and sends it as a VBAN stream. show_service
subscribes to every sender its `show.yaml` lists.

- **The deployment** lists the sender in `deployments/<id>/show.yaml`. Use the host alias `windows`, which
  show_service resolves to the Windows machine its WSL runs on, so there's no fixed address to keep up to date:
  ```yaml
  senders:
    laptop: {hosts: [windows], port: 6984, streams: [room-mic], label: Laptop}
  ```
  Name the stream `room-mic` when it is the room's main mic, because Auto listens to `room-mic` first.
- **Windows needs** Python 3.12 with `py -3.12 -m pip install --user sounddevice numpy`, and microphone access for
  desktop apps switched on (Settings > Privacy & security > Microphone).
- **Install:** run `bash tools/show_sender/deploy-pc22.sh` in WSL. Then, once, in an **admin** PowerShell, run
  `powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\<you>\dev\screen-machine\tools\show_sender\windows-setup.ps1`.
  It sets the firewall (it removes the Block rules Windows adds when its "Allow access" prompt is dismissed, and
  allows UDP 6984 from WSL only), adds a start-at-logon launcher, and starts the agent.
- **Check:** `tail /mnt/c/Users/<you>/AppData/Local/screen-machine/audio-agent/agent.log` should say `mic: off
  (show_service: nothing needs it)` (heard, idle) or `on (subscribed)`. The mic opens only while something needs it
  (Auto, a mode that follows the music, or AI listening).
- **Auto's ears in full** also need the ML worker. Build `~/.venvs/sm-show-ml` from `show_service/ml/requirements.txt`,
  then fetch the models with `python3 tools/show/fetch_models.py`. The one exported model is made by
  `tools/show/export_onnx.py beat-this-small0` (its docstring has the throwaway venv; that venv also needs PyYAML).

PC22 does the Windows side through Spectre's audio-agent card instead of the script.

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
- makes `.env` and `media-server/local-scripts/.env` 0600 and `~/.config/screen-machine/` 0700 (it says when one was
  wider);
- proves every moved file matches its pre-pull content (`ORIG_HEAD`);
- runs `smconfig check`, and saves the effective config to `logs/`;
- lists anything in Spectre or the boot script that still names an old path;
- installs PyYAML into every `~/.venvs/sm-*` that lacks it, with one `PyYAML in ~/.venvs/<name>` line each. If pip
  can't install it, that line is a FAIL with the command to run by hand.

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

A file that turns up at an old path later (a session or tool following an old note, say) is read by nothing, so its
edits never take effect. `smconfig check` notes each one, and the migration's plan and `--apply` move it.

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
