"""Piano MIDI (show_service/midi.py): wire seq healing, the note table, pedals, presence and the bus shapes
(docs/SHOW_ENGINE.md §3.5, §11.1; README "midi.py")."""

from __future__ import annotations

import math

import numpy as np
import pytest

from show_service import contract, vban
from show_service.midi import (LEVEL, RELEASED, T_OFF, T_ON, TAU_SUS, VEL_ON, Piano)

SESS = "wire-a"


class Bus:
    def __init__(self) -> None:
        self.msgs: list[dict] = []

    def __call__(self, msg: dict) -> None:
        assert set(msg) == {"type", "data"}
        self.msgs.append(msg)

    def of(self, mtype: str) -> list[dict]:
        return [m["data"] for m in self.msgs if m["type"] == mtype]

    def notes_on(self) -> list[int]:
        return [d["pitch"] for d in self.of(contract.MUSIC_NOTE) if d["on"]]

    def notes_off(self) -> list[int]:
        return [d["pitch"] for d in self.of(contract.MUSIC_NOTE) if not d["on"]]


def to_host(t_ns: int) -> float:            # the sender's clock is ours, for these tests
    return t_ns / 1e9


def on(t: float, p: int, v: int = 80, ch: int = 0) -> list[int]:
    return [int(round(t * 1e9)), 0x90 | ch, p, v]


def off(t: float, p: int, v: int = 0, ch: int = 0) -> list[int]:
    return [int(round(t * 1e9)), 0x80 | ch, p, v]


def cc(t: float, n: int, v: int) -> list[int]:
    return [int(round(t * 1e9)), 0xB0, n, v]


def batch(seq: int, ev: list, prev=(), sess: str = SESS) -> vban.MidiBatch:
    b = vban.parse_midi_events(vban.midi_events_obj(seq, ev, prev, sess=sess))
    assert b is not None
    return b


def state(seq: int, t: float, keys: dict[int, int] | None = None, cc64: int = 0, cc66: int = 0, cc67: int = 0,
          sess: str = SESS) -> vban.MidiState:
    k = [0] * 88
    for p, v in (keys or {}).items():
        k[p - 21] = v
    s = vban.parse_midi_state(vban.midi_state_obj(seq, k, cc64, cc66, cc67, int(round(t * 1e9)), sess=sess))
    assert s is not None
    return s


class Player:
    """Sends numbered batches with the last two as ``prev``, the way the sender does."""

    def __init__(self, piano: Piano, sess: str = SESS) -> None:
        self.piano, self.sess, self.seq = piano, sess, 0
        self.sent: list[tuple[int, list]] = []

    def send(self, ev: list, *, drop: bool = False, arrival: float | None = None) -> None:
        self.seq += 1
        prev = [(s, e) for s, e in reversed(self.sent[-2:])]
        self.sent.append((self.seq, ev))
        if not drop:
            t = ev[-1][0] / 1e9 + 0.002 if arrival is None else arrival
            self.piano.on_midi(batch(self.seq, ev, prev, self.sess), t, to_host)


# -- shapes -----------------------------------------------------------------------------------------------------------
def test_note_and_notes_shapes_exact():
    bus = Bus()
    p = Piano("room-piano", bus)
    assert p.source == "live"
    p.on_midi(batch(1, [on(10.0, 60, 90), cc(10.01, 64, 127)]), 10.012, to_host)
    note = bus.of(contract.MUSIC_NOTE)[0]
    assert set(note) == set(contract.NoteData.__annotations__)
    assert note == {"seq": 1, "t_ms": 10000.0, "on": True, "pitch": 60, "vel": 90, "source": "live",
                    "sess": p.sess}
    assert p.sess.startswith("room-piano-") and len(p.sess) == len("room-piano-") + 8
    pedal = bus.of(contract.MUSIC_PEDAL)[0]
    assert set(pedal) == set(contract.PedalData.__annotations__)
    assert pedal == {"seq": 2, "t_ms": 10010.0, "pedal": "damper", "value": 1.0, "source": "live", "sess": p.sess}
    p.tick(10.05)
    notes = bus.of(contract.MUSIC_NOTES)[-1]
    assert set(notes) == set(contract.NotesData.__annotations__)
    assert notes["keys"] == [[60, 90, 10000.0]] and notes["seq"] == 2 and notes["present"] is True
    assert notes["pedals"] == {"damper": 1.0, "soft": 0.0, "sostenuto": 0.0}
    assert notes["t_ms"] == pytest.approx(10050.0)


def test_test_stream_is_marked_test():
    bus = Bus()
    p = Piano("test-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60)]), 1.0, to_host)
    assert p.source == "test" and bus.of(contract.MUSIC_NOTE)[0]["source"] == "test"
    assert Piano("room-piano", bus, source="test").source == "test"


def test_note_on_velocity_zero_is_a_note_off():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 64, 70)]), 1.0, to_host)
    p.on_midi(batch(2, [on(1.2, 64, 0)]), 1.2, to_host)
    offs = [d for d in bus.of(contract.MUSIC_NOTE) if not d["on"]]
    assert [(d["pitch"], d["vel"], d["t_ms"]) for d in offs] == [(64, 0, 1200.0)]
    assert p.table[64, RELEASED] == 1.0 and p.table[64, T_OFF] == pytest.approx(1.2)


def test_t_on_ms_is_exactly_the_notes_t_ms():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(3.1234567, 60)]), 3.13, lambda t: t / 1e9 + 0.0004321)
    t_note = bus.of(contract.MUSIC_NOTE)[0]["t_ms"]
    # the map moves afterwards: the stored strike time must not
    p.on_midi(state(1, 3.5, {60: 80}), 3.6, lambda t: t / 1e9 + 0.05)
    p.tick(3.7)
    p.tick(4.5)
    for n in bus.of(contract.MUSIC_NOTES):
        assert n["keys"][0][2] == t_note


def test_to_host_nan_falls_back_to_arrival():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60)]), 77.5, lambda t: math.nan)
    assert bus.of(contract.MUSIC_NOTE)[0]["t_ms"] == 77500.0


# -- wire seq ---------------------------------------------------------------------------------------------------------
def test_prev_heals_one_lost_batch():
    bus = Bus()
    p = Piano("room-piano", bus)
    pl = Player(p)
    pl.send([on(1.0, 60)])
    pl.send([off(1.1, 60)], drop=True)
    pl.send([on(1.2, 62)])
    assert bus.notes_off() == [60] and bus.notes_on() == [60, 62]
    s = p.stats()
    assert s["healed_prev"] == 1 and s["lost"] == 0
    assert list(p.held()) == [62]


def test_three_lost_batches_prev_heals_two_and_the_state_clears_the_stuck_note():
    bus = Bus()
    p = Piano("room-piano", bus)
    pl = Player(p)
    pl.send([on(1.0, 60)])
    pl.send([off(1.1, 60)], drop=True)          # beyond prev's reach: 60 sticks ...
    pl.send([on(1.2, 62)], drop=True)
    pl.send([on(1.3, 64)], drop=True)
    pl.send([on(1.4, 65)])
    assert p.stats()["lost"] == 1 and p.stats()["healed_prev"] == 2
    assert sorted(p.held()) == [60, 62, 64, 65]
    # ... until the next state (every 0.5 s): it lists the keys physically down
    p.on_midi(state(5, 1.5, {62: 80, 64: 80, 65: 80}), 1.502, to_host)
    assert sorted(p.held()) == [62, 64, 65]
    off60 = [d for d in bus.of(contract.MUSIC_NOTE) if not d["on"]]
    assert [(d["pitch"], d["t_ms"]) for d in off60] == [(60, 1500.0)]
    assert p.stats()["healed_state"] == 1


def test_state_synthesises_missing_note_on_and_pedals():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60)]), 1.0, to_host)
    p.on_midi(state(1, 1.5, {60: 80, 67: 55}, cc64=100, cc67=127), 1.5, to_host)
    assert sorted(p.held()) == [60, 67]
    assert p.table[67, VEL_ON] == 55 and p.table[67, T_ON] == pytest.approx(1.5)
    assert p.pedals["damper"] == pytest.approx(100 / 127) and p.pedals["soft"] == 1.0
    assert {d["pedal"] for d in bus.of(contract.MUSIC_PEDAL)} == {"damper", "soft"}
    # a state that changes nothing synthesises nothing
    n = len(bus.msgs)
    p.on_midi(state(1, 2.0, {60: 80, 67: 55}, cc64=100, cc67=127), 2.0, to_host)
    assert len(bus.msgs) == n


def test_duplicates_and_stale_states_are_ignored():
    bus = Bus()
    p = Piano("room-piano", bus)
    b1 = batch(1, [on(1.0, 60)])
    p.on_midi(b1, 1.0, to_host)
    p.on_midi(b1, 1.01, to_host)
    p.on_midi(batch(2, [off(1.1, 60)], [(1, [on(1.0, 60)])]), 1.1, to_host)
    p.on_midi(state(1, 1.05, {60: 80}), 1.2, to_host)          # older than batch 2: stale
    assert bus.notes_on() == [60] and bus.notes_off() == [60]
    assert p.stats()["old"] == 2 and list(p.held()) == []


def test_sess_restart_releases_and_starts_a_new_bus_sess():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60)], sess="A"), 1.0, to_host)
    p.on_midi(batch(2, [on(1.1, 62)], sess="A"), 1.1, to_host)
    old = p.sess
    p.on_midi(batch(1, [on(5.0, 70)], sess="B"), 5.0, to_host)
    notes = bus.of(contract.MUSIC_NOTE)
    offs = [d for d in notes if not d["on"]]
    assert sorted(d["pitch"] for d in offs) == [60, 62] and all(d["sess"] == old for d in offs)
    assert p.sess != old and notes[-1] == {**notes[-1], "pitch": 70, "on": True, "sess": p.sess}
    assert list(p.held()) == [70] and p.stats()["restarts"] == 1
    # seq keeps increasing across sessions
    seqs = [m["data"]["seq"] for m in bus.msgs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_sessless_restart_seq_1_after_40():
    bus = Bus()
    p = Piano("room-piano", bus)
    for s in range(1, 41):
        p.on_midi(vban.MidiBatch(s, [tuple(on(s * 0.1, 60 + s % 12))], [], None), s * 0.1, to_host)
    old = p.sess
    p.on_midi(vban.MidiBatch(1, [tuple(on(10.0, 30))], [], None), 10.0, to_host)
    assert p.sess != old and 30 in p.held()
    assert p.stats()["restarts"] == 1 and p.stats()["old"] == 0


def test_first_packet_mid_session_applies_prev():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1234, [off(1.2, 60)], [(1233, [on(1.1, 60)])]), 1.21, to_host)
    assert bus.notes_on() == [60] and bus.notes_off() == [60]


# -- the model --------------------------------------------------------------------------------------------------------
def test_hot_note_cap_on_fast_repeats_only():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60, 40), off(1.05, 60)]), 1.06, to_host)
    p.on_midi(batch(2, [on(1.1, 60, 127)]), 1.11, to_host)       # 0.1 s later: absurd jump, capped
    p.on_midi(batch(3, [off(1.2, 60), on(1.6, 60, 127)]), 1.61, to_host)   # 0.5 s later: allowed
    vels = [d["vel"] for d in bus.of(contract.MUSIC_NOTE) if d["on"]]
    assert vels == [40, 72, 127]
    assert p.stats()["capped"] == 1
    # tunable per instance
    q = Piano("room-piano", Bus())
    q.hot_jump = 10
    q.on_midi(batch(1, [on(1.0, 50, 40), off(1.01, 50), on(1.05, 50, 120)]), 1.06, to_host)
    assert q.table[50, VEL_ON] == 50


def test_damper_holds_released_notes_and_damps_without_it():
    p = Piano("room-piano", Bus())
    amp = (100 / 127) ** 1.2
    p.on_midi(batch(1, [on(0.0, 60, 100), off(0.1, 60)]), 0.11, to_host)
    assert p.levels(0.1)[60] == pytest.approx(amp * math.exp(-0.1 / TAU_SUS[60]))
    assert p.levels(0.6)[60] == pytest.approx(amp * math.exp(-0.1 / TAU_SUS[60]) * math.exp(-0.5 / 0.12))
    q = Piano("room-piano", Bus())
    q.on_midi(batch(1, [cc(0.0, 64, 127), on(0.01, 60, 100), off(0.1, 60)]), 0.11, to_host)
    assert q.levels(0.6)[60] == pytest.approx(amp * math.exp(-0.59 / TAU_SUS[60]))
    # damper up at 1.0: from then on it damps fast
    q.on_midi(batch(2, [cc(1.0, 64, 0)]), 1.01, to_host)
    at1 = amp * math.exp(-0.99 / TAU_SUS[60])
    assert q.levels(1.0)[60] == pytest.approx(at1)
    assert q.levels(1.3)[60] == pytest.approx(at1 * math.exp(-0.3 / 0.12))
    # half damper: in between
    r = Piano("room-piano", Bus())
    r.on_midi(batch(1, [cc(0.0, 64, 64), on(0.01, 60, 100), off(0.1, 60)]), 0.11, to_host)
    assert q.levels(0.6)[60] > r.levels(0.6)[60] > p.levels(0.6)[60]


def test_held_and_ringing_decay_by_pitch():
    p = Piano("room-piano", Bus())
    p.on_midi(batch(1, [on(0.0, 36, 100), on(0.0, 84, 100)]), 0.01, to_host)
    lv = p.levels(1.0)
    assert lv[36] > lv[84] > 0.0                      # higher pitches decay faster
    assert TAU_SUS[21] == pytest.approx(5.0) and TAU_SUS[108] >= 0.3


def test_sostenuto_latches_keys_down_at_press():
    p = Piano("room-piano", Bus())
    p.on_midi(batch(1, [on(0.0, 48, 100), cc(0.1, 66, 127), off(0.2, 48), on(0.3, 60, 100), off(0.4, 60)]),
              0.41, to_host)
    lv = p.levels(1.0)
    assert lv[48] == pytest.approx((100 / 127) ** 1.2 * math.exp(-1.0 / TAU_SUS[48]))
    assert lv[60] < 0.01                              # struck after the press: damped
    p.on_midi(batch(2, [cc(1.0, 66, 0)]), 1.01, to_host)
    assert p.levels(1.5)[48] == pytest.approx(lv[48] * math.exp(-0.5 / 0.12))


def test_top_keys_have_no_dampers():
    p = Piano("room-piano", Bus())
    p.on_midi(batch(1, [on(0.0, 96, 100), off(0.05, 96)]), 0.06, to_host)
    assert p.levels(0.5)[96] == pytest.approx((100 / 127) ** 1.2 * math.exp(-0.5 / TAU_SUS[96]))


def test_soft_pedal_shades_levels_not_velocities():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [cc(0.0, 67, 127), on(0.01, 60, 100)]), 0.02, to_host)
    assert p.table[60, LEVEL] == pytest.approx(0.75 * (100 / 127) ** 1.2)
    assert bus.of(contract.MUSIC_NOTE)[0]["vel"] == 100


def test_levels_include_restrike_max_and_silence():
    p = Piano("room-piano", Bus())
    assert not p.levels(0.0).any()
    p.on_midi(batch(1, [on(0.0, 60, 127), on(0.05, 60, 30)]), 0.06, to_host)
    assert p.levels(0.05)[60] == pytest.approx(math.exp(-0.05 / TAU_SUS[60]))   # the louder ringing note wins


# -- channels, presence, staleness, rate ------------------------------------------------------------------------------
def test_all_channels_feed_one_keyboard_by_default():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60, ch=1)]), 1.0, to_host)
    assert bus.notes_on() == [60]


def test_replay_channel_can_be_ignored():
    bus = Bus()
    p = Piano("room-piano", bus, ignore_channels=(2,))
    p.on_midi(batch(1, [on(1.0, 60, ch=1), off(1.1, 60, ch=1)]), 1.1, to_host)   # channel 2: the box replaying
    p.tick(1.2)
    assert bus.of(contract.MUSIC_NOTE) == [] and p.present is False and p.stats()["ignored"] == 2
    p.on_midi(batch(2, [on(1.3, 62)]), 1.3, to_host)                             # channel 1: the keys
    p.tick(1.4)
    assert bus.notes_on() == [62] and p.present is True


def test_state_channels_keep_the_playback_channel_held():
    """The kiosk sender's state holds channel 1's keys only: with state_channels=(1,) a channel-2 (playback) note
    and pedal survive it, while a stuck channel-1 note is still cleared. The default (None) heals every channel."""
    for state_channels, released in (((1,), [61]), (None, [61, 64])):
        bus = Bus()
        p = Piano("room-piano", bus, state_channels=state_channels)
        p.on_midi(batch(1, [on(1.0, 61), on(1.0, 64, ch=1), [int(1e9), 0xB1, 64, 127]]), 1.01, to_host)
        p.on_midi(state(1, 1.5, keys={}, cc64=0), 1.5, to_host)        # 61 "stuck" (its note-off was lost)
        assert bus.notes_off() == released, state_channels
        assert p.pedals["damper"] == (1.0 if state_channels else 0.0)
        p.on_midi(state(1, 2.0, keys={60: 70}), 2.0, to_host)            # a state-synthesised strike ...
        p.on_midi(state(1, 2.5, keys={}), 2.5, to_host)                  # ... is the state's to clear
        assert 60 in bus.notes_on() and bus.notes_off()[-1] == 60


def test_present_falls_with_a_resting_damper_and_idle_states():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(0.0, 60), off(0.2, 60)]), 0.21, to_host)
    p.tick(0.3)
    assert p.present
    t, seen = 0.5, {}
    while t < 12.0:                                   # 2 Hz states, damper resting at 3 counts
        p.on_midi(state(1, t, {}, cc64=3), t + 0.002, to_host)
        for dt in (0.0, 0.25):
            p.tick(t + 0.01 + dt)
            seen[round(t + 0.01 + dt, 2)] = p.present
        t += 0.5
    assert seen[10.26] is True and seen[10.51] is False and seen[11.76] is False   # 10 s after the 0->3 change
    p.on_midi(state(1, 12.0, {}, cc64=40), 12.002, to_host)                     # a pedal down holds it up
    p.tick(12.01)
    assert p.present
    notes = bus.of(contract.MUSIC_NOTES)
    assert any(n["present"] for n in notes) and any(not n["present"] for n in notes)


def test_no_packets_for_5_s_releases_everything():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(1.0, 60), on(1.0, 64)]), 1.0, to_host)
    p.tick(5.9)
    assert p.present and bus.notes_off() == []
    p.tick(6.1)
    offs = [d for d in bus.of(contract.MUSIC_NOTE) if not d["on"]]
    assert sorted(d["pitch"] for d in offs) == [60, 64] and all(d["vel"] == 0 for d in offs)
    assert p.present is False and p.stats()["stale_releases"] == 2
    assert bus.of(contract.MUSIC_NOTES)[-1]["keys"] == []


def test_notes_rate_fast_after_a_change_then_slow():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(0.0, 60)]), 0.0, to_host)
    for i in range(1, 400):                           # 4 s of 10 ms ticks
        p.tick(i * 0.01)
    times = [n["t_ms"] / 1000.0 for n in bus.of(contract.MUSIC_NOTES)]
    early = [t for t in times if t < 1.0]
    late = [t for t in times if t > 1.5]
    assert 8 <= len(early) <= 11
    gaps = np.diff(late)
    assert np.all(gaps > 0.45) and np.all(gaps < 0.55)
    assert all(n["keys"] == [[60, 80, 0.0]] for n in bus.of(contract.MUSIC_NOTES))


def test_nothing_published_before_any_packet():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.tick(1.0)
    assert bus.msgs == [] and p.sess == ""


# -- voice: one note source on the bus --------------------------------------------------------------------------------
def test_an_idle_piano_goes_quiet_after_its_present_false_frame():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.on_midi(batch(1, [on(0.0, 60), off(0.1, 60)]), 0.11, to_host)
    t = 0.2
    while t < 20.0:                                   # the kiosk sender's 2 Hz states, nobody playing
        p.on_midi(state(1, t, {}), t + 0.002, to_host)
        p.tick(t + 0.01)
        p.tick(t + 0.26)
        t += 0.5
    notes = bus.of(contract.MUSIC_NOTES)
    flip = max(i for i, n in enumerate(notes) if n["present"]) + 1
    assert notes[flip]["present"] is False and flip == len(notes) - 1        # the flip frame, then nothing
    assert notes[flip]["t_ms"] < 10.6e3


def test_a_muted_piano_keeps_its_table_and_publishes_nothing():
    bus = Bus()
    p = Piano("test-piano", bus)
    p.set_voice(False, 0.0)
    p.on_midi(batch(1, [on(1.0, 60, 90), on(1.0, 64, 70), cc(1.0, 64, 127)]), 1.01, to_host)
    for i in range(50):
        p.tick(1.02 + 0.01 * i)
    assert bus.msgs == [] and p.present and p.stats()["muted"] == 3
    assert sorted(p.held().tolist()) == [60, 64] and p.levels(1.5)[60] > 0
    p.set_voice(True, 1.6)                            # gains the voice: one snapshot at once, then the usual cadence
    assert [m["type"] for m in bus.msgs] == [contract.MUSIC_NOTES]
    snap = bus.of(contract.MUSIC_NOTES)[0]
    assert snap["keys"] == [[60, 90, 1000.0], [64, 70, 1000.0]] and snap["present"] and snap["sess"] == p.sess
    assert snap["pedals"]["damper"] == 1.0 and snap["seq"] == p.seq
    p.set_voice(False, 1.7)
    p.on_midi(batch(2, [off(1.8, 60)]), 1.81, to_host)
    p.tick(2.5)
    assert len(bus.msgs) == 1


def test_gaining_the_voice_in_the_same_packet_publishes_that_strike():
    bus = Bus()
    p = Piano("room-piano", bus)
    p.set_voice(False, 0.0)
    p.on_midi(batch(1, [on(1.0, 60, 90)]), 1.01, to_host)
    assert bus.msgs == [] and p.present_at(1.01) and not p.present
    p.set_voice(True, 1.01)                           # what __main__ does right after this packet
    assert [m["type"] for m in bus.msgs] == [contract.MUSIC_NOTE, contract.MUSIC_NOTES]
    note, snap = bus.of(contract.MUSIC_NOTE)[0], bus.of(contract.MUSIC_NOTES)[0]
    assert note["on"] and note["pitch"] == 60 and note["t_ms"] == 1000.0 and note["sess"] == snap["sess"] == p.sess
    assert snap["keys"] == [[60, 90, 1000.0]] and snap["present"] is True
