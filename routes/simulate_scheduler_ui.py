from flask import request, render_template_string
from routes.scheduler_api import simulate_schedule, default_context
import json
from datetime import datetime

HTML_TEMPLATE = """<!doctype html>
sche<html><head><title>Scheduler Simulator</title><style>
html, body { height: 100%; margin: 0; font-family: sans-serif; }
.container { display: flex; height: 100vh; }
.left, .right { flex: 1; display: flex; flex-direction: column; padding: 1rem; box-sizing: border-box; }
textarea { height: 100%; width: 100%; flex: 1; font-family: monospace; font-size: 0.9rem; resize: none; }
pre { flex: 1; background: #111; color: #0f0; padding: 1rem; overflow-y: auto; margin: 0; }
form input[type="text"], form input[type="number"] { margin-bottom: 0.5rem; }
</style></head><body><div class="container">
<div class="left"><h2>Test Scheduler</h2><form method="POST" style="display: flex; flex-direction: column; height: 100%;">
<label>Simulated Date (YYYY-MM-DD): <input type="text" name="sim_date" value="2025-04-25"></label>
<label>Start Time (HH:MM): <input type="text" name="start_time" value="08:00"></label>
<label>End Time (HH:MM): <input type="text" name="end_time" value="12:00"></label>
<label>Step (minutes): <input type="number" name="step_minutes" value="5"></label>
<label>Schedule JSON:</label>
<textarea name="schedule_json">{{ schedule_json }}</textarea>
<input type="submit" value="Simulate"></form></div>
<div class="right"><h3>Simulation Output</h3><pre>{{ output }}</pre></div>
</div></body></html>"""

def render_form(schedule_json='', output=''):
    return render_template_string(HTML_TEMPLATE, schedule_json=schedule_json, output=output)

def simulate_scheduler_handler():
    if request.method == "POST":
        try:
            schedule_json = request.form.get("schedule_json", "")
            sim_date = request.form.get("sim_date", datetime.now().strftime("%Y-%m-%d"))
            start_time = request.form.get("start_time", "08:00")
            end_time = request.form.get("end_time", "12:00")
            step_minutes = int(request.form.get("step_minutes", 1))

            start_dt = datetime.strptime(f"{sim_date} {start_time}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{sim_date} {end_time}", "%Y-%m-%d %H:%M")

            schedule = json.loads(schedule_json)
            context = default_context()

            output = simulate_schedule(schedule, start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"), step_minutes, context)


            output.append("\n---\nPrompt History:")
            output.extend([f"- {line}" for line in context["prompt_history"]])

            return render_form(schedule_json=schedule_json, output="\n".join(output))

        except Exception as e:
            return render_form(schedule_json=request.form.get("schedule_json", ""), output=f"Error: {e}")

    return render_form(schedule_json='', output='')
