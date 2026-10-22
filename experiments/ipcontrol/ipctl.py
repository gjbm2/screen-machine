#!/usr/bin/env python3
"""Spike client for Samsung IP Control (JSON-RPC over HTTPS :1516) on the Frame Pro TVs.

Read-only by default. Pairing and setters are separate, explicit subcommands.

  ipctl.py <ip> pair                      # shows an Allow prompt ON THE TV (owner must accept; not in Art Mode)
  ipctl.py <ip> get <method> [...]        # e.g. get gameModeControl getTVStates getDeviceInformation
  ipctl.py <ip> survey                    # all read-only getters relevant to the Game Mode spike
  ipctl.py <ip> watch [secs]              # poll gameModeControl + getTVStates.pictureMode, print changes
  ipctl.py <ip> set <method> <key>=<val>  # CHANGES A TV SETTING - owner approval per change

Token: one file per TV at $IPCTL_TOKEN_DIR (default ~/.config/screen-machine/ipcontrol), mode 600.
The TV handles one connection at a time, so calls are strictly sequential.
Error -32700 "Parse error" with no envelope = missing/stale token (per TheFab21/ha-samsungtv-smart).
"""
import http.client
import json
import os
import ssl
import sys
import time

PORT = 1516
TOKEN_DIR = os.path.expanduser(os.environ.get('IPCTL_TOKEN_DIR', '~/.config/screen-machine/ipcontrol'))
SURVEY = ['getDeviceInformation', 'getTVStates', 'gameModeControl', 'getVideoStates', 'artModeControl',
          'powerControl', 'colorToneControl', 'gammaModeControl', 'colorSpaceControl', 'filmModeControl',
          'autoMotionPlusControl', 'contrastEnhancerControl', 'localDimmingControl', 'digitalCleanViewControl',
          'brightnessOptimizationControl', 'backlightControl']


def token_path(ip):
    return os.path.join(TOKEN_DIR, f'{ip}.token')


def load_token(ip):
    try:
        with open(token_path(ip)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def call(ip, method, params=None, token=None, timeout=8):
    body = {'jsonrpc': '2.0', 'id': '1', 'method': method}
    if token is not None or params:
        body['params'] = {**({'AccessToken': token} if token is not None else {}), **(params or {})}
    payload = json.dumps(body).encode()
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(ip, PORT, timeout=timeout, context=ctx)
    try:
        conn.request('POST', '/', payload, {'Content-Type': 'application/json', 'Accept': 'application/json'})
        raw = conn.getresponse().read()
    finally:
        conn.close()
    data = json.loads(raw)
    if 'result' in data:
        return True, data['result']
    return False, data.get('error', data)


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    ip, cmd, rest = argv[0], argv[1], argv[2:]
    if cmd == 'pair':
        print(f'{time.strftime("%T")} createAccessToken -> {ip}: accept the prompt on the TV with the remote...')
        ok, res = call(ip, 'createAccessToken', timeout=60)
        if not ok or 'AccessToken' not in res:
            print('pairing failed:', res); return 1
        os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
        fd = os.open(token_path(ip), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(res['AccessToken'])
        print('paired; token stored at', token_path(ip)); return 0
    token = load_token(ip)
    if token is None:
        print(f'no token for {ip}; run: ipctl.py {ip} pair'); return 1
    if cmd in ('get', 'survey'):
        for m in (rest if cmd == 'get' else SURVEY):
            ok, res = call(ip, m, token=token)
            print(f'{m:32s} {"OK " if ok else "ERR"} {json.dumps(res)}')
        return 0
    if cmd == 'watch':
        # Logs every state change and every failed call; a stats line every 5 min doubles as a reliability soak.
        secs = float(rest[0]) if rest else 1.0; last = None
        ok_n = err_n = 0; rtts = []; t_stats = time.time()
        while True:
            t0 = time.time()
            try:
                ok1, gm = call(ip, 'gameModeControl', token=token)
                ok2, st = call(ip, 'getTVStates', token=token)
                rtt = 1000 * (time.time() - t0)
                if not (ok1 and ok2):
                    raise RuntimeError(f'rpc error gm={json.dumps(gm)} st={json.dumps(st)}')
                ok_n += 1; rtts.append(rtt)
                cur = (gm.get('gameMode'), st.get('pictureMode'), st.get('inputSource'))
                if cur != last:
                    print(f'{time.strftime("%T")} gameMode={cur[0]} pictureMode={cur[1]} input={cur[2]} '
                          f'(rtt {rtt:.0f} ms)', flush=True)
                    last = cur
            except Exception as e:  # noqa: BLE001 - soak wants every failure logged, never a crash
                err_n += 1
                print(f'{time.strftime("%T")} ERROR after {1000 * (time.time() - t0):.0f} ms: '
                      f'{type(e).__name__}: {e}', flush=True)
            if time.time() - t_stats >= 300:
                s = sorted(rtts) or [0]
                print(f'{time.strftime("%T")} STATS ok={ok_n} err={err_n} rtt p50={s[len(s) // 2]:.0f} '
                      f'p95={s[int(len(s) * .95)]:.0f} max={s[-1]:.0f} ms', flush=True)
                t_stats = time.time()
            time.sleep(max(0, secs - (time.time() - t0)))
    if cmd == 'set':
        method, kv = rest[0], dict(a.split('=', 1) for a in rest[1:])
        kv = {k: int(v) if v.lstrip('-').isdigit() else v for k, v in kv.items()}
        print(f'{time.strftime("%T")} SET {method} {kv}')
        ok, res = call(ip, method, kv, token=token)
        print('OK ' if ok else 'ERR', json.dumps(res))
        ok, res = call(ip, method, token=token)
        print('readback', 'OK ' if ok else 'ERR', json.dumps(res)); return 0
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
