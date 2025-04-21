import os
import threading
import runpod
import requests
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# Path to the job registry file
_JOB_REGISTRY_FILE = "runpod_jobs.log"
_registry_lock = threading.Lock()

def add_job(job_id: str, endpoint_id: str):
    with _registry_lock:
        with open(_JOB_REGISTRY_FILE, "a") as f:
            f.write(f"{job_id},{endpoint_id}\n")

def get_all_jobs() -> list[tuple[str, str]]:
    if not os.path.exists(_JOB_REGISTRY_FILE):
        return []
    with _registry_lock:
        with open(_JOB_REGISTRY_FILE, "r") as f:
            lines = set(line.strip() for line in f if line.strip())
            return [tuple(line.split(",", 1)) for line in lines if "," in line]

def clear_registry():
    """Clear the job registry file."""
    with _registry_lock:
        if os.path.exists(_JOB_REGISTRY_FILE):
            os.remove(_JOB_REGISTRY_FILE)

def cancel_all_jobs() -> int:
    api_key = os.getenv("RUNPOD_API_KEY")
    if not api_key:
        error("❌ RUNPOD_API_KEY not set.")
        return 0

    job_pairs = get_all_jobs()
    debug(f"\n🔍 Found {len(job_pairs)} tracked job(s).")

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    cancelled = 0
    for job_id, endpoint_id in job_pairs:
        url = f"https://api.runpod.ai/v2/{endpoint_id}/cancel/{job_id}"
        debug(f"[cancel_all_jobs] DEBUG: Attempting to cancel {job_id} via {endpoint_id}")
        try:
            response = requests.post(url, headers=headers)
            debug(f"[cancel_all_jobs] DEBUG: Status {response.status_code} - {response.text}")

            if response.status_code == 200:
                info(f"❌ Cancelled job: {job_id}")
                cancelled += 1
            else:
                debug(f"⚠️ Failed to cancel {job_id}: {response.status_code} - {response.text}")

        except Exception as e:
            error(f"🔥 Exception while cancelling job {job_id}: {e}")

    clear_registry()
    info(f"\n✅ Done. Cancelled {cancelled} job(s) and cleared registry.")
    return cancelled