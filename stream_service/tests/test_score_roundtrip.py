"""Roundtrip: MIDI played into the live score (ScoreBuilder, fed frame by frame as live_score._listen feeds it, with
the real analysis worker when its venv and weights are there) -> bars -> what a reader sees on the page.

Simple material must come out musically right (owner, 17 Oct: the scales test file produced mad scores):
  * every played note written once, in the order played (none lost or doubled across bar lines or rewrites);
  * spelled in the key: the key signature is the scale's, and no diatonic note carries an accidental;
  * one note value from the first note to the last (an even run of eighths may be read as quarters at twice the
    tempo, or 16ths as eighths, but never changes value mid-phrase);
  * readable staves: no note more than 3 ledger lines off its staff, no note on the other staff from both its
    neighbours in a one-hand run;
  * a simple duple metre (2/4 or 4/4).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON, phrase
from stream_service.score.client import Analyser
from stream_service.score.mei import signature, spell
from stream_service.score.quantize import BeatGrid, ScoreBuilder

FPS = 30.0
LATENCY_S = 0.6                       # a reply lands this long after its request (the worker takes 0.3-1.5 s)
MAJOR = (0, 2, 4, 5, 7, 9, 11)
FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 5: -1, 10: -2, 3: -3}     # major tonic pitch class -> key signature


# -- material -------------------------------------------------------------------------------------------------------
def scale(tonic: int, octaves: int, value_beats: float, bpm: float, hands: str = "rh", loops: int = 1,
          gap_s: float = 3.0) -> tuple[list, list]:
    """A major scale from MIDI pitch ``tonic`` up ``octaves`` and back down, evenly in ``value_beats`` (0.5 =
    eighths), legato-ish (90 % of each value); ``hands`` "both" adds the octave below.  Looped like the test piano
    (a gap shorter than the phrase's end silence).  -> (MIDI events, the pitches in played order)."""
    beat = 60.0 / bpm
    up = [tonic + 12 * (i // 7) + MAJOR[i % 7] for i in range(7 * octaves + 1)]
    run = up + up[-2::-1]
    ev, played, t = [], [], 0.0
    for _ in range(loops):
        for i, p in enumerate(run):
            on = t + i * value_beats * beat
            for q in ([p - 12, p] if hands == "both" else [p]):     # low first, as read() orders a chord
                ev += [(on, NOTE_ON, q, 70), (on + value_beats * beat * 0.9, NOTE_OFF, q, 40)]
                played.append(q)
        t += len(run) * value_beats * beat + gap_s
    return sorted(ev), played


# -- the live score's input loop, offline ---------------------------------------------------------------------------
class SyncAnalyser:
    """The real analysis worker (client.Analyser: its venv, pipes and protocol), answering in simulated time: each
    reply is waited for, then delivered LATENCY_S after its request."""

    def __init__(self) -> None:
        self.worker = Analyser()

    def ask(self, req: dict) -> dict:
        self.worker.submit("a", req)
        _key, meta, _ = self.worker.out.get(timeout=60)
        assert meta.get("ok"), meta.get("error")
        return meta

    def close(self) -> None:
        self.worker.close()


def transcribe(events: list, analyser: SyncAnalyser | None, tail_s: float = 8.0) -> list:
    """Feed the events frame by frame (onsets and releases at their own times, as sound_view's ages give them);
    -> the bars a reader ends up with (a rewrite replaces the phrase's bars so far, as live_score does), each with
    the key signature it was engraved in."""
    sb = ScoreBuilder()
    if analyser is not None:
        sb.use_analysis(True)
    evs = sorted(events)
    end = evs[-1][0] + tail_s
    out, pending, i, t = [], [], 0, 0.0
    while t <= end:
        while i < len(evs) and evs[i][0] <= t:
            te, st, d1, d2 = evs[i]
            if st == NOTE_ON and d2 > 0:
                sb.note_on(d1, d2 / 127.0, te)
            elif st in (NOTE_ON, NOTE_OFF):
                sb.note_off(d1, te)
            elif st == CC and d1 == 64:
                sb.pedal(d2 / 127.0, te)
            i += 1
        if analyser is not None:
            req = sb.analysis_request(t)
            if req is not None:
                pending.append((t + LATENCY_S, analyser.ask(req)))
            for due in [p for p in pending if p[0] <= t]:
                pending.remove(due)
                sb.on_analysis(due[1])
        for bar in sb.poll(t):
            if bar.rewrite:
                while out and not out[-1][0].marker:
                    out.pop()
                continue
            out.append((bar, sb.key()[2]))
        t += 1.0 / FPS
    return out


@dataclass
class Written:
    at: int            # 16ths from the start of the score
    pitch: int
    staff: int         # 0 treble, 1 bass
    dur: int           # 16ths, tied pieces summed
    fifths: int
    meter: tuple


def read(bars: list) -> list[Written]:
    """The notes as a reader sees them: each note once at its onset, its written value summed over its ties."""
    notes: list[Written] = []
    open_: dict[int, Written] = {}
    base = 0
    for bar, fifths in bars:
        if bar.marker:
            continue
        for staff, events in enumerate(bar.staves):
            for e in events:
                for p, nid, tin in zip(e.pitches, e.ids, e.tie_in):
                    if tin and nid in open_:
                        open_[nid].dur += e.dur
                    else:
                        w = Written(base + e.start, p, staff, e.dur, fifths, bar.meter)
                        notes.append(w)
                        open_[nid] = w
        base += bar.units
    return sorted(notes, key=lambda w: (w.at, w.pitch))


def ledger_lines(pitch: int, staff: int) -> int:
    """Ledger lines a note needs on its staff (treble E4-F5, bass G2-A3)."""
    step = lambda m: (m // 12) * 7 + (0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6)[m % 12]  # noqa: E731
    lo, hi = (64, 77) if staff == 0 else (43, 57)
    s = step(pitch)
    return max(0, (s - step(hi)) // 2, (step(lo) - s) // 2)


def assert_musical(notes: list[Written], played: list[int], fifths: int, value16: int, one_hand: bool) -> None:
    assert [w.pitch for w in notes] == played, "notes lost, doubled or out of order"
    assert {w.fifths for w in notes} == {fifths}, f"key signature {sorted({w.fifths for w in notes})}, want {fifths}"
    for w in notes:
        letter, alt, _ = spell(w.pitch, w.fifths, signature(w.fifths))
        assert alt == signature(w.fifths)[letter], f"pitch {w.pitch} spelled with an accidental in {w.fifths}"
    onsets = sorted({w.at for w in notes})
    steps = {b - a for a, b in zip(onsets, onsets[1:]) if b - a <= 4 * value16}   # the gaps between loops aside
    assert len(steps) == 1, f"note values change mid-phrase: onsets {sorted(steps)} 16ths apart"
    (step,) = steps
    assert step in (value16 // 2 or value16, value16, 2 * value16), f"a {value16}-16ths run written {step} apart"
    assert {w.dur for w in notes} == {step}, f"durations {sorted({w.dur for w in notes})}, want {step}"
    worst = max(ledger_lines(w.pitch, w.staff) for w in notes)
    assert worst <= 3, f"a note {worst} ledger lines off its staff"
    if one_hand:
        flips = [b.pitch for a, b, c in zip(notes, notes[1:], notes[2:]) if b.staff != a.staff and b.staff != c.staff]
        assert not flips, f"notes {flips} jump to the other staff inside a run"
    assert {w.meter for w in notes} <= {(2, 4), (4, 4)}, f"metres {sorted({w.meter for w in notes})}"


# -- with the analysis worker (how the scene runs) -----------------------------------------------------------------
@pytest.fixture(scope="module")
def analyser():
    if not Analyser.available():
        pytest.skip("the analysis venv (~/.venvs/sm-score-ml) or PM2S weights aren't installed")
    a = SyncAnalyser()
    yield a
    a.close()


CASES = [
    # id, tonic, octaves, value (beats), bpm, hands, loops
    ("C-major-8ths", 60, 1, 0.5, 100, "rh", 1),
    ("G-major-8ths", 55, 2, 0.5, 100, "rh", 1),
    ("D-major-8ths", 62, 1, 0.5, 100, "rh", 1),
    ("F-major-8ths", 53, 2, 0.5, 100, "rh", 1),
    ("Bb-major-8ths", 58, 1, 0.5, 100, "rh", 1),
    ("C-major-quarters-80", 60, 2, 1.0, 80, "rh", 1),
    ("C-major-8ths-120", 60, 2, 0.5, 120, "rh", 1),
    ("C-major-16ths-90", 60, 2, 0.25, 90, "rh", 1),
    ("C-major-16ths-66", 60, 2, 0.25, 66, "rh", 1),
    ("C-major-left-hand", 36, 2, 0.5, 100, "lh", 1),
    ("C-major-octaves", 60, 1, 0.5, 96, "both", 1),
    ("G-major-looped", 55, 2, 0.5, 100, "rh", 2),
]


@pytest.mark.parametrize("name,tonic,octaves,value,bpm,hands,loops", CASES, ids=[c[0] for c in CASES])
def test_scales_come_out_musically_right(analyser, name, tonic, octaves, value, bpm, hands, loops):
    ev, played = scale(tonic, octaves, value, bpm, hands, loops)
    notes = read(transcribe(ev, analyser))
    assert_musical(notes, played, FIFTHS[tonic % 12], int(round(value * 4)), hands != "both")


def test_the_test_pianos_scale_phrase_looped(analyser):
    """The built-in `scale` pattern (two octaves of C major from C3 in eighths at 110, a crescendo), looped as the
    test piano plays it: once it was written on the bass staff up to C5, and at double time from the second pass."""
    once = phrase("scale")
    length = max(t for t, *_ in once)
    ev = [(t + k * (length + 3.0), s, a, b) for k in range(3) for t, s, a, b in once]
    played = [a for _t, s, a, b in sorted(ev) if s == NOTE_ON and b > 0]
    assert_musical(read(transcribe(ev, analyser)), played, 0, 2, True)


# -- without it (the fallback beat clock) --------------------------------------------------------------------------
@pytest.mark.parametrize("tonic,value,bpm", [(60, 0.5, 100), (55, 0.5, 90), (53, 1.0, 80)])
def test_scales_without_the_analysis(tonic, value, bpm):
    ev, played = scale(tonic, 2 if tonic < 60 else 1, value, bpm)
    assert_musical(read(transcribe(ev, None)), played, FIFTHS[tonic % 12], int(round(value * 4)), True)


# -- the pieces that went wrong -------------------------------------------------------------------------------------
def test_merge_keeps_our_phase_and_level():
    """A reply half a beat out of phase, counted at twice our tempo, or in threes against our twos: spliced as it
    came, each once wrote the rest of the phrase at double time.  The written beats never move."""
    ours = [i * 0.5 for i in range(12)]
    for theirs in ([0.25 + i * 0.5 for i in range(18)],            # half a beat off ours, over the same playing
                   [3.0 + i * 0.25 for i in range(24)],            # double time
                   [3.0 + i * 0.75 for i in range(8)]):            # three of our half-beats per beat
        g = BeatGrid(ours, 0)
        g.merge(theirs, keep_beat=6)
        assert g.t[:7] == ours[:7]
        d = [b - a for a, b in zip(g.t, g.t[1:])]
        assert all(abs(x - 0.5) < 0.13 for x in d), d


def test_merge_never_drops_the_written_beats():
    """The gap filler only ever looked at the new beats' spacing; it must never thin out the ones bars sit on."""
    ours = [0.0, 0.45, 0.9, 1.14, 1.36, 1.59, 1.82, 2.05, 2.27]   # a reading that changed level, already written
    g = BeatGrid(ours, 0)
    g.merge([2.73 + i * 0.45 for i in range(6)], keep_beat=7)
    assert g.t[:8] == ours[:8]


def test_a_one_hand_run_reads_by_register_not_the_hand_model():
    """The hand model calls a one-hand scale all left hand: C3..C5 then sat on the bass staff, C5 four ledger
    lines up.  A single line goes by register, keeps its staff within 2 ledger lines, and never flickers."""
    sb = ScoreBuilder()
    run = [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71, 72]
    ids = []
    for i, p in enumerate(run):
        ids.append(sb.note_on(p, 0.6, i * 0.3))
        sb.note_off(p, i * 0.3 + 0.27)
    sb.hand = {i: 1 for i in ids}                                 # all "left hand"
    staves = [sb._staff(n, sb._alone(n, sb.notes, 10.0), None) for n in sb.notes]
    assert all(s == 0 for p, s in zip(run, staves) if p >= 60) and all(s == 1 for p, s in zip(run, staves) if p < 60)


def test_a_phrases_last_note_keeps_its_value():
    """The last eighth of a run isn't stretched to the bar line (that rule is for staccato before a next note)."""
    bar = ScoreBuilder._voice([(0, 2, 60, 1, False, False), (2, 4, 62, 2, False, False)], 4, 4, None)
    assert [(e.start, e.dur, bool(e.pitches)) for e in bar][:3] == [(0, 2, True), (2, 2, True), (4, 4, False)]
    joined = ScoreBuilder._voice([(12, 14, 60, 1, False, False)], 4, 4, 16)    # the next note at the next bar
    assert [(e.start, e.dur) for e in joined if e.pitches] == [(12, 4)]


def test_a_short_note_just_before_a_bar_line_is_written():
    """A note whose onset rounds up onto the bar line belongs to the next bar; when its release rounded there too,
    the bar before forgot it and the next never saw it (a few % of real playing went unwritten)."""
    sb = ScoreBuilder()
    t, beat = 0.0, 0.5
    for i in range(16):                                        # steady quarters at 120: the clock locks on
        sb.note_on(60 + i % 3, 0.6, i * beat)
        sb.note_off(60 + i % 3, i * beat + 0.4)
    grace = 8 * beat - 0.04                                    # a 40 ms grace just before bar 3's line, 30 ms long
    sb.note_on(72, 0.6, grace)
    sb.note_off(72, grace + 0.03)
    bars = []
    while t < 20.0:
        bars += sb.poll(t)
        t += 0.02
    written = [p for b in bars for st in b.staves for e in st for p, tin in zip(e.pitches, e.tie_in) if not tin]
    assert written.count(72) == 1 and len(written) == 17


def test_real_timing_is_left_alone():
    """The level clean-up undoes only a sustained change of level (it once replaced every beat off by a quarter with
    a rigid one, and real pieces drifted off their notes): single stretched or squeezed beats stand."""
    from stream_service.score.quantize import _same_level
    rubato = [0.0, 0.5, 1.15, 1.6, 2.1, 2.55, 3.3, 3.8, 4.25]      # beats stretched and squeezed by up to a half
    assert _same_level(rubato, 0.5) == rubato
    doubled = [0.0, 0.5, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]           # a stretch counted twice as fast
    assert _same_level(doubled, 0.5) == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]


def test_accented_playing_is_never_metreless():
    """Even onsets with a beat's accents carry a metre: the analysis decides it, not common time by default."""
    sb = ScoreBuilder()
    for i in range(24):
        sb.note_on(60 + i % 5, 0.9 if i % 3 == 0 else 0.5, i * 0.3)    # accented in threes
        sb.note_off(60 + i % 5, i * 0.3 + 0.27)
    assert not sb._metreless()
    even = ScoreBuilder()
    for i in range(24):
        even.note_on(60 + i % 5, 0.4 + 0.01 * i, i * 0.3)              # a plain crescendo
        even.note_off(60 + i % 5, i * 0.3 + 0.27)
    assert even._metreless()


def test_bars_written_past_the_last_beat_keep_their_bar_lines():
    """A bar written on the grid's extrapolation (past the analysis's last beat) froze nothing: the next reply put its
    beats elsewhere and the notes between the two bar lines were never written."""
    g = BeatGrid([i * 0.5 for i in range(9)], 0)                # beats to 4.0 s
    line = g.time(12)                                          # bar 3's end, written at 6.0 s on the extrapolation
    g.merge([4.0 + i * 0.47 for i in range(12)], keep_beat=12)
    assert g.time(12) == line


def test_the_note_glow_asks_where_a_note_is_written():
    """live_score places each note's glow by builder._staff(note): it must say what the bar will say."""
    sb = ScoreBuilder()
    ids = []
    for i, p in enumerate((60, 62, 64, 65, 67, 69, 71, 72)):
        ids.append(sb.note_on(p, 0.6, i * 0.3))
        sb.note_off(p, i * 0.3 + 0.27)
    sb.hand = {i: 1 for i in ids}                              # the hand model: all "left hand"
    assert all(sb._staff(n) == 0 for n in sb.notes)            # a single line from middle C up: treble, as written
