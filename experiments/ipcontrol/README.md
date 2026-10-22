# Samsung IP Control on the Frame Pro TVs

Spike tooling from 2026-10-13 (screen-machine-85, Game Mode spike). Evidence and context are in
`docs/GAME_MODE_AUTO_SPIKE_HANDOVER.md` §10. This is the basis for the server-side, read-only TV state adapter
(DevicesPort, `AV_PROGRAMME.md` K5).

## The interface

- **Endpoint:** both TVs (north `192.168.1.150`, south `192.168.1.151`, QE75LS03FWUXXU) serve JSON-RPC 2.0 by HTTPS
  POST to `https://<tv>:1516/`.
  - The certificate is self-signed (`CN = Samsung IP Control G2`), so turn verification off.
  - Send `Content-Type: application/json` and `Accept: application/json`.
  - Request body: `{"jsonrpc":"2.0","id":"1","method":"<m>","params":{"AccessToken":"<t>"}}`.
  - Params containing only `AccessToken` make the call a get. Any extra key, e.g. `"gameMode":"On"`, makes it a set.
- **Tokens:**
  - Both TVs were paired on 2026-10-13. Tokens are in `~/.config/screen-machine/ipcontrol/<ip>.token` on the WSL host
    (mode 600) and **never in the repo**.
  - Re-pairing (`createAccessToken`) shows an Allow prompt on the TV. Someone must accept it with the remote, and the TV
    must not be in Art Mode.
- **Errors:**
  - A bare `{"code": -32700, "message": "Parse error"}` with no envelope means the token is missing or stale.
  - `-32601` from a "none-ambient" method (picture levels, input, volume …) can just mean the TV is in Art Mode.
  - `-32002` means the TV refused in its current state.
- **Protocol reference:** TheFab21/ha-samsungtv-smart, `IP_Control_Protocol_Reference.md` and
  `notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md` (a decompile of the 2025 Frame firmware).

## ipctl.py

Standard library only, so plain `python3` runs it. It is read-only unless you use `pair` or `set`.

```bash
python3 ipctl.py <ip> pair                      # Allow prompt ON THE TV; stores the token
python3 ipctl.py <ip> get <method> [...]        # e.g. get getTVStates gameModeControl getDeviceInformation
python3 ipctl.py <ip> survey                    # every getter relevant to picture state
python3 ipctl.py <ip> watch [secs]              # poll gameMode + pictureMode/input; logs changes, errors, 5-min stats
python3 ipctl.py <ip> set <method> <key>=<val>  # CHANGES A TV SETTING; owner approval for each change
```

## Behaviour to design for

- **One connection at a time.** The TV serves a single connection, and overlapping requests get reset (also seen by
  the HA integration). All callers must be serialised through one per-TV client.
- **Reliability over the TVs' Wi-Fi** (2026-10-13, two soaks, 19:19–19:29 and 19:37–19:44 UTC; each poll was
  `gameModeControl` + `getTVStates` every 2 s from WSL):

  | Measure | Result |
  |---|---|
  | Calls | ≈ 2,400 |
  | Timeouts or connection failures | 0 |
  | Empty replies (`{"jsonrpc":"2.0","id":"1"}`, no `result`) | 2; retry them |
  | Poll time, two calls per poll | p50 ≈ 660 ms, p95 770–850 ms, max 3.5 s |
  | Per call (each opens TLS) | ≈ 330 ms |

- **Setters work on HDMI.** `gameModeControl` set to On was confirmed by the readback `pictureMode: Game` in about
  1.7 s, with no hotplug on the kiosk. Game Mode is still never used: it shows the Samsung Game Bar and flickers.

## Baseline readback, 2026-10-13 (19:16 and 19:37 UTC, identical)

Both TVs are identical except volume (north 28, south 100). This is the reference for settings-drift checks;
firmware updates reset picture settings.

| Field | Value |
|---|---|
| `getDeviceInformation` | modelID `25_RSM_FTV`, FWVersion `T-RSMFDEUC-0090-1296.8` |
| `getTVStates` | pictureMode `FilmmakerMode`, inputSource `HDMI1`, pictureSize `16:9`, soundMode `Standard`, speakerSelect `internal`, mute `muteOff` |
| `getVideoStates` | contrast 45, sharpness 10, brightness 0, color 25, tint 0 |
| `gameModeControl` | gameMode `Auto` (the owner may set it to Off) |
| `colorToneControl` | `Warm2` |
| `gammaModeControl` | `BT.1886` |
| `colorSpaceControl` | `Auto` |
| `localDimmingControl` | `Standard` |
| `backlightControl` | 40 |
| Off | `filmMode`, `autoMotionPlus`, `contrastEnhancer`, `digitalCleanView`, `brightnessOptimization` |
| `artModeControl` / `powerControl` | `artModeOff` / `powerOn` |
