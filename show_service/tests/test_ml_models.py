"""The worker's model pieces (README S4.4): the numpy front ends and post-processing, label matching, facts loading
and degrading when models are missing; and, when ~/.venvs/sm-show-ml and the fetched models are present (skipped
otherwise), one real run of the worker on 10 s of a test-music track, printing every result and its cost (``-s``).

The real run uses a tmp models dir of symlinks, its own pipe, and the ML venv's python; it never writes the real
models dir and binds no port."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import time

import numpy as np
import pytest

from show_service import contract as C
from show_service.ml import models as M
from show_service.ml import protocol as P

FS = C.ML_FS
ML_PY = pathlib.Path(C.ML_PYTHON)
MUSIC = pathlib.Path.home() / ".cache" / "screen-machine" / "test-music"


def test_to_key_name():
    assert [M.to_key_name(k) for k in ("C", "C#", "Db", "Eb", "Bb", "Cb", "E#", "G", "", None, "H")] == \
        ["C", "C#", "C#", "D#", "A#", "B", "F", "G", None, None, None]


def test_level_dbfs():
    assert M.level_dbfs(np.zeros(100, np.float32)) == -150.0
    assert M.level_dbfs(np.ones(100, np.float32)) == pytest.approx(0.0, abs=1e-6)
    assert M.level_dbfs(np.full(100, 0.1, np.float32)) == pytest.approx(-20.0, abs=1e-4)


def test_match_groups_comma_rule_and_lacking():
    labels = ["Speech", "Male speech", "Music", "Hubbub", "Silence", "Choir"]
    groups = {"speech": ["Speech", "Male speech, man speaking", "Female speech, woman speaking"],
              "music": ["music"], "crowd": ["Hubbub, speech noise, speech babble"], "singing": ["Choir", "Choir"],
              "silence": ["Silence"]}
    idx, lacking = M.match_groups(labels, groups)
    assert idx["speech"] == [0, 1] and idx["music"] == [2] and idx["crowd"] == [3] and idx["singing"] == [5]
    assert idx["applause"] == [] and idx["conversation"] == []
    assert lacking == ["speech: Female speech, woman speaking"]


def test_beat_logmel_shape_and_a_tone():
    fs = 22050
    t = np.arange(fs * 3) / fs
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    spec = M.beat_logmel(x)
    assert spec.shape == (1 + len(x) // 441, 128) and spec.dtype == np.float32 and spec.min() >= 0
    band = int(np.argmax(spec[len(spec) // 2]))
    fb = M.mel_fbank(fs, 1024, 30.0, 11000.0, 128)
    centre_bin = int(np.argmax(fb[:, band]))
    assert abs(centre_bin * fs / 1024 - 1000) < 60


def test_minimal_peaks_matches_the_reference_rules():
    beat = np.full(300, -5.0, np.float32)
    down = np.full(300, -5.0, np.float32)
    for f in (10, 35, 60, 85, 110):
        beat[f] = 3.0
    beat[61] = 3.0                                   # an adjacent equal peak: merged to 60.5
    beat[200] = -0.5                                 # a peak under 0.5 probability: none
    down[12] = 2.0                                   # moved to the nearest beat (10)
    down[84] = 1.0                                   # -> 85
    b, d = M.minimal_peaks(beat, down, 50.0)
    assert list(np.round(b * 50, 3)) == [10, 35, 60.5, 85, 110]
    assert list(np.round(d * 50, 3)) == [10, 85]
    b, d = M.minimal_peaks(np.full(50, -1.0), np.full(50, -1.0))
    assert len(b) == 0 and len(d) == 0


def test_budget_terminate_and_check():
    clock = [0.0]
    b = M.Budget(50, clock=lambda: clock[0])
    b.check()
    clock[0] = 0.049
    b.check()
    clock[0] = 0.051
    with pytest.raises(M.Overrun):
        b.check()
    b2 = M.Budget(1e6)
    b2.terminate()
    with pytest.raises(M.Overrun):
        b2.check()


def write_index(d: pathlib.Path, entries: dict) -> None:
    (d / "index.json").write_text(json.dumps(entries))


def test_model_facts_labels_and_errors(tmp_path):
    (tmp_path / "head.onnx").write_bytes(b"x")
    (tmp_path / "labels.json").write_text(json.dumps({"id2label": {"1": "b", "0": "a", "10": "k"},
                                                       "classes": ["x", "y"]}))
    write_index(tmp_path, {
        "inline": {"file": "labels.json", "facts": {"kind": "binary", "labels": ["no", "yes"], "yes": "yes"}},
        "byid": {"file": "labels.json", "facts": {"kind": "styles", "labels": {"file": "labels.json",
                                                                               "key": "id2label"}}},
        "badkind": {"file": "labels.json", "facts": {"kind": "magic"}},
        "nofile": {"file": "gone.onnx", "facts": {"kind": "binary"}}})
    idx = M.read_index(tmp_path)
    m = M.Model("inline", tmp_path, idx, session=False)
    assert m.labels == ["no", "yes"] and m.index_of("yes") == 1
    with pytest.raises(M.ModelError):
        m.index_of("maybe")
    assert M.Model("byid", tmp_path, idx, session=False).labels == ["a", "b", "k"]
    with pytest.raises(M.ModelError, match="kind"):
        M.Model("badkind", tmp_path, idx, session=False)
    with pytest.raises(M.ModelError, match="missing"):
        M.Model("nofile", tmp_path, idx, session=False)
    with pytest.raises(M.ModelError, match="missing"):
        M.Model("absent", tmp_path, idx, session=False)


def test_load_degrades_without_models(tmp_path):
    from show_service.ml import worker as W
    b = M.load(W.DEFAULTS, tmp_path, log=lambda s: None)
    assert b.tasks["ced"] is None and b.tasks["tags"] is None and b.tasks["beats"] is None
    for name in ("ced-mini", "discogs-effnet", "beat-this-small0"):
        assert b.models[name] == "missing"
    fake = M.load(W.DEFAULTS, tmp_path, fake=True)
    assert all(v == "fake" for v in fake.models.values()) and all(fake.tasks.values())


# -- the real thing (skipped without the ML venv and the models) -------------------------------------------------------
def _have_real() -> bool:
    return ML_PY.exists() and (C.MODELS_DIR / "index.json").is_file() and shutil.which("ffmpeg") is not None and \
        (MUSIC / "index.json").is_file()


@pytest.mark.skipif(not _have_real(), reason="needs ~/.venvs/sm-show-ml, fetched models, ffmpeg and test music")
def test_real_worker_on_ten_seconds_of_music(tmp_path):
    from show_service.ml import supervisor as S
    models = tmp_path / "models"
    models.mkdir()
    for p in C.MODELS_DIR.iterdir():                  # a symlinked copy: tests never use the real dir itself
        (models / p.name).symlink_to(p)
    track = MUSIC / json.loads((MUSIC / "index.json").read_text())["house-galeria-disco-memorias"]["file"]
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "60", "-t", "10", "-i", str(track), "-f",
                          "f32le", "-ac", "1", "-ar", str(FS), "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").copy()
    import yaml
    ml = yaml.safe_load(open(C.REPO_ROOT / "config" / "room" / "show.yaml"))["ml"]
    ml.update({"python": str(ML_PY), "models_dir": str(models)})
    # 10 s of audio: short key and beats windows so every task runs once or more
    ml["tasks"]["key"]["window_s"] = 6
    ml["tasks"]["beats"].update({"every_s": 3, "window_s": 8, "interior_margin_s": 1})
    lines: list[dict] = []

    async def main():
        sup = S.WorkerSupervisor(ml, log_path=tmp_path / "ml-worker.log", on_line=lines.append)
        sup.start()
        end = time.monotonic() + 30
        while sup.state != "running" and time.monotonic() < end:
            await asyncio.sleep(0.05)
        assert sup.state == "running", sup.snapshot()
        t0 = time.monotonic()
        for i in range(0, len(x), 1600):              # real time, 100 ms blocks
            sup.feeder.write("test-audio", t0 + i / FS, x[i:i + 1600])
            await asyncio.sleep(max(0.0, t0 + (i + 1600) / FS - time.monotonic()))
        await asyncio.sleep(2.5)
        snap = sup.snapshot()
        await sup.stop()
        return snap

    snap = asyncio.run(main())
    hello = next(o for o in lines if o["type"] == "hello")
    print("\nhello models:", hello["models"], "\nhello tasks:", hello["tasks"], "versions:", hello["versions"])
    for o in lines:
        if o["type"] == "hello":
            continue
        shown = dict(o)
        if "emb" in shown:
            shown["emb"] = f"<{len(o['emb'])} b64 chars>"
        if "genre_parents" in shown:
            shown["genre_parents"] = dict(sorted(o["genre_parents"].items(), key=lambda kv: -kv[1])[:4])
        print(json.dumps(P.clean(shown, 3))[:700])
    print("feed:", snap["feed"], "bad_lines:", snap["bad_lines"])
    assert snap["bad_lines"] == 0 and all(P.check_line(o) is None for o in lines)
    assert hello["tasks"] == {"ced": True, "tags": True, "key": True, "beats": True}
    kinds = {o["type"] for o in lines}
    assert {"ced", "tags", "key", "beats"} <= kinds, kinds
    ced = [o for o in lines if o["type"] == "ced"]
    assert max(o["music_p"] for o in ced) > 0.5
    tags = [o for o in lines if o["type"] == "tags"]
    top = max(tags[-1]["genre_parents"], key=tags[-1]["genre_parents"].get)
    assert top in C.DISCOGS_PARENTS
    beats = [o for o in lines if o["type"] == "beats"]
    assert beats[-1]["bpm"] is not None and 60 < beats[-1]["bpm"] < 200


def test_beat_this_hears_a_quiet_mic_at_its_own_level():
    """26, 19 Oct: Beat This! has no gain stage, and PC22's mic gives music at about -48 dBFS (no beats found); each
    window is scaled to -20 dBFS RMS first, by at most +40 dB, and a louder one is left alone."""
    rng = np.random.default_rng(3)
    quiet = (rng.standard_normal(16000) * 10 ** (-48 / 20)).astype(np.float32)
    up = M.beats_level(quiet)
    assert up.dtype == np.float32 and M.level_dbfs(up) == pytest.approx(-20.0, abs=0.1)
    hush = (rng.standard_normal(16000) * 10 ** (-90 / 20)).astype(np.float32)
    assert M.level_dbfs(M.beats_level(hush)) == pytest.approx(-50.0, abs=0.1)          # +40 dB at most
    loud = (rng.standard_normal(16000) * 10 ** (-12 / 20)).astype(np.float32)
    assert M.beats_level(loud) is loud
