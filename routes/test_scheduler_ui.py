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
import base64
from urllib.parse import quote
from jsonschema.validators import validator_for

BASE_URL = "http://localhost:5000"

# === Scheduler Control Panel UI ===
test_scheduler_bp = Blueprint("test_scheduler", __name__)

from routes.utils import _load_json_once
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# Get schema from API
def get_schema():
    """Get the schema from the API endpoint."""
    try:
        response = requests.get(f"{BASE_URL}/api/scheduler/schema")
        if response.ok:
            return response.json()
        else:
            error(f"Failed to get schema from API: {response.status_code} {response.text}")
            return None
    except Exception as e:
        error(f"Error getting schema from API: {str(e)}")
        return None

# Generate minimal valid data for a schema
def minimal_valid_data(schema):
    """Generate minimal valid data for a schema."""
    if not schema:
        return {}
        
    if schema.get("type") == "object":
        result = {}
        for prop_name, prop_schema in schema.get("properties", {}).items():
            if not prop_schema.get("required", False):
                continue
            result[prop_name] = minimal_valid_data(prop_schema)
        return result
    elif schema.get("type") == "array":
        return []
    elif schema.get("type") == "string":
        return ""
    elif schema.get("type") == "number":
        return 0
    elif schema.get("type") == "boolean":
        return False
    elif schema.get("type") == "null":
        return None
    else:
        return None

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
    /* Destination context preview */
    .destination-context-preview {
      display: inline-block;
      font-size: 12px;
      color: #666;
      margin-left: 10px;
      max-width: 300px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    /* Tab styles */
    .tab-container {
      margin-top: 10px;
    }
    .tab-buttons {
      display: flex;
      border-bottom: 1px solid #ddd;
    }
    .tab-button {
      padding: 8px 16px;
      background: #f5f5f5;
      border: 1px solid #ddd;
      border-bottom: none;
      cursor: pointer;
      margin-right: 2px;
    }
    .tab-button.active {
      background: white;
      border-bottom: 1px solid white;
      margin-bottom: -1px;
    }
    .tab-content {
      padding: 15px;
      border: 1px solid #ddd;
      border-top: none;
    }
    .tab-panel {
      display: none;
    }
    .tab-panel.active {
      display: block;
    }
  </style>
  <script>
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
      // Update status for all schedulers every 5 seconds
      const statusElements = document.querySelectorAll('.scheduler-status');
      if (statusElements.length > 0) {
        setInterval(() => {
          statusElements.forEach(element => {
            const destination = element.id.replace('scheduler-status-', '');
            updateSchedulerStatus(destination);
          });
        }, 30000);
      }
      
      // Initial updates
      statusElements.forEach(element => {
        const destination = element.id.replace('scheduler-status-', '');
        updateSchedulerStatus(destination);
      });

      // Set up tab switching
      document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', function() {
          const layerId = this.getAttribute('data-layer');
          const tabId = this.getAttribute('data-tab');
          
          // Deactivate all tabs in this layer
          document.querySelectorAll(`[data-layer="${layerId}"] .tab-button`).forEach(btn => {
            btn.classList.remove('active');
          });
          document.querySelectorAll(`#layer-${layerId} .tab-panel`).forEach(panel => {
            panel.classList.remove('active');
          });
          
          // Activate the clicked tab
          this.classList.add('active');
          document.querySelector(`#layer-${layerId} .tab-panel-${tabId}`).classList.add('active');
        });
      });
    });

    async function clearContext(destination) {
      if (!confirm('Clear context for ' + destination + '?')) return;
      try {
        const response = await fetch(`/api/schedulers/${destination}/context/clear`, { method: 'POST' });
        if (response.ok) {
          alert('Context cleared for ' + destination);
          location.reload();
        } else {
          const data = await response.json();
          alert('Failed to clear context: ' + (data.error || response.status));
        }
      } catch (e) {
        alert('Error clearing context: ' + e);
      }
    }

    function openCreateEditor(destination) {
      fetch(`/test-scheduler/create-editor-url?destination=${destination}`)
        .then(response => response.json())
        .then(data => {
          if (data.url) {
            window.open(data.url, '_blank');
          } else {
            console.error('Invalid response from server:', data);
          }
        })
        .catch(error => {
          console.error('Error:', error);
          alert('Failed to open editor: ' + error.message);
        });
    }
    function openLogs(destination) {
      window.open('/test-scheduler/logs?destination=' + encodeURIComponent(destination), '_blank');
    }
  </script>
</head>
<body>
  <div class="main-content">
  <h1>Scheduler Control Panel</h1>
    
    <div class="actions-bar">
      <strong>Global Actions:</strong>
      <!-- Removed Load Schedules from Files button -->
    </div>

  <!-- Removed Publish Destinations section and log side panel -->

  <h2>Running Schedulers</h2>
    {% if running_schedulers %}
      {% for dest in running_schedulers %}
        <div class="scheduler-info">
          <h3>
            {{ dest }}
            {% if dest in layer_contexts and 0 in layer_contexts[dest] and layer_contexts[dest][0].vars %}
              <span class="destination-context-preview">
                Context: {% for key, value in layer_contexts[dest][0].vars.items() %}{{ key }}={{ value|truncate(20) }}{% if not loop.last %}, {% endif %}{% endfor %}
              </span>
            {% endif %}
          </h3>
          <div>
            <span class="status">Status: <span id="scheduler-status-{{ dest }}" class="scheduler-status running">running</span></span>
            <button class="btn" type="button" onclick="openCreateEditor('{{ dest }}')">Create</button>
        <form method="POST" action="/test-scheduler?stop={{ dest }}" style="display:inline">
          <input class="btn" type="submit" value="Stop">
        </form>
            <button class="btn" onclick="toggleSchedulerPause('{{ dest }}', 'pause')">Pause</button>
            <button class="btn" onclick="toggleSchedulerPause('{{ dest }}', 'unpause')">Unpause</button>
            <button class="btn" type="button" onclick="clearContext('{{ dest }}')">Clear Context</button>
            <button class="btn" type="button" onclick="openLogs('{{ dest }}')">Show logs</button>
          </div>
          
          {% if dest in schedule_stacks %}
            <div class="schedule-stack">
              <h4>Schedule Stack</h4>
              {% for layer in schedule_stacks[dest] %}
                <div id="layer-{{ dest }}-{{ loop.index0 }}" class="stack-layer">
                  <div>
                    <strong>Layer {{ loop.index }}</strong>
                    <div>
                      <a class="btn" href="/test-scheduler?edit={{ dest }}&layer={{ loop.index0 }}" target="_blank">Edit</a>
                    </div>
                  </div>
                  
                  <div class="tab-container">
                    <!-- Tab buttons -->
                    <div class="tab-buttons" data-layer="{{ dest }}-{{ loop.index0 }}">
                      <button class="tab-button active" data-layer="{{ dest }}-{{ loop.index0 }}" data-tab="schedule">Schedule</button>
                      <button class="tab-button" data-layer="{{ dest }}-{{ loop.index0 }}" data-tab="context">Context</button>
                    </div>
                    
                    <!-- Tab content -->
                    <div class="tab-content">
                      <!-- Schedule tab -->
                      <div class="tab-panel tab-panel-schedule active">
                        <pre>{{ layer|tojson(indent=2) }}</pre>
                      </div>
                      
                      <!-- Context tab -->
                      <div class="tab-panel tab-panel-context">
                        {% if dest in layer_contexts and loop.index0 in layer_contexts[dest] %}
                          <div class="context-vars">
                            <h4>Context Variables:</h4>
                            <pre>{{ layer_contexts[dest][loop.index0]|tojson(indent=2) }}</pre>
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
                        {% else %}
                          <p>No context information available for this layer.</p>
                        {% endif %}
                      </div>
                    </div>
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
          <h3>
            {{ dest }}
            {% if dest in layer_contexts and 0 in layer_contexts[dest] and layer_contexts[dest][0].vars %}
              <span class="destination-context-preview">
                Context: {% for key, value in layer_contexts[dest][0].vars.items() %}{{ key }}={{ value|truncate(20) }}{% if not loop.last %}, {% endif %}{% endfor %}
              </span>
            {% endif %}
          </h3>
          <div>
            <span class="status">Status: <span id="scheduler-status-{{ dest }}" class="scheduler-status stopped">stopped</span></span>
            <button class="btn" type="button" onclick="openCreateEditor('{{ dest }}')">Create</button>
            <form method="POST" action="/test-scheduler?start={{ dest }}" style="display:inline">
              <input class="btn" type="submit" value="Start">
        </form>
            <button class="btn" type="button" onclick="openLogs('{{ dest }}')">Show logs</button>
          </div>
          
          {% if dest in schedule_stacks %}
            <div class="schedule-stack">
              <h4>Schedule Stack</h4>
              {% for layer in schedule_stacks[dest] %}
                <div id="layer-{{ dest }}-{{ loop.index0 }}" class="stack-layer">
                  <div>
                    <strong>Layer {{ loop.index }}</strong>
                    <div>
                      <a class="btn" href="/test-scheduler?edit={{ dest }}&layer={{ loop.index0 }}" target="_blank">Edit</a>
                    </div>
                  </div>
                  
                  <div class="tab-container">
                    <!-- Tab buttons -->
                    <div class="tab-buttons" data-layer="{{ dest }}-{{ loop.index0 }}">
                      <button class="tab-button active" data-layer="{{ dest }}-{{ loop.index0 }}" data-tab="schedule">Schedule</button>
                      <button class="tab-button" data-layer="{{ dest }}-{{ loop.index0 }}" data-tab="context">Context</button>
                    </div>
                    
                    <!-- Tab content -->
                    <div class="tab-content">
                      <!-- Schedule tab -->
                      <div class="tab-panel tab-panel-schedule active">
                        <pre>{{ layer|tojson(indent=2) }}</pre>
                      </div>
                      
                      <!-- Context tab -->
                      <div class="tab-panel tab-panel-context">
                        {% if dest in layer_contexts and loop.index0 in layer_contexts[dest] %}
                          <div class="context-vars">
                            <h4>Context Variables:</h4>
                            <pre>{{ layer_contexts[dest][loop.index0]|tojson(indent=2) }}</pre>
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
                        {% else %}
                          <p>No context information available for this layer.</p>
                        {% endif %}
                      </div>
                    </div>
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
            debug(f"Edit request received for {dest} layer {layer}")
            return schedule_editor(dest, layer)
            
        # Get all scheduler states - INCLUDING those that are paused but not running
        current_running_schedulers = []
        paused_schedulers = []
        stopped_schedulers = []
        
        # Get context and history for each scheduler
        scheduler_states_data = {}
        
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
        
        # Get schedule stacks and contexts for ALL schedulers
        schedule_stacks = {}
        layer_contexts = {}
        logs = {}
        scheduler_statuses = {}
        
        for dest in all_schedulers:
            # Check the actual state from scheduler_states or disk
            state = None
            if dest in scheduler_states:
                state = scheduler_states[dest]
            else:
                try:
                    loaded_state = load_scheduler_state(dest)
                    state = loaded_state.get("state", "stopped")
                except Exception as e:
                    debug(f"Could not load state for {dest}: {e}")
                    state = "stopped"
            
            # Categorize by actual state
            if state == "running":
                current_running_schedulers.append(dest)
            elif state == "paused":
                paused_schedulers.append(dest)
            else:
                stopped_schedulers.append(dest)
                
            debug(f"Categorized {dest} as {state}")
            
            # First try to get the schedule stack from the running scheduler
            if dest in scheduler_schedule_stacks and scheduler_schedule_stacks[dest]:
                schedule_stacks[dest] = scheduler_schedule_stacks[dest]
                debug(f"Found schedule stack in memory for {dest}: {len(schedule_stacks[dest])} layers")
            else:
                # If not in memory, try to load from disk
                try:
                    loaded_state = load_scheduler_state(dest)
                    if "schedule_stack" in loaded_state and loaded_state["schedule_stack"]:
                        schedule_stacks[dest] = loaded_state["schedule_stack"]
                        debug(f"Loaded schedule stack from disk for {dest}: {len(loaded_state['schedule_stack'])} layers")
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
                    loaded_state = load_scheduler_state(dest)
                    if "context_stack" in loaded_state and loaded_state["context_stack"]:
                        layer_contexts[dest] = {}
                        for i, ctx in enumerate(loaded_state["context_stack"]):
                            # Show all context variables, not just vars
                            layer_contexts[dest][i] = ctx
                        debug(f"Loaded context stack from disk for {dest}: {len(loaded_state['context_stack'])} layers")
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

            # Store the state in scheduler_statuses for display
            scheduler_statuses[dest] = state
            
            # Save context data for this destination
            if dest in scheduler_contexts_stacks and scheduler_contexts_stacks[dest]:
                context = scheduler_contexts_stacks[dest][-1]
                scheduler_states_data[dest] = {
                    "context": context,
                    "history": {k: v for k, v in context.items() if isinstance(v, list)},
                    "vars": context.get("vars", {})
                }
            else:
                scheduler_states_data[dest] = {
                    "context": {},
                    "history": {},
                    "vars": {}
                }

        # Only auto-select log destination when submitting a new schedule
        selected_log = request.args.get('log', '')
        if request.method == "POST" and "destination" in request.form:
            selected_log = request.form["destination"]

        debug(f"Rendering template with schedule_stacks: {schedule_stacks}")
        debug(f"Rendering template with layer_contexts: {layer_contexts}")
        debug(f"Using running schedulers: {current_running_schedulers}")
        debug(f"Using paused schedulers: {paused_schedulers}")
        debug(f"Using stopped schedulers: {stopped_schedulers}")

        return render_template_string(
            HTML_TEMPLATE,
            all_destinations=all_schedulers,
            running_schedulers=current_running_schedulers,
            paused_schedulers=paused_schedulers,
            stopped_schedulers=stopped_schedulers,
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
            debug(f"TEST UI stopping scheduler for {dest}")
            stop_scheduler(dest)
        elif "start" in request.args:
            dest = request.args["start"]
            debug(f"TEST UI starting scheduler for {dest}")
            # Get the current schedule from the stack
            if dest in scheduler_schedule_stacks and scheduler_schedule_stacks[dest]:
                schedule = scheduler_schedule_stacks[dest][-1]
                start_scheduler(dest, schedule)
            else:
                error(f"No schedule found for {dest}")
        elif "pause" in request.args:
            dest = request.args["pause"]
            debug(f"TEST UI pausing scheduler for {dest}")
            api_pause_scheduler(dest)
            return jsonify({"status": "paused"})  # Return JSON instead of redirect
        elif "unpause" in request.args:
            dest = request.args["unpause"]
            debug(f"TEST UI unpausing scheduler for {dest}")
            api_unpause_scheduler(dest)
            return jsonify({"status": "running"})  # Return JSON instead of redirect
        elif "unload_schedule" in request.args:
            dest = request.args["unload_schedule"]
            debug(f"TEST UI unloading schedule for {dest}")
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
                                debug(f"TEST UI stopping scheduler after unloading last schedule for {dest}")
                                stop_scheduler(dest)
            except Exception as e:
                error(f"Error unloading schedule: {str(e)}")
        else:
            # Handle schedule submission
            destination = request.form["destination"]
            schedule_json = request.form["schedule_json"]
            debug(f"TEST UI loading schedule for {destination}")
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

# Prevent PyTest from treating this view function as a test case
test_scheduler.__test__ = False

def get_important_triggers(publish_destination: str) -> List[Dict[str, Any]]:
    """Get the list of important triggers for a destination."""
    if publish_destination in important_triggers:
        return important_triggers[publish_destination]
    return []

def schedule_editor(destination, layer, message=None, error=False):
    """Get editor URL with state"""
    try:
        # Get the current schedule data
        response = requests.get(f"{BASE_URL}/api/schedulers/{destination}/schedule/{layer}")
        if not response.ok:
            raise Exception(f"Failed to get schedule: {response.text}")
            
        current_schedule = response.json().get("schedule", {})
        
        # Get the schema
        schema = get_schema()
        
        # Load VITE URL from environment
        vite_url = os.getenv('VITE_URL', 'http://localhost:5173')
        if vite_url.endswith('/'):
            vite_url = vite_url[:-1]
            
        # Match the config.ts pattern: use absolute URL in development, relative in production
        is_dev = not os.path.exists('./.env.production')
        api_url = f"{BASE_URL}/api" if is_dev else '/api'
        
        # Construct the editor URL with state
        editor_state = {
            'schema': schema,  # This is now always the current schema from the file
            'currentData': current_schedule,
            'returnUrl': f'/test-scheduler?destination={destination}',
            'saveEndpoint': f'{BASE_URL}/api/schedulers/{destination}/schedule/{layer}',
            'saveMethod': 'PUT'
        }
        
        # Convert state to JSON and properly encode it
        state_json = json.dumps(editor_state, separators=(',', ':'))  # Compact JSON
        state_b64 = base64.urlsafe_b64encode(state_json.encode('utf-8')).decode('utf-8')
        
        # Construct the final URL - no need for additional URL encoding since urlsafe_b64encode is used
        editor_url = f'{vite_url}/SchemaEditor?state={state_b64}'
        
        debug(f"Redirecting to editor URL: {editor_url}")
        return redirect(editor_url)
    except Exception as e:
        error(f"Error preparing schedule editor: {str(e)}")
        return f"Error: {str(e)}", 500

# Add a new route for logs view
@test_scheduler_bp.route("/test-scheduler/logs")
def logs_view():
    destination = request.args.get("destination")
    if not destination:
        return "No destination specified", 400
    return render_template_string("""
    <!doctype html>
    <html>
    <head>
      <title>Logs for {{ destination }}</title>
      <style>
        body { background: #111; color: #0f0; font-family: monospace; margin: 0; padding: 0; }
        .log-content { padding: 2vw; font-size: 1.1em; white-space: pre-wrap; }
        .header { background: #222; color: #fff; padding: 1vw; font-size: 1.5em; }
      </style>
      <script>
        async function fetchLogs() {
          try {
            const resp = await fetch('/api/schedulers/{{ destination }}');
            if (!resp.ok) {
              document.getElementById('log-content').textContent = 'Failed to load logs: ' + resp.statusText;
              return;
            }
            const contentType = resp.headers.get('content-type') || '';
            const logContent = document.getElementById('log-content');
            if (!contentType.includes('application/json')) {
              const text = await resp.text();
              logContent.textContent = 'Failed to load logs: Invalid response format.\\n' + text;
              console.error('Non-JSON response:', text);
              return;
            }
            try {
              const data = await resp.json();
              logContent.textContent = (data.log || []).join('\\n');
            } catch (e) {
              const text = await resp.text();
              logContent.textContent = 'Failed to parse logs as JSON.\\n' + text;
              console.error('JSON parse error:', e, 'Response text:', text);
            }
          } catch (e) {
            document.getElementById('log-content').textContent = 'Failed to load logs.';
            console.error('Fetch error:', e);
          }
        }
        setInterval(fetchLogs, 10000);
        window.onload = fetchLogs;
      </script>
    </head>
    <body>
      <div class="header">Logs for {{ destination }}</div>
      <div id="log-content" class="log-content">Loading...</div>
    </body>
    </html>
    """, destination=destination)

@test_scheduler_bp.route("/test-scheduler/create-editor-url")
def create_editor_url_route():
    """Route to create editor URL"""
    destination = request.args.get('destination')
    if not destination:
        return jsonify({"error": "No destination provided"}), 400
    return create_editor_url(destination)

def create_editor_url(destination):
    """Create a URL for the schema editor with the current state."""
    try:
        schema = get_schema()
        if not schema:
            return jsonify({"error": "Failed to get schema"}), 500

        # Get current schedule data
        schedule_data = get_schedule_data()
        
        # Construct editor state
        editor_state = {
            "schema": schema,
            "initialData": schedule_data,
            "returnUrl": destination,
            "saveEndpoint": "/api/test-scheduler/save",
            "saveMethod": "POST"
        }

        # Get Vite URL from environment
        vite_url = os.getenv('VITE_URL', 'http://localhost:5173')
        if vite_url.endswith('/'):
            vite_url = vite_url[:-1]

        # Return URL to SchemaEdit component with state
        editor_url = f"{vite_url}/schema-edit?state={base64.b64encode(json.dumps(editor_state).encode()).decode()}"
        debug(f"len(editor_url): {len(editor_url)}")
        return jsonify({"url": editor_url, "state": editor_state})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_schedule_data():
    """Get the current schedule data from the API."""
    try:
        response = requests.get(f"{BASE_URL}/api/scheduler/schedule")
        if response.ok:
            return response.json()
        else:
            error(f"Failed to get schedule data from API: {response.status_code} {response.text}")
            return {}
    except Exception as e:
        error(f"Error getting schedule data from API: {str(e)}")
        return {}