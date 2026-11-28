# PC22: finsbury's config needs rebuilding for the current code (28 Nov 2026)

Written for a session on PC22. **The job is yours: go through `deployments/finsbury/` and rebuild it properly for
the code as it now is.** This note only says that it is needed and where the gaps were seen. It does not design
finsbury's config; that is for PC22 to work out for its own room.

## Why

PC22's code is current: it merged the laptop's work on 28 Nov 09:18 (`e8f06c05`). Its config is not. finsbury's
files were last brought up on 27 Nov 07:45 (`5ed4786c` the GigBARs, `1a1bcde1` Karaoke). About 180 commits have
landed since, most of them made and tried on the laptop against `deployments/laptop/`, and several read config
that finsbury does not have.

**Nothing warns you.** `python -m smconfig check` says `ok` for finsbury and the rig reports no problems. A
feature with no config for it does nothing, or falls back to a default that was chosen for the laptop's room.

## What to compare against

- `deployments/laptop/`: the config the new code was built and run against. It is a working example for another
  room, not a template to paste.
- `config/room/show.yaml`: the shared settings; its comments say which keys a site sets in its own `show.yaml`.
- The docs for each feature: `docs/LIGHTS.md`, `docs/GIGBAR_RIG.md`, `docs/KARAOKE.md`, `docs/SOUND.md`,
  `docs/PICKUP_FROM_LAPTOP.md`.
- `git log 5ed4786c..HEAD`: everything since finsbury was last brought up.

## Gaps seen (a starting list, not the whole of it)

Seen from the laptop on 28 Nov about 10:00, in finsbury's files in git (`a8361670`) and in PC22's own read-only
answers (`/rs/show/api/v1/rig`, `.../rig/space`, `.../lights/library`, `/api/sound/outputs`, `/api/karaoke/now`).

| Where | finsbury now | What the code now reads |
|---|---|---|
| `show.yaml` `lights.groups` | Only `remote-lights` added. The shared `lamps` group holds 17 lights: pars, derbies, lasers, strobes, the hazer and the four Hue lamps. | Groups for the bars' parts (the laptop has `lamps`, `lasers`, `derbies`, `flashes`, with `scenes_for`, `carries_beat`, `reads_under_lux`, `lands_on`). The laser, derby and strobe scenes are drawn only by a group that takes them. |
| `show.yaml` `lights.lineups` | None. | Which parts play in each kind of section of a song (the laptop has thirteen). |
| `show.yaml` `lights.room_light` | Not set: PC22 runs on the shared `other_lux: 1.0`, the laptop room's figure. | The room's own figures. |
| `lights/fixtures.yaml` | Each bar's strobes are one fixture (`gigbar-flash`). No `streaks` on the derby type. | Each strobe LED as a light of its own (`gigbar-strobe-led`, `gigbar-strobe-led-s`); `streaks` on `gigbar-derby`. |
| `lights/space.yaml`, `space.added.yaml` | No `layouts`, no `bars`: every part placed on its own, with the laptop room's provisional places and poses. No `singer` place. `haze_half_life_s` unset. | A bar as one thing (`layouts`, `bars`); the places the scenes aim at; the room's own measurements. |
| `lights/fixtures.setup.yaml` | Empty. | Each light's lean, each head's home (set on the Lights page, with the real bars). |
| `room/modes.json` | The `karaoke` mode has no `director.light_scenes` (Party and Disco have theirs). No `transition_s`. | A mode's scene tags pick out the scenes written for it. |
| `calibration/latency.json` | No sound output measured. Karaoke plays on `sonos:drawing-room` at the default 500 ms. | Each sound output's delay, measured in Settings › Sound & timing. |

**The owner's word on one of these (28 Nov):** the four Hue lamps in the room "are designed to be part of the
washes".

Looked at and found current: `deployment.yaml`, `publish-destinations.json`, `rooms.json`, `room/room.yaml`,
`services/*.env`.

## On the machine, not in git

- **PC22's working tree has config that git does not.** The running room has a `dance-floor` region, a keep-out
  round the mirror ball and a planned screen `sm-screen-1`, written by the Room editor. Run
  `git status deployments/finsbury` and commit PC22's own files before changing anything.
- `tools/virtual_node/` changed on 27 Nov; Spectre's `screen-machine-virtual-node` card re-copies it on restart.
- Karaoke duets (28 Nov) run `tools/karaoke/voices.py` in the karaoke venv (`librosa`, `onnxruntime`,
  `torchaudio`).
- `python -m smconfig.secrets check`.

## Rules for the work

- Tell the owning session before editing its file (docs/AV_PROGRAMME.md §11).
- `python -m smconfig check` after each edit. show_service re-reads `show.yaml`, `fixtures.yaml` and the space
  files by itself; room_service re-reads `modes.json`.
- When it is done, update this note or delete it.
