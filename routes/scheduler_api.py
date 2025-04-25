from datetime import datetime, timedelta
import random
from typing import Dict, Any, List

# === Context Initialization ===
def default_context():
    return {
        "vars": {},
        "prompt_history": [],
        "last_generated": None
    }

# === Instruction Execution ===
def run_instruction(instruction: Dict[str, Any], context: Dict[str, Any], now: datetime, output: List[str]):
    action = instruction["action"]

    handler_map = {
        "clear_history": handle_clear_history,
        "random_choice": handle_random_choice,
        "devise_prompt": handle_devise_prompt,
        "generate": handle_generate,
        "animate": handle_animate,
        "display": handle_display,
        "sleep": handle_sleep,
        "wait": handle_wait
    }

    if action in handler_map:
        handler_map[action](instruction, context, now, output)
    else:
        output.append(f"[{now.strftime('%H:%M')}] Unknown action: {action}")


def handle_clear_history(instruction, context, now, output):
    context["prompt_history"] = []
    output.append(f"[{now.strftime('%H:%M')}] Cleared prompt history.")


def handle_random_choice(instruction, context, now, output):
    var = instruction["var"]
    choice = random.choice(instruction["choices"])
    context["vars"][var] = choice
    output.append(f"[{now.strftime('%H:%M')}] Randomly chose '{choice}' for var '{var}'.")


def handle_devise_prompt(instruction, context, now, output):
    theme = instruction.get("theme") or context["vars"].get(instruction.get("theme_var"))
    prompt = f"Prompt about {theme}"
    context["vars"][instruction["var"]] = prompt
    context["prompt_history"].append(prompt)
    output.append(f"[{now.strftime('%H:%M')}] Devised prompt: '{prompt}'.")


def handle_generate(instruction, context, now, output):
    prompt = context["vars"].get(instruction["prompt_var"])
    from routes.generate import generate_image
    image_path = generate_image(prompt=prompt, workflow="default")
    context["last_generated"] = image_path
    context["prompt_history"].append(prompt)
    output.append(f"[{now.strftime('%H:%M')}] Generated image from: '{prompt}'.")


def handle_animate(instruction, context, now, output):
    base = context["last_generated"]
    anim = f"Animated: '{base}'"
    output.append(f"[{now.strftime('%H:%M')}] {anim}")


def handle_display(instruction, context, now, output):
    mode = instruction["mode"]
    img = context.get("last_generated")
    result = f"Displayed ({mode}) image."
    output.append(f"[{now.strftime('%H:%M')}] {result}")


def handle_sleep(instruction, context, now, output):
    duration = instruction["duration"]
    output.append(f"[{now.strftime('%H:%M')}] Sleeping display for {duration} minutes.")


def handle_wait(instruction, context, now, output):
    duration = instruction["duration"]
    output.append(f"[{now.strftime('%H:%M')}] Waiting for {duration} minutes (no-op).")


# === Schedule Resolver ===
def resolve_schedule(schedule: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    date_str = now.strftime("%-d-%b")  # e.g., 25-Dec
    day_str = now.strftime("%A")       # e.g., Friday
    time_str = now.strftime("%H:%M")   # e.g., 08:00
    minute_of_day = now.hour * 60 + now.minute

    for rule in schedule.get("day_of_year", []):
        if rule["date"] == date_str:
            return rule["instructions"]

    for rule in schedule.get("day_of_week", []):
        if rule["day"] == day_str:
            return rule["instructions"]

    for block in schedule.get("time_of_day", []):
        if "time" in block and block["time"] == time_str:
            return block["instructions"]
        elif "repeat" in block and "between" in block:
            start = datetime.strptime(block["between"][0], "%H:%M").time()
            end = datetime.strptime(block["between"][1], "%H:%M").time()
            in_window = start <= now.time() <= end
            if in_window and minute_of_day % block["repeat"] == 0:
                return block["instructions"]

    return []


# === Run scheduler in real time ===
from flask import Blueprint, request, jsonify
import json

scheduler_bp = Blueprint("scheduler_bp", __name__)

@scheduler_bp.route("/api/schedulers", methods=["GET"])
def api_list_schedulers():
    return jsonify({"running": list_running_schedulers()})

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["GET"])
def api_get_scheduler_log(publish_destination):
    return jsonify({"log": get_scheduler_log(publish_destination)})

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["POST"])
def api_start_scheduler(publish_destination):
    try:
        schedule = request.json
        start_scheduler(publish_destination, schedule)
        return jsonify({"status": "started", "destination": publish_destination})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["DELETE"])
def api_stop_scheduler(publish_destination):
    stop_scheduler(publish_destination)
    return jsonify({"status": "stopped", "destination": publish_destination})

# === Scheduler status ===
def list_running_schedulers() -> List[str]:
    return list(running_schedulers.keys())

import asyncio

running_schedulers = {}
scheduler_logs: Dict[str, List[str]] = {}

async def run_scheduler(schedule: Dict[str, Any], publish_destination: str, step_minutes: int = 1):
    context = default_context()
    scheduler_logs[publish_destination] = []
    context = default_context()
    while True:
        now = datetime.now()
        instructions = resolve_schedule(schedule, now)
        for instr in instructions:
            run_instruction(instr, context, now, scheduler_logs[publish_destination])  # discard log output in real mode
        await asyncio.sleep(step_minutes * 60)


def start_scheduler(publish_destination: str, schedule: Dict[str, Any]):
    if publish_destination in running_schedulers:
        stop_scheduler(publish_destination)
    task = asyncio.create_task(run_scheduler(schedule, publish_destination))
    running_schedulers[publish_destination] = task


def get_scheduler_log(publish_destination: str) -> List[str]:
    return scheduler_logs.get(publish_destination, [])

def stop_scheduler(publish_destination: str):
    task = running_schedulers.pop(publish_destination, None)
    if task:
        task.cancel()

# === Simulate schedule with context ===
def simulate_schedule(schedule: Dict[str, Any], start_time: str, end_time: str, step_minutes: int, context: Dict[str, Any]) -> List[str]:
    now = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")
    output = []

    while now <= end:
        instructions = resolve_schedule(schedule, now)
        for instr in instructions:
            run_instruction(instr, context, now, output)
        now += timedelta(minutes=step_minutes)

    return output
