# Light-sensor ingest keys

`make_sensor_key.py` creates (or rotates) the key a light relay uses on room_service's
`WS /v1/ingest/lux` (LIVE_V1 §2.2b). The key never appears on screen, in chat or in a log:
the script writes it to two 0600 files and prints only their paths.

| File | Where | What |
|---|---|---|
| `~/.config/screen-machine/room_sensors.json` | this host (room_service) | `{sensor_id: key}`; room_service re-reads it on change |
| `~/.config/screen-machine/light-relay/<sensor_id>.room.json` | this host, to copy | `{"url", "key"}` for the relay |
| `~/.config/light-relay/room.json` | the kiosk (calib-3 relay) | the copy; read at relay start and on SIGHUP |

## Owner procedure (LIVE_V1 Q11: deploying to the kiosk is the owner's step)

```bash
~/.venvs/sm-room/bin/python tools/room/ingest_keys/make_sensor_key.py living-room --url ws://192.168.1.205:8700/v1/ingest/lux
ssh gjbm2@192.168.1.10 'umask 077; mkdir -p ~/.config/light-relay && cat > ~/.config/light-relay/room.json && kill -HUP "$(sed -n "s/.*\"pid\": *\([0-9]*\).*/\1/p" ~/latency_cal/relay.json)"' < ~/.config/screen-machine/light-relay/living-room.room.json
```

- The second line writes the file 0600 (`umask 077`) and sends SIGHUP to the relay by the PID it
  records in `~/latency_cal/relay.json`. It deliberately avoids `pkill -f lightsense.py`, which would
  also match the remote shell running the command.
- The relay must be calib-3 (`tools/room/latency_cal/lightsense.py`; `~/latency_cal/relay.json` says
  `"version": "calib-3"`). An older relay treats SIGHUP as fatal; systemd then restarts it without the
  sink.
- `--url` must point at the room_service the kiosk can reach: production is `:8700` (needs the
  Spectre portproxy `0.0.0.0:8700 -> WSL:8700`, docs/PORTS.md); a dev instance uses its own port.
- Check: the relay log says `Room ingest: on, ws://…` then `Room ingest: connected`, and
  `GET /api/v1/status` on room_service shows the sensor under `mask.ingest.connections`.

## Rotating, removing

- **Rotate:** run the first line again, then the second. Until the relay has the new file,
  room_service closes its socket with 4401 and the relay keeps its legacy send to Flask's :8765.
- **Remove a sensor:** delete its entry from `room_sensors.json`. With no keys left (or no file),
  ingest is off and every screen's mask comes from Flask, as before.
- **Turn the relay's sink off:** delete `~/.config/light-relay/room.json` on the kiosk and SIGHUP the
  relay (second line without the `cat`).
