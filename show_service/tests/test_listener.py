"""listener.py (README "Stage 3 > S3.12"): gating, cadence and backoff on a fake clock, beliefs, the music.insight
shape, Describe it (LLM and keywords), audio normalising and encoding. A fake utils.llm stands in: no network, no
OpenAI, no real state dir (tmp paths)."""

from __future__ import annotations

import asyncio
import json
import math

import numpy as np
import pytest

from show_service import contract as c
from show_service import listener as L
from show_service.ring import Rings
from utils.llm.core import LLMResult

VOCAB = "moods: [calm, mellow, warm, bright, euphoric, dreamy]\ngenre: [soul, jazz, house]\n"


def verdict(genre="soul", conf=0.8, moods=("warm", "mellow", "sparkly"), content="music", track_change=False,
            section="chorus", traj="building", eta=20.0):
    return {"content": content, "genre": {"label": genre, "detail": "neo soul", "confidence": conf},
            "instrumentation": ["Piano", "bass", "piano"], "vocals": "female",
            "mood": {"words": list(moods), "confidence": 0.7},
            "section": {"label": section, "confidence": 0.6},
            "trajectory": {"state": traj, "eta_to_change_s": eta, "confidence": 0.5},
            "track_change": track_change, "same_track_as_previous": True,
            "evidence": " ".join(["word"] * 40)}


def ok(data, model="gpt-x"):
    return LLMResult(True, data, None, "listen", "openai", model, 1234.0, {"prompt_tokens": 10}, 0.01, [])


def failed(error="http 500: boom"):
    return LLMResult(False, None, error, "listen", None, None, 50.0, {}, 0.0, [{"model": "gpt-x", "error": error}])


class FakeLLM:
    def __init__(self) -> None:
        self.usable = {"listen": ["gpt-x"], "describe": ["gpt-y"]}
        self.calls: list = []
        self.results: list = []
        self.budget = False
        self.delay = 0.0

    def status(self):
        return {"tasks": {t: {"models": list(u) or ["gpt-x"], "usable": list(u)} for t, u in self.usable.items()},
                "budgets": {"listen": 15.0}, "spend_today_usd": {"listen": 0.25},
                "calls_today": {"listen": len(self.calls)}}

    def over_budget(self, task):
        return self.budget

    def task_settings(self, task):
        return {"audio": {"format": "mp3", "sample_rate": 24000, "bitrate": "64k"}}

    async def acall_json(self, task, **kw):
        self.calls.append((task, kw))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.results:
            r = self.results.pop(0)
            return r(task, kw) if callable(r) else r
        return ok(verdict())


SENDERS = {"test-audio": {"hosts": ["localhost"], "port": 40001, "streams": ["test-audio"], "on_demand": True,
                          "room_sound": False}}


class Rig:
    """A listener on a fake ctx. By default it may listen: the switch on, a mode that listens, and a test-audio
    stream whose sender says room_sound: false (so only the music check is skipped)."""

    def __init__(self, tmp_path, stream="test-audio", settings=None, on=True,
                 target=({"vibe": "home", "listening": True},)):
        self.now = 1000.0
        self.rings = Rings(seconds=40.0)
        self.pub: list = []
        self.persisted = 0
        self.health = {"active_audio": stream}
        self.target = target[0] if isinstance(target, tuple) else target
        self.vibe = None
        self.feats = {"lufs_s": -24.0, "energy": 0.4, "gate": 0.6, "brightness": 0.5, "onset": 0.2}
        self.clock = {"gate": "none", "bpm": 0.0, "state": "searching", "conf": 0.0}
        self.harmony = {"key": None, "mode": None, "conf": 0.0}
        self.notified: list = []
        self.encoded: list = []
        ctx = c.BrainContext(
            publish=self.pub.append,
            features=lambda: dict(self.feats, t_ms=self.now * 1000.0, stream=self.health.get("active_audio")),
            clock=lambda: self.clock, harmony=lambda: self.harmony, health=lambda: self.health,
            rings=self.rings, target=lambda: self.target, vibe=lambda: self.vibe,
            persist=self._persist, notify=lambda k, p: self.notified.append(k))
        self.llm = FakeLLM()
        vocab = tmp_path / "vocab.yaml"
        vocab.write_text(VOCAB)
        self.log_path = tmp_path / "listener.jsonl"
        self.lis = L.Listener(ctx, llm=self.llm, mono=lambda: self.now, wall=lambda: 1.79e9 + self.now,
                              log_path=self.log_path, vocab_path=vocab, encoder=self._encode)
        self.lis.configure(dict({"senders": SENDERS}, **(settings or {
            "listener": {"cadence_s": 20, "clip_s": 24, "min_gap_s": 8, "relaxed_cadence_s": 45, "agree_reads": 3,
                         "music_gate": "classifier", "describe_timeout_s": 0.5},
            "situations": {"music": {"dsp_min_gate": 0.3}}})))
        self.lis.on = bool(on)                            # as restored from state.json (set_on would persist)

    def _persist(self):
        self.persisted += 1

    async def _encode(self, pcm, fs, **kw):
        self.encoded.append((pcm, fs, kw))
        return b"MP3DATA", "mp3"

    def fill(self, stream=None, seconds=30.0, amp=0.05):
        stream = stream or self.health["active_audio"]
        rng = np.random.default_rng(0)
        n = int(seconds * 16000)
        t0 = self.now - seconds
        for i in range(0, n, 1600):
            x = (amp * rng.standard_normal(1600)).astype(np.float32)
            self.rings.write(stream, i, t0 + i / 16000.0, x, 16000, c.AUDIO_SEGMENT if i == 0 else 0)

    async def tick(self, dt=1.0):
        self.now += dt
        self.lis.tick(self.now)
        for _ in range(3):
            t = self.lis._task
            if t is not None:
                await asyncio.wait([t], timeout=5)
            await asyncio.sleep(0)

    async def ticks(self, n, dt=1.0):
        for _ in range(n):
            await self.tick(dt)


def run(coro):
    return asyncio.run(coro)


# -- gating -------------------------------------------------------------------------------------------------------------
def test_switch_off_sends_nothing_from_the_room(tmp_path):
    rig = Rig(tmp_path, stream="room-mic", on=False)
    rig.fill()

    async def go():
        await rig.ticks(30)
    run(go())
    v = rig.lis.view()
    assert v["on"] is False and v["effective"] is False and v["why"] == L.WHY_OFF
    assert rig.llm.calls == [] and rig.pub == []
    assert rig.lis.persist() == {"on": False, "set_at": None}


@pytest.mark.parametrize("stream", ["test-audio", "phone-test", "test-anything", "room-mic"])
def test_every_stream_needs_the_switch_and_a_mode_that_listens(tmp_path, stream):
    """Privacy is never decided by a stream's name: test streams, phone-test and test-* need AI listening on and a
    mode that listens, exactly as the room mic does (§7.5: the switch stops every upload)."""
    for on, target in ((False, {"vibe": "home", "listening": True}), (True, None),
                       (True, {"vibe": "home", "listening": False})):
        rig = Rig(tmp_path, stream=stream, on=on, target=target)
        rig.fill()

        async def go():
            await rig.ticks(25)
        run(go())
        assert rig.llm.calls == [] and rig.pub == [], (stream, on, target)
        assert rig.lis.view()["why"] == (L.WHY_OFF if not on else L.WHY_MODE)
        assert rig.rings.armed is False                   # nothing recorded for nothing


@pytest.mark.parametrize("stream", ["phone-test", "test-anything", "room-mic"])
def test_only_room_sound_false_skips_the_music_check(tmp_path, stream):
    """A name like test-* or phone-test carries room sound unless its show.yaml sender says room_sound: false."""
    rig = Rig(tmp_path, stream=stream)
    rig.fill()

    async def go():
        await rig.ticks(5)
    run(go())
    assert rig.lis.view()["why"] == L.WHY_P1 and rig.llm.calls == []
    assert rig.lis.room_sound(stream) is True and rig.lis.room_sound("test-audio") is False


def test_a_room_sound_false_stream_is_listened_to_with_the_switch_on(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()
    rig.llm.results = [ok(verdict(genre="Rock", moods=("warm", "sparkly", "MELLOW")))]

    async def go():
        await rig.tick()
    run(go())
    assert rig.rings.armed is True
    assert len(rig.llm.calls) == 1
    task, kw = rig.llm.calls[0]
    assert task == "listen" and kw["audio"] == b"MP3DATA" and kw["audio_format"] == "mp3"
    assert kw["tool"] is L.LISTEN_TOOL and "soul, jazz, house" in kw["system"]
    assert "Timeline" in kw["user"][0] and len(kw["user"]) == 1          # no previous verdict yet
    pcm, fs, enc = rig.encoded[0]
    assert fs == 16000 and pcm.dtype == np.int16 and len(pcm) == 24 * 16000
    assert enc == {"fmt": "mp3", "sample_rate": 24000, "bitrate": "64k"}
    assert [m["type"] for m in rig.pub] == [c.MUSIC_INSIGHT]
    ins = rig.pub[0]["data"]
    assert set(ins) == set(c.InsightData.__annotations__)
    assert ins["genre"] == {"label": "unknown", "detail": "neo soul", "confidence": 0.8}   # rock isn't in vocab
    assert ins["mood"]["words"] == ["warm", "mellow"]                                      # sparkly dropped
    assert ins["instrumentation"] == ["piano", "bass"]
    assert len(ins["evidence"].split()) == 25 and ins["stream"] == "test-audio"
    assert ins["window"]["t1_ms"] - ins["window"]["t0_ms"] == pytest.approx(24000.0, abs=1.0)
    assert ins["segment_id"] is None and ins["section"]["segment_id"] is None
    assert rig.notified == ["card"]
    v = rig.lis.view()
    assert v["last"] == ins and v["model"] == "gpt-x" and v["budget_usd"] == 15.0
    lines = [json.loads(x) for x in rig.log_path.read_text().splitlines()]
    assert lines[-2]["kind"] == "verdict" and lines[-2]["insight"]["genre"]["detail"] == "neo soul"
    assert lines[-1]["kind"] == "genre_detail" and lines[-1]["detail"] == "neo soul"   # §7.3: grows the vocabulary
    assert rig.lis.log(2)[0]["kind"] == "verdict"


def test_a_bad_room_sound_flag_counts_as_room_sound(tmp_path):
    s = {"senders": {"x": {"streams": ["test-audio"], "room_sound": "no"}}}
    rig = Rig(tmp_path, settings=s)
    assert rig.lis.room_sound("test-audio") is True and any("room_sound" in p for p in rig.lis.problems)


def test_the_ring_records_only_while_a_clip_could_go(tmp_path):
    rig = Rig(tmp_path, stream="test-audio", on=False)
    rig.fill()                                           # recorded before the first tick (armed by default)
    rig.lis.tick(rig.now)
    assert rig.rings.armed is False
    rig.fill(seconds=5.0)                                # skipped: not armed
    rig.lis.set_on(True)
    assert rig.rings.armed is True
    rig.fill(seconds=3.0)                                # recording again: what was held before is forgotten
    assert rig.rings.info()["test-audio"]["held_s"] == pytest.approx(3.0, abs=0.1)


def test_no_classifier_means_no_room_mic_clip(tmp_path):
    rig = Rig(tmp_path, stream="room-mic", on=False)
    rig.fill()
    rig.lis.set_on(True)
    assert rig.persisted == 1 and rig.lis.persist()["on"] is True

    async def go():
        await rig.ticks(5)
    run(go())
    assert rig.lis.view()["why"] == L.WHY_P1 and rig.llm.calls == []


def test_dsp_music_gate(tmp_path):
    s = {"listener": {"music_gate": "dsp"}, "situations": {"music": {"dsp_min_gate": 0.3}}}
    rig = Rig(tmp_path, stream="room-mic", settings=s)
    rig.fill()

    async def go():
        await rig.ticks(3)
        assert rig.lis.view()["why"] == L.WHY_NO_MUSIC and rig.llm.calls == []
        rig.clock = {"gate": "beat", "bpm": 120.0, "state": "locked", "conf": 0.8}
        await rig.tick()
        assert rig.lis.view()["effective"] is True and len(rig.llm.calls) == 1
        assert "120.0 bpm" in rig.llm.calls[0][1]["user"][0]
    run(go())


@pytest.mark.parametrize("case,why", [("mode_off", L.WHY_MODE), ("no_target_live", L.WHY_MODE),
                                      ("vibe_off", L.WHY_VIBE), ("quiet", L.WHY_QUIET),
                                      ("no_sound", L.WHY_NO_SOUND), ("no_model", L.WHY_NO_MODEL),
                                      ("too_little_audio", L.WHY_NO_SOUND)])
def test_why(tmp_path, case, why):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()
    if case == "mode_off":
        rig.target = {"vibe": "home", "listening": False}
    elif case == "no_target_live":
        rig.target = None
        rig.health["active_audio"] = "room-mic"
    elif case == "vibe_off":
        rig.vibe = {"listener": False}
    elif case == "quiet":
        rig.feats["gate"] = 0.02
    elif case == "no_sound":
        rig.lis.ctx.features = lambda: None
    elif case == "no_model":
        rig.llm.usable["listen"] = []
    elif case == "too_little_audio":
        rig.rings = Rings()
        rig.lis.ctx.rings = rig.rings
        rig.fill(seconds=3.0)

    async def go():
        await rig.ticks(12)
    run(go())
    v = rig.lis.view()
    assert v["why"] == why and v["effective"] is False and v["next_in_s"] is None
    assert rig.llm.calls == []


# -- cadence ------------------------------------------------------------------------------------------------------------
def test_cadence_min_gap_and_one_in_flight(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()

    async def go():
        await rig.tick()
        assert len(rig.llm.calls) == 1
        await rig.ticks(19)
        assert len(rig.llm.calls) == 1 and rig.lis.view()["next_in_s"] == 1.0
        await rig.tick()
        assert len(rig.llm.calls) == 2
        # a slow call: one in flight at most, and min_gap after it ends
        rig.llm.delay = 0.2
        rig.lis.cfg["cadence_s"] = 1.0
        rig.now += 30
        rig.lis.tick(rig.now)
        assert rig.lis._task is not None
        rig.lis.tick(rig.now + 1)
        await asyncio.wait([rig.lis._task], timeout=5)
        await asyncio.sleep(0)
        assert len(rig.llm.calls) == 3 and rig.lis._task is None
        end = rig.lis._last_end
        rig.llm.delay = 0.0
        while len(rig.llm.calls) == 3:
            await rig.tick()
        assert rig.now - end >= 8.0
    run(go())


def test_relaxed_after_agreeing_confident_reads_and_back(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()

    async def go():
        times = []
        for _ in range(200):
            n = len(rig.llm.calls)
            if n == 3:
                rig.llm.results = [ok(verdict(genre="jazz"))]
            await rig.tick()
            if len(rig.llm.calls) > n:
                times.append(rig.now)
            if len(times) == 5:
                break
        gaps = [round(b - a) for a, b in zip(times, times[1:])]
        assert gaps == [20, 20, 45, 20], gaps          # 3 agreeing soul reads -> 45 s; jazz resets it
    run(go())


def test_budget_slows_and_errors_back_off(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()

    async def go():
        rig.llm.budget = True
        await rig.tick()
        assert rig.lis.view()["cadence_s"] == 60.0
        rig.llm.budget = False
        rig.llm.results = [failed(), failed()]
        await rig.ticks(60)
        assert rig.lis.errors >= 2
        assert rig.lis.view()["cadence_s"] == 80.0          # 20 x 2^2 after two failures in a row
        assert [e["kind"] for e in rig.lis.log(10)].count("error") == 2
        await rig.ticks(80)
        assert rig.lis._errors_row == 0 and rig.lis.view()["cadence_s"] == 20.0
    run(go())


def test_switching_off_cancels_a_room_call_in_flight(tmp_path):
    rig = Rig(tmp_path, stream="room-mic", settings={"listener": {"music_gate": "dsp"}})
    rig.clock["gate"] = "beat"
    rig.fill()
    rig.llm.delay = 5.0

    async def go():
        rig.now += 1
        rig.lis.tick(rig.now)
        await asyncio.sleep(0.05)
        task = rig.lis._task
        assert task is not None
        rig.lis.set_on(False)
        await asyncio.wait([task], timeout=2)
        await asyncio.sleep(0)
        assert task.cancelled() and rig.lis._task is None and rig.pub == []
        assert rig.lis.view()["why"] == L.WHY_OFF
    run(go())


def test_switching_off_cancels_a_test_stream_call_too(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()
    rig.llm.delay = 5.0

    async def go():
        rig.now += 1
        rig.lis.tick(rig.now)
        await asyncio.sleep(0.05)
        task = rig.lis._task
        assert task is not None
        rig.lis.set_on(False)
        await asyncio.wait([task], timeout=2)
        await asyncio.sleep(0)
        assert task.cancelled() and rig.pub == []
    run(go())


def test_gate_is_checked_again_right_before_the_upload(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()

    async def enc(pcm, fs, **kw):
        rig.target = {"vibe": "home", "listening": False}  # the mode changed while the clip was being encoded
        return b"MP3", "mp3"
    rig.lis._encode = enc
    run(rig.tick())
    assert rig.llm.calls == [] and rig.lis.log(1)[0] == {"at": rig.lis.log(1)[0]["at"], "kind": "skipped",
                                                          "stream": "test-audio", "why": L.WHY_MODE}


def test_persist_restore(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.restore({})
    assert rig.lis.on is False
    rig.lis.restore({"on": True, "set_at": 1.79e9})
    assert rig.lis.persist() == {"on": True, "set_at": 1.79e9}
    assert rig.lis.provides()["beliefs"]() == rig.lis.beliefs()


def test_bad_settings_fall_back_to_design_defaults(tmp_path):
    rig = Rig(tmp_path, settings={"listener": {"cadence_s": -1, "music_gate": "maybe", "agree_reads": 0}})
    assert rig.lis.cfg["quiet_gate"] == 0.1 and rig.lis.cfg["min_clip_s"] == 10.0          # design defaults
    assert rig.lis.cfg["cadence_s"] == 20.0 and rig.lis.cfg["music_gate"] == "classifier"
    assert rig.lis.cfg["agree_reads"] == 3 and len(rig.lis.problems) == 3


# -- beliefs ------------------------------------------------------------------------------------------------------------
def _ins(rig, **kw):
    return L.to_insight(verdict(**kw), vocab=rig.lis.vocab, stream="s", model="m", latency_ms=1.0,
                        window=(0.0, 0.0), t_ms=0.0)


def test_genre_changes_on_two_reads_in_a_row_or_a_track_change(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    b.add(_ins(rig, genre="soul"), 0.0)
    assert b.view(0.0)["genre"]["word"] == "soul"
    b.add(_ins(rig, genre="jazz"), 20.0)
    b.add(_ins(rig, genre="jazz"), 40.0)                   # jazz leads by > 0.15: one win
    assert b.view(40.0)["genre"]["word"] == "soul"
    b.add(_ins(rig, genre="jazz"), 60.0)                   # the second read in a row
    assert b.view(60.0)["genre"]["word"] == "jazz"
    b.add(_ins(rig, genre="house", track_change=True), 70.0)
    v = b.view(70.0)
    assert v["genre"]["word"] == "house" and v["genre"]["conf"] == 0.5     # one read: half confident


def test_confidence_from_agreement_decay_and_expiry(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    ins = _ins(rig, genre="soul")
    ins["window"] = {"t0_ms": 0.0, "t1_ms": 5000.0}
    b.add(ins, 20.0)
    b.add(ins, 20.0)
    v = b.view(20.0)
    assert v["state"] == "fresh" and v["genre"]["conf"] == 1.0
    assert [m["word"] for m in v["moods"]] == ["warm", "mellow"] and v["moods"][0]["conf"] == 1.0
    assert v["instruments"] == ["piano", "bass"] and v["vocals"] == "female"
    assert v["trajectory"]["eta_s"] == 5.0                 # 20 s from the clip's end (5 s), read at 20 s
    assert v["section"] == {"label": "chorus", "conf": 0.6}
    v = b.view(65.0)                                        # 45 s on: mood votes halved, section lapsed
    assert v["state"] == "holding" and v["moods"][0]["conf"] == pytest.approx(0.5, abs=0.01)
    assert v["section"] is None and v["trajectory"]["eta_s"] == 0.0
    v = b.view(110.0)                                       # 90 s: fading, half way
    assert v["state"] == "fading" and v["genre"]["conf"] < 0.5
    v = b.view(141.0)
    assert v["state"] == "none" and v["genre"] is None and v["moods"] == []
    assert b.view(150.0)["age_s"] is None


def test_speech_adds_no_votes(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    b.add(_ins(rig, content="speech"), 0.0)
    v = b.view(0.0)
    assert v["genre"] is None and v["moods"] == [] and v["content"] == "speech" and v["vocals"] is None


# -- Describe it ----------------------------------------------------------------------------------------------------------
CTX = {"vibe": "home", "persona": "A warm living room.", "current_mode": "gallery",
       "palettes": [{"name": "ember", "label": "Ember"}, {"name": "deep-sea", "label": "Deep Sea"}],
       "moods": [], "modes": [{"name": "gallery", "label": "Gallery"}, {"name": "party", "label": "Party"}]}


def test_keyword_fallback():
    k = L.keyword_intent("calmer and mellow please, ember colours", CTX, ["mellow", "warm"])
    assert k["steps"] == -2 and k["palette"] == "ember" and k["moods"] == ["mellow"] and not k["keep"]
    assert k["reply"].startswith("Okay: calmer ×2, palette Ember, moods mellow")
    k = L.keyword_intent("let's PARTY, loud loud loud loud", CTX, [])
    assert k["steps"] == 3 and k["switch_mode"] == "party" and k["reply"].endswith("Switch to Party?")
    k = L.keyword_intent("keep it, colours from the painting", CTX, [])
    assert k["keep"] and k["palette"] == "art" and k["steps"] == 0
    assert L.keyword_intent("deep sea please", CTX, [])["palette"] == "deep-sea"
    assert L.keyword_intent("gallery", CTX, [])["switch_mode"] is None        # already the mode
    k = L.keyword_intent("xyzzy", CTX, [])
    assert k == {"steps": 0, "palette": None, "moods": None, "keep": False, "switch_mode": None,
                 "reply": L.NOT_CAUGHT}
    k = L.keyword_intent("party", CTX, [])                 # a mode's name offers the switch and never steps
    assert k["steps"] == 0 and k["switch_mode"] == "party"
    ctx = dict(CTX, modes=CTX["modes"] + [{"name": "loud", "label": "Loud"}])
    assert L.keyword_intent("loud", ctx, [])["steps"] == 0                   # a step word that names a mode


def test_step_words_name_no_mode_or_vibe():
    import yaml
    vibes = set((yaml.safe_load(c.VIBES_FILE.read_text()).get("vibes") or {}))
    modes = set((json.loads((c.CONFIG_ROOM_DIR / "modes.json").read_text()).get("modes") or {}))
    assert not (L.CALMER_WORDS | L.WILDER_WORDS) & (vibes | modes)


def test_describe_via_llm_is_kept_to_what_the_card_can_set(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    rig.llm.results = [LLMResult(True, {"steps": 7, "palette": "Ember", "moods": ["dreamy", "smoky"], "keep": True,
                                        "switch_mode": "gallery", "reply": "Smoky and warm."},
                                 None, "describe", "openai", "gpt-y", 300.0, {}, 0.001, [])]
    intent, via = run(rig.lis.describe("smoky jazz club", CTX))
    assert via == "llm"
    assert intent == {"steps": 3, "palette": "ember", "moods": ["dreamy"], "keep": True, "switch_mode": None,
                      "reply": "Smoky and warm."}
    task, kw = rig.llm.calls[0]
    assert task == "describe" and kw["tool"] is L.DESCRIBE_TOOL and '"smoky jazz club"' in kw["user"]
    assert "ember (Ember)" in kw["user"] and "party (Party)" in kw["user"]


@pytest.mark.parametrize("how", ["no_model", "fails", "slow"])
def test_describe_falls_back_to_keywords(tmp_path, how):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    if how == "no_model":
        rig.llm.usable["describe"] = []
    elif how == "fails":
        rig.llm.results = [failed()]
    else:
        rig.llm.delay = 3.0

    async def go():
        t = asyncio.get_running_loop().time()
        out = await rig.lis.describe("calmer, warm", CTX)
        return out, asyncio.get_running_loop().time() - t
    (intent, via), took = run(go())
    assert via == "keywords" and intent["steps"] == -1 and intent["moods"] == ["warm"]
    assert took < 1.5
    assert rig.lis.log(1)[0]["via"] == "keywords"


# -- audio -----------------------------------------------------------------------------------------------------------------
def test_normalise_to_minus_16_lufs():
    fs = 16000
    t = np.arange(10 * fs) / fs
    x = (0.02 * np.sin(2 * np.pi * 997 * t) * 32767).astype(np.int16)
    y, lufs, gain = L.normalise(x, fs)
    assert lufs is not None and -45 < lufs < -30 and gain > 0
    _, lufs2, gain2 = L.normalise(y, fs)
    assert lufs2 == pytest.approx(-16.0, abs=0.5) and abs(gain2) < 0.5
    z, lufs3, gain3 = L.normalise(np.zeros(fs, np.int16), fs)
    assert lufs3 is None and gain3 == 0.0 and not z.any()
    _, _, g = L.normalise((2e-3 * np.sin(2 * np.pi * 997 * t) * 32767).astype(np.int16), fs)
    assert g == L.GAIN_CLAMP_DB


def test_encode_mp3_and_wav():
    fs = 16000
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(2 * fs) / fs) * 32767).astype(np.int16)

    async def go():
        return await L.encode(x, fs, fmt="mp3", sample_rate=24000, bitrate="64k")
    data, fmt = run(go())
    if fmt == "mp3":
        assert data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
        assert 10_000 < len(data) < 25_000                  # 2 s at 64 kbps
    else:                                                   # no ffmpeg on this box: the WAV fallback
        assert data[:4] == b"RIFF"
    w = L.wav_bytes(x, fs, 24000)
    assert w[:4] == b"RIFF" and int.from_bytes(w[24:28], "little") == 24000
    assert abs(len(w) - 44 - 2 * 2 * 24000) < 100
    data, fmt = run(L.encode(x, fs, fmt="wav"))
    assert fmt == "wav" and data[:4] == b"RIFF"


def test_attach_builds_the_component():
    lis = L.attach(c.BrainContext(publish=lambda m: None))
    assert lis.name == "listener" and lis.tick_hz == 1.0
    assert set(lis.view()) == set(c.ListenerView.__annotations__)
    assert lis.beliefs()["state"] == "none" and not math.isnan(lis.interval())


# -- Stage 4: the CED gate, section calls, segments (README S4.8) -----------------------------------------------------
def ced(t, music=0.8, speech=0.1, stream="room-mic"):
    return {"type": "ced", "t": t, "window_s": 2.0, "stream": stream, "seg": 1, "music_p": music, "speech_p": speech,
            "conversation_p": 0.0, "singing_p": 0.0, "crowd_p": 0.0, "applause_p": 0.0, "silence_p": 0.0,
            "cost_ms": 20.0}


def ml_view(rows, stream="room-mic"):
    return {"state": "running", "why": None, "stream": stream, "ced": rows[-1] if rows else None,
            "ced_recent": rows, "tags": None, "emb_recent": [], "key": None, "beats": None, "vote": None,
            "ages": {}, "hello": None}


def stage4_rig(tmp_path, stream="room-mic") -> Rig:
    rig = Rig(tmp_path, stream=stream)
    rig.ml = None
    rig.mc = None
    rig.lis.ctx.ml = lambda: rig.ml
    rig.lis.ctx.music_context = lambda: rig.mc
    return rig


def readings(rig, spans):
    """CED readings one a second, ending now: spans = [(seconds, music, speech), ...] oldest first."""
    total = sum(n for n, _, _ in spans)
    rows, t = [], rig.now - total + 1
    for n, m, sp in spans:
        for _ in range(n):
            rows.append(ced(t, m, sp))
            t += 1
    return rows


def test_ced_gate_reasons(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill()

    async def go():
        await rig.tick()
        assert rig.lis.view()["why"] == L.WHY_NO_CED                  # no worker
        rig.ml = ml_view([ced(rig.now - 10)])                         # stale
        await rig.tick()
        assert rig.lis.view()["why"] == L.WHY_NO_CED
        rig.ml = ml_view(readings(rig, [(20, 0.8, 0.1), (1, 0.8, 0.4)]))
        await rig.tick(0.0)
        assert rig.lis.view()["why"] == L.WHY_TALKING                 # the latest reading: people are talking
        rig.ml = ml_view(readings(rig, [(20, 0.8, 0.5), (6, 0.9, 0.1)]))
        await rig.tick(0.0)
        assert rig.lis.view()["why"] == L.WHY_SHORT                   # 6 s of music without talking: too short
        assert rig.lis.snapshot()["ced"]["run"] is not None
        assert rig.llm.calls == []
    run(go())


def test_the_clip_is_the_longest_music_run_talking_in_the_middle(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    rig.ml = ml_view(readings(rig, [(12, 0.8, 0.1), (3, 0.7, 0.6), (9, 0.9, 0.05)]))
    t_first = rig.now - 24 + 1

    async def go():
        await rig.tick(0.0)
    run(go())
    assert len(rig.llm.calls) == 1
    pcm, fs, _ = rig.encoded[0]
    # the 12 s run before the talking, not the 9 s; its last second is also in the first talking reading's window,
    # so it ends a second early (11 s): no speech rides along at the clip's end
    assert len(pcm) == pytest.approx(11 * 16000, abs=2)
    ins = rig.pub[0]["data"]
    assert ins["window"]["t0_ms"] == pytest.approx((t_first - 1.0) * 1000.0, abs=1.0)
    assert ins["window"]["t1_ms"] == pytest.approx((t_first + 10) * 1000.0, abs=1.0)


def test_a_tie_goes_to_the_latest_run(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    rig.ml = ml_view(readings(rig, [(11, 0.8, 0.1), (2, 0.1, 0.6), (11, 0.8, 0.1)]))
    run(rig.tick(0.0))
    ins = rig.pub[0]["data"]
    # both runs lose their last second (the next reading failed, or hasn't come yet): a tie, the latest wins
    assert ins["window"]["t1_ms"] == pytest.approx((rig.now - 1.0) * 1000.0, abs=1.0)


def test_babble_and_conversation_under_music_count_as_talking(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    rows = readings(rig, [(20, 0.8, 0.1)])
    for r in rows:
        r["conversation_p"] = 0.6                                     # guests talking over the music
    rig.ml = ml_view(rows)
    run(rig.tick(0.0))
    assert rig.lis.view()["why"] == L.WHY_TALKING and rig.llm.calls == []
    for r in rows[:-1]:                                               # only the latest is clear: still no clip
        r["conversation_p"] = 0.0
    rows[-1]["conversation_p"] = 0.0
    for r in rows[5:9]:
        r["crowd_p"] = 0.5                                            # chatter in the middle splits the run
    rig.ml = ml_view(rows)
    run(rig.tick(0.0))
    assert len(rig.llm.calls) == 1
    pcm, fs, _ = rig.encoded[0]
    assert len(pcm) == pytest.approx(10 * 16000, abs=2)               # 11 readings after the chatter, less the last
    rig2 = Rig(tmp_path, settings={"listener": {"ced": {"speech_words": ["speech"]}}})   # config, not code
    assert rig2.lis.speech_words == ("speech",) and rig2.lis.problems == []


def test_a_run_ending_before_the_latest_reading_keeps_its_last_second(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    rig.ml = ml_view(readings(rig, [(20, 0.8, 0.1)]))
    run_, why = rig.lis._ced_run("room-mic", rig.now, t_end=rig.now - 3.0)   # a section call's clip end
    assert why is None and run_[1] == pytest.approx(rig.now - 3.0)         # the next reading passed: vouched for


def test_a_stretched_ced_cadence_is_still_fresh(tmp_path):
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    rows = [ced(rig.now - 5.0 - 3.0 * k) for k in range(8)][::-1]         # every 3 s, the newest 5 s old
    rig.ml = ml_view(rows)
    run(rig.tick(0.0))
    assert rig.lis.view()["why"] == L.WHY_NO_CED                       # stale at results_stale_s
    rig.ml = {**ml_view(rows), "stale_s": {"ced": 9.0, "tags": 9.0, "key": 9.0, "beats": 30.0}}
    run(rig.tick(0.0))
    assert rig.lis.view()["why"] != L.WHY_NO_CED                       # the worker says CED comes every 6 s now


def fill_until(rig, t_end, seconds=38.0):
    """Audio in the ring for the ``seconds`` that end at ``t_end`` (it may run ahead of the fake clock)."""
    now = rig.now
    rig.now = t_end
    rig.fill(seconds=seconds)
    rig.now = now


def test_section_call_at_the_boundary_plus_8_s_with_min_gap_and_the_segment_map(tmp_path):
    rig = stage4_rig(tmp_path, stream="test-audio")                    # room_sound false: no CED gate here
    fill_until(rig, rig.now + 14.0)
    rig.mc = {"t_ms": rig.now * 1000.0, "section": {"id": 2, "letter": "A", "started_ms": (rig.now - 20) * 1000.0}}

    async def go():
        await rig.tick()                                               # the regular call (t = 1001)
        assert len(rig.llm.calls) == 1
        first_end = rig.lis._last_end
        rig.mc["t_ms"] = rig.now * 1000.0
        boundary = rig.now + 3.0
        rig.lis.on_music_event({"kind": "section_change", "at_ms": (boundary + 1.5) * 1000.0, "t_ms": rig.now * 1e3,
                                "seq": 1, "section_id": 3, "letter": None, "predicted": False,
                                "boundary_ms": boundary * 1000.0})
        rig.lis.on_music_event({"kind": "section_change", "at_ms": (boundary + 60) * 1000.0, "t_ms": rig.now * 1e3,
                                "seq": 2, "section_id": 9, "letter": "A", "predicted": True,
                                "boundary_ms": (boundary + 60) * 1000.0})   # a predicted one: no call
        assert rig.lis._section_due == pytest.approx(boundary + 8.0)
        rig.lis._sections.pop()                                        # (forget the predicted one's history entry)
        while rig.now < boundary + 8.0 - 1.0:
            await rig.tick()
        assert len(rig.llm.calls) == 1                                 # not before it is due
        await rig.tick()
        assert len(rig.llm.calls) == 2 and rig.now - first_end >= 8.0
        user = rig.llm.calls[1][1]["user"]
        seg = next(u for u in user if u.startswith("Segment map"))
        assert "#2 A 0-" in seg and "#3 " in seg
        ins = rig.pub[-1]["data"]
        assert ins["window"]["t1_ms"] == pytest.approx((boundary + 8.0) * 1000.0, abs=1.0)
        assert ins["segment_id"] == 3 and ins["section"]["segment_id"] == 3
        assert rig.lis.snapshot()["sections"]["section_calls"] == 1
        # min_gap: a boundary right after a call waits for min_gap_s after that call ended
        rig.rings = Rings(seconds=40.0)                               # (a fresh ring: times only move forward)
        rig.lis.ctx.rings = rig.rings
        fill_until(rig, rig.now + 12.0)
        end = rig.lis._last_end
        rig.lis.on_music_event({"kind": "section_change", "at_ms": rig.now * 1e3, "t_ms": rig.now * 1e3, "seq": 3,
                                "section_id": 4, "letter": None, "predicted": False,
                                "boundary_ms": (rig.now - 7.5) * 1000.0})
        due = rig.lis._section_due
        assert due < end + 8.0
        n = len(rig.llm.calls)
        while len(rig.llm.calls) == n and rig.now < end + 20.0:
            await rig.tick()
        assert len(rig.llm.calls) == n + 1 and rig.now >= end + 8.0
        assert rig.lis.snapshot()["sections"]["section_calls"] == 2
    run(go())


def test_section_calls_slow_down_with_the_cadence(tmp_path):
    """Relaxed after agreeing verdicts (45 s) or over the daily budget (x3): a section call waits at least half the
    interval after the last call, and there is at most one per interval, so false boundaries don't become uploads."""
    lis = stage4_rig(tmp_path, stream="test-audio").lis
    lis._last_start, lis._last_end = 1000.0, 1004.0
    assert lis.interval() == 20.0
    assert not lis._section_allowed(1011.0)                            # min_gap_s (8) after the call ended
    assert lis._section_allowed(1012.0)                                # >= 20 / 2 after it started
    lis._agree = int(lis.cfg["agree_reads"])                           # relaxed: every 45 s
    assert not lis._section_allowed(1012.0) and lis._section_allowed(1022.5)
    lis._last_section_start = 1022.5
    lis._last_start = 1022.5
    assert not lis._section_allowed(1060.0)                            # one section call per interval at most
    assert lis._section_allowed(1067.5)
    lis._budget_hit = True                                             # over budget: x3
    assert lis.interval() == 135.0 and not lis._section_allowed(1080.0)


def test_track_change_lets_the_next_verdict_win_at_once(tmp_path):
    rig = stage4_rig(tmp_path, stream="test-audio")
    rig.lis.vocab.poll(0.0)
    b = rig.lis.beliefs_state
    b.add(_ins(rig, genre="soul"), 0.0)
    b.add(_ins(rig, genre="soul"), 20.0)
    b.add(_ins(rig, genre="jazz"), 40.0)
    assert b.view(40.0)["genre"]["word"] == "soul"
    rig.lis.on_music_event({"kind": "track_change", "at_ms": 41000.0, "t_ms": 41000.0, "seq": 1, "track_id": 2,
                            "via": "dsp"})
    b.add(_ins(rig, genre="jazz"), 60.0)                   # one read, no track_change of its own
    assert b.view(60.0)["genre"]["word"] == "jazz"
    b.add(_ins(rig, genre="soul"), 80.0)                   # the flag is used once: back to two reads
    assert b.view(80.0)["genre"]["word"] == "jazz"
    assert rig.lis.log(1)[0]["kind"] == "track_change"


def test_ced_settings(tmp_path):
    rig = Rig(tmp_path, settings={"listener": {"ced": {"music_min": 0.7, "speech_max": 2}, "section_call_after_s": 5},
                                  "ml": {"results_stale_s": 4}})
    assert rig.lis.ced_cfg == {"music_min": 0.7, "speech_max": 0.3} and rig.lis.cfg["section_call_after_s"] == 5.0
    assert rig.lis.results_stale_s == 4.0 and any("listener.ced.speech_max" in p for p in rig.lis.problems)
    assert rig.lis.provides()["on_music_event"] == rig.lis.on_music_event


# -- the mic on demand (README S5.16) ----------------------------------------------------------------------------------
def test_a_stream_the_mic_keeps_off_never_gives_a_clip(tmp_path):
    for how in ("health", "state", "rings"):
        rig = Rig(tmp_path, stream="test-audio")
        rig.fill()
        if how == "health":
            rig.health["off"] = ["test-audio"]
        elif how == "state":
            rig.health["streams"] = {"test-audio": {"state": "off"}}
        else:
            rig.rings.set_off(["test-audio"])
        run(rig.ticks(3))
        assert rig.llm.calls == [] and rig.pub == [] and rig.lis.view()["why"] == L.WHY_MIC_OFF, how


def test_a_clip_cut_before_the_mic_went_off_is_never_uploaded(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()

    async def enc(pcm, fs, **kw):
        rig.rings.set_off(["room-mic"])                    # a mic went off while the clip was being encoded
        return b"MP3", "mp3"
    rig.lis._encode = enc
    run(rig.tick())
    assert rig.llm.calls == [] and rig.lis.log(1)[0]["why"] == L.WHY_MIC_WENT_OFF
