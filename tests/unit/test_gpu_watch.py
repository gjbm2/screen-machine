"""utils.gpu_watch: who used the GPU, from the card, Windows' split and the renderer's own measure."""
from utils import gpu_watch as G


def test_parse_smi_reads_a_row_and_its_throttle_reasons():
    d = G.parse_smi("54, 2820, 8151, 24, 27, 2760, 3090, 70, 45.91, 0x0000000000000024")
    assert d["util"] == 54 and d["enc"] == 24 and d["dec"] == 27 and d["max_mhz"] == 3090
    assert d["thr"] == ["power cap", "thermal"]
    assert G.parse_smi("[N/A], 1")  == {}


def test_render_share_is_span_times_rate():
    diag = {"sessions": [
        {"screen": "vj", "state": "live", "fps": 30, "window": {"render_ms": {"p50": 6.5}, "content_fps": 30.0}},
        {"screen": "tv", "state": "idle", "window": {"render_ms": {"p50": 9}}}]}
    r = G.render_share(diag)
    assert list(r) == ["vj"] and r["vj"]["pct"] == 19.5


def test_lanes_split_wsl_between_the_renderer_and_karaoke_prep():
    s = {"win": {"vmwp|3d": 70, "vmwp|videoencode": 25, "brave|3d": 30, "brave|videodecode": 10, "dwm|3d": 12,
                 "Code|3d": 4},
         "render": {"vj": {"pct": 20}}, "jobs": [{"l": "Removing vocals", "cpu": 300, "gpu": True}]}
    ln = G.lanes(s)
    assert ln == {"Renderer": 20, "Karaoke prep": 50, "Browser": 30, "Desktop": 12, "Other Windows": 4,
                  "Video encode": 25, "Video decode": 10}
    s["jobs"] = []
    assert "Other WSL" in G.lanes(s)


def test_lanes_without_windows_use_the_card():
    ln = G.lanes({"gpu": {"enc": 22, "dec": 3}, "render": {"vj": {"pct": 7}}})
    assert ln == {"Renderer": 7, "Video encode": 22, "Video decode": 3}


def test_summary_finds_a_hot_run_and_what_ran():
    def s(t, u, jobs=()):
        return {"t": t, "gpu": {"util": u}, "win": {"vmwp|3d": u}, "render": {}, "jobs": list(jobs)}
    sep = {"l": "Removing vocals", "cpu": 320, "gpu": True, "song": "Rehab"}
    ss = [s(0, 40), s(2, 99, [sep]), s(4, 100, [sep]), s(6, 98, [sep]), s(8, 30)]
    sm = G.summary(ss)
    assert sm["samples"] == 5 and sm["hot_s"] == 6
    assert len(sm["hot"]) == 1 and sm["hot"][0]["s"] == 6
    assert sm["hot"][0]["jobs"] == ["Removing vocals (Rehab)"]
    assert "Karaoke prep" in sm["hot"][0]["lanes"]
    assert "Runs at 95%+" in G.report(ss)


def test_labels():
    assert G.label("/v/bin/python /r/tools/karaoke/separate.py --in x") == ("Removing vocals", True)
    assert G.label("ffmpeg -i pipe:0 -c:v hevc_nvenc -gpu 0") == ("Stream encoder", True)
    assert G.label("/v/python -m show_service.ml.worker --models m")[0] == "Show models"
    assert G.label("bash") is None
