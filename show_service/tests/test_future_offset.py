"""Notes stamped up to +0.6 s in the future (3a's check before Sources applies a runtime acoustic offset).

A rendered test-piano run (sources.py: an instrument other than windows-gs, heard on PC22) sets the test-piano stream's
acoustic_offset_ms to +L (about 650 ms) while it plays, so every MIDI event's host time is L after the packet arrives:
the sound is heard that much later than the MIDI is sent. These tests drive what consumes those times, exactly as
``__main__`` wires them (NoteTap.on_midi, then Piano.on_midi; on_tick: Piano.tick, then Harmony.tick with the present
piano; the PianoView built from the voice piano and the tap), with ``to_host = stamp + 0.6`` and arrival = stamp +
2 ms, and check presence, the note table, chord detection and piano_playing / piano_idle still behave.

What holds (asserted below):
- the table keeps the sound times (T_ON / T_OFF = stamp + L) and the bus carries them (music.note t_ms L ahead);
- ``present`` and the voice hand-over run on arrival times (unchanged by L);
- the hot-repeat cap and the state heal work in the shifted time base (a state's synthesised events at its stamp + L);
- staleness releases at the tick's time, after every strike (5 s > L), so no release precedes its strike;
- harmony, the PianoView and Auto's piano words keep working (NoteTap stamps arrival, so piano_playing and
  piano_idle read exactly as with no offset).

What moves (documented, asserted as the behaviour):
- ``Piano.levels(now)`` clamps a future anchor (no decay before it): a note counts as sounding at its full strike level
  from the moment its packet arrives, L before it is heard. music.harmony (t_ms = now) therefore describes the chord
  L early on the bus; on the glass that is close to the sound, since the screens show harmony a photon lead (about
  0.57 s) after now.
- music.notes' ``t_ms`` is max(now, the newest event) and its keys' t_on_ms are future sound times (sound_view holds
  them until its photon time, up to FUTURE_MAX_MS 2000).
- PianoView.last_note_age_s is 0 (clamped) until the sound time has passed.
- A wire restart (a new sender sess) releases the held keys at the packet's arrival, which can be up to L before
  their strikes' sound times: the release's t_ms precedes the strike's (only at a sender restart mid-note).
"""
from __future__ import annotations

import types

import numpy as np
import pytest

from show_service import __main__ as main_mod
from show_service import contract, midi, structure, vban
from show_service.harmony import Harmony
from show_service.midi import RELEASED, T_OFF, T_ON, VEL_ON, Piano
from show_service.situations import NoteTap, Situations

L_S = 0.6                       # the runtime acoustic offset under test (sources.py's default lead is 0.65 s)
ARRIVE_S = 0.002                # transport: the packet arrives 2 ms after its stamp
STREAM = contract.TEST_PIANO_STREAM
SESS = "wire-a"
SETTINGS = {"situations": {"midi": {"streams": [STREAM], "channels": [1], "window_s": 10, "min_notes": 12,
                                    "min_span_s": 6, "min_pitches": 4, "idle_note_s": 1.0}}}


def on(t: float, p: int, v: int = 80) -> list[int]:
    return [int(round(t * 1e9)), 0x90, p, v]


def off(t: float, p: int, v: int = 0) -> list[int]:
    return [int(round(t * 1e9)), 0x80, p, v]


def cc(t: float, n: int, v: int) -> list[int]:
    return [int(round(t * 1e9)), 0xB0, n, v]


class Rig:
    """__main__'s show-rx wiring for one MIDI stream, on a simulated clock (the sender's clock is the host's, as for
    the test piano in WSL): packets in, then on_tick every 10 ms."""

    def __init__(self, offset_s: float = L_S) -> None:
        self.msgs: list[dict] = []
        self.offset_s = offset_s
        self.piano = Piano(STREAM, self.msgs.append, state_channels=(1,))
        self.tap = NoteTap()
        self.harmony = Harmony(self.msgs.append)
        self.now = 99.0
        self.seq = 0
        self.sent: list[tuple[int, list]] = []
        self.sess = SESS
        self.view: contract.PianoView = dict(main_mod.NEUTRAL_PIANO_VIEW)   # type: ignore[assignment]
        self._pv = types.SimpleNamespace(_voice=self.piano, tap=self.tap, _pv_last=0.0, _vel_ema=None, _vel_t=None,
                                         _midi_mod=midi)
        self.health = {"streams": {STREAM: {"kind": "midi", "state": "streaming", "test": True}}}
        ctx = contract.BrainContext(publish=lambda m: None, health=lambda: self.health, notes=self.tap,
                                    piano=lambda: self.view)
        ctx.room = lambda: None
        self.ctx = ctx
        self.sit = Situations(ctx)
        self.sit.configure(SETTINGS)
        self.sit.start()

    def to_host(self, t_ns: int) -> float:
        return t_ns / 1e9 + self.offset_s

    def advance(self, t: float) -> None:
        """on_tick every 10 ms up to ``t`` (Piano.tick, then Harmony.tick with the present piano), the PianoView
        every 0.5 s as __main__ builds it."""
        while self.now + 0.01 <= t + 1e-9:
            self.now = round(self.now + 0.01, 6)
            self._tick()
        self.now = t

    def _tick(self) -> None:
        now = self.now
        self.piano.tick(now)
        self.harmony.tick(now, self.piano if self.piano.present else None, None)
        if abs((now * 100) % 50) < 1e-6:
            self.view = main_mod.Show._build_piano_view(self._pv, now)   # type: ignore[arg-type]

    def packet(self, msg, stamp_s: float) -> None:
        arrival = stamp_s + ARRIVE_S
        self.advance(arrival)
        self.tap.on_midi(STREAM, msg, arrival, self.to_host)
        self.piano.on_midi(msg, arrival, self.to_host)

    def send(self, ev: list, *, drop: bool = False) -> None:
        self.seq += 1
        prev = [(s, e) for s, e in reversed(self.sent[-2:])]
        self.sent.append((self.seq, ev))
        if drop:
            return
        b = vban.parse_midi_events(vban.midi_events_obj(self.seq, ev, prev, sess=self.sess))
        self.packet(b, max(e[0] for e in ev) / 1e9)

    def state(self, t: float, keys: dict[int, int] | None = None, cc64: int = 0) -> None:
        k = [0] * 88
        for p, v in (keys or {}).items():
            k[p - 21] = v
        s = vban.parse_midi_state(vban.midi_state_obj(self.seq, k, cc64, 0, 0, int(round(t * 1e9)), sess=self.sess))
        self.packet(s, t)

    def notes(self, on_: bool | None = None) -> list[dict]:
        return [m["data"] for m in self.msgs if m["type"] == contract.MUSIC_NOTE
                and (on_ is None or m["data"]["on"] is on_)]

    def harmony_frames(self) -> list[dict]:
        return [m["data"] for m in self.msgs if m["type"] == contract.MUSIC_HARMONY]

    def build_view(self) -> contract.PianoView:
        self.view = main_mod.Show._build_piano_view(self._pv, self.now)   # type: ignore[arg-type]
        return self.view


# -- the note table, presence and the bus -----------------------------------------------------------------------------
def test_the_table_and_the_bus_keep_the_sound_times_and_presence_runs_on_arrival():
    r = Rig()
    r.advance(100.0)
    r.send([on(100.0, 60, 90), on(100.0, 64, 80), on(100.0, 67, 70)])
    now = r.now
    assert r.piano.present_at(now)                       # the packet that brings the chord makes it present at once
    assert list(r.piano.held()) == [60, 64, 67]
    assert r.piano.table[[60, 64, 67], T_ON] == pytest.approx([100.6] * 3)
    ons = r.notes(True)
    assert [d["pitch"] for d in ons] == [60, 64, 67]
    assert all(d["t_ms"] == pytest.approx(100600.0) for d in ons)
    assert all(d["t_ms"] - now * 1000.0 == pytest.approx(598.0) for d in ons)    # L ahead of now, minus transport
    r.advance(100.1)
    assert r.piano.present
    r.send([off(100.3, 64, 30)])
    assert list(r.piano.held()) == [60, 67]
    assert r.piano.table[64, RELEASED] == 1.0 and r.piano.table[64, T_OFF] == pytest.approx(100.9)
    offs = r.notes(False)
    assert offs[-1]["pitch"] == 64 and offs[-1]["vel"] == 30 and offs[-1]["t_ms"] == pytest.approx(100900.0)
    # music.notes: t_ms is the newest event's sound time (in the future), keys at their strikes' sound times
    snap = r.piano.notes(r.now)
    assert snap["t_ms"] == pytest.approx(100900.0) and snap["t_ms"] > r.now * 1000.0
    assert [k[0] for k in snap["keys"]] == [60, 67] and snap["keys"][0][2] == pytest.approx(100600.0)
    # levels: a future anchor is clamped (full strike level until it is heard), then the usual decay
    lv_now, lv_heard, lv_later = r.piano.levels(r.now), r.piano.levels(100.6), r.piano.levels(102.6)
    assert lv_now[60] == pytest.approx(lv_heard[60]) and lv_heard[60] > 0.5
    assert lv_later[60] < lv_heard[60]
    assert r.piano.levels(100.9)[64] == pytest.approx(r.piano.levels(r.now)[64])     # released in the future
    assert r.piano.levels(101.9)[64] < 0.01 * r.piano.levels(100.9)[64]               # damped after it is heard


def test_the_voice_hand_over_and_staleness_never_release_before_a_strike():
    r = Rig()
    r.piano.set_voice(False, 99.0)                       # __main__: a new Piano starts muted
    r.advance(100.0)
    r.send([on(100.0, 60, 90)])
    assert r.piano.present_at(r.now)                     # __main__.on_midi gives it the voice within this packet
    r.piano.set_voice(True, r.now)
    assert [d["pitch"] for d in r.notes(True)] == [60]   # the muted strike went out at once, at its sound time
    assert r.notes(True)[0]["t_ms"] == pytest.approx(100600.0)
    r.send([on(100.5, 62, 70), cc(100.5, 64, 127)])
    assert r.piano.pedals["damper"] == 1.0
    r.advance(106.0)                                     # no packet for 5 s: stale, everything released at the tick
    assert not r.piano.held().size
    for p in (60, 62):
        on_t = [d["t_ms"] for d in r.notes(True) if d["pitch"] == p][-1]
        off_t = [d["t_ms"] for d in r.notes(False) if d["pitch"] == p][-1]
        assert off_t > on_t                              # 5 s of staleness > L: never before its strike
    r.advance(117.0)
    assert not r.piano.present                           # the hold (10 s of arrival time) ends as without an offset


def test_the_hot_repeat_cap_and_a_state_heal_work_in_the_shifted_time_base():
    r = Rig()
    r.advance(100.0)
    r.send([on(100.0, 60, 40)])
    r.send([off(100.05, 60)])
    r.send([on(100.1, 60, 127)])                         # a hot repeat: capped at 40 + 32
    assert r.piano.table[60, VEL_ON] == 72 and r.piano.stats()["capped"] == 1
    r.send([on(100.2, 64, 80)], drop=True)               # lost, and no later batch carries it in prev
    r.state(100.5, keys={60: 72, 64: 80})                # the state strikes it at its stamp + L
    assert 64 in r.piano.held() and r.piano.table[64, T_ON] == pytest.approx(101.1)
    r.state(101.0, keys={64: 80})                        # 60 is up: released at 101.0 + L
    assert 60 not in r.piano.held() and r.piano.table[60, T_OFF] == pytest.approx(101.6)
    assert r.piano.stats()["healed_state"] == 2


def test_a_wire_restart_releases_at_arrival_which_can_precede_the_strike():
    """Documented edge: a new sender session mid-note releases what the old one held at the packet's arrival, up to L
    before the strike is heard (only when the sender restarts while a key is down)."""
    r = Rig()
    r.advance(100.0)
    r.send([on(100.0, 60, 90)])
    r.sess = "wire-b"
    r.seq, r.sent = 0, []
    r.send([on(100.2, 62, 80)])
    old_off = [d for d in r.notes(False) if d["pitch"] == 60][-1]
    assert old_off["t_ms"] == pytest.approx(100202.0) and old_off["t_ms"] < 100600.0
    assert old_off["sess"] != r.notes(True)[-1]["sess"]          # under the old bus sess; the new strike under the new


# -- harmony (chord detection) --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("offset_s", [0.0, L_S])
def test_harmony_finds_the_chord_and_the_key_with_future_notes(offset_s):
    r = Rig(offset_s)
    r.advance(100.0)
    r.send([on(100.0, 48, 70), on(100.0, 60, 90), on(100.0, 64, 80), on(100.0, 67, 70)])
    r.advance(100.05)
    first = [h for h in r.harmony_frames() if h["t_ms"] > 100000.0][0]
    c = first["chroma12"]
    assert c[0] == 1.0 and c[4] > 0.3 and c[7] > 0.3 and sum(c) - c[0] - c[4] - c[7] < 1e-6
    assert first["t_ms"] <= 100050.0                     # described at once: with L, before it is heard (100.6 s)
    r.advance(104.0)
    last = r.harmony_frames()[-1]
    assert (last["key"], last["mode"]) == ("C", "major") and last["conf"] > 0.3
    assert last["chord_change"] < 0.05                   # a held chord: no change
    r.send([off(104.0, 48), off(104.0, 60), off(104.0, 64), off(104.0, 67),
            on(104.0, 53, 70), on(104.0, 65, 90), on(104.0, 69, 80), on(104.0, 72, 70)])   # F major
    r.advance(104.2)
    fr = r.harmony_frames()[-1]
    f = fr["chroma12"]
    assert f[5] == 1.0 and f[9] > 0.2                    # F and A lead once the new chord's packet is in
    assert fr["chord_change"] > 0.2                      # the change is detected (L early with the offset)
    # with an offset the old chord still rings until its release is heard (levels clamp the future release)
    if offset_s:
        assert f[0] > 0.2                                # C: the C chord's release is at 104.6, not heard yet
    r.advance(106.0)
    assert r.harmony_frames()[-1]["chroma12"][0] < 0.6   # C has damped once both are heard


# -- Auto's piano words, the PianoView and structure's vouch ---------------------------------------------------------------
@pytest.mark.parametrize("offset_s", [0.0, L_S])
def test_piano_playing_idle_and_the_piano_view_read_as_without_an_offset(offset_s):
    r = Rig(offset_s)
    r.advance(100.0)
    pitches = (60, 62, 64, 65, 67, 69)
    t = 100.0
    for i in range(16):                                  # 16 notes over 7.5 s on 6 pitches
        p = pitches[i % len(pitches)]
        r.send([on(t, p, 70)])
        r.send([off(t + 0.3, p)])
        t = round(t + 0.5, 6)
    r.advance(t)
    reading = r.sit.read("piano_playing", None, r.now)
    assert reading["value"] is True, reading
    assert reading["evidence"]["notes"] == 16 and reading["evidence"]["pitches"] == 6
    r.send([on(t, 72, 90), cc(t, 64, 100)])               # a key and the damper down
    view = r.build_view()
    assert view["present"] is True and view["stream"] == STREAM and view["keys_down"] == 1
    assert view["damper"] > 0.7 and view["notes_per_s"] > 0
    assert view["last_note_age_s"] == 0.0                # clamped while the strike is still in the future...
    assert structure.piano_vouches(r.ctx, 15.0) is True
    r.advance(r.now + 0.01)
    assert r.sit.read("piano_idle", None, r.now)["value"] is False
    r.send([off(r.now + 0.01, 72), cc(r.now + 0.01, 64, 0)])
    r.advance(r.now + 3.0)
    view = r.build_view()
    assert view["keys_down"] == 0 and view["damper"] == 0.0
    assert 1.5 < view["last_note_age_s"] < 3.1           # ...and counts from the sound time once it has passed
    assert r.sit.read("piano_idle", None, r.now)["value"] is True


def test_the_offset_moves_every_bus_time_by_exactly_l():
    """The same performance with and without the offset: identical messages but for t_ms (+L)."""
    runs = []
    for offset_s in (0.0, L_S):
        r = Rig(offset_s)
        r.advance(100.0)
        r.send([on(100.0, 60, 90), cc(100.0, 64, 64)])
        r.send([on(100.4, 64, 127), off(100.45, 60, 20)])
        r.state(100.5, keys={64: 127}, cc64=64)
        r.send([off(101.0, 64), cc(101.0, 64, 0)])
        runs.append([m["data"] for m in r.msgs if m["type"] in (contract.MUSIC_NOTE, contract.MUSIC_PEDAL)])
    a, b = runs
    assert len(a) == len(b) == 6
    for x, y in zip(a, b):
        assert y["t_ms"] - x["t_ms"] == pytest.approx(L_S * 1000.0)
        assert {k: v for k, v in x.items() if k not in ("t_ms", "sess")} == \
               {k: v for k, v in y.items() if k not in ("t_ms", "sess")}
    assert np.isfinite([d["t_ms"] for d in b]).all()
