"""Alert categories and severity resolution.

Categories are convention strings; unknown categories default to "error".
Severity drives delivery policy (see config.ALERT_THROTTLE and dispatcher.py):

    critical  email immediately; short per-key cooldown; 6-h reminders while recurring
    error     email immediately; long per-key cooldown; counts roll into next email
    warning   batched into one rollup email per hour at most
    info      never emailed; counted in state and logged only
"""

SEVERITIES = ("info", "warning", "error", "critical")

DEFAULT_SEVERITIES = {
    "openai.quota": "critical",
    "openai.auth": "critical",
    "openai.error": "error",
    "runpod.job_failed": "error",
    "runpod.timeout": "error",
    "generation.job_failed": "error",
    "scheduler.loop_died": "critical",
    "scheduler.wedged": "critical",
    "scheduler.instruction_error": "error",
    "ws.server_died": "critical",
    "app.thread_crash": "error",
    "app.unhandled": "error",
    "app.startup": "info",
    "device.samsung_token": "warning",
    "assemblyai.stream": "warning",
    "media.download_failed": "warning",
    "alerting.storm": "error",
    "alerting.dropped": "error",
    "selftest": "critical",
}


def resolve_severity(category, severity):
    if severity in SEVERITIES:
        return severity
    return DEFAULT_SEVERITIES.get(str(category), "error")
