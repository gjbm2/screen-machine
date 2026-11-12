"""
Admin API for remote media server management.
Blueprint at /api/admin-355729b8. All routes require
Authorization: Bearer <ADMIN_API_TOKEN> (set in .env).
"""

import base64
import hmac
import re
import subprocess
import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from dotenv import dotenv_values

import smconfig
from utils.logger import warning

admin_api = Blueprint('admin_api', __name__)


def admin_token_ok(req) -> bool:
    """True if the request carries the ADMIN_API_TOKEN bearer token."""
    # Read lazily: this module is imported before app.py calls load_dotenv()
    expected = os.environ.get('ADMIN_API_TOKEN', '')
    auth = req.headers.get('Authorization', '')
    provided = auth[7:] if auth.startswith('Bearer ') else ''
    # Fail closed: an unset token means no access at all. Compare bytes:
    # compare_digest raises on non-ASCII str (headers decode as latin-1)
    return bool(expected) and hmac.compare_digest(
        provided.encode('utf-8', 'surrogateescape'), expected.encode('utf-8'))


@admin_api.before_request
def _require_admin_token():
    # CORS preflight carries no credentials
    if request.method == 'OPTIONS':
        return None
    if not admin_token_ok(request):
        return jsonify({'error': 'unauthorized'}), 401

# The kiosk mini-PC comes from the deployment's manifest (kiosk.host/user/screens); the SSH secrets stay in
# media-server/local-scripts/.env, whose MEDIA_SERVER_HOST/USER override the manifest's host and user.
_KIOSK = smconfig.deployment().kiosk
_env_path = os.path.join(os.path.dirname(__file__), '..', 'media-server', 'local-scripts', '.env')
_env = dotenv_values(_env_path)
SSH_HOST = _env.get('MEDIA_SERVER_HOST', str(_KIOSK.get('host') or ''))
SSH_USER = _env.get('MEDIA_SERVER_USER', str(_KIOSK.get('user') or ''))
SSH_PORT = _env.get('MEDIA_SERVER_PORT', '22')


def _kiosk_password():
    """The kiosk login's password, for sudo: read at each use, so a new one in the .env needs no Flask restart."""
    return dotenv_values(_env_path).get('MEDIA_SERVER_PASSWORD') or ''

_NAME_RX = re.compile(r'^[a-z][a-z0-9_]*$')
_ID_RX = re.compile(r'^[A-Za-z0-9_.-]+$')


def kiosk_screens(kiosk=None):
    """[(screen id, short name, CDP port)] of the kiosk's screens (manifest kiosk.screens, in order; finsbury:
    north-screen/north/9222, south-screen/south/9223). An entry without a shell-safe lower-case name, a shell-safe id
    and a cdp_port is skipped: they go into the shell scripts run on the kiosk."""
    kiosk = _KIOSK if kiosk is None else kiosk
    out = []
    for sid, spec in (kiosk.get('screens') or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        name = str(spec.get('name') or '')
        try:
            port = int(spec.get('cdp_port'))
        except (TypeError, ValueError):
            port = None
        if not _NAME_RX.match(name) or not _ID_RX.match(str(sid)) or port is None:
            warning(f"[admin] kiosk screen {sid!r} skipped: it needs a lower-case name and a cdp_port")
            continue
        out.append((str(sid), name, port))
    return out


def _screen_by_name(name):
    return next((s for s in kiosk_screens() if s[1] == name), None)


def _screen_choice_error():
    names = [n for _, n, _ in kiosk_screens()]
    return 'screen must be ' + ' or '.join(f'"{n}"' for n in names) if names else 'this kiosk has no screens'


@admin_api.before_request
def _require_kiosk_admin():
    # Registered after the token check. Off, or no kiosk to talk to: a clear refusal, never an SSH attempt.
    if request.method == 'OPTIONS':
        return None
    if not smconfig.enabled('kiosk_admin'):
        warning(f"[admin] {request.path} refused: kiosk_admin is off in this deployment")
        return jsonify({'success': False, 'output': '', 'error': 'kiosk_admin is off in this deployment',
                        'timestamp': datetime.now().isoformat()}), 409
    if not SSH_HOST or not SSH_USER:
        warning(f"[admin] {request.path} refused: no kiosk host/user (deployment.yaml kiosk:, or MEDIA_SERVER_*)")
        return jsonify({'success': False, 'output': '', 'error': 'no kiosk is configured for this deployment',
                        'timestamp': datetime.now().isoformat()}), 503
    return None


def _ssh_run(remote_cmd, timeout=30, sudo=False):
    """Run a command on the media server via SSH and return result dict.

    Key auth only (the kiosk refuses passwords since 12 Nov 2026). With sudo=True the login password goes to the
    remote command's stdin for its one `sudo -S -p ''`: never in a command line, where ps and shell traces show it.
    ControlMaster=no as in kiosk-ctl.sh: a persistent master spawned here would hold the output pipe open."""
    ssh_cmd = [
        'ssh', '-q', '-o', 'StrictHostKeyChecking=no',
        '-o', 'BatchMode=yes', '-o', 'ControlMaster=no',
        '-o', 'LogLevel=ERROR',
        '-p', SSH_PORT,
        f'{SSH_USER}@{SSH_HOST}',
        remote_cmd
    ]
    try:
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=timeout,
            input=f'{_kiosk_password()}\n' if sudo else '',
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr.strip() if result.stderr.strip() else None,
            'timestamp': datetime.now().isoformat()
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'output': '',
            'error': f'Command timed out after {timeout}s',
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }


def status_script(screens=None):
    """The shell script /status runs on the kiosk: one block of lines per kiosk screen (kiosk_screens(); the
    variables are the screen's name upper-cased, NORTH_PID, ...), and one X screen per TV for the X11 fallback, in
    manifest order. For finsbury it is exactly the script this route always ran (tests/unit/test_deployment_api.py)."""
    screens = kiosk_screens() if screens is None else screens
    up = [(sid, n.upper()) for sid, n, _ in screens]
    chrome = "".join(
        [f"{v}_PID=$(pgrep -f '^[^ ]*chrome[^ ]* .*display/{sid}' | head -1)\n" for sid, v in up]
        + [f'echo "{v}_CHROME=${{{v}_PID:+running}}"\n' for _, v in up]
        + [f'[ -z "${v}_PID" ] && echo "{v}_CHROME=stopped"\n' for _, v in up]
        + ["\n# Current URLs from Chrome command lines\n"]
        + [f'{v}_CMD=$(ps -p "${v}_PID" -o args= 2>/dev/null || true)\n' for _, v in up]
        + [f'{v}_URL=$(echo "${v}_CMD" | grep -oP \'https?://\\S+\' | tail -1)\n' for _, v in up]
        + [f'echo "{v}_URL=${{{v}_URL:-unknown}}"\n' for _, v in up]
        + ["\n"])
    xscreens = ", ".join(f":0.{i} {n}" for i, (_, n, _p) in enumerate(screens))
    xrandr = " ".join(f"DISPLAY=:0.{i} xrandr --query;" for i in range(len(screens)))
    return (_STATUS_HEAD + chrome
            + _STATUS_TAIL.replace("__XSCREENS__", xscreens).replace("__XRANDR__", xrandr))


_STATUS_HEAD = r"""
echo "UPTIME=$(uptime -p)"
# kiosk.service is the sway kiosk (default since 2026-10-13); the X11 fallback kiosk-x11.service counts as active too
KIOSK=$(systemctl is-active kiosk.service)
[ "$KIOSK" != active ] && systemctl is-active --quiet kiosk-x11.service && KIOSK=active
echo "KIOSK=$KIOSK"

# Chrome screens. The browser's command line starts /opt/google/chrome/chrome, so anchor on a first word containing
# "chrome": an unanchored pattern also matches this ssh shell's own command line (which contains the pattern)
"""

_STATUS_TAIL = r"""# Displays: one "<output> connected ..." line per connected output, from sway (the Wayland kiosk, default since
# 2026-10-13; every socket is tried, a crashed sway can leave a stale one), else Xorg (kiosk-x11 fallback: one X
# screen per TV, __XSCREENS__; on a single-screen X, :0.1 just fails), else the kernel's connector
# status when no display server answers
displays() {
    for s in /run/user/$(id -u)/sway-ipc.*.sock; do
        out=$(SWAYSOCK=$s timeout 3 swaymsg -t get_outputs -r 2>/dev/null | jq -r '.[] | "\(.name) connected "
            + (if .active then "\(.current_mode.width)x\(.current_mode.height)@\(.current_mode.refresh / 1000)Hz" else "(inactive)" end)' 2>/dev/null)
        [ -n "$out" ] && { echo "$out"; return; }
    done
    out=$({ __XRANDR__ } 2>/dev/null | grep ' connected')
    [ -n "$out" ] && { echo "$out"; return; }
    for f in /sys/class/drm/card*-*/status; do
        [ "$(cat "$f" 2>/dev/null)" = connected ] || continue
        n=${f%/status}; n=${n##*/}
        echo "${n#card*-} connected ($(cat "${f%/status}/enabled" 2>/dev/null))"
    done
}
displays | while read line; do echo "DISPLAY_LINE=$line"; done

# Resources
echo "MEM_PERCENT=$(free | grep Mem | awk '{printf "%.0f", ($3/$2)*100}')"
echo "DISK_PERCENT=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')"
echo "LOAD=$(uptime | awk -F'load average:' '{print $2}' | cut -d, -f1 | tr -d ' ')"
"""


@admin_api.route('/admin-355729b8/status', methods=['GET'])
def status():
    """Compact status: uptime, kiosk, disk, memory, displays, chrome."""
    cmd = status_script()
    return jsonify(_ssh_run(cmd))


@admin_api.route('/admin-355729b8/logs', methods=['GET'])
def logs():
    """Recent kiosk service logs."""
    cmd = "sudo -S -p '' journalctl -u kiosk.service --no-pager -n 50 2>/dev/null || echo \"Cannot read logs\""
    return jsonify(_ssh_run(cmd, sudo=True))


@admin_api.route('/admin-355729b8/restart-kiosk', methods=['POST'])
def restart_kiosk():
    """Full kiosk service restart (both screens)."""
    # Restarts whichever kiosk unit is running: kiosk.service (sway, default since 2026-10-13) or the X11 fallback
    # kiosk-x11.service, so a restart never switches modes. No pkill: both units stop their own Chrome
    # (kiosk.service's ExecStopPost, kiosk-x11's KillMode=control-group), and a pkill -f pattern written here would
    # match this ssh shell's own command line and kill it between the stop and the start.
    cmd = """
KIOSK_UNIT=kiosk.service
systemctl is-active --quiet kiosk-x11.service && KIOSK_UNIT=kiosk-x11.service
sudo -S -p '' systemctl restart $KIOSK_UNIT
sleep 3
echo "UNIT=$KIOSK_UNIT"
echo "STATUS=$(systemctl is-active $KIOSK_UNIT)"
"""
    return jsonify(_ssh_run(cmd, timeout=30, sudo=True))


@admin_api.route('/admin-355729b8/restart-screen', methods=['POST'])
def restart_screen():
    """Restart Chrome for one screen only. Body: {screen: <a kiosk screen's name, e.g. "north">}."""
    data = request.get_json() or {}
    screen = data.get('screen', '').lower()
    picked = _screen_by_name(screen)
    if picked is None:
        return jsonify({
            'success': False, 'output': '',
            'error': _screen_choice_error(),
            'timestamp': datetime.now().isoformat()
        }), 400
    screen_id = picked[0]

    # Pattern anchored on the browser binary (first word contains "chrome"): unanchored, it matches this ssh shell's
    # own command line and pkill kills the shell instead of Chrome.
    cmd = f"""
# the X11 fallback's kiosk-loop.sh does not relaunch a Chrome that exits, so killing one would leave its TV blank
if systemctl is-active --quiet kiosk-x11.service; then
    echo "RESULT=unsupported: the X11 fallback (kiosk-x11.service) does not relaunch Chrome; restart the kiosk instead"
    exit 1
fi
pkill -f '^[^ ]*chrome[^ ]* .*display/{screen_id}' 2>/dev/null || true
sleep 2
# under sway, kiosk-wayland.sh relaunches a Chrome that exits within 5 s
echo "Killed {screen_id} Chrome. kiosk-wayland.sh will relaunch it."
# Check if it came back
sleep 5
if pgrep -f '^[^ ]*chrome[^ ]* .*display/{screen_id}' >/dev/null; then
    echo "RESULT=restarted"
else
    echo "RESULT=killed_awaiting_relaunch"
fi
"""
    return jsonify(_ssh_run(cmd, timeout=20))


@admin_api.route('/admin-355729b8/set-url', methods=['POST'])
def set_url():
    """Change URL for a screen. Body: {screen: <a kiosk screen's name, e.g. "north">, url: string}."""
    data = request.get_json() or {}
    screen = data.get('screen', '').lower()
    url = data.get('url', '').strip()
    picked = _screen_by_name(screen)
    if picked is None:
        return jsonify({
            'success': False, 'output': '',
            'error': _screen_choice_error(),
            'timestamp': datetime.now().isoformat()
        }), 400
    if not url:
        return jsonify({
            'success': False, 'output': '',
            'error': 'url is required',
            'timestamp': datetime.now().isoformat()
        }), 400

    port = picked[2]   # the screen's Chrome DevTools port (kiosk.screens.<id>.cdp_port)

    # Write a CDP navigation script to remote, then execute it.
    # repr() yields a valid Python string literal for the URL, and the script
    # travels base64-encoded so no shell (some echos eat backslashes) can turn
    # quotes in the URL into code on the media server.
    script = _CDP_SCRIPT.replace('__PORT__', str(port)).replace('__URL__', repr(str(url)))
    encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
    cmd = f"echo {encoded} | base64 -d > /tmp/_cdp_nav.py && python3 /tmp/_cdp_nav.py"
    return jsonify(_ssh_run(cmd, timeout=15))


# CDP navigation script template — uses only python3 stdlib
_CDP_SCRIPT = r"""
import json, socket, os, re, sys, base64, urllib.request

port = __PORT__
url = __URL__

try:
    tabs = json.loads(urllib.request.urlopen("http://localhost:%d/json" % port, timeout=3).read())
except Exception as e:
    print("CDP_ERROR=Cannot connect to debug port %d: %s" % (port, e))
    sys.exit(1)

if not tabs:
    print("CDP_ERROR=No tabs found")
    sys.exit(1)

ws_url = tabs[0].get("webSocketDebuggerUrl", "")
if not ws_url:
    print("CDP_ERROR=No webSocketDebuggerUrl")
    sys.exit(1)

m = re.match(r"ws://([^:]+):(\d+)(.*)", ws_url)
host, ws_port, path = m.group(1), int(m.group(2)), m.group(3)

key = base64.b64encode(os.urandom(16)).decode()
s = socket.socket()
s.settimeout(5)
s.connect((host, ws_port))
handshake = "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, ws_port, key)
s.send(handshake.encode())
s.recv(4096)

msg = json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}).encode()
mask_key = os.urandom(4)
frame = bytearray([0x81])
ln = len(msg)
if ln < 126:
    frame.append(0x80 | ln)
else:
    frame.append(0x80 | 126)
    frame.extend(ln.to_bytes(2, "big"))
frame.extend(mask_key)
frame.extend(bytes(b ^ mask_key[i % 4] for i, b in enumerate(msg)))
s.send(frame)

data = s.recv(4096)
if data and data[0] == 0x81:
    pl = data[1] & 0x7f
    off = 2
    if pl == 126:
        pl = int.from_bytes(data[2:4], "big")
        off = 4
    result = json.loads(data[off:off+pl].decode())
    if "error" in result:
        print("CDP_ERROR=%s" % result["error"].get("message", str(result["error"])))
    else:
        print("RESULT=navigated")
        print("URL=%s" % url)
else:
    print("RESULT=navigated")
    print("URL=%s" % url)
s.close()
"""


@admin_api.route('/admin-355729b8/reboot', methods=['POST'])
def reboot():
    """Full server reboot."""
    cmd = "sudo -S -p '' reboot"
    return jsonify(_ssh_run(cmd, timeout=10, sudo=True))
