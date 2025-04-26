from flask import Blueprint, render_template_string, request, redirect, url_for, render_template, jsonify
import requests
import json
from typing import List, Dict, Any
from routes.scheduler_api import (
    important_triggers,
    running_schedulers,
    scheduler_schedule_stacks,
    scheduler_contexts_stacks,
    api_load_schedule,
    api_unload_schedule,
    api_get_schedule_stack,
    api_get_scheduler_context,
    api_get_scheduler_log,
    api_pause_scheduler,
    api_unpause_scheduler,
    api_get_scheduler_status,
    stop_scheduler,
    start_scheduler
)
from utils.logger import log_to_console, info, error, warning, debug, console_logs
import os
from datetime import datetime

BASE_URL = "http://localhost:5000"

# === Scheduler Control Panel UI ===
test_scheduler_bp = Blueprint("test_scheduler", __name__)

from routes.utils import _load_json_once
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# Path to scheduler state directory
SCHEDULER_DIR = os.path.join(os.path.dirname(__file__), "scheduler")

# Ensure scheduler_states directory exists
SCHEDULER_STATES_DIR = "scheduler_states"
if not os.path.exists(SCHEDULER_STATES_DIR):
    os.makedirs(SCHEDULER_STATES_DIR)
    debug(f"Created scheduler_states directory at {os.path.abspath(SCHEDULER_STATES_DIR)}")

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Scheduler Control Panel</title>
  <style>
    body { 
      font-family: sans-serif; 
      margin: 0; 
      padding: 0; 
      background: #f8f8f8;
      display: flex;
      height: 100vh;
      overflow: hidden;
    }
    .main-content { 
      flex: 1; 
      padding: 20px;
      overflow-y: auto;
    }
    .log-panel {
      width: 400px;
      background: white;
      border-left: 1px solid #ddd;
      display: flex;
      flex-direction: column;
    }
    .log-panel h3 {
      margin: 0;
      padding: 15px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid #ddd;
    }
    .log-panel select {
      padding: 5px;
      font-size: 14px;
    }
    .log-content {
      flex: 1;
      background: #111;
      color: #0f0;
      padding: 15px;
      font-family: monospace;
      white-space: pre-wrap;
      overflow-y: auto;
    }
    .log-content.empty {
      color: #666;
    }
    h1, h2, h3, h4 { margin-top: 20px; }
    .btn { 
      padding: 5px 10px; 
      background: #444; 
      color: white; 
      border: none; 
      cursor: pointer; 
      text-decoration: none; 
      display: inline-block;
      margin-right: 5px;
    }
    .running { color: green; }
    .paused { color: orange; }
    .stopped { color: red; }
    form { margin-bottom: 20px; }
    textarea { width: 100%; height: 200px; font-family: monospace; }
    pre { background: #f5f5f5; padding: 10px; overflow: auto; }
    .status { font-weight: bold; }
    .scheduler-info { border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; }
    .stack-layer { border-left: 3px solid #ddd; padding-left: 10px; margin-bottom: 10px; }
    .stack-layer.active { border-left-color: green; }
    .context-vars { background: #f5f5f5; padding: 10px; margin-top: 10px; }
    .actions-bar { 
      margin: 15px 0; 
      padding: 10px;
      background: #f5f5f5; 
      border: 1px solid #ddd;
      display: flex;
      align-items: center;
    }
    .debug-info {
      background: #f0f0f0;
      padding: 10px;
      margin: 10px 0;
      border: 1px solid #ddd;
      font-family: monospace;
      font-size: 12px;
    }
  </style>
  <script>
    // Function to fetch and update logs
    async function updateLogs() {
      const logDestination = document.getElementById('log-destination');
      const selectedDest = logDestination.value;
      if (!selectedDest) return;
      
      try {
        const response = await fetch(`/api/schedulers/${selectedDest}`);
        const data = await response.json();
        const logContent = document.getElementById('log-content');
        
        if (data.log && data.log.length > 0) {
          logContent.textContent = data.log.join('\\n');
          logContent.classList.remove('empty');
          // Auto-scroll to bottom if already at bottom
          const isAtBottom = logContent.scrollHeight - logContent.clientHeight <= logContent.scrollTop + 1;
          if (isAtBottom) {
            logContent.scrollTop = logContent.scrollHeight;
          }
        } else {
          logContent.textContent = 'No logs available. Start a scheduler to view logs.';
          logContent.classList.add('empty');
        }
      } catch (error) {
        console.error('Error fetching logs:', error);
      }
    }

    // Function to fetch and update scheduler status
    async function updateSchedulerStatus(destination) {
      try {
        const response = await fetch(`/api/schedulers/${destination}/status`);
        const data = await response.json();
        const statusElement = document.getElementById(`scheduler-status-${destination}`);
        if (statusElement) {
          statusElement.textContent = data.status;
          statusElement.className = `scheduler-status ${data.status}`;
        }
      } catch (error) {
        console.error('Error fetching scheduler status:', error);
      }
    }

    // Function to handle pause/unpause
    async function toggleSchedulerPause(destination, action) {
      try {
        const response = await fetch(`/test-scheduler?${action}=${destination}`, {
          method: 'POST'
        });
        if (response.ok) {
          updateSchedulerStatus(destination);
          updateLogs();
        }
      } catch (error) {
        console.error('Error toggling scheduler state:', error);
      }
    }

    // Start auto-refresh when page loads
    document.addEventListener('DOMContentLoaded', function() {
      // Update logs every 5 seconds
      setInterval(updateLogs, 5000);
      
      // Update status for all schedulers every 5 seconds
      const statusElements = document.querySelectorAll('.scheduler-status');
      if (statusElements.length > 0) {
        setInterval(() => {
          statusElements.forEach(element => {
            const destination = element.id.replace('scheduler-status-', '');
            updateSchedulerStatus(destination);
          });
        }, 5000);
      }
      
      // Initial updates
      updateLogs();
      statusElements.forEach(element => {
        const destination = element.id.replace('scheduler-status-', '');
        updateSchedulerStatus(destination);
      });
    });
  </script>
</head>
<body>
  <div class="main-content">
    <h1>Scheduler Control Panel</h1>
    
    <div class="actions-bar">
      <strong>Global Actions:</strong>
      <a href="/test-scheduler?load_from_files=true" class="btn">Load Schedules from Files</a>
    </div>

    <form method="POST" action="/test-scheduler">
      <div>
        <label for="destination">Publish Destination:</label>
        <select name="destination" id="destination">
          {% for dest in all_destinations %}
            <option value="{{ dest }}" {% if dest == selected %}selected{% endif %}>{{ dest }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label for="schedule_json">Schedule JSON:</label>
        <textarea name="schedule_json" id="schedule_json" rows="10">{{ current_schedule }}</textarea>
      </div>
      <div>
        <input class="btn" type="submit" value="Load Schedule">
      </div>
    </form>

    <h2>Running Schedulers</h2>
    {% if running_schedulers %}
      {% for dest in running_schedulers %}
        <div class="scheduler-info">
          <h3>{{ dest }}</h3>
          <div>
            <span class="status">Status: <span id="scheduler-status-{{ dest }}" class="scheduler-status running">running</span></span>
            <form method="POST" action="/test-scheduler?stop={{ dest }}" style="display:inline">
              <input class="btn" type="submit" value="Stop">
            </form>
            <button class="btn" onclick="toggleSchedulerPause('{{ dest }}', 'pause')">Pause</button>
            <button class="btn" onclick="toggleSchedulerPause('{{ dest }}', 'unpause')">Unpause</button>
          </div>
          
          {% if dest in schedule_stacks %}
            <div class="schedule-stack">
              <h4>Schedule Stack</h4>
              {% for layer in schedule_stacks[dest] %}
                <div class="stack-layer">
                  <div>
                    <strong>Layer {{ loop.index }}</strong>
                    <div>
                      <a class="btn" href="/test-scheduler?edit={{ dest }}&layer={{ loop.index0 }}">Edit</a>
                      <a class="btn" href="/test-scheduler?context={{ dest }}&layer={{ loop.index0 }}">View Context</a>
                    </div>
                  </div>
                  <div>
                    <pre>{{ layer|tojson(indent=2) }}</pre>
                    {% if dest in layer_contexts and loop.index0 in layer_contexts[dest] %}
                      <div class="context-vars">
                        <h4>Context Variables:</h4>
                        <ul>
                          {% for key, value in layer_contexts[dest][loop.index0].items() %}
                            {% if key == "vars" %}
                              {% for var_key, var_value in value.items() %}
                                <li><strong>{{ var_key }}:</strong> {{ var_value }}</li>
                              {% endfor %}
                            {% elif key == "history" %}
                              <li><strong>History:</strong>
                                <ul>
                                  {% for hist_key, hist_value in value.items() %}
                                    <li><strong>{{ hist_key }}:</strong> {{ hist_value }}</li>
                                  {% endfor %}
                                </ul>
                              </li>
                            {% else %}
                              <li><strong>{{ key }}:</strong> {{ value }}</li>
                            {% endif %}
                          {% endfor %}
                        </ul>
                      </div>
                    {% endif %}
                  </div>
                </div>
              {% endfor %}
              <form method="POST" action="/test-scheduler?unload_schedule={{ dest }}" style="display:inline">
                <input class="btn" type="submit" value="Unload Top Schedule">
              </form>
            </div>
          {% else %}
            <div class="debug-info">
              No schedule stack found for {{ dest }}. Available stacks: {{ schedule_stacks.keys()|list }}
            </div>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p>No schedulers currently running.</p>
    {% endif %}

    <h2>Stopped Schedulers</h2>
    {% if stopped_schedulers %}
      {% for dest in stopped_schedulers %}
        <div class="scheduler-info stopped">
          <h3>{{ dest }}</h3>
          <div>
            <span class="status">Status: <span id="scheduler-status-{{ dest }}" class="scheduler-status stopped">stopped</span></span>
            <form method="POST" action="/test-scheduler?start={{ dest }}" style="display:inline">
              <input class="btn" type="submit" value="Start">
            </form>
          </div>
          
          {% if dest in schedule_stacks %}
            <div class="schedule-stack">
              <h4>Schedule Stack</h4>
              {% for layer in schedule_stacks[dest] %}
                <div class="stack-layer">
                  <div>
                    <strong>Layer {{ loop.index }}</strong>
                    <div>
                      <a class="btn" href="/test-scheduler?edit={{ dest }}&layer={{ loop.index0 }}">Edit</a>
                      <a class="btn" href="/test-scheduler?context={{ dest }}&layer={{ loop.index0 }}">View Context</a>
                    </div>
                  </div>
                  <div>
                    <pre>{{ layer|tojson(indent=2) }}</pre>
                    {% if dest in layer_contexts and loop.index0 in layer_contexts[dest] %}
                      <div class="context-vars">
                        <h4>Context Variables:</h4>
                        <ul>
                          {% for key, value in layer_contexts[dest][loop.index0].items() %}
                            {% if key == "vars" %}
                              {% for var_key, var_value in value.items() %}
                                <li><strong>{{ var_key }}:</strong> {{ var_value }}</li>
                              {% endfor %}
                            {% elif key == "history" %}
                              <li><strong>History:</strong>
                                <ul>
                                  {% for hist_key, hist_value in value.items() %}
                                    <li><strong>{{ hist_key }}:</strong> {{ hist_value }}</li>
                                  {% endfor %}
                                </ul>
                              </li>
                            {% else %}
                              <li><strong>{{ key }}:</strong> {{ value }}</li>
                            {% endif %}
                          {% endfor %}
                        </ul>
                      </div>
                    {% endif %}
                  </div>
                </div>
              {% endfor %}
              <form method="POST" action="/test-scheduler?unload_schedule={{ dest }}" style="display:inline">
                <input class="btn" type="submit" value="Unload Top Schedule">
              </form>
            </div>
          {% else %}
            <div class="debug-info">
              No schedule stack found for {{ dest }}. Available stacks: {{ schedule_stacks.keys()|list }}
            </div>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p>No stopped schedulers.</p>
    {% endif %}
  </div>
  
  <div class="log-panel">
    <h3>
      <span>Logs</span>
      <select id="log-destination" onchange="updateLogs()">
        <option value="">Select a destination</option>
        {% for dest in schedulers %}
          <option value="{{ dest }}" {% if dest == selected %}selected{% endif %}>{{ dest }}</option>
        {% endfor %}
      </select>
    </h3>
    <div id="log-content" class="log-content {% if not selected or not logs[selected] %}empty{% endif %}">
      {% if selected and logs[selected] %}
        {{ logs[selected]|join('\n') }}
      {% else %}
        No logs available. Start a scheduler to view logs.
      {% endif %}
    </div>
  </div>
</body>
</html>
"""

# New editor page template
EDITOR_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Edit Schedule</title>
  <style>
    body {
      font-family: sans-serif;
      margin: 0;
      padding: 20px;
      background: #f8f8f8;
    }
    .container {
      max-width: 800px;
      margin: 0 auto;
      background: white;
      padding: 20px;
      border-radius: 5px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    h1 {
      margin-top: 0;
      margin-bottom: 20px;
    }
    textarea {
      width: 100%;
      height: 400px;
      font-family: monospace;
      padding: 10px;
      border: 1px solid #ddd;
      border-radius: 4px;
      margin-bottom: 20px;
    }
    .btn {
      padding: 8px 16px;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      background: #444;
      color: white;
      font-size: 14px;
      margin-right: 10px;
    }
    .alert {
      padding: 10px;
      margin-bottom: 15px;
      border-radius: 4px;
    }
    .alert-success {
      background-color: #dff0d8;
      color: #3c763d;
      border: 1px solid #d6e9c6;
    }
    .alert-danger {
      background-color: #f2dede;
      color: #a94442;
      border: 1px solid #ebccd1;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Edit Schedule - {{ destination }} (Layer {{ layer }})</h1>
    
    {% if message %}
    <div class="alert {% if error %}alert-danger{% else %}alert-success{% endif %}">
      {{ message }}
    </div>
    {% endif %}
    
    <form method="POST" action="/test-scheduler/edit">
      <input type="hidden" name="destination" value="{{ destination }}">
      <input type="hidden" name="layer" value="{{ layer }}">
      <textarea name="schedule_json" rows="20">{{ schedule_json }}</textarea>
      <div>
        <input type="submit" class="btn" value="Save">
        <a href="/test-scheduler?log={{ destination }}" class="btn">Cancel</a>
      </div>
    </form>
  </div>
</body>
</html>
"""

@test_scheduler_bp.route("/test-scheduler", methods=["GET", "POST"])
def test_scheduler():
    if request.method == "GET":
        # Handle edit requests
        if "edit" in request.args:
            dest = request.args["edit"]
            layer = int(request.args.get("layer", 0))
            return schedule_editor(dest, layer)
            
        # Get all scheduler states
        current_running_schedulers = list(running_schedulers.keys())
        stopped_schedulers = [dest for dest in scheduler_schedule_stacks.keys() 
                            if dest not in current_running_schedulers]
        
        # Get context and history for each scheduler
        scheduler_states = {}
        for dest in current_running_schedulers + stopped_schedulers:
            if dest in scheduler_contexts_stacks and scheduler_contexts_stacks[dest]:
                context = scheduler_contexts_stacks[dest][-1]
                scheduler_states[dest] = {
                    "context": context,
                    "history": {k: v for k, v in context.items() if isinstance(v, list)},
                    "vars": context.get("vars", {})
                }
            else:
                scheduler_states[dest] = {
                    "context": {},
                    "history": {},
                    "vars": {}
                }

        # Get all schedulers (both running and stopped) for the log dropdown
        all_schedulers = []
        try:
            # Get valid publish destinations
            dest_data = _load_json_once("publish_destinations", "publish-destinations.json")
            if isinstance(dest_data, list):
                all_schedulers = [d["id"] for d in dest_data if isinstance(d, dict) and "id" in d]
            elif isinstance(dest_data, dict):
                all_schedulers = list(dest_data.keys())
            debug(f"Found {len(all_schedulers)} valid destinations: {all_schedulers}")
        except Exception as e:
            error(f"Error getting scheduler list: {str(e)}")
        
        # Get running schedulers
        debug(f"Found {len(current_running_schedulers)} running schedulers: {current_running_schedulers}")
        
        # Get schedule stacks and contexts for ALL schedulers
        schedule_stacks = {}
        layer_contexts = {}
        logs = {}
        scheduler_statuses = {}
        
        for dest in all_schedulers:
            # First try to get the schedule stack from the running scheduler
            if dest in scheduler_schedule_stacks and scheduler_schedule_stacks[dest]:
                schedule_stacks[dest] = scheduler_schedule_stacks[dest]
                debug(f"Found schedule stack in memory for {dest}: {len(schedule_stacks[dest])} layers")
            else:
                # If not in memory, try to load from disk
                try:
                    state = load_scheduler_state(dest)
                    if "schedule_stack" in state and state["schedule_stack"]:
                        schedule_stacks[dest] = state["schedule_stack"]
                        debug(f"Loaded schedule stack from disk for {dest}: {len(state['schedule_stack'])} layers")
                except Exception as e:
                    error(f"Error loading state for {dest}: {str(e)}")
            
            # Load context stack
            if dest in scheduler_contexts_stacks and scheduler_contexts_stacks[dest]:
                layer_contexts[dest] = {}
                for i, ctx in enumerate(scheduler_contexts_stacks[dest]):
                    # Show all context variables, not just vars
                    layer_contexts[dest][i] = ctx
                debug(f"Found context stack in memory for {dest}: {len(layer_contexts[dest])} layers")
                debug(f"Context data for {dest}: {layer_contexts[dest]}")
            else:
                try:
                    state = load_scheduler_state(dest)
                    if "context_stack" in state and state["context_stack"]:
                        layer_contexts[dest] = {}
                        for i, ctx in enumerate(state["context_stack"]):
                            # Show all context variables, not just vars
                            layer_contexts[dest][i] = ctx
                        debug(f"Loaded context stack from disk for {dest}: {len(state['context_stack'])} layers")
                        debug(f"Context data for {dest}: {layer_contexts[dest]}")
                except Exception as e:
                    error(f"Error loading context for {dest}: {str(e)}")
            
            # Get logs for each scheduler
            try:
                response = requests.get(f"{BASE_URL}/api/schedulers/{dest}")
                if response.ok:
                    logs[dest] = response.json().get("log", [])
                else:
                    logs[dest] = ["Error fetching log."]
            except Exception as e:
                error(f"Error getting logs for {dest}: {str(e)}")
                logs[dest] = ["Error fetching log."]

            # Get scheduler status
            try:
                response = requests.get(f"{BASE_URL}/api/schedulers/{dest}/status")
                if response.ok:
                    scheduler_statuses[dest] = response.json().get("status", "stopped")
                else:
                    scheduler_statuses[dest] = "unknown"
            except Exception as e:
                error(f"Error getting status for {dest}: {str(e)}")
                scheduler_statuses[dest] = "unknown"

        # Only auto-select log destination when submitting a new schedule
        selected_log = request.args.get('log', '')
        if request.method == "POST" and "destination" in request.form:
            selected_log = request.form["destination"]

        debug(f"Rendering template with schedule_stacks: {schedule_stacks}")
        debug(f"Rendering template with layer_contexts: {layer_contexts}")

        return render_template_string(
            HTML_TEMPLATE,
            all_destinations=all_schedulers,
            running_schedulers=current_running_schedulers,
            stopped_schedulers=[d for d in all_schedulers if d not in current_running_schedulers],
            schedule_stacks=schedule_stacks,
            layer_contexts=layer_contexts,
            logs=logs,
            scheduler_statuses=scheduler_statuses,
            schedulers=all_schedulers,
            selected=selected_log
        )

    if request.method == "POST":
        # Handle form submissions
        if "stop" in request.args:
            dest = request.args["stop"]
            stop_scheduler(dest)
        elif "start" in request.args:
            dest = request.args["start"]
            # Get the current schedule from the stack
            if dest in scheduler_schedule_stacks and scheduler_schedule_stacks[dest]:
                schedule = scheduler_schedule_stacks[dest][-1]
                start_scheduler(dest, schedule)
            else:
                error(f"No schedule found for {dest}")
        elif "pause" in request.args:
            dest = request.args["pause"]
            api_pause_scheduler(dest)
            return jsonify({"status": "paused"})  # Return JSON instead of redirect
        elif "unpause" in request.args:
            dest = request.args["unpause"]
            api_unpause_scheduler(dest)
            return jsonify({"status": "running"})  # Return JSON instead of redirect
        elif "unload_schedule" in request.args:
            dest = request.args["unload_schedule"]
            try:
                # First try to unload via API
                response = requests.delete(f"{BASE_URL}/api/schedulers/{dest}/schedule")
                if not response.ok:
                    # If API fails, try direct unload
                    if dest in scheduler_schedule_stacks and scheduler_schedule_stacks[dest]:
                        # Only unload if there are schedules to unload
                        if scheduler_schedule_stacks[dest]:
                            scheduler_schedule_stacks[dest].pop()
                            if dest in scheduler_contexts_stacks and scheduler_contexts_stacks[dest]:
                                scheduler_contexts_stacks[dest].pop()
                            scheduler_logs[dest].append(f"[{datetime.now().strftime('%H:%M')}] Unloaded schedule")
                            
                            # Update persisted state
                            update_scheduler_state(
                                dest,
                                schedule_stack=scheduler_schedule_stacks[dest],
                                context_stack=scheduler_contexts_stacks[dest] if dest in scheduler_contexts_stacks else []
                            )
                            
                            # If this was the last schedule, stop the scheduler
                            if not scheduler_schedule_stacks[dest]:
                                stop_scheduler(dest)
            except Exception as e:
                error(f"Error unloading schedule: {str(e)}")
        else:
            # Handle schedule submission
            destination = request.form["destination"]
            schedule_json = request.form["schedule_json"]
            try:
                schedule = json.loads(schedule_json.strip())
                response = requests.post(f"{BASE_URL}/api/schedulers/{destination}/schedule", json=schedule)
                if not response.ok:
                    error(f"Failed to load schedule: {response.text}")
                    return response.text
            except json.JSONDecodeError as e:
                error(f"Invalid JSON format: {str(e)}")
                return f"Error: Invalid JSON format - {str(e)}"
            except Exception as e:
                error(f"Error loading schedule: {str(e)}")
                return f"Error: {str(e)}"
        return redirect(url_for("test_scheduler.test_scheduler"))

def get_important_triggers(publish_destination: str) -> List[Dict[str, Any]]:
    """Get the list of important triggers for a destination."""
    if publish_destination in important_triggers:
        return important_triggers[publish_destination]
    return []

def schedule_editor(destination, layer, message=None, error=False):
    """Server-side schedule editor page."""
    debug(f"Opening editor for {destination} layer {layer}")
    
    # Fetch the schedule
    try:
        response = requests.get(f"{BASE_URL}/api/schedulers/{destination}/schedule/{layer}")
        debug(f"Schedule response: {response.status_code} - {response.text}")
        
        if not response.ok:
            return f"Error: Failed to load schedule. Status {response.status_code} - {response.text}"
            
        data = response.json()
        if "error" in data:
            return f"Error: {data['error']}"
            
        schedule_json = json.dumps(data["schedule"], indent=2)
        
        return render_template_string(
            EDITOR_TEMPLATE,
            destination=destination,
            layer=layer,
            schedule_json=schedule_json,
            message=message,
            error=error
        )
        
    except Exception as e:
        error_msg = f"Error loading schedule: {str(e)}"
        error(error_msg)
        return error_msg

@test_scheduler_bp.route("/test-scheduler/edit", methods=["POST"])
def save_schedule():
    """Handle schedule save from the editor."""
    debug("Handling schedule save")
    
    try:
        destination = request.form["destination"]
        layer = int(request.form["layer"])
        schedule_json = request.form["schedule_json"]
        
        debug(f"Saving schedule for {destination} layer {layer}")
        
        # Parse and validate JSON
        try:
            schedule = json.loads(schedule_json)
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON format: {str(e)}"
            error(error_msg)
            return schedule_editor(destination, layer, message=error_msg, error=True)
            
        # Send to API
        response = requests.put(
            f"{BASE_URL}/api/schedulers/{destination}/schedule/{layer}",
            json=schedule
        )
        debug(f"Save response: {response.status_code} - {response.text}")
        
        if not response.ok:
            error_msg = f"Failed to save schedule: {response.text}"
            error(error_msg)
            return schedule_editor(destination, layer, message=error_msg, error=True)
            
        # Success, redirect back to main page
        return redirect(url_for('test_scheduler.test_scheduler', log=destination))
        
    except Exception as e:
        error_msg = f"Error saving schedule: {str(e)}"
        error(error_msg)
        return error_msg

def initialize_from_schedule_files():
    """Initialize schedulers from JSON files in schedule directories."""
    # Check in multiple potential directories for schedule files
    schedule_dirs = [
        os.path.join(os.path.dirname(__file__), "scheduler"),
        os.path.join(os.path.dirname(__file__), "scheduled")
    ]
    
    schedule_files_found = False
    
    # Check if any schedulers are already running
    running_schedulers = []
    try:
        response = requests.get(f"{BASE_URL}/api/schedulers")
        if response.ok:
            running_schedulers = response.json().get('running', [])
            debug(f"Currently running schedulers: {running_schedulers}")
    except Exception as e:
        error(f"Error checking running schedulers: {str(e)}")
    
    for scheduler_dir in schedule_dirs:
        if not os.path.exists(scheduler_dir):
            debug(f"Scheduler directory {scheduler_dir} does not exist")
            continue
        
        debug(f"Checking for schedule files in {scheduler_dir}")
        schedule_files = [f for f in os.listdir(scheduler_dir) 
                         if f.endswith('.json') and not f.endswith('_state.json')]
        
        if not schedule_files:
            debug(f"No schedule files found in {scheduler_dir}")
            continue
            
        schedule_files_found = True
        debug(f"Found schedule files in {scheduler_dir}: {schedule_files}")
        
        for filename in schedule_files:
            dest_name = os.path.splitext(filename)[0]
            file_path = os.path.join(scheduler_dir, filename)
            
            # Skip if already running
            if dest_name in running_schedulers:
                debug(f"Scheduler for {dest_name} is already running, skipping")
                continue
                
            debug(f"Loading schedule file from {file_path} for destination {dest_name}")
            try:
                with open(file_path, 'r') as f:
                    schedule_data = json.load(f)
                
                # Use the API to load the schedule, which will handle context automatically
                debug(f"Sending schedule to API for {dest_name}")
                response = requests.post(f"{BASE_URL}/api/schedulers/{dest_name}/schedule", json=schedule_data)
                
                if response.ok:
                    info(f"Successfully initialized scheduler for {dest_name}")
                else:
                    error(f"Failed to initialize scheduler for {dest_name}: {response.text}")
                    
            except Exception as e:
                error(f"Error loading schedule file {file_path}: {str(e)}")
    
    if not schedule_files_found:
        debug("No schedule files found in any directory")
        return False
    
    return True