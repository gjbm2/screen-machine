"""What a screen shows, as one document (owner, 24 Nov: the TV page used to poll <screen>.jpg and .mp4 every second and
show whichever was newer, so a still's clip, published a moment after it, became the screen's picture).

output/<screen>.state.json, written in one step each time the screen's shown file lands (publisher._publish_to_destination),
and pushed to the screen's pages through room_service. The push is only a speed-up: a page reads the document itself at
start, on waking, on reconnecting and every ~10 s, so a page that slept or lost its socket is right on its next read.

    {"v": 1, "screen", "seq", "at",
     "still":      {"url", "width", "height", "origin"} | null,     the picture on the screen, as fitted to it
     "clip":       {"url", "eased", "pace", "play_s", "rest_s", "returns"} | null,   the still's own moving clip
     "transition": {"url", "from", "to", "at", ...} | null,         into this still from the one before (played once)
     "video":      {"url", "clip": {...} | null} | null}           a video someone published as the picture itself

A still with a clip is still a still: the page shows the picture and plays the clip over it now and then. Every url is
a file that never changes (a hard link under output/_screens/<screen>/), so a page can fetch it ahead and keep it.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

from utils.logger import info, warning

V = 1
FOLDER = "_screens"
KEEP = 12                 # files kept per screen (never one the current document names)
_LOCK = threading.RLock()
VIDEOS = (".mp4", ".m4v", ".mov", ".webm")


def output_root() -> Path:
    from routes.bucketer import BASE_OUTPUT
    return Path(BASE_OUTPUT)


def path(screen: str) -> Path:
    return output_root() / f"{screen}.state.json"


def read(screen: str) -> dict | None:
    try:
        return json.loads(path(screen).read_text("utf-8"))
    except Exception:
        return None


def _keep(screen: str, shown: Path, seq: int) -> str:
    """A never-changing copy of the shown file (a hard link: no bytes copied), as its /output URL."""
    folder = output_root() / FOLDER / screen
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{seq:06d}{shown.suffix.lower()}"
    tmp = folder / f".{dst.name}.tmp"
    tmp.unlink(missing_ok=True)
    try:
        os.link(shown, tmp)
    except OSError:
        shutil.copy2(shown, tmp)
    os.replace(tmp, dst)
    return f"/output/{FOLDER}/{screen}/{dst.name}"


def _prune(screen: str, state: dict) -> None:
    folder = output_root() / FOLDER / screen
    named = {Path(str((state.get(k) or {}).get("url") or "")).name for k in ("still", "clip", "video")}
    files = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")), reverse=True)
    for p in files[KEEP:]:
        if p.name not in named:
            p.unlink(missing_ok=True)


def _size(p: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:
        return None, None


def _name(v) -> str:
    return Path(str(v or "")).name


def _clip_info(meta: dict, shown: Path) -> dict | None:
    clip = meta.get("clip")
    if not isinstance(clip, dict):
        from routes.clip_ease import clip_tag
        clip = clip_tag(shown)
    return clip if isinstance(clip, dict) else None


def _is_clip_of(meta: dict, clip: dict | None, prev: dict | None) -> bool:
    """A still's own clip, published after it: it names the still on the screen and comes back to it (a screen clip).
    A video someone published as the picture itself names no still, or doesn't return to its first frame."""
    still = (prev or {}).get("still")
    if not still or not clip or not clip.get("eased") or clip.get("returns") is False or clip.get("transition"):
        return False
    named = _name(meta.get("still"))
    return bool(named) and named in (_name(still.get("origin")), _name(still.get("name")))


def decide(prev: dict | None, shown: Path, meta: dict, keep=None) -> dict:
    """The screen's next document, given the one before, the file that just landed and its metadata. keep(shown) gives
    the shown file's never-changing URL (tests pass their own)."""
    seq = int((prev or {}).get("seq") or 0) + 1
    url = keep(shown) if keep else shown.name
    state = {"v": V, "seq": seq, "at": round(time.time(), 3), "still": None, "clip": None, "transition": None, "video": None}
    if shown.suffix.lower() in VIDEOS:
        clip = _clip_info(meta, shown)
        if _is_clip_of(meta, clip, prev):
            state["still"], state["transition"] = prev["still"], prev.get("transition")
            state["clip"] = {"url": url, **{k: clip.get(k) for k in ("eased", "pace", "play_s", "rest_s", "returns")}}
        else:
            state["video"] = {"url": url, "clip": clip}
        return state
    w, h = _size(shown)
    state["still"] = {"url": url, "width": w, "height": h, "origin": meta.get("origin"), "name": meta.get("filename")}
    t = meta.get("transition")
    if isinstance(t, dict) and t.get("url"):
        state["transition"] = {k: t.get(k) for k in ("url", "from", "to", "at", "play_s", "idea")}
    return state


def placed(screen: str, shown: Path, meta: dict | None) -> dict | None:
    """The screen's file `shown` has just landed: write its document in one step and push it to the screen's pages."""
    try:
        with _LOCK:
            prev = read(screen)
            state = decide(prev, Path(shown), meta or {}, keep=lambda p: _keep(screen, p, int((prev or {}).get("seq") or 0) + 1))
            state["screen"] = screen
            tmp = path(screen).with_name(f".{screen}.state.json.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), "utf-8")
            os.replace(tmp, path(screen))
            _prune(screen, state)
        kind = "video" if state["video"] else "still + clip" if state["clip"] else "still"
        info(f"[screen_state] {screen} #{state['seq']}: {kind}" + (" (with its transition)" if state["transition"] and not state["clip"] else ""))
        threading.Thread(target=push, args=(screen, state), name=f"state-{screen}", daemon=True).start()
        return state
    except Exception as e:
        warning(f"[screen_state] {screen}: could not write what it shows: {e}")
        return None


def push(screen: str, state: dict) -> None:
    """Tell the screen's pages at once (room_service's page sockets). Best effort: pages read the document anyway."""
    try:
        import requests
        import smconfig
        url = smconfig.url("room").rstrip("/") + f"/api/v1/screens/{screen}/art-state"
        requests.post(url, json=state, timeout=1.5)
    except Exception as e:
        info(f"[screen_state] {screen}: push not sent ({type(e).__name__}); its pages read it themselves")
