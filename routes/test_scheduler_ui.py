from flask import Blueprint, render_template_string, request, redirect, url_for
import requests
BASE_URL = "http://localhost:5000"

# === Scheduler Control Panel UI ===
test_scheduler_bp = Blueprint("test_scheduler", __name__)

from routes.utils import _load_json_once
from utils.logger import log_to_console, info, error, warning, debug, console_logs

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Scheduler Control Panel</title>
  <style>
    body { font-family: sans-serif; margin: 0; padding: 2rem; background: #f8f8f8; }
    h1 { margin-bottom: 1rem; }
    .log { background: #111; color: #0f0; padding: 1rem; margin-top: 1rem; white-space: pre-wrap; }
    form { margin-bottom: 1rem; }
    input, textarea { font-family: monospace; width: 100%; margin: 0.25rem 0; }
    .btn { padding: 0.5rem 1rem; margin-top: 0.5rem; display: inline-block; background: #444; color: white; border: none; cursor: pointer; }
    .running { color: green; }
    .stopped { color: red; }
  </style>
</head>
<body>
  <h1>Scheduler Control Panel</h1>

  <form method="POST" action="/test-scheduler">
    <label>Publish Destination:
  <select name="destination">
    {% for dest in all_destinations %}
      <option value="{{ dest }}" {% if dest == selected %}selected{% endif %}>{{ dest }}</option>
    {% endfor %}
  </select>
</label>
    <label>Schedule JSON:</label>
    <textarea name="schedule_json" rows="10"></textarea>
    <input class="btn" type="submit" value="Start Scheduler">
  </form>

  <h2>Running Schedulers</h2>
  <ul>
    {% for dest in schedulers %}
      <li>
        <strong>{{ dest }}</strong>
        <form method="POST" action="/test-scheduler?stop={{ dest }}" style="display:inline">
          <input class="btn" type="submit" value="Stop">
        </form>
        <form method="GET" action="/test-scheduler?log={{ dest }}" style="display:inline">
          <input class="btn" type="submit" value="View Log">
        </form>
      </li>
    {% endfor %}
  </ul>

  {% if log %}
  <h2>Log: {{ log_name }}</h2>
  <div class="log">{{ log }}</div>
  {% endif %}
</body>
</html>
"""

@test_scheduler_bp.route("/test-scheduler", methods=["GET", "POST"])
def test_scheduler():
    return test_scheduler_handler()

def test_scheduler_handler():
    dest_data = _load_json_once("publish_destinations", "publish-destinations.json")
    all_destinations = [d["id"] for d in dest_data if isinstance(d, dict) and "id" in d]
    selected = request.form.get("destination") if request.method == "POST" else None
    log = None
    log_name = None
    schedulers = []

    if request.method == "POST":
        stop = request.args.get("stop")
        if stop:
            requests.delete(f"{BASE_URL}/api/schedulers/{stop}")
        else:
            destination = request.form["destination"]
            schedule_json = request.form["schedule_json"]
            try:
                schedule = schedule_json.strip()
                requests.post(f"{BASE_URL}/api/schedulers/{destination}", json=schedule)
            except Exception as e:
                return f"Error: {e}"

    if request.method == "GET" and request.args.get("log"):
        log_name = request.args.get("log")
        response = requests.get(f"{BASE_URL}/api/schedulers/{log_name}")
        if response.ok:
            log = "\n".join(response.json().get("log", []))

    schedulers_resp = requests.get(f"{BASE_URL}/api/schedulers")
    if schedulers_resp.ok:
        schedulers = schedulers_resp.json().get("running", [])

    return render_template_string(HTML_TEMPLATE, schedulers=schedulers, log=log, log_name=log_name, all_destinations=all_destinations, selected=selected)