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


COMING = [{"kind": "Chorus", "eta_min_s": 25, "eta_max_s": 10, "signs": ["louder", "drums-in", "wobble"],
           "size": "big", "confidence": 0.7}]


def verdict(genre="soul", conf=0.8, moods=("warm", "mellow", "sparkly"), content="music", track_change=False,
            traj="building", ending="fade", intensity=0.3, change="slow", transitions="gentle", coming=None):
    """README S5.22's answer (the model's tool arguments)."""
    return {"content": content,
            "character": "soft late-night soul band with brushed drums and a long tail of extra words here",
            "genre": {"label": genre, "detail": "neo soul", "confidence": conf},
            "mood": {"words": list(moods), "confidence": 0.7},
            "approach": {"intensity": intensity, "change": change, "transitions": transitions, "confidence": 0.7},
            "trajectory": {"state": traj, "confidence": 0.5}, "ending": ending,
            "coming": [dict(x) for x in (COMING if coming is None else coming)],
            "track_change": track_change, "why": " ".join(["word"] * 20)}


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
    """A listener on a fake ctx. By default it may listen: the switch on, the mode's permission on (show.ai), a mode
    that listens, and a test-audio stream whose sender says room_sound: false (so only the music check is skipped)."""

    def __init__(self, tmp_path, stream="test-audio", settings=None, on=True,
                 target=({"vibe": "home", "listening": True},), mode=True, room=None):
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
        self.room = room                                  # a RoomView (the mode's label for WHY_MODE_OFF), or None
        ctx = c.BrainContext(
            publish=self.pub.append,
            features=lambda: dict(self.feats, t_ms=self.now * 1000.0, stream=self.health.get("active_audio")),
            clock=lambda: self.clock, harmony=lambda: self.harmony, health=lambda: self.health,
            rings=self.rings, target=lambda: self.target, vibe=lambda: self.vibe,
            persist=self._persist, notify=lambda k, p: self.notified.append(k), room=lambda: self.room)
        self.llm = FakeLLM()
        vocab = tmp_path / "vocab.yaml"
        vocab.write_text(VOCAB)
        self.log_path = tmp_path / "listener.jsonl"
        self.lis = L.Listener(ctx, llm=self.llm, mono=lambda: self.now, wall=lambda: 1.79e9 + self.now,
                              log_path=self.log_path, vocab_path=vocab, encoder=self._encode)
        self.lis.configure(dict({"senders": SENDERS}, **(settings or {
            "listener": {"cadence_s": 15, "fast_cadence_s": 5, "clip_s": 10, "min_gap_s": 4, "max_in_flight": 2,
                         "music_gate": "classifier", "describe_timeout_s": 0.5},
            "situations": {"music": {"dsp_min_gate": 0.3}}})))
        self.lis.on = bool(on)                            # as restored from state.json (set_on would persist)
        self.lis.mode = bool(mode)                        # owner, 20 Oct: the mode's permission, as restored
        self.auto_on, self.mic = True, {"on": True}         # owner, 17 Oct: uploads need Auto on and the mic on
        self.lis.set_auto(lambda: self.auto_on)
        self.lis.set_mic(lambda: self.mic)

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
            ts = list(self.lis._tasks)
            if ts:
                await asyncio.wait(ts, timeout=5)
            await asyncio.sleep(0)

    async def ticks(self, n, dt=1.0):
        for _ in range(n):
            await self.tick(dt)


def run(coro):
    return asyncio.run(coro)


# -- gating ------------------------------------------------------------------------------------------------------------
def test_switch_off_sends_nothing_from_the_room(tmp_path):
    rig = Rig(tmp_path, stream="room-mic", on=False)
    rig.fill()

    async def go():
        await rig.ticks(30)
    run(go())
    v = rig.lis.view()
    assert v["on"] is False and v["effective"] is False and v["why"] == L.WHY_OFF
    assert rig.llm.calls == [] and rig.pub == []
    assert rig.lis.persist() == {"on": False, "set_at": None, "mode": True}


@pytest.mark.parametrize("stream", ["test-audio", "phone-test", "test-anything", "room-mic"])
def test_every_stream_needs_the_switch_auto_and_the_mic(tmp_path, stream):
    """Privacy is never decided by a stream's name: test streams, phone-test and test-* need AI listening on, Auto on
    and the mic on, exactly as the room mic does (§7.5: the switch stops every upload)."""
    for case in ("switch_off", "auto_off", "auto_unknown", "mic_off", "vibe_off"):
        rig = Rig(tmp_path, stream=stream, on=case != "switch_off")
        rig.fill()
        if case == "auto_off":
            rig.auto_on = False
        elif case == "auto_unknown":
            rig.auto_on = None
        elif case == "mic_off":
            rig.mic = {"on": False}
        elif case == "vibe_off":
            rig.vibe = {"listener": False}

        async def go():
            await rig.ticks(25)
        run(go())
        assert rig.llm.calls == [] and rig.pub == [], (stream, case)
        want = {"switch_off": L.WHY_OFF, "auto_off": L.WHY_AUTO_OFF, "auto_unknown": L.WHY_AUTO_OFF,
                "mic_off": L.WHY_MIC_OFF, "vibe_off": L.WHY_VIBE}[case]
        assert rig.lis.view()["why"] == want, (stream, case)
        assert rig.rings.armed is False                   # nothing recorded for nothing


@pytest.mark.parametrize("target", [None, {"vibe": "home", "listening": False}, {"vibe": "home", "listening": True}])
def test_the_modes_listening_no_longer_gates(tmp_path, target):
    """Owner, 17 Oct: the AI listening switch, Auto and the mic decide (and a feel's own listener: off); a mode with
    no show target, or one whose show.main says listening: false, listens like any other."""
    rig = Rig(tmp_path, stream="test-audio", target=target)
    rig.fill()
    run(rig.tick())
    assert rig.lis.view()["effective"] is True and rig.rings.armed is True
    assert len(rig.llm.calls) == 1 and [m["type"] for m in rig.pub] == [c.MUSIC_INSIGHT]
    assert not hasattr(L, "WHY_MODE")                  # the show target's old "this mode doesn't listen"


def test_rule_0_still_holds_for_room_sound_without_the_music_check(tmp_path):
    """Owner, 17 Oct (1fe8b6f3): room sound goes only as substantive music: the CED music/speech run (or the DSP
    gate) and the loudness floor, with Auto and the mic on. Dropping the mode's wish changes none of it."""
    rig = Rig(tmp_path, stream="room-mic", target=None)  # no mode wish at all
    rig.fill()
    run(rig.ticks(5))
    assert rig.lis.view()["why"] == L.WHY_NO_CED and rig.llm.calls == []          # no classifier: nothing goes
    s = {"listener": {"music_gate": "dsp"}, "situations": {"music": {"dsp_min_gate": 0.3}}}
    rig = Rig(tmp_path, stream="room-mic", settings=s, target=None)
    rig.fill()
    run(rig.ticks(3))
    assert rig.lis.view()["why"] == L.WHY_NO_MUSIC and rig.llm.calls == []        # no beat, no key: no music
    rig.feats["gate"] = 0.05                                                     # under the floor...
    run(rig.ticks(12))
    rig.clock = {"gate": "beat", "bpm": 120.0, "state": "locked", "conf": 0.8}  # ...with a beat
    run(rig.ticks(2))
    assert rig.lis.view()["why"] == L.WHY_QUIET and rig.llm.calls == []
    rig.feats["gate"] = 0.6
    rig.auto_on = False                                                          # loud music, Auto off
    run(rig.ticks(12))
    assert rig.lis.view()["why"] == L.WHY_AUTO_OFF and rig.llm.calls == []
    rig.auto_on = True
    run(rig.tick())
    rig.fill()                                                                   # re-armed: the ring starts afresh
    run(rig.ticks(2))
    assert len(rig.llm.calls) == 1


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
    assert fs == 16000 and pcm.dtype == np.int16 and len(pcm) == 10 * 16000          # README S5.22: a 10 s clip
    assert enc == {"fmt": "mp3", "sample_rate": 24000, "bitrate": "64k"}
    assert [m["type"] for m in rig.pub] == [c.MUSIC_INSIGHT]
    ins = rig.pub[0]["data"]
    assert set(ins) == set(c.InsightData.__annotations__)
    assert ins["genre"] == {"label": "unknown", "detail": "neo soul", "confidence": 0.8}   # rock isn't in vocab
    assert ins["mood"]["words"] == ["warm", "mellow"]                                      # sparkly dropped
    assert len(ins["character"].split()) == 12 and len(ins["why"].split()) == 12
    assert ins["approach"] == {"intensity": 0.3, "change": "slow", "transitions": "gentle", "confidence": 0.7}
    assert ins["ending"] == "fade" and ins["stream"] == "test-audio"
    assert ins["coming"] == [{"kind": "chorus", "eta_min_s": 10.0, "eta_max_s": 25.0, "signs": ["louder", "drums_in"],
                              "size": "big", "confidence": 0.7}]                       # ordered, cleaned
    assert ins["window"]["t1_ms"] - ins["window"]["t0_ms"] == pytest.approx(10000.0, abs=1.0)
    assert ins["segment_id"] is None
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


@pytest.mark.parametrize("case,why", [("vibe_off", L.WHY_VIBE), ("quiet", L.WHY_QUIET),
                                      ("no_sound", L.WHY_NO_SOUND), ("no_model", L.WHY_NO_MODEL),
                                      ("too_little_audio", L.WHY_NO_SOUND)])
def test_why(tmp_path, case, why):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()
    if case == "vibe_off":
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


# -- cadence (README S5.22) ---------------------------------------------------------------------------------------------
def test_cadence_steady_and_starts_min_gap_apart(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()
    rig.llm.results = [ok(verdict(coming=[])) for _ in range(20)]      # nothing coming: the steady cadence

    async def go():
        await rig.tick()
        assert len(rig.llm.calls) == 1
        await rig.ticks(14)
        assert len(rig.llm.calls) == 1 and rig.lis.view()["next_in_s"] == 1.0 and rig.lis.view()["fast"] is None
        await rig.tick()
        assert len(rig.llm.calls) == 2 and rig.lis.view()["cadence_s"] == 15.0
        rig.lis.cfg["cadence_s"] = 1.0                    # never closer than min_gap_s (4) between starts
        times = []
        for _ in range(12):
            n = len(rig.llm.calls)
            await rig.tick()
            if len(rig.llm.calls) > n:
                times.append(rig.now)
        assert [round(b - a) for a, b in zip(times, times[1:])] == [4, 4]
    run(go())


def test_up_to_two_calls_in_flight(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()
    rig.lis.cfg["cadence_s"] = 4.0
    rig.llm.delay = 0.3

    async def go():
        for _ in range(3):
            rig.now += 4.0
            rig.lis.tick(rig.now)
            await asyncio.sleep(0.01)
        assert len(rig.lis._tasks) == 2 and rig.lis.view()["in_flight"] == 2   # the third waits for a free slot
        assert len(rig.llm.calls) == 2
        await asyncio.wait(list(rig.lis._tasks), timeout=5)
        await asyncio.sleep(0)
        assert rig.lis._tasks == [] and len(rig.pub) == 2
        rig.now += 4.0
        rig.lis.tick(rig.now)
        await asyncio.sleep(0.01)
        assert len(rig.llm.calls) == 3
        await asyncio.wait(list(rig.lis._tasks), timeout=5)
    run(go())


def test_fast_cadence_while_something_develops(tmp_path):
    rig = stage4_rig(tmp_path, stream="test-audio")
    lis = rig.lis
    now = rig.now
    assert lis._developing(now) is None and lis.interval() == 15.0
    rig.mc = {"t_ms": now * 1000.0, "section": {"id": 1, "phase": "build"}}
    assert lis._developing(now) == "a build"
    lis._fast = lis._developing(now)
    assert lis.interval() == 5.0
    rig.mc["section"]["phase"] = "outro"
    assert lis._developing(now) == "the music is fading"
    rig.mc = None
    lis._beliefs = {"coming": [{"t0_s": now + 25.0, "t1_s": now + 40.0}]}
    assert lis._developing(now) is None                                  # opens more than 20 s ahead
    lis._beliefs = {"coming": [{"t0_s": now + 15.0, "t1_s": now + 30.0}]}
    assert lis._developing(now) == "a forecast window"
    lis._beliefs = {"coming": [{"t0_s": now - 10.0, "t1_s": now - 1.0}]}
    assert lis._developing(now) is None                                  # already passed
    lis._beliefs = {}
    lis.on_music_event({"kind": "track_end_armed", "at_ms": (now + 25.0) * 1000.0, "eta_s": 25.0, "id": "e"})
    assert lis._developing(now) == "the song's end is near"
    lis.on_music_event({"kind": "track_end_armed", "at_ms": (now + 25.0) * 1000.0, "eta_s": None, "id": "e",
                        "detail": "disarmed"})
    assert lis._developing(now) is None
    lis.on_music_event({"kind": "section_change", "at_ms": now * 1000.0, "boundary_ms": (now - 4) * 1000.0,
                        "section_id": 2, "predicted": False})
    assert lis._developing(now) == "a new section" and lis._developing(now + 11.0) is None
    lis.on_music_event({"kind": "section_change", "at_ms": (now + 30) * 1000.0, "section_id": 3, "predicted": True})
    assert lis._developing(now + 20.0) is None                           # a predicted line is no boundary yet


def test_budget_slows_and_errors_back_off(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()

    async def go():
        rig.llm.budget = True
        await rig.tick()
        assert rig.lis.view()["cadence_s"] == 45.0
        rig.llm.budget = False
        rig.llm.results = [failed(), failed()]
        await rig.ticks(60)
        assert rig.lis.errors >= 2
        assert rig.lis.view()["cadence_s"] == 60.0          # 15 x 2^2 after two failures in a row
        assert [e["kind"] for e in rig.lis.log(10)].count("error") == 2
        await rig.ticks(80)
        assert rig.lis._errors_row == 0 and rig.lis.view()["cadence_s"] == 15.0
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
        task = rig.lis._tasks[0]
        rig.lis.set_on(False)
        await asyncio.wait([task], timeout=2)
        await asyncio.sleep(0)
        assert task.cancelled() and rig.lis._tasks == [] and rig.pub == []
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
        task = rig.lis._tasks[0]
        rig.lis.set_on(False)
        await asyncio.wait([task], timeout=2)
        await asyncio.sleep(0)
        assert task.cancelled() and rig.pub == []
    run(go())


def test_gate_is_checked_again_right_before_the_upload(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()

    async def enc(pcm, fs, **kw):
        rig.auto_on = False                                # Auto went off while the clip was being encoded
        return b"MP3", "mp3"
    rig.lis._encode = enc
    run(rig.tick())
    assert rig.llm.calls == [] and rig.lis.log(1)[0] == {"at": rig.lis.log(1)[0]["at"], "kind": "skipped",
                                                          "stream": "test-audio", "why": L.WHY_AUTO_OFF}


def test_persist_restore(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.restore({})
    assert rig.lis.on is False and rig.lis.mode is False  # never saved: off (fail closed)
    rig.lis.restore({"on": True, "set_at": 1.79e9})
    assert rig.lis.persist() == {"on": True, "set_at": 1.79e9, "mode": False}
    rig.lis.restore({"on": True, "set_at": 1.79e9, "mode": True})
    assert rig.lis.persist() == {"on": True, "set_at": 1.79e9, "mode": True}
    for bad in ("yes", 1, None, [True]):                 # a bad value: off
        rig.lis.restore({"on": True, "mode": bad})
        assert rig.lis.mode is False, bad
    assert rig.lis.provides()["beliefs"]() == rig.lis.beliefs()


def test_bad_settings_fall_back_to_design_defaults(tmp_path):
    rig = Rig(tmp_path, settings={"listener": {"cadence_s": -1, "music_gate": "maybe", "max_in_flight": 0,
                                               "est_fast_share": 2}})
    assert rig.lis.cfg["quiet_gate"] == 0.1 and rig.lis.cfg["min_clip_s"] == 6.0           # design defaults
    assert rig.lis.cfg["cadence_s"] == 15.0 and rig.lis.cfg["music_gate"] == "classifier"
    assert rig.lis.cfg["max_in_flight"] == 2 and rig.lis.cfg["est_fast_share"] == 0.5 and len(rig.lis.problems) == 4
    assert L.DEFAULTS["clip_s"] == 10.0 and L.DEFAULTS["fast_cadence_s"] == 5.0 and L.DEFAULTS["min_gap_s"] == 4.0


# -- beliefs -----------------------------------------------------------------------------------------------------------
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
    assert v["trajectory"] == {"state": "building", "conf": 0.5} and v["ending"] == "fade"
    assert v["approach"] == {"intensity": 0.3, "intensity_conf": 1.0, "change": "slow", "change_conf": 1.0,
                             "transitions": "gentle", "transitions_conf": 1.0, "conf": 1.0}
    assert v["character"].startswith("soft late-night soul band")
    # the chorus window: 10-25 s from the clip's end (5 s) on the acoustic clock
    assert [(x["kind"], x["t0_s"], x["t1_s"], x["in_s"], x["size"]) for x in v["coming"]] == \
        [("chorus", 15.0, 30.0, [0.0, 10.0], "big")]
    v = b.view(65.0)                                        # 45 s on: mood votes halved; the window passed
    assert v["state"] == "holding" and v["moods"][0]["conf"] == pytest.approx(0.5, abs=0.01)
    assert v["coming"] == [] and v["approach"]["intensity_conf"] == pytest.approx(0.5, abs=0.01)
    assert v["ending"] == "fade"                            # 45 s < ENDING_HOLD_S
    v = b.view(81.0)
    assert v["ending"] is None                              # held 60 s
    v = b.view(110.0)                                       # 90 s: fading, half way
    assert v["state"] == "fading" and v["genre"]["conf"] < 0.5
    v = b.view(141.0)
    assert v["state"] == "none" and v["genre"] is None and v["moods"] == [] and v["approach"] is None
    assert b.view(150.0)["age_s"] is None


def test_approach_agreement_and_the_latest_word_that_says_something(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    b.add(_ins(rig, intensity=0.2, change="slow", transitions="gentle", ending="cold"), 0.0)
    v = b.view(0.0)["approach"]
    assert v["intensity_conf"] == 0.5 and v["conf"] == 0.5      # one read: half confident, as a mood
    b.add(_ins(rig, intensity=0.9, change="fast", transitions="wild", ending="unknown"), 1.0)
    v = b.view(1.0)
    ap = v["approach"]
    assert ap["intensity"] == pytest.approx(0.55, abs=0.01) and ap["intensity_conf"] == 0.0   # 0.35 apart: no agreement
    assert ap["change_conf"] < 0.6 and v["ending"] == "cold"   # unknown never clears what was said
    b.add(_ins(rig, intensity=0.3, change="slow", transitions="gentle", ending="segue", track_change=True), 2.0)
    v = b.view(2.0)                                         # a new song: the votes start again
    assert v["approach"]["intensity"] == 0.3 and v["approach"]["change"] == "slow" and v["ending"] == "segue"


def test_an_older_clips_verdict_replaces_nothing_newer(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    new = _ins(rig, ending="cold", coming=[{"kind": "drop", "eta_min_s": 5, "eta_max_s": 9, "signs": ["low_in"],
                                            "size": "big", "confidence": 0.8}])
    new["window"] = {"t0_ms": 10000.0, "t1_ms": 20000.0}
    old = _ins(rig, ending="fade", coming=[])
    old["window"] = {"t0_ms": 5000.0, "t1_ms": 15000.0}
    b.add(new, 22.0)
    b.add(old, 23.0)                                        # two in flight: the older clip came back second
    v = b.view(23.0)
    assert v["ending"] == "cold" and [x["kind"] for x in v["coming"]] == ["drop"] and v["age_s"] == 0.0
    assert b.votes["mood"]["warm"] == pytest.approx(1.0 + 0.5 ** (1 / 45.0), abs=1e-3)   # its votes still count


def test_speech_adds_no_votes(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    b = L.BeliefState()
    b.add(_ins(rig, content="speech"), 0.0)
    v = b.view(0.0)
    assert v["genre"] is None and v["moods"] == [] and v["content"] == "speech"
    assert v["character"] is None and v["approach"] is None and v["ending"] is None and v["coming"] == []


# -- Describe it -------------------------------------------------------------------------------------------------------
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


# -- audio -------------------------------------------------------------------------------------------------------------
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


CED24 = {"listener": {"cadence_s": 15, "fast_cadence_s": 5, "clip_s": 24, "min_gap_s": 4, "min_clip_s": 10,
                      "music_gate": "classifier"}}          # the run arithmetic below was written for 24 s clips


def stage4_rig(tmp_path, stream="room-mic", settings=None) -> Rig:
    rig = Rig(tmp_path, stream=stream, settings=settings)
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
    rig = stage4_rig(tmp_path, settings=CED24)
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
    rig = stage4_rig(tmp_path, settings=CED24)
    rig.fill(seconds=35.0)
    rig.ml = ml_view(readings(rig, [(11, 0.8, 0.1), (2, 0.1, 0.6), (11, 0.8, 0.1)]))
    run(rig.tick(0.0))
    ins = rig.pub[0]["data"]
    # both runs lose their last second (the next reading failed, or hasn't come yet): a tie, the latest wins
    assert ins["window"]["t1_ms"] == pytest.approx((rig.now - 1.0) * 1000.0, abs=1.0)


def test_babble_and_conversation_under_music_count_as_talking(tmp_path):
    rig = stage4_rig(tmp_path, settings=CED24)
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


def test_sparse_ced_readings_join_into_one_run(tmp_path):
    """21 Oct (09's audit): readings every 4-8 s, all music without talking, never made a run over 1 s, so the gate
    said "not enough music without talking" for good. A reading now vouches back to the one before it while that one
    was still "now" (MLView.stale_s)."""
    rig = stage4_rig(tmp_path)
    rig.fill(seconds=35.0)
    gaps = [0.0, 5.0, 4.0, 7.0, 6.0, 8.0]                             # the newest first, seconds before it
    ts, t = [], rig.now
    for g in gaps:
        t -= g
        ts.append(t)
    rows = [ced(x, 0.72, 0.001) for x in sorted(ts)]
    stale = {"ced": 24.0, "tags": 24.0, "key": 24.0, "beats": 30.0}  # 3 x the worker's 8 s
    rig.ml = {**ml_view(rows), "stale_s": stale}
    run_, why = rig.lis._ced_run("room-mic", rig.now)
    assert why is None and run_[1] - run_[0] >= 6.0
    rows[-3] = ced(rows[-3]["t"], 0.1, 0.6)                           # talking in between splits it
    rig.ml = {**ml_view(rows), "stale_s": stale}
    run_, why = rig.lis._ced_run("room-mic", rig.now)
    assert why == L.WHY_SHORT
    far = [ced(rig.now - 30.0), ced(rig.now - 0.5)]                    # further apart than the stale time: no join
    rig.ml = {**ml_view(far), "stale_s": {**stale, "ced": 9.0}}
    run_, why = rig.lis._ced_run("room-mic", rig.now)
    assert why == L.WHY_SHORT


def fill_until(rig, t_end, seconds=38.0):
    """Audio in the ring for the ``seconds`` that end at ``t_end`` (it may run ahead of the fake clock)."""
    now = rig.now
    rig.now = t_end
    rig.fill(seconds=seconds)
    rig.now = now


def test_section_call_at_the_boundary_plus_8_s_with_min_gap_and_the_segment_map(tmp_path):
    rig = stage4_rig(tmp_path, stream="test-audio")                    # room_sound false: no CED gate here
    rig.lis.cfg["fast_cadence_s"] = 15.0                               # (the section call alone, not the fast cadence)
    fill_until(rig, rig.now + 14.0)
    rig.mc = {"t_ms": rig.now * 1000.0, "section": {"id": 2, "letter": "A", "started_ms": (rig.now - 20) * 1000.0}}

    async def go():
        await rig.tick()                                               # the regular call (t = 1001)
        assert len(rig.llm.calls) == 1
        first_start = rig.lis._last_start
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
        assert len(rig.llm.calls) == 2 and rig.now - first_start >= 8.0
        user = rig.llm.calls[1][1]["user"]
        seg = next(u for u in user if u.startswith("Segment map"))
        assert "#2 A 0-" in seg and "#3 " in seg
        ins = rig.pub[-1]["data"]
        assert ins["window"]["t1_ms"] == pytest.approx((boundary + 8.0) * 1000.0, abs=1.0)
        assert ins["segment_id"] == 3
        assert rig.lis.snapshot()["sections"]["section_calls"] == 1
        # a boundary right after a call waits for half the interval (7.5 s) after that call started
        rig.rings = Rings(seconds=40.0)                               # (a fresh ring: times only move forward)
        rig.lis.ctx.rings = rig.rings
        fill_until(rig, rig.now + 12.0)
        start = rig.lis._last_start
        rig.lis.on_music_event({"kind": "section_change", "at_ms": rig.now * 1e3, "t_ms": rig.now * 1e3, "seq": 3,
                                "section_id": 4, "letter": None, "predicted": False,
                                "boundary_ms": (rig.now - 7.5) * 1000.0})
        due = rig.lis._section_due
        assert due < start + 7.5
        n = len(rig.llm.calls)
        while len(rig.llm.calls) == n and rig.now < start + 20.0:
            await rig.tick()
        assert len(rig.llm.calls) == n + 1 and rig.now >= start + 7.5
        assert rig.lis.snapshot()["sections"]["section_calls"] == 2
    run(go())


def test_section_calls_slow_down_with_the_cadence(tmp_path):
    """Over the daily budget (x3) or backed off: a section call waits at least min_gap_s and half the interval after
    the last call started, and there is at most one per interval, so false boundaries don't become uploads."""
    lis = stage4_rig(tmp_path, stream="test-audio").lis
    lis._last_start = 1000.0
    assert lis.interval() == 15.0
    assert not lis._section_allowed(1007.0)                            # under 15 / 2 after the call started
    assert lis._section_allowed(1007.5)
    lis._fast = "a new section"                                        # fast: every 5 s, but min_gap_s (4) holds
    assert lis.interval() == 5.0 and not lis._section_allowed(1003.9) and lis._section_allowed(1004.0)
    lis._fast = None
    lis._last_section_start = lis._last_start = 1007.5
    assert not lis._section_allowed(1020.0)                            # one section call per interval at most
    assert lis._section_allowed(1022.5)
    lis._budget_hit = True                                             # over budget: x3
    assert lis.interval() == 45.0 and not lis._section_allowed(1040.0)


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



def test_nothing_goes_to_openai_unless_auto_and_the_mic_are_on(tmp_path):
    """Owner, 17 Oct: Auto off, or the mic off, or unknown -> no audio to OpenAI (cost, and it's intrusive)."""
    r = Rig(tmp_path)
    assert r.lis._gate(r.now)[0] != L.WHY_AUTO_OFF
    r.auto_on = False
    assert r.lis._gate(r.now)[0] == L.WHY_AUTO_OFF
    r.auto_on = None                                     # unknown (the brain starting): off
    assert r.lis._gate(r.now)[0] == L.WHY_AUTO_OFF
    r.auto_on, r.mic = True, {"on": False, "muted": True}
    assert r.lis._gate(r.now)[0] == L.WHY_MIC_OFF
    r.lis.set_mic(None)                                  # no mic component at all: off
    assert r.lis._gate(r.now)[0] == L.WHY_MIC_OFF
    r.lis.configure({"senders": SENDERS, "listener": {"requires_auto": False, "requires_mic": False}})
    assert r.lis._gate(r.now)[0] not in (L.WHY_AUTO_OFF, L.WHY_MIC_OFF)   # config can relax it (not the default)


# -- owner, 20 Oct: the mode's permission (room_service's show.ai) -----------------------------------------------------
NORMAL = {"current": "normal", "target": None, "modes": {"normal": {"label": "Normal"}, "live": {"label": "Live"}}}


def test_the_mode_must_allow_it(tmp_path):
    """Owner, 20 Oct: "the default should be that it doesn't send except on a live mode". With the switch, Auto and
    the mic on, a mode that hasn't allowed it sends nothing, says so in the mode's words, and records nothing."""
    for room, want in ((None, "AI listening is off in this mode"), (NORMAL, "AI listening is off in Normal"),
                       (dict(NORMAL, target="live"), "AI listening is off in Live")):   # going to Live: its label
        rig = Rig(tmp_path, stream="test-audio", mode=False, room=room)
        rig.fill()

        async def go():
            await rig.ticks(25)
        run(go())
        v = rig.lis.view()
        assert rig.llm.calls == [] and rig.pub == [] and rig.rings.armed is False
        assert v["why"] == want and v["mode"] is False and v["on"] is True and v["effective"] is False
    assert L.why_mode(None) == L.why_mode("  ") == "AI listening is off in this mode"


def test_the_switch_is_still_the_master_and_the_mode_comes_before_auto(tmp_path):
    rig = Rig(tmp_path, on=False, mode=True)
    assert rig.lis._gate(rig.now)[0] == L.WHY_OFF        # the Room page's switch off: off, whatever the mode says
    rig = Rig(tmp_path, mode=False, room=NORMAL)
    rig.auto_on, rig.mic = False, {"on": False}
    assert rig.lis._gate(rig.now)[0] == "AI listening is off in Normal"


def test_a_mode_that_allows_it_listens(tmp_path):
    rig = Rig(tmp_path, stream="test-audio", mode=False, room=NORMAL)
    rig.fill()
    run(rig.tick())
    assert rig.lis.view()["effective"] is False and rig.rings.armed is False
    v = rig.lis.set_mode(True)                           # room_service's show.ai: this mode lets the AI listen
    assert v["mode"] is True and rig.rings.armed is True and rig.persisted == 1
    assert rig.lis.persist()["mode"] is True and rig.lis.log(1)[0]["kind"] == "mode"
    rig.lis.set_mode(True)                               # room's re-sync: no change, nothing persisted or logged
    assert rig.persisted == 1 and len([e for e in rig.lis.log(50) if e["kind"] == "mode"]) == 1
    rig.fill()

    async def go():
        await rig.ticks(12)
    run(go())
    assert rig.lis.view()["effective"] is True and len(rig.llm.calls) >= 1 and rig.pub


def test_requires_mode_false_relaxes_it_and_a_bad_value_keeps_the_default(tmp_path):
    rig = Rig(tmp_path, mode=False)
    assert rig.lis._gate(rig.now)[0] == "AI listening is off in this mode"
    rig.lis.configure({"senders": SENDERS, "listener": {"requires_mode": False}})
    assert not rig.lis._gate(rig.now)[0].startswith("AI listening is off")
    rig.lis.configure({"senders": SENDERS, "listener": {"requires_mode": "yes"}})
    assert rig.lis.cfg["requires_mode"] is True
    assert "listener.requires_mode: 'yes' is not true/false" in rig.lis.problems
    assert rig.lis._gate(rig.now)[0] == "AI listening is off in this mode"


def test_the_mode_going_off_cancels_a_call_in_flight(tmp_path):
    rig = Rig(tmp_path, stream="test-audio", room=NORMAL)
    rig.fill()
    rig.llm.delay = 5.0

    async def go():
        rig.now += 1
        rig.lis.tick(rig.now)
        await asyncio.sleep(0.05)
        task = rig.lis._tasks[0]
        v = rig.lis.set_mode(False)                      # the room left the live mode
        await asyncio.wait([task], timeout=2)
        await asyncio.sleep(0)
        assert task.cancelled() and rig.lis._tasks == [] and rig.pub == []
        assert v["why"] == "AI listening is off in Normal" and rig.rings.armed is False
    run(go())


def test_the_mode_is_checked_again_right_before_the_upload(tmp_path):
    rig = Rig(tmp_path, stream="test-audio")
    rig.fill()

    async def enc(pcm, fs, **kw):
        rig.lis.mode = False                             # the mode's permission went while the clip was encoded
        return b"MP3", "mp3"
    rig.lis._encode = enc
    run(rig.tick())
    assert rig.llm.calls == [] and rig.lis.log(1)[0]["kind"] == "skipped"
    assert rig.lis.log(1)[0]["why"] == "AI listening is off in this mode"


def test_the_modes_permission_survives_a_restart(tmp_path):
    """Persisted next to the switch (state.json's "listener"): a show_service restart keeps what room last said."""
    from show_service.state_store import StateStore
    rig = Rig(tmp_path, mode=False)
    rig.lis.set_mode(True)
    store = StateStore(tmp_path / "state.json")
    assert store.save({"listener": rig.lis.persist()})
    again = Rig(tmp_path, mode=False)                    # a fresh listener: off until restored
    assert again.lis._gate(again.now)[0] == "AI listening is off in this mode"
    again.lis.restore(StateStore(tmp_path / "state.json").load()["listener"])
    assert again.lis.mode is True and not (again.lis._gate(again.now)[0] or "").startswith("AI listening is off")
    rig.lis.set_mode(False)
    assert store.save({"listener": rig.lis.persist()})
    again.lis.restore(StateStore(tmp_path / "state.json").load()["listener"])
    assert again.lis.mode is False


# -- README S5.20: provider, cost, reliability -------------------------------------------------------------------------
class _Resp:
    """A Chat Completions answer with one tool call (what the openai SDK hands back)."""

    def __init__(self, args: dict) -> None:
        fn = type("F", (), {"name": "report_music", "arguments": json.dumps(args)})()
        msg = type("M", (), {"tool_calls": [type("C", (), {"function": fn})()], "content": None})()
        self.choices = [type("Ch", (), {"message": msg})()]
        pd = type("PD", (), {"audio_tokens": 240, "cached_tokens": 0})()
        self.usage = type("U", (), {"prompt_tokens": 1840, "completion_tokens": 200, "prompt_tokens_details": pd,
                                    "completion_tokens_details": None})()


class _FakeClient:
    """AsyncOpenAI's two calls the layer makes: models.list() and chat.completions.create()."""

    def __init__(self, answer: dict, ids=("m1", "m2")) -> None:
        self.requests: list = []
        outer = self

        class _Models:
            async def list(self, timeout=None):
                return type("P", (), {"data": [type("I", (), {"id": i})() for i in ids]})()

        class _Completions:
            async def create(self, **kw):
                outer.requests.append(kw)
                return _Resp(answer)

        self.models = _Models()
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _real_llm(tmp_path, monkeypatch, answer):
    """utils.llm itself on tmp files (two models, m1 then m2), with a fake client: no network, no real ledger."""
    import utils.llm as m
    from utils.llm import core
    cfg = tmp_path / "llm.yaml"
    cfg.write_text("version: 1\ntasks:\n  listen:\n    models:\n      - {provider: openai, model: m1}\n"
                   "      - {provider: openai, model: m2}\n    timeout_s: 2\n"
                   "prices:\n  m1: {text_in: 2.5, text_out: 10, audio_in: 32}\n  m2: {text_in: 2.5, text_out: 10}\n")
    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-NOTAREALKEY0123456789")
    m.configure(config_path=cfg, env_file=env, ledger_path=tmp_path / "ledger.jsonl")
    client = _FakeClient(answer)
    monkeypatch.setattr(core._Layer, "aclient", lambda self, prov, key: client)
    return m, client


def test_off_vocabulary_words_are_kept_and_never_sent_to_the_fallback(tmp_path, monkeypatch):
    """Audit, 17 Oct: a word outside its list failed the whole answer, and the same clip was then charged again on the
    fallback. The words are plain strings and the call is lenient: one call, cleaned (README S5.20, S5.22)."""
    v = verdict(traj="Winding down", content="Music", ending="Fade out", change="glacial")
    v["genre"]["confidence"] = 1.4                                 # out of range: soft, clamped
    v["coming"] = [dict(COMING[0], kind=f"thing{i}") for i in range(3)]   # too many: soft, cut to 2
    m, client = _real_llm(tmp_path, monkeypatch, v)
    try:
        rig = Rig(tmp_path)
        rig.lis._llm = m                                           # the real layer, not the fake
        rig.fill()
        run(rig.tick())
        assert [r["model"] for r in client.requests] == ["m1"]     # no second, paid call to m2
        ins = rig.pub[0]["data"]
        assert ins["trajectory"]["state"] == "winding_down" and ins["ending"] == "unknown"
        assert ins["approach"]["change"] is None and ins["approach"]["transitions"] == "gentle"
        assert ins["content"] == "music" and ins["genre"]["confidence"] == 1.0
        assert [x["kind"] for x in ins["coming"]] == ["new_section", "new_section"]
        verdict_log = [e for e in rig.lis.log(20) if e["kind"] == "verdict"][0]
        assert verdict_log["problems"] and any("more than 2" in p for p in verdict_log["problems"])
        assert m.status()["calls_today"]["listen"] == 1
        sent = client.requests[0]["tools"][0]["function"]["parameters"]["properties"]
        assert "enum" not in sent["ending"] and "enum" not in sent["content"]
        assert "winding_down" in sent["trajectory"]["properties"]["state"]["description"]   # the words, as a hint
        assert "phrase_line" in client.requests[0]["messages"][0]["content"]
    finally:
        m.configure()


def test_the_tool_has_no_word_lists_left_to_fail_on():
    from utils.llm import check
    v = verdict(traj="climbing", content="music with talking", ending="abrupt", change="glacial",
                transitions="explosive", coming=[dict(COMING[0], kind="riff", size="huge", signs=["sparkle"])])
    assert check(v, L.LISTEN_TOOL["parameters"]) == ([], [])
    assert set(L.LISTEN_TOOL["parameters"]["required"]) == {"content", "character", "genre", "mood", "approach",
                                                            "trajectory", "ending", "coming", "track_change", "why"}
    assert L.word_of("Pre-chorus", c.SECTION_LABELS, "unknown") == "pre_chorus"
    assert L.word_of(7, c.SECTION_LABELS, "unknown") == "unknown"
    assert L.word_of("Key lift", c.COMING_KINDS, None) == "key_lift"


def test_a_garbled_answer_never_crashes_to_insight(tmp_path):
    rig = Rig(tmp_path)
    rig.lis.vocab.poll(0.0)
    ins = L.to_insight({"genre": "jazz", "mood": ["calm"], "approach": "wild", "trajectory": "up", "coming": "soon",
                        "ending": 3, "character": None}, vocab=rig.lis.vocab, stream="s", model="m", latency_ms=1.0,
                       window=(0.0, 1.0), t_ms=0.0)
    assert ins["genre"]["label"] == "unknown" and ins["mood"]["words"] == [] and ins["coming"] == []
    assert ins["trajectory"]["state"] == "steady" and ins["ending"] == "unknown" and ins["character"] == ""
    assert ins["approach"] == {"intensity": None, "change": None, "transitions": None, "confidence": 0.0}
    one = L.to_insight({"coming": [{"kind": "drop", "eta_max_s": 400, "signs": "low_in"}, {"kind": "chorus"}]},
                       vocab=rig.lis.vocab, stream="s", model="m", latency_ms=1.0, window=(0.0, 1.0), t_ms=0.0)
    assert one["coming"] == [{"kind": "drop", "eta_min_s": 120.0, "eta_max_s": 120.0, "signs": [], "size": "medium",
                              "confidence": 0.0}]                  # one end, cut to 120 s; no window: dropped


class EstLLM(FakeLLM):
    """FakeLLM with utils.llm's estimate() and probe()."""

    def __init__(self, usd=0.004, label="Google", model="gemini-3.8-flash", basis="price table") -> None:
        super().__init__()
        self.est = {"model": model, "provider": "gemini", "label": label, "usd": usd, "basis": basis, "calls": 0}
        self.est_args: list = []
        self.probed: list = []
        self.resolved = [{"configured": "gemini-3.8-flash", "provider": "gemini", "model": model, "via": "explicit",
                          "skip": None}]

    def estimate(self, task, *, audio_s=None):
        self.est_args.append((task, audio_s))
        return dict(self.est)

    def probe(self, task=None):
        self.probed.append(task)
        return {"resolved": self.resolved}

    def status(self):
        st = super().status()
        st["tasks"]["listen"]["resolved"] = [dict(r) for r in self.resolved]
        return st


def test_the_estimate_shows_per_hour_and_per_evening_even_while_off(tmp_path):
    rig = Rig(tmp_path, on=False, settings={"listener": {"cadence_s": 20, "fast_cadence_s": 5, "clip_s": 10,
                                                         "evening_h": 4, "est_fast_share": 0.5}})
    rig.lis._llm = rig.llm = EstLLM(usd=0.004026)
    run(rig.tick())
    v = rig.lis.view()
    assert v["on"] is False and v["why"] == L.WHY_OFF
    assert rig.llm.est_args[-1] == ("listen", 10.0)                    # the clip's length prices its audio
    # half the time every 20 s, half every 5 s: 3600 x (0.5 / 20 + 0.5 / 5) = 450 clips an hour x $0.004026
    assert v["estimate"] == {"per_call_usd": 0.00403, "per_hour_usd": 1.812, "per_evening_usd": 7.25,
                             "evening_h": 4.0, "cadence_s": 8.0, "steady_cadence_s": 20.0, "fast_cadence_s": 5.0,
                             "fast_share": 0.5, "fast_share_basis": "assumed", "clip_s": 10.0,
                             "model": "gemini-3.8-flash", "provider": "Google", "basis": "price table"}
    assert v["est_evening_usd"] == 7.25 and v["provider"] == "Google"
    assert set(v) == set(c.ListenerView.__annotations__)
    rig.lis.configure({"senders": SENDERS, "listener": {"evening_h": 3, "cadence_s": 45, "est_fast_share": 0}})
    run(rig.tick())
    assert rig.lis.view()["estimate"]["per_evening_usd"] == round(0.004026 * 3600 / 45 * 3, 2)
    rig.lis._eff_s, rig.lis._fast_s = 3600.0, 900.0                   # an hour measured: a quarter of it fast
    run(rig.tick())
    e = rig.lis.view()["estimate"]
    assert e["fast_share_basis"] == "measured" and e["fast_share"] == pytest.approx(0.25, abs=0.01)
    rig.llm.est = dict(rig.llm.est, usd=None, basis=None)               # a model with no price: no estimate
    run(rig.tick())
    assert rig.lis.view()["estimate"] is None and rig.lis.view()["est_evening_usd"] is None


def test_the_view_carries_character_approach_and_the_cue_sheet(tmp_path):
    rig = Rig(tmp_path)
    rig.fill()
    sheet = [{"kind": "chorus", "words": "Big change at the chorus, likely in 10-25 s", "window_s": [10.0, 25.0],
              "size": "big"}]
    rig.lis.ctx.cue_sheet = lambda: sheet
    rig.llm.results = [ok(verdict(coming=[])), ok(verdict(coming=[]))]
    run(rig.tick())
    run(rig.ticks(15))
    v = rig.lis.view()
    assert v["character"].startswith("soft late-night soul band") and v["armed"] == sheet
    assert {k: v["approach"][k] for k in ("intensity", "change", "transitions")} == \
        {"intensity": 0.3, "change": "slow", "transitions": "gentle"}
    assert v["approach"]["conf"] == pytest.approx(0.9, abs=0.01)       # two agreeing reads, 15 s apart
    rig.lis.ctx.cue_sheet = lambda: 1 / 0                              # a failing cue engine: an empty sheet
    run(rig.tick())
    assert rig.lis.view()["armed"] == []


def test_the_message_says_what_structure_knows_now(tmp_path):
    rig = stage4_rig(tmp_path, stream="test-audio")
    rig.fill()
    rig.mc = {"t_ms": rig.now * 1000.0, "section": {"id": 3, "phase": "build", "bars_in_section": 6,
                                                     "expected_bars": 8, "chorus": True, "started_ms": 0.0}}
    rig.lis.on_music_event({"kind": "track_end_armed", "at_ms": (rig.now + 40.0) * 1000.0, "eta_s": 40.0})

    async def go():
        rig.mc["t_ms"] = (rig.now + 1) * 1000.0
        await rig.tick()
    run(go())
    user = rig.llm.calls[0][1]["user"]
    line = next(u for u in user if u.startswith("Now, from the room's own analysis"))
    assert "phase build" in line and "6 bars into this section (it lasted 8 bars last time)" in line
    assert "probable chorus" in line and "ends about" in line


def test_the_snapshot_and_the_log_show_what_the_models_resolved_to(tmp_path):
    rig = Rig(tmp_path)
    rig.lis._llm = rig.llm = EstLLM()
    rig.lis._warm_up()
    assert rig.llm.probed == ["listen"]                                 # discovery before the first call
    run(rig.tick())
    snap = rig.lis.snapshot()
    assert snap["llm"]["resolved"][0]["model"] == "gemini-3.8-flash" and snap["llm"]["usable"] == ["gpt-x"]
    models = [e for e in rig.lis.log(50) if e["kind"] == "models"]
    assert len(models) == 1 and models[0]["resolved"][0]["via"] == "explicit"
    run(rig.ticks(3))
    assert len([e for e in rig.lis.log(50) if e["kind"] == "models"]) == 1          # only when it changes
    rig.llm.resolved = [dict(rig.llm.resolved[0], model="gemini-3.9-flash", via="discovered")]
    run(rig.tick())
    assert [e for e in rig.lis.log(50) if e["kind"] == "models"][-1]["resolved"][0]["model"] == "gemini-3.9-flash"


def test_the_real_llm_yaml_lists_gemini_first_then_gpt_audio_1_5(tmp_path, monkeypatch):
    """config/llm.yaml as shipped: Gemini Flash first, gpt-audio-1.5 the fallback; with no Gemini key the Gemini entry
    is skipped ("no key") and the listener runs on gpt-audio-1.5 exactly as before."""
    import utils.llm as m
    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-NOTAREALKEY0123456789")
    m.configure(env_file=env, ledger_path=tmp_path / "ledger.jsonl")
    try:
        t = m.status()["tasks"]["listen"]
        assert m.status()["config"]["error"] is None
        assert [(r["provider"], r["configured"]) for r in t["resolved"]] == [("gemini", "gemini-3.8-flash"),
                                                                             ("openai", "gpt-audio-1.5")]
        assert t["resolved"][0]["skip"] == "no key for gemini (GEMINI_API_KEY)" and t["usable"] == ["gpt-audio-1.5"]
        e = m.estimate("listen", audio_s=24)
        assert (e["model"], e["label"], e["basis"]) == ("gpt-audio-1.5", "OpenAI", "price table")
        assert e["usd"] == pytest.approx((1600 * 2.5 + 240 * 32 + 200 * 10) / 1e6)
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaNOTAREALKEY0123456789abcd")
        e = m.estimate("listen", audio_s=24)
        assert (e["model"], e["label"]) == ("gemini-3.8-flash", "Google")
        assert e["usd"] == pytest.approx(((1600 + 768) * 0.75 + 600 * 3.75) / 1e6)
        settings = m.task_settings("listen")
        assert settings["models"][0]["options"]["reasoning_effort"] == "low"
        assert settings["models"][0]["discover"] and settings["audio"]["format"] == "mp3"
    finally:
        m.configure()


def test_music_moods_map_the_feel_to_visual_moods(tmp_path):
    """04's vocab.yaml music_moods (17 Oct): the listener says how the music feels; beliefs' moods are the visual
    moods those words mean (first counts most), feel keeps its own words."""
    vf = tmp_path / "vocab.yaml"
    vf.write_text("moods: [calm, mellow, dark, warm, cool]\ngenre: [jazz]\n"
                  "music_moods:\n  melancholic: [mellow, cool, dark, sparkly]\n  peaceful: [calm, mellow]\n")
    v = L.Vocab(vf)
    v.poll(0.0)
    assert v.music_moods == {"melancholic": ["mellow", "cool", "dark"], "peaceful": ["calm", "mellow"]}
    ins = L.to_insight(verdict(moods=("melancholic", "peaceful", "warm")), vocab=v, stream="s", model="m",
                       latency_ms=1.0, window=(0.0, 0.0), t_ms=0.0)
    assert ins["mood"]["words"] == ["melancholic", "peaceful"]          # the feel words; a visual word isn't one
    b = L.BeliefState()
    b.mood_map = v.music_moods
    b.add(ins, 0.0)
    b.add(ins, 0.0)
    out = b.view(0.0)
    assert [f["word"] for f in out["feel"]] == ["melancholic", "peaceful"]
    assert [m["word"] for m in out["moods"]] == ["calm", "mellow", "cool"]
    assert out["moods"][1]["conf"] == 1.0 and out["moods"][2]["conf"] == 0.85
    b.clear()
    assert b.mood_map == v.music_moods
