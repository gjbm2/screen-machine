"""ComfyUI step progress -> a job's stage and overall percent (docs/JOBS.md, "Progress from RunPod").

The RunPod worker relays ComfyUI's websocket frames as {job_id, comfy: {type, data}}. A workflow runs
its stages in order (workflows.json `processing_stages`, e.g. Rendering 90 / Upscaling 10); each stage
reports `progress` frames with value/max. A new stage is detected when value drops or max changes.
"""

DEFAULT_STAGES = [{"name": "Rendering", "weight": 100}]


def normalise_stages(raw):
    """workflows.json `processing_stages` (names, or {name, weight}) -> [{name, weight}]."""
    if not raw or not isinstance(raw, list):
        return list(DEFAULT_STAGES)
    if all(isinstance(s, str) for s in raw):
        weight = 100 / len(raw)
        return [{"name": s, "weight": weight} for s in raw]
    stages = []
    for s in raw:
        if isinstance(s, dict) and s.get("name"):
            try:
                weight = float(s.get("weight", 0))
            except (TypeError, ValueError):
                weight = 0
            stages.append({"name": str(s["name"]), "weight": max(weight, 0)})
    return stages or list(DEFAULT_STAGES)


class ComfyProgress:
    """Folds a job's comfy frames into (stage_index, percent, message). Not thread-safe: the tracker
    calls it under its lock."""

    def __init__(self, processing_stages=None):
        self.stages = normalise_stages(processing_stages)
        self.total_weight = sum(s["weight"] for s in self.stages) or 1
        self.stage_index = 0
        self.last_value = None
        self.last_max = None

    @property
    def stage_names(self):
        return [s["name"] for s in self.stages]

    def feed(self, comfy):
        """One comfy frame -> dict of job fields to change ({} when the frame says nothing new)."""
        if not isinstance(comfy, dict):
            return {}
        kind = comfy.get("type")
        data = comfy.get("data") or {}
        if kind == "progress":
            try:
                value = int(data.get("value", 0))
                max_val = int(data.get("max", 1)) or 1
            except (TypeError, ValueError):
                return {}
            if value == self.last_value and max_val == self.last_max:
                return {}
            if self.last_value is not None and (value < self.last_value or max_val != self.last_max):
                self.stage_index = min(self.stage_index + 1, len(self.stages) - 1)
            self.last_value, self.last_max = value, max_val
            done = sum(s["weight"] for s in self.stages[:self.stage_index])
            within = self.stages[self.stage_index]["weight"] * min(value / max_val, 1.0)
            percent = int(100 * (done + within) / self.total_weight)
            name = self.stages[self.stage_index]["name"]
            return {"stage_index": self.stage_index, "percent": max(0, min(percent, 100)),
                    "message": f"{name} {value}/{max_val}"}
        if kind == "status":
            remaining = ((data.get("status") or {}).get("exec_info") or {}).get("queue_remaining")
            if remaining == 0 and self.last_value is not None:
                return {"message": "Finalising"}
            if remaining:
                return {"message": "Starting the workflow"}
        return {}
