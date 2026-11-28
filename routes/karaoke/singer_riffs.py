"""karaoke.singer_riffs: a few AI pictures made from each singer's own photo (owner, 28 Nov: "if singer photos are
uploaded, we should commission a few ai photo gen riffs of each singer (queued sensibly, etc. dropped in when
complete). e.g. 'this person on stage at Wembley', 'this person singing at ronnie scotts', 'this person dressed as a
popstar' etc. and make those available in same way ... spirit is fun").

Each photo gets PER_SINGER scenes picked at random from SCENES, listed in the singer's row of singer_photos' index
(``riffs``: [{scene, state, file?, w?, h?}], state queued | making | done | failed). One worker makes them one at a
time, round the singers (everyone's first before anyone's second), the earliest photo first. A new photo starts its
riffs again; the name's riffs go with its photo (singer_photos.drop / clear).

Made pictures join the singer's own photo in the photo scenes (singer_photos.pictures_of). The making itself is
``backend``: None until a generator is set (set_backend); until then the riffs wait, queued, and start when it is.
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from utils.logger import error, info

PER_SINGER = 4
LIKENESS = ("Keep this person's face, hair and likeness exactly as in the photo, recognisably them. Photorealistic, "
            "joyful, family friendly, no text or logos. ")
SCENES = {
    "wembley": "This person singing on the main stage at Wembley Stadium, a huge crowd with phone lights, big stage "
               "lights and confetti.",
    "ronnie": "This person singing at Ronnie Scott's jazz club in London: an intimate room, warm low light, a jazz "
              "trio behind them.",
    "popstar": "This person dressed as a dazzling pop star: sequins, a bold jacket, a glamorous studio portrait.",
    "glasto": "This person headlining the Pyramid Stage at Glastonbury at sunset, flags waving in the crowd.",
    "rock": "This person as a rock star with an electric guitar, smoke and spotlights on a concert stage.",
    "disco": "This person on a 1970s disco dance floor under a mirror ball, flares and sparkle.",
    "westend": "This person starring in a West End musical, a big theatre stage, bright costumes and the chorus "
               "behind them.",
    "unplugged": "This person singing an acoustic session on a cosy stage with fairy lights and a guitarist.",
}

LABELS = {"wembley": "Wembley", "ronnie": "Ronnie Scott's", "popstar": "Pop star", "glasto": "Glastonbury",
          "rock": "Rock star", "disco": "Disco", "westend": "West End", "unplugged": "Unplugged"}

Backend = Callable[[Path, str], bytes]     # (the singer's photo, the prompt) -> the made picture (JPEG/PNG bytes)
backend: Optional[Backend] = None
_wake = threading.Event()
_thread: Optional[threading.Thread] = None


def set_backend(fn: Optional[Backend]) -> None:
    """The generator riffs are made with (None: they wait); photos kept before there was one get theirs; starts the
    worker."""
    global backend
    backend = fn
    if fn is not None:
        from . import singer_photos as sp
        with sp._lock:
            idx = sp._index()
            old = [r for r in idx.values() if "riffs" not in r]
            for r in old:
                r["riffs"] = plan()
            if old:
                sp._write(idx)
    start()


def plan() -> list[dict]:
    """A new photo's riffs: PER_SINGER scenes, queued."""
    return [{"scene": k, "state": "queued"} for k in random.sample(list(SCENES), PER_SINGER)]


def prompt(scene: str) -> str:
    return LIKENESS + SCENES[scene]


def start() -> None:
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_run, name="karaoke-singer-riffs", daemon=True)
        _thread.start()
    _wake.set()


def poke() -> None:
    """Something was queued."""
    _wake.set()


def next_job(idx: dict) -> Optional[tuple[str, int]]:
    """(name key, riff index) to make next: the fewest done first (round the singers), the earliest photo first."""
    best = None
    for key, row in idx.items():
        riffs = row.get("riffs") or []
        if any(r.get("state") == "making" for r in riffs):
            continue
        q = next((i for i, r in enumerate(riffs) if r.get("state") == "queued"), None)
        if q is None:
            continue
        rank = (sum(r.get("state") == "done" for r in riffs), row.get("at") or 0)
        if best is None or rank < best[0]:
            best = (rank, key, q)
    return (best[1], best[2]) if best else None


def _run() -> None:
    from . import singer_photos as sp
    while True:
        _wake.wait(60)
        _wake.clear()
        while backend is not None:
            with sp._lock:
                idx = sp._index()
                job = next_job(idx)
                if job is None:
                    break
                key, i = job
                row = idx[key]
                r = row["riffs"][i]
                r["state"] = "making"
                photo_file = row["file"]
                sp._write(idx)
            fn = backend
            ok, out = False, None
            t0 = time.time()
            try:
                data = fn(sp.folder() / photo_file, prompt(r["scene"])) if fn else None
                out = _save(sp, photo_file, r["scene"], data) if data else None
                ok = out is not None
            except Exception as e:                      # noqa: BLE001 - one riff failing never stops the rest
                error(f"karaoke singer riff: {key} {r['scene']}: {e}")
            with sp._lock:
                idx = sp._index()
                row = idx.get(key)
                if row and row["file"] == photo_file and i < len(row.get("riffs") or []):
                    row["riffs"][i] = ({"scene": r["scene"], "state": "done", **out} if ok
                                       else {"scene": r["scene"], "state": "failed"})
                    sp._write(idx)
                elif out:                               # the photo changed or went meanwhile: not wanted now
                    (sp.folder() / out["file"]).unlink(missing_ok=True)
            if ok:
                info(f"karaoke singer riff: {key} {r['scene']} in {time.time() - t0:.0f} s")


def _save(sp, photo_file: str, scene: str, data: bytes) -> Optional[dict]:
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((sp.SAVE_PX, sp.SAVE_PX))
    file = f"{photo_file[:-4]}-{scene}.jpg"
    im.save(sp.folder() / file, "JPEG", quality=88)
    return {"file": file, "w": im.width, "h": im.height}
