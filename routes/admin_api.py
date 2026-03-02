"""
Admin API for remote media server management.
Blueprint at /api/admin-k9x7m.
"""

import subprocess
import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from dotenv import dotenv_values

admin_api = Blueprint('admin_api', __name__)

# Load SSH config from media-server env
_env_path = os.path.join(os.path.dirname(__file__), '..', 'media-server', 'local-scripts', '.env')
_env = dotenv_values(_env_path)
SSH_HOST = _env.get('MEDIA_SERVER_HOST', '192.168.1.92')
SSH_USER = _env.get('MEDIA_SERVER_USER', 'gjbm2')
SSH_PORT = _env.get('MEDIA_SERVER_PORT', '22')
SSH_PASS = _env.get('MEDIA_SERVER_PASSWORD', '')


def _ssh_run(remote_cmd, timeout=30):
    """Run a command on the media server via SSH and return result dict."""
    ssh_cmd = [
        'sshpass', '-p', SSH_PASS,
        'ssh', '-q', '-o', 'StrictHostKeyChecking=no',
        '-o', 'LogLevel=ERROR',
        '-p', SSH_PORT,
        f'{SSH_USER}@{SSH_HOST}',
        remote_cmd
    ]
    try:
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=timeout
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


@admin_api.route('/admin-k9x7m/status', methods=['GET'])
def status():
    """Compact status: uptime, kiosk, disk, memory, displays, chrome."""
    cmd = r"""
echo "UPTIME=$(uptime -p)"
echo "KIOSK=$(systemctl is-active kiosk.service)"

# Chrome screens
NORTH_PID=$(pgrep -f 'google-chrome.*north-screen|chromium.*north-screen' | head -1)
SOUTH_PID=$(pgrep -f 'google-chrome.*south-screen|chromium.*south-screen' | head -1)
echo "NORTH_CHROME=${NORTH_PID:+running}"
echo "SOUTH_CHROME=${SOUTH_PID:+running}"
[ -z "$NORTH_PID" ] && echo "NORTH_CHROME=stopped"
[ -z "$SOUTH_PID" ] && echo "SOUTH_CHROME=stopped"

# Current URLs from Chrome command lines
NORTH_CMD=$(ps -p "$NORTH_PID" -o args= 2>/dev/null || true)
SOUTH_CMD=$(ps -p "$SOUTH_PID" -o args= 2>/dev/null || true)
NORTH_URL=$(echo "$NORTH_CMD" | grep -oP 'https?://\S+' | tail -1)
SOUTH_URL=$(echo "$SOUTH_CMD" | grep -oP 'https?://\S+' | tail -1)
echo "NORTH_URL=${NORTH_URL:-unknown}"
echo "SOUTH_URL=${SOUTH_URL:-unknown}"

# Displays
DISPLAY=:0 xrandr --query 2>/dev/null | grep ' connected' | while read line; do echo "DISPLAY_LINE=$line"; done

# Resources
echo "MEM_PERCENT=$(free | grep Mem | awk '{printf "%.0f", ($3/$2)*100}')"
echo "DISK_PERCENT=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')"
echo "LOAD=$(uptime | awk -F'load average:' '{print $2}' | cut -d, -f1 | tr -d ' ')"
"""
    return jsonify(_ssh_run(cmd))


@admin_api.route('/admin-k9x7m/logs', methods=['GET'])
def logs():
    """Recent kiosk service logs."""
    cmd = 'sudo journalctl -u kiosk.service --no-pager -n 50 2>/dev/null || echo "Cannot read logs"'
    # Use password for sudo
    full_cmd = f'echo "{SSH_PASS}" | sudo -S journalctl -u kiosk.service --no-pager -n 50 2>/dev/null || echo "Cannot read logs"'
    return jsonify(_ssh_run(full_cmd))


@admin_api.route('/admin-k9x7m/restart-kiosk', methods=['POST'])
def restart_kiosk():
    """Full kiosk service restart (both screens)."""
    cmd = f"""
echo "{SSH_PASS}" | sudo -S systemctl stop kiosk.service
sleep 2
pkill -f 'google-chrome.*kiosk' 2>/dev/null || true
pkill -f 'chromium.*kiosk' 2>/dev/null || true
sleep 1
echo "{SSH_PASS}" | sudo -S systemctl start kiosk.service
sleep 3
echo "STATUS=$(systemctl is-active kiosk.service)"
"""
    return jsonify(_ssh_run(cmd, timeout=30))


@admin_api.route('/admin-k9x7m/restart-screen', methods=['POST'])
def restart_screen():
    """Restart Chrome for one screen only. Body: {screen: "north"|"south"}."""
    data = request.get_json() or {}
    screen = data.get('screen', '').lower()
    if screen not in ('north', 'south'):
        return jsonify({
            'success': False, 'output': '',
            'error': 'screen must be "north" or "south"',
            'timestamp': datetime.now().isoformat()
        }), 400

    cmd = f"""
pkill -f '(google-chrome|chromium).*{screen}-screen' 2>/dev/null || true
sleep 2
# The kiosk service webview-manager will auto-relaunch the killed Chrome
echo "Killed {screen}-screen Chrome. Webview manager will relaunch it."
# Check if it came back
sleep 5
if pgrep -f '(google-chrome|chromium).*{screen}-screen' >/dev/null; then
    echo "RESULT=restarted"
else
    echo "RESULT=killed_awaiting_relaunch"
fi
"""
    return jsonify(_ssh_run(cmd, timeout=20))


@admin_api.route('/admin-k9x7m/set-url', methods=['POST'])
def set_url():
    """Change URL for a screen. Body: {screen: "north"|"south", url: string}."""
    data = request.get_json() or {}
    screen = data.get('screen', '').lower()
    url = data.get('url', '').strip()
    if screen not in ('north', 'south'):
        return jsonify({
            'success': False, 'output': '',
            'error': 'screen must be "north" or "south"',
            'timestamp': datetime.now().isoformat()
        }), 400
    if not url:
        return jsonify({
            'success': False, 'output': '',
            'error': 'url is required',
            'timestamp': datetime.now().isoformat()
        }), 400

    port = 9222 if screen == 'north' else 9223

    # Write a CDP navigation script to remote, then execute it
    script = _CDP_SCRIPT.replace('__PORT__', str(port)).replace('__URL__', url)
    # Escape single quotes in the script for shell
    escaped = script.replace("'", "'\\''")
    cmd = f"echo '{escaped}' > /tmp/_cdp_nav.py && python3 /tmp/_cdp_nav.py"
    return jsonify(_ssh_run(cmd, timeout=15))


# CDP navigation script template — uses only python3 stdlib
_CDP_SCRIPT = r"""
import json, socket, os, re, sys, base64, urllib.request

port = __PORT__
url = "__URL__"

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


@admin_api.route('/admin-k9x7m/reboot', methods=['POST'])
def reboot():
    """Full server reboot."""
    cmd = f'echo "{SSH_PASS}" | sudo -S reboot'
    return jsonify(_ssh_run(cmd, timeout=10))
