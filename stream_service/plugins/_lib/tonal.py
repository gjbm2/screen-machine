"""Tonal maths for the harmony-driven plug-ins: what is sounding, in which key, which chord, and how far from home.

Host side, pure numpy, deterministic.  Everything here is 12-element maths: a per-frame call costs a few
microseconds, the stateful classes preallocate their arrays, and nothing touches the GPU.  Plug-ins read the notes
table of :class:`stream_service.sound_view.SoundFrame` (``frame.notes[128, 4]``, column LEVEL) and the room's key
(``frame.key`` / ``frame.mode``), never the 10 Hz ``frame.chroma``, which is not photon-aligned.

Conventions used throughout
  pc            pitch class 0..11, C = 0 (MIDI pitch % 12).
  key index     0..11 = C..B major, 12..23 = C..B minor (as show_service/harmony.py).  ``-1`` = none.
  triad id      the same layout for chords: root pc, + 12 for a minor triad (the 24 Tonnetz / Spiral triads).
  line of fifths (lof)  an unbounded integer k: C 0, G 1, D 2, F -1, Bb -2, F# 6, Gb -6.  ``k = 7 pc (mod 12)``
                picks the pitch class; the multiple of 12 picks the spelling (F# 6 vs Gb -6).  A key's tonic is
                spelled with the fewest accidentals (majors Db..F#, minors Bb..D#).
  fifths_of(x)  an interval of x semitones as a signed number of fifths, -5..+6: V +1, ii +2, vi +3, IV -1, bVII -2.
  time          seconds; every stateful ``update`` takes the frame's ``dt`` and is safe at dt = 0 (no change) and at
                any large dt (it settles, it never overshoots).  Negative or non-finite dt counts as 0.

What is here
  * Pitch-class energy.  ``pc_energy``: the sum of LEVEL per pitch class (a held chord of three equal notes is
    three 1s).  ``bass_pitch``: the lowest pitch sounding above a floor (chord readers weight its pc x1.5).
    ``loudest``: the n loudest sounding pitches, bass first.  ``AttackRelease``: a per-element exponential easer,
    rising with time constant ``attack_s`` and falling with ``release_s`` (alpha = 1 - exp(-dt / tau)).
  * Key.  ``KeyTracker`` is show_service/harmony.py's key finder re-run on the plug-in's own notes, with the SAME
    constants and hysteresis, so it agrees with the room: the max-normalised pc energy is averaged (EMA, tau
    KEY_TAU_S 8 s), Pearson-correlated with the 24 Krumhansl-Kessler (1982) profiles (``KK_Z @ zscore``), and the
    best key is taken once it leads the runner-up by KEY_MARGIN (0.05 in r) for KEY_FIRST_S (1 s) with r >
    KEY_MIN_R (0.3); later it changes only when another key beats it by KEY_MARGIN for KEY_HOLD_S (2 s).
    ``conf`` = clamp(r) x clamp((r - best other r) / 0.1).  ``KeyResolver`` is what a plug-in uses: the room's key
    (``frame.key``/``frame.mode``) when it has one, else its own tracker's, else C major (``known`` False); a new
    key must be proposed for ``hold_s`` (0.5 s) before it is taken, and each change is reported once as a
    :class:`KeyChange` (fifths the tonic moved, the shortest way, -5..+6).
  * Spelling.  ``spell(pc, ref)``: the line-of-fifths index of ``pc`` nearest ``ref`` (Chew & Chen 2005), so a
    plug-in that keeps ``ref`` as an EMA of recent positions spells Ab vs G# from context.  ``lof_name`` names it
    ('Dbb', 'F#', 'Ex' for a double sharp).
  * Chew's Spiral Array (Chew 2000; Herremans & Chew 2016).  Pitch k sits at P(k) = (sin k pi/2, cos k pi/2, k h),
    h = sqrt(2/15), a helix rising one fifth per quarter turn.  A major triad is C_M(k) = w1 P(k) + w2 P(k+1)
    + w3 P(k+4), a minor C_m(k) = w1 P(k) + w2 P(k+1) + w3 P(k-3), with w = (0.536, 0.274, 0.19).  A major key is
    T_M(k) = w1 C_M(k) + w2 C_M(k+1) + w3 C_M(k-1); a minor key mixes its major and minor IV and V chords by
    alpha = beta = 0.75.  The centre of effect CE = sum e P(k) / sum e is where the sounding notes pull.  Tensile
    strain S = |X - T_key| / N0 with N0 = |P(0) - P(12)| = 12 h; bearing beta = (X.z - z of the tonic triad) / h,
    in fifths (+ sharp side).  In C major (S / beta): C 0.08/0, G 0.20/+1, Am 0.22/+1.7, F 0.23/-1, Dm 0.26/+0.7,
    D 0.32/+2, F# 0.56/+6.  ``SpiralKey`` holds one key's point, its 24 triad stations and these measures.
  * Chords.  ``ChordReader`` scores every root x quality template against the pc energy (bass pc weighted) and
    changes chord with hysteresis: a challenger must score at least ``min_score`` and lead the current chord by
    ``lead`` for ``hold_s`` without a break (and the caller's ``allow`` gate must be open).  Two scorings:
    ``"cosine"`` (templates root 1.0, third 0.9, fifth 0.8, seventh 0.7; cosine similarity; conf = kappa =
    smoothstep(0.75, 0.95, cos)) and ``"score"`` (in-template energy - 0.6 x out-of-template energy - 0.05 per
    tone, + 0.3 when the bass is the root, + 0.1 for a diatonic root; c max-normalised).  Augmented and diminished
    sevenths have no audible root: of the equal rotations the reader keeps the one nearest the previous root on
    the line of fifths.  ``roman`` names a chord in a key (I, bVI, ii°, viiø7, V7/V, vii°7/V), ``inversion`` gives
    its figure ('6', '6/4', '6/5', '4/3', '4/2'), ``function_class`` its function (T, PD, D, chromatic) and
    ``resolution`` whether a dominant's next root obeyed (down a fifth) or deceived (up a step: V -> vi, V -> VI).
  * Pitch-class DFT (Amiot; Yust).  F_k = sum_n c_n exp(-2 pi i k n / 12), k = 0..6 (the rest mirror: F_12-k =
    conj F_k).  |F_k| measures how well the set divides the octave into k parts; arg F_k turns 30 deg x k per
    semitone of transposition.  ``symmetry`` gives sigma_n for n = 2, 3, 4, 6: the share of non-DC energy on
    multiples of n, rescaled so chance is 0 and a set unchanged by transposition by 12/n semitones is 1 (sigma_n =
    clamp((s_n - b_n) / (1 - b_n)), b_n = (12/n - 1) / 11).  Triads give sigma3 0.19, dim7 sigma2 = sigma4 = 1,
    augmented sigma3 1, whole-tone sigma2 = sigma3 = sigma6 = 1, a tritone sigma2 1.  |F5| / sum c is diatonicity
    (a major scale 0.53) and arg F5 its place on the circle of fifths.
  * Tonnetz (Euler; Cohn's neo-Riemannian P, L, R).  Node (a, b) has pc 7a + 4b (fifths along a, major thirds
    along b) and spelling a + 4b on the line of fifths.  The up triangle at (a, b) is the major triad on pc(a, b);
    the down triangle {(a+1, b), (a, b+1), (a+1, b+1)} the minor triad on pc(a, b) + 4.  P keeps root and fifth
    (C <-> Cm), R root and third (C <-> Am), L third and fifth (C <-> Em); each is a flip of the triangle over the
    shared edge.  ``PLR_DIST``/``PLR_NEXT`` are the BFS tables on the 24 triads (from C: Am, Cm, Em 1; F# 4; Bbm 5).
    The lattice repeats every (4, -1) and (0, 3); a copy of a triangle offset by (da, db) is da x 701.955 + db x
    386.314 cents away in just intonation (``lattice_cents``: (-4, 1) is the syntonic comma, -21.5 c).
  * Small shared pieces: ``smoothstep``, ``smootherstep``, ``ema_alpha``, ``midi_hz`` (equal temperament, A4 440 Hz)
    and ``RingLog`` (a fixed-size numpy ring of records: chords, onsets).
"""
from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple, Optional, Sequence, Union

import numpy as np

LEVEL = 0                                  # stream_service.sound_view column of SoundFrame.notes
N_PITCH = 128
SILENT = 1e-6                              # show_service/harmony.py's silence threshold
PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")    # = show_service KEY_NAMES (sharps)
KEY_NAMES = PC_NAMES
MODES = ("major", "minor")

# -- show_service/harmony.py, copied (plug-ins never import show_service) ------------------------------------------
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 9, 10, 11)        # natural minor + raised 6th and 7th (melodic / harmonic)
KEY_TAU_S = 8.0
KEY_MIN_R = 0.3
KEY_MARGIN = 0.05
KEY_FIRST_S = 1.0
KEY_HOLD_S = 2.0
CONF_MARGIN = 0.1
KEY_RATE_HZ = 10.0                         # harmony.py publishes at 10 Hz; the tracker scores at the same rate


def _zrows(profile: np.ndarray) -> np.ndarray:
    rows = np.stack([np.roll(profile, k) for k in range(12)])
    rows = rows - rows.mean(axis=1, keepdims=True)
    return rows / (rows.std(axis=1, keepdims=True) * 12.0)


KK_Z = np.concatenate([_zrows(MAJOR_PROFILE), _zrows(MINOR_PROFILE)])     # (24, 12): r = KK_Z @ (c - mean) / std
SCALE_MASK = np.zeros((24, 12), bool)                                     # key index -> its scale's pcs
for _k in range(12):
    SCALE_MASK[_k, [(_k + i) % 12 for i in MAJOR_SCALE]] = True
    SCALE_MASK[12 + _k, [(_k + i) % 12 for i in MINOR_SCALE]] = True

_PC32 = np.zeros((N_PITCH, 12), np.float32)                               # pitch -> pc, one-hot
_PC32[np.arange(N_PITCH), np.arange(N_PITCH) % 12] = 1.0
_PITCHES = np.arange(N_PITCH)


# ---------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------
def _dt(dt: float) -> float:
    return dt if dt > 0.0 and dt < math.inf else 0.0      # NaN, negative and inf -> 0


def _clamp01(x: float) -> float:
    return 0.0 if not x > 0.0 else (1.0 if x > 1.0 else float(x))


def smoothstep(e0: float, e1: float, x: float) -> float:
    """0 below ``e0``, 1 above ``e1``, the cubic 3t^2 - 2t^3 between."""
    t = _clamp01((x - e0) / (e1 - e0)) if e1 != e0 else float(x >= e1)
    return t * t * (3.0 - 2.0 * t)


def smootherstep(e0: float, e1: float, x: float) -> float:
    """Perlin's quintic 6t^5 - 15t^4 + 10t^3 (zero first and second derivative at both ends)."""
    t = _clamp01((x - e0) / (e1 - e0)) if e1 != e0 else float(x >= e1)
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def ema_alpha(dt: float, tau: float) -> float:
    """The share of the way an EMA with time constant ``tau`` (s) moves in ``dt`` (s): 1 - exp(-dt/tau); 0 for dt <= 0,
    1 for tau <= 0."""
    dt = _dt(dt)
    if dt == 0.0:
        return 0.0
    return 1.0 if not tau > 0.0 else 1.0 - math.exp(-dt / tau)


def midi_hz(pitch: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
    """Equal-tempered frequency (Hz) of a MIDI pitch, A4 = 69 = 440 Hz.  Scalar in, float out; array in, array out."""
    if isinstance(pitch, np.ndarray):
        return 440.0 * np.exp2((pitch - 69.0) / 12.0)
    return 440.0 * 2.0 ** ((float(pitch) - 69.0) / 12.0)


def fifths_of(semitones: int) -> int:
    """An interval (semitones, any integer) as signed fifths, -5..+6: 0 0, 7 +1, 2 +2, 9 +3, 4 +4, 11 +5, 6 +6,
    5 -1, 10 -2, 3 -3, 8 -4, 1 -5.  ``fifths_of(x) % 12`` is the circle-of-fifths step 0..11 (hues)."""
    f = (7 * int(semitones)) % 12
    return f - 12 if f > 6 else f


def scale_has(key: int, pc: int) -> bool:
    """Is ``pc`` in key ``key``'s scale (show_service's sets: major, or natural minor + raised 6th and 7th)."""
    return bool(SCALE_MASK[key, pc % 12])


# ---------------------------------------------------------------------------------------------------------------
# Pitch-class energy
# ---------------------------------------------------------------------------------------------------------------
def pc_energy(notes: np.ndarray, out: Optional[np.ndarray] = None) -> np.ndarray:
    """The summed LEVEL of each pitch class, from a notes table ``[128, 4]`` (SoundFrame.notes) or a ``[128]`` level
    column.  Returns ``out`` (float32 [12]; allocated when None).  ~2 us."""
    lv = notes[:, LEVEL] if notes.ndim == 2 else notes
    if out is None:
        out = np.empty(12, np.float32)
    if lv.dtype == np.float32 and out.dtype == np.float32:
        np.dot(lv, _PC32[:lv.shape[0]], out=out)
    else:
        out[:] = np.asarray(lv, np.float32) @ _PC32[:lv.shape[0]]
    np.maximum(out, 0.0, out=out)
    return out


def bass_pitch(notes: np.ndarray, floor: float = 0.02) -> int:
    """The lowest MIDI pitch whose LEVEL is above ``floor``; -1 when nothing is.  ``bass_pitch(..) % 12`` is the
    bass pc the chord readers weight."""
    lv = notes[:, LEVEL] if notes.ndim == 2 else notes
    i = int(np.argmax(lv > floor))
    return i if lv[i] > floor else -1


def loudest(notes: np.ndarray, n: int, floor: float = 0.02) -> np.ndarray:
    """Up to ``n`` loudest pitches with LEVEL above ``floor``, as int pitches sorted low to high (bass first)."""
    lv = notes[:, LEVEL] if notes.ndim == 2 else notes
    on = np.flatnonzero(lv > floor)
    if on.size > n:
        on = on[np.argpartition(lv[on], on.size - n)[on.size - n:]]
        on.sort()
    return on


class AttackRelease:
    """Per-element easing of a vector (default 12 pcs): rising elements move with time constant ``attack_s``,
    falling ones with ``release_s`` (both may be changed between calls, e.g. a longer release under the damper).
    ``value`` is the eased vector (float64, updated in place; read it, don't keep a reference across resets)."""

    def __init__(self, n: int = 12, attack_s: float = 0.12, release_s: float = 0.4) -> None:
        self.attack_s = float(attack_s)
        self.release_s = float(release_s)
        self.value = np.zeros(n)
        self._d = np.empty(n)
        self._up = np.empty(n)

    def step(self, x: np.ndarray, dt: float) -> np.ndarray:
        """Advance by ``dt`` seconds toward ``x``; returns ``value``.  dt = 0 changes nothing; a huge dt lands on x."""
        dt = _dt(dt)
        if dt == 0.0:
            return self.value
        aa, ar = ema_alpha(dt, self.attack_s), ema_alpha(dt, self.release_s)
        np.subtract(x, self.value, out=self._d)
        np.maximum(self._d, 0.0, out=self._up)
        self._up *= aa - ar
        self._d *= ar
        self._d += self._up                            # rising: d * aa; falling: d * ar
        self.value += self._d
        return self.value

    def reset(self, x: Optional[np.ndarray] = None) -> None:
        if x is None:
            self.value.fill(0.0)
        else:
            self.value[:] = x


# ---------------------------------------------------------------------------------------------------------------
# Keys: names, spelling, Krumhansl-Kessler
# ---------------------------------------------------------------------------------------------------------------
_LETTERS = "FCGDAEB"


def lof_name(k: int, unicode: bool = False) -> str:
    """The spelled name of line-of-fifths index ``k``: 0 'C', -1 'F', 6 'F#', -2 'Bb', -9 'Dbb', 14 'Fx' (double
    sharp 'x'; with ``unicode`` the signs are ♭ ♯ 𝄫 𝄪)."""
    k = int(k)
    letter = _LETTERS[(k + 1) % 7]
    acc = (k + 1) // 7
    if acc == 0:
        return letter
    if unicode:
        if acc > 0:
            return letter + "\U0001D12A" * (acc // 2) + "♯" * (acc % 2)
        return letter + "\U0001D12B" * (-acc // 2) + "♭" * (-acc % 2)
    if acc > 0:
        return letter + "x" * (acc // 2) + "#" * (acc % 2)
    return letter + "b" * -acc


def lof_pc(k: int) -> int:
    """The pitch class of line-of-fifths index ``k``."""
    return (7 * int(k)) % 12


def spell(pc: int, ref: float) -> int:
    """The line-of-fifths index of ``pc`` nearest ``ref`` (Chew & Chen): k = 7 pc (mod 12), k + 12m closest to
    ``ref``; an exact tie goes sharp-ward.  ``spell(pc, 0)`` gives -5..+6 (Db..F#)."""
    k0 = (7 * int(pc)) % 12
    return k0 + 12 * math.floor((ref - k0) / 12.0 + 0.5)


_K0 = ((7 * np.arange(12)) % 12).astype(np.float64)     # each pc's base line-of-fifths index 0..11


def spell_all(ref: float, out: Optional[np.ndarray] = None) -> np.ndarray:
    """``spell(pc, ref)`` for pc 0..11 at once, into ``out`` (int64 [12])."""
    if out is None:
        out = np.empty(12, np.int64)
    m = (ref + 6.0 - _K0) / 12.0                           # floor((ref - k0) / 12 + 0.5); one tiny temporary
    np.floor(m, out=m)
    m *= 12.0
    np.add(m, _K0, out=out, casting="unsafe")
    return out


def key_index(key: Optional[str], mode: Optional[str] = "major") -> int:
    """'A#'/'Bb'/'B♭' + 'major'|'minor' -> key index 0..23; -1 for None or anything unreadable (frame.key/mode)."""
    if not key or not isinstance(key, str):
        return -1
    s = key.strip().replace("♭", "b").replace("♯", "#")
    if not s or s[0].upper() not in "CDEFGAB":
        return -1
    pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[s[0].upper()]
    for ch in s[1:]:
        if ch == "#":
            pc += 1
        elif ch == "b":
            pc -= 1
        elif ch == "x":
            pc += 2
        else:
            return -1
    if mode == "minor":
        return 12 + pc % 12
    return pc % 12 if mode in ("major", None) else -1


def key_tonic(key: int) -> int:
    return key % 12


def key_minor(key: int) -> bool:
    return key >= 12


def key_tonic_lof(key: int) -> int:
    """The tonic's line-of-fifths index with the fewest accidentals: majors Db -5 .. F# +6, minors Bb -2 .. D# +9."""
    t = key % 12
    if key >= 12:
        return fifths_of((t + 3) % 12) + 3          # the relative major's spelling, three fifths up
    return fifths_of(t)


def key_signature(key: int) -> int:
    """Sharps (+) or flats (-) in the key signature: C 0, G 1, F -1, A minor 0, E minor 1, F# major 6."""
    return key_tonic_lof(key) - (3 if key >= 12 else 0)


def key_label(key: int, short: bool = False, unicode: bool = False) -> str:
    """'Eb major' / 'C# minor'; ``short``: Sapp's 'Eb' / 'c#' (minor lower case).  '' for -1."""
    if key < 0:
        return ""
    name = lof_name(key_tonic_lof(key), unicode)
    if short:
        return name[0].lower() + name[1:] if key >= 12 else name
    return f"{name} {MODES[key >= 12]}"


def key_scores(chroma: np.ndarray, out: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """Pearson r of ``chroma`` (12, any scale) with the 24 Krumhansl-Kessler profiles (0-11 major, 12-23 minor), into
    ``out`` (float64 [24]); None for a flat chroma (silence, or every pc alike).  Same numbers as harmony.py."""
    d = np.asarray(chroma, np.float64)
    m = float(d.sum()) / 12.0
    var = float(d @ d) / 12.0 - m * m
    if not var > SILENT * SILENT:
        return None
    if out is None:
        out = np.empty(24)
    np.dot(KK_Z, d, out=out)
    out -= KK_Z.sum(axis=1) * m                      # rows are zero-mean, so this term is ~0; kept for exactness
    out /= math.sqrt(var)
    return out


def _runner_up(scores: np.ndarray, k: int) -> float:
    top = float(scores.max())
    return top if scores[k] < top else float(np.partition(scores, -2)[-2])


def key_conf(scores: np.ndarray, k: int) -> float:
    """harmony.py's conf: clamp(r_k) x clamp((r_k - the best other r) / 0.1)."""
    r = float(scores[k])
    return _clamp01(r) * _clamp01((r - _runner_up(scores, k)) / CONF_MARGIN)


def key_estimate(chroma: np.ndarray) -> tuple[int, float]:
    """(key index, conf) by Krumhansl-Kessler on one chroma with no history; (-1, 0.0) when it is flat."""
    s = key_scores(chroma)
    if s is None:
        return -1, 0.0
    k = int(np.argmax(s))
    return k, key_conf(s, k)


class KeyTracker:
    """show_service/harmony.py's key finder on this plug-in's own pc energy (module docstring): same EMA, profiles,
    margins and hold times, scored at ``rate_hz`` (10 Hz, as harmony publishes).

    Read after ``update``: ``key`` (index, -1 before the first), ``conf``, ``scores`` (24 r values, valid when
    ``has_scores``), ``avg`` (the 8 s average, max-normalised pcs), ``pending`` (the challenger's index or -1) and
    ``pending_frac`` (0..1 of the hold it has served)."""

    def __init__(self, rate_hz: float = KEY_RATE_HZ) -> None:
        self.period_s = 1.0 / rate_hz if rate_hz and rate_hz > 0 else 0.0
        self.avg = np.zeros(12)
        self.scores = np.zeros(24)
        self.has_scores = False
        self.key = -1
        self.conf = 0.0
        self.pending = -1
        self.pending_frac = 0.0
        self.t = 0.0
        self.scored = False            # this update re-scored the keys (10 Hz)
        self._since = 0.0
        self._next = 0.0
        self._started = False
        self._c = np.zeros(12)

    def update(self, pc_e: np.ndarray, dt: float) -> int:
        """Feed this frame's pc energy (any scale; it is max-normalised as harmony.py's chroma); returns ``key``."""
        dt = _dt(dt)
        self.t += dt
        self.scored = False
        c = self._c
        c[:] = pc_e
        np.maximum(c, 0.0, out=c)
        m = float(c.max())
        if m > SILENT:
            c /= m
        else:
            c.fill(0.0)
        if not self._started:
            self.avg[:] = c
            self._started = True
        elif dt > 0.0:
            c -= self.avg
            c *= 1.0 - math.exp(-dt / KEY_TAU_S)
            self.avg += c
        if self.t + 1e-9 >= self._next:
            self._next = self.t + self.period_s if self.t - self._next > self.period_s else self._next + self.period_s
            self._score()
        return self.key

    def _score(self) -> None:
        self.scored = True
        s = key_scores(self.avg, self.scores)
        self.has_scores = s is not None
        if s is None:
            self.pending, self.pending_frac = -1, 0.0
            self.conf = 0.0
            return
        best = int(np.argmax(s))
        t = self.t
        if self.key < 0:
            if s[best] > KEY_MIN_R and s[best] - _runner_up(s, best) >= KEY_MARGIN:
                self._challenge(best, t, KEY_FIRST_S)
            else:
                self.pending, self.pending_frac = -1, 0.0
        elif best != self.key and s[best] - s[self.key] >= KEY_MARGIN:
            self._challenge(best, t, KEY_HOLD_S)
        else:
            self.pending, self.pending_frac = -1, 0.0
        self.conf = 0.0 if self.key < 0 else key_conf(s, self.key)

    def _challenge(self, best: int, t: float, need: float) -> None:
        if self.pending != best:
            self.pending, self._since, self.pending_frac = best, t, 0.0
        elif t - self._since >= need - 1e-9:
            self.key, self.pending, self.pending_frac = best, -1, 0.0
        else:
            self.pending_frac = (t - self._since) / need


class KeyChange(NamedTuple):
    """One key change.  ``frm``/``to`` are key indices (``frm`` is 0, C major, for the first known key: ``first``).
    ``fifths``: how far the tonic moved on the circle of fifths, the short way, -5..+6 (C -> G +1, C -> Am +3).
    ``signature``: the change in key-signature accidentals (C -> Am 0, C -> E major +4)."""
    frm: int
    to: int
    fifths: int
    signature: int
    source: str
    first: bool


class KeyResolver:
    """The key a plug-in draws in: the room's (``frame.key``/``frame.mode``, show_service's detector over the same
    notes) whenever it has one, else this plug-in's own :class:`KeyTracker` (the test piano sends notes but no key),
    else C major with ``known`` False.  A different key must be proposed for ``hold_s`` without a break before it is
    taken (the first known key is taken at once).

    Read after ``update``: ``key`` (index 0..23, never -1), ``tonic`` (pc), ``minor``, ``mode`` ('major'|'minor'),
    ``known``, ``source`` ('room'|'own'|'default'), ``conf`` (0..1: how well the last ~8 s of notes fit ``key``,
    from the own tracker's scores; SoundFrame carries no key confidence), ``pending`` (a proposed different key or
    -1) and ``pending_s``, ``change`` (the :class:`KeyChange` made by this update, else None), ``tonic_lof``,
    ``label`` ('Eb major') and ``tracker``."""

    def __init__(self, hold_s: float = 0.5, rate_hz: float = KEY_RATE_HZ) -> None:
        self.hold_s = float(hold_s)
        self.tracker = KeyTracker(rate_hz)
        self.key = 0
        self.known = False
        self.source = "default"
        self.conf = 0.0
        self.pending = -1
        self.pending_s = 0.0
        self.change: Optional[KeyChange] = None
        self._room_in: tuple = (None, None)
        self._room_k = -1

    @property
    def tonic(self) -> int:
        return self.key % 12

    @property
    def minor(self) -> bool:
        return self.key >= 12

    @property
    def mode(self) -> str:
        return MODES[self.key >= 12]

    @property
    def tonic_lof(self) -> int:
        return key_tonic_lof(self.key)

    @property
    def label(self) -> str:
        return key_label(self.key)

    def update(self, pc_e: np.ndarray, dt: float, room_key: Optional[str] = None,
               room_mode: Optional[str] = None) -> Optional[KeyChange]:
        """Advance with this frame's pc energy and the room's key strings; returns ``change``."""
        dt = _dt(dt)
        own = self.tracker.update(pc_e, dt)
        if (room_key, room_mode) != self._room_in:
            self._room_in = (room_key, room_mode)
            self._room_k = key_index(room_key, room_mode)
        if self._room_k >= 0:
            prop, src = self._room_k, "room"
        elif own >= 0:
            prop, src = own, "own"
        else:
            prop, src = -1, self.source
        self.change = None
        if prop < 0 or prop == self.key and self.known:
            self.pending, self.pending_s = -1, 0.0
            if prop >= 0:
                self.source = src
        elif not self.known:
            self._take(prop, src, first=True)
        else:
            if self.pending != prop:
                self.pending, self.pending_s = prop, 0.0
            else:
                self.pending_s += dt
            if self.pending_s >= self.hold_s - 1e-9:
                self._take(prop, src, first=False)
        tr = self.tracker
        if tr.scored or self.change is not None:
            self.conf = key_conf(tr.scores, self.key) if tr.has_scores else 0.0
        return self.change

    def _take(self, k: int, src: str, first: bool) -> None:
        frm = self.key
        self.change = KeyChange(frm, k, fifths_of(k % 12 - frm % 12), key_signature(k) - key_signature(frm),
                                src, first)
        self.key, self.known, self.source = k, True, src
        self.pending, self.pending_s = -1, 0.0


# ---------------------------------------------------------------------------------------------------------------
# Chew's Spiral Array
# ---------------------------------------------------------------------------------------------------------------
SPIRAL_H = math.sqrt(2.0 / 15.0)           # rise per fifth (r = 1)
SPIRAL_R = 1.0
SPIRAL_W = (0.536, 0.274, 0.19)            # chord and key weights (partitura: 0.516, 0.315, 0.168, same ordering)
SPIRAL_ALPHA = 0.75                        # minor key: share of the major V
SPIRAL_BETA = 0.75                         # minor key: share of the minor iv
SPIRAL_N0 = 12.0 * SPIRAL_H                # |P(0) - P(12)|: the strain unit


def spiral_point(k: Union[float, np.ndarray]) -> np.ndarray:
    """P(k) = (r sin(k pi/2), r cos(k pi/2), k h); ``k`` scalar -> (3,), array -> (..., 3)."""
    k = np.asarray(k, np.float64)
    a = k * (math.pi / 2.0)
    return np.stack([SPIRAL_R * np.sin(a), SPIRAL_R * np.cos(a), k * SPIRAL_H], axis=-1)


def chord_point(k: Union[float, np.ndarray], minor: Union[bool, np.ndarray] = False) -> np.ndarray:
    """C_M(k) or C_m(k) for a triad rooted at line-of-fifths ``k`` (vectorised over k and minor)."""
    k = np.asarray(k, np.float64)
    third = np.where(np.asarray(minor), k - 3.0, k + 4.0)
    w1, w2, w3 = SPIRAL_W
    return w1 * spiral_point(k) + w2 * spiral_point(k + 1.0) + w3 * spiral_point(third)


def key_point(k: float, minor: bool = False) -> np.ndarray:
    """T_M(k) or T_m(k) for a key whose tonic is line-of-fifths ``k`` (3,)."""
    w1, w2, w3 = SPIRAL_W
    if not minor:
        return w1 * chord_point(k) + w2 * chord_point(k + 1) + w3 * chord_point(k - 1)
    a, b = SPIRAL_ALPHA, SPIRAL_BETA
    return (w1 * chord_point(k, True)
            + w2 * (a * chord_point(k + 1, False) + (1 - a) * chord_point(k + 1, True))
            + w3 * (b * chord_point(k - 1, True) + (1 - b) * chord_point(k - 1, False)))


_SIN4 = np.array([0.0, 1.0, 0.0, -1.0])                  # sin(k pi/2), cos(k pi/2) by k mod 4 (k & 3)
_COS4 = np.array([1.0, 0.0, -1.0, 0.0])


def centre_of_effect(e: np.ndarray, ks: np.ndarray, out: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """CE = sum e[pc] P(ks[pc]) / sum e (``ks``: each pc's spelled index, e.g. ``spell_all(ref)``) into ``out`` (3,);
    None when sum e < 1e-4 (the caller keeps its long-term CE)."""
    tot = float(e.sum())
    if tot < 1e-4:
        return None
    if out is None:
        out = np.empty(3)
    q = np.bitwise_and(ks, 3)                                       # k mod 4, negatives included
    e = np.asarray(e, np.float64)
    out[0] = float(e @ _SIN4[q]) / tot
    out[1] = float(e @ _COS4[q]) / tot
    out[2] = float(e @ ks) * SPIRAL_H / tot
    return out


class SpiralKey:
    """One key on the Spiral Array: its point and its 24 triad stations, spelled relative to the tonic (roots -5..+6
    fifths from it, so in C: Db, Eb, Ab, Bb, F#).  Arrays indexed by triad id (root pc, +12 minor):

      ``k_tonic``  the tonic's line-of-fifths index (key_tonic_lof); ``point`` T_key (3,); ``z_tonic`` the tonic
                   triad's z; ``dominant`` C_M(k_tonic + 1) (3,).
      ``k_root`` (24,) int, ``stations`` (24, 3), ``strain`` (24,) S of each station, ``bearing`` (24,) in fifths.
    """

    def __init__(self, key: int) -> None:
        self.key = int(key)
        self.minor = key >= 12
        self.tonic = key % 12
        self.k_tonic = key_tonic_lof(key)
        self.point = key_point(self.k_tonic, self.minor)
        self.z_tonic = float(chord_point(self.k_tonic, self.minor)[2])
        self.dominant = chord_point(self.k_tonic + 1, False)
        rel = (np.arange(24) % 12 - self.tonic) % 12
        self.k_root = self.k_tonic + np.array([fifths_of(int(r)) for r in rel])
        self.stations = chord_point(self.k_root, np.arange(24) >= 12)
        self.strain = np.linalg.norm(self.stations - self.point, axis=1) / SPIRAL_N0
        self.bearing = (self.stations[:, 2] - self.z_tonic) / SPIRAL_H

    def strain_of(self, x: np.ndarray) -> float:
        """Tensile strain S = |X - T_key| / N0 (0.08 for the tonic triad, ~0.2 for V, 0.56 for the tritone)."""
        d = x - self.point
        return math.sqrt(float(d @ d)) / SPIRAL_N0

    def bearing_of(self, x: np.ndarray) -> float:
        """Bearing beta = (X.z - z of the tonic triad) / h, in fifths: + toward the dominant (sharp) side."""
        return (float(x[2]) - self.z_tonic) / SPIRAL_H

    def dominant_distance(self, x: np.ndarray) -> float:
        """|X - C_M(k_tonic + 1)| / N0: how near X is to the key's dominant triad."""
        d = x - self.dominant
        return math.sqrt(float(d @ d)) / SPIRAL_N0


# ---------------------------------------------------------------------------------------------------------------
# Chords
# ---------------------------------------------------------------------------------------------------------------
QUALITY_INTERVALS: dict[str, tuple[int, ...]] = {      # semitones above the root: root, third, fifth, seventh
    "maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "hdim7": (0, 3, 6, 10), "dim7": (0, 3, 6, 9),
    "sus4": (0, 5, 7), "5": (0, 7),
}
QUALITIES = tuple(QUALITY_INTERVALS)
TRIADS = ("maj", "min")
ALL_QUALITIES = QUALITIES
QUALITY_SUFFIX = {"maj": "", "min": "m", "dim": "°", "aug": "+", "7": "7", "maj7": "maj7", "m7": "m7",
                  "hdim7": "ø7", "dim7": "°7", "sus4": "sus4", "5": "5"}
_KIND = {"maj": "M", "aug": "M", "7": "M", "maj7": "M", "sus4": "M", "5": "M",
         "min": "m", "m7": "m", "dim": "d", "hdim7": "d", "dim7": "d"}
_SYMMETRIC = {"aug": 4, "dim7": 3}                     # rotation period (semitones) of the root-less chords
COSINE_WEIGHTS = (1.0, 0.9, 0.8, 0.7)                  # root, third, fifth, seventh


def triad_id(root: int, minor: bool) -> int:
    return root % 12 + (12 if minor else 0)


def chord_pcs(root: int, quality: str) -> tuple[int, ...]:
    return tuple((root + i) % 12 for i in QUALITY_INTERVALS[quality])


def chord_label(root: int, quality: str, key: int = -1, unicode: bool = False) -> str:
    """'Eb', 'F#m', 'B°', 'G7', 'Fø7': the root spelled -5..+6 fifths from the key's tonic (from C when ``key`` is -1:
    Db, Eb, F#, Ab, Bb)."""
    if key >= 0:
        k = key_tonic_lof(key) + fifths_of(root - key % 12)
    else:
        k = fifths_of(root)
    return lof_name(k, unicode) + QUALITY_SUFFIX[quality]


class ChordReader:
    """Reads the chord from pc energy with hysteresis (module docstring).

    ``qualities``: any of QUALITIES (default the 24 triads).  ``method``: "cosine" (tonal_gravity, tonnetz_walk) or
    "score" (fifths_ladder).  ``min_score``/``lead``/``hold_s``: a challenger must score >= min_score and beat the
    current chord by ``lead`` for ``hold_s`` seconds (defaults: cosine 0.80 / 0.05 / 0.08; score 1.0 / 0.1 / 0.15).
    ``rate_hz``: evaluate at most this often (None: every update).  ``bass_gain``: the bass pc's energy is
    multiplied by it.  ``conf_range``: conf = smoothstep(lo, hi, the current chord's score) (cosine 0.75..0.95 =
    Chew's kappa; score 1.2..2.6).

    Read after ``update``: ``chord`` (id = quality index x 12 + root, -1 before the first), ``root``, ``quality``
    (str or None), ``minor`` (a minor- or diminished-kind chord: its triad() is the minor triad), ``conf``, ``score`` (the current chord's raw score), ``best`` /
    ``best_score`` (the top candidate now), ``changed`` (this update), ``prev`` (the chord before), ``age_s`` (since
    the current chord was taken), ``bass`` (the bass pc at the last evaluation, -1 none), ``n_pcs`` (pcs above 10% of
    the loudest), ``sounding``, and ``scores`` (len(qualities) x 12, valid while sounding)."""

    def __init__(self, qualities: Sequence[str] = TRIADS, method: str = "cosine", *,
                 min_score: Optional[float] = None, lead: Optional[float] = None, hold_s: Optional[float] = None,
                 rate_hz: Optional[float] = None, bass_gain: float = 1.5, bass_bonus: Optional[float] = None,
                 diatonic_bonus: float = 0.1, conf_range: Optional[tuple[float, float]] = None) -> None:
        if method not in ("cosine", "score"):
            raise ValueError(f"method must be 'cosine' or 'score', not {method!r}")
        self.qualities = tuple(qualities)
        self.method = method
        cos = method == "cosine"
        self.min_score = (0.80 if cos else 1.0) if min_score is None else float(min_score)
        self.lead = (0.05 if cos else 0.1) if lead is None else float(lead)
        self.hold_s = (0.08 if cos else 0.15) if hold_s is None else float(hold_s)
        self.period_s = 1.0 / rate_hz if rate_hz else 0.0
        self.bass_gain = float(bass_gain)
        self.bass_bonus = (0.0 if cos else 0.3) if bass_bonus is None else float(bass_bonus)
        self.diatonic_bonus = 0.0 if cos else float(diatonic_bonus)
        self.conf_range = ((0.75, 0.95) if cos else (1.2, 2.6)) if conf_range is None else tuple(conf_range)
        nq = len(self.qualities)
        tm = np.zeros((nq * 12, 12))
        size = np.zeros(nq * 12)
        for qi, q in enumerate(self.qualities):
            iv = QUALITY_INTERVALS[q]
            for r in range(12):
                for j, i in enumerate(iv):
                    w = (COSINE_WEIGHTS[j] if q != "5" else (1.0, 0.8)[j]) if cos else 1.0
                    tm[qi * 12 + r, (r + i) % 12] = w
                size[qi * 12 + r] = len(iv)
        if cos:
            tm /= np.linalg.norm(tm, axis=1, keepdims=True)
            self._t = tm
            self._const = np.zeros(nq * 12)
        else:
            self._t = 1.6 * tm                                  # in - 0.6 (sum - in) = 1.6 in - 0.6 sum
            self._const = -0.05 * size
        self._root_of = np.tile(np.arange(12), nq)
        self._rows_of_root = [np.flatnonzero(self._root_of == r) for r in range(12)]
        self.scores = np.zeros(nq * 12)
        self._x = np.zeros(12)
        self._dia = np.zeros(nq * 12)
        self._dia_key = -2
        self.chord = -1
        self.prev = -1
        self.conf = 0.0
        self.score = 0.0
        self.best = -1
        self.best_score = 0.0
        self.changed = False
        self.age_s = 0.0
        self.bass = -1
        self.n_pcs = 0
        self.sounding = False
        self._cand = -1
        self._cand_s = 0.0
        self._t_since = math.inf

    @property
    def root(self) -> int:
        return -1 if self.chord < 0 else self.chord % 12

    @property
    def quality(self) -> Optional[str]:
        return None if self.chord < 0 else self.qualities[self.chord // 12]

    @property
    def minor(self) -> bool:
        return self.chord >= 0 and _KIND[self.qualities[self.chord // 12]] != "M"

    def triad(self) -> int:
        """The current chord as a triad id (root, +12 for minor kind), -1 before the first."""
        return -1 if self.chord < 0 else triad_id(self.chord % 12, self.minor)

    def reset(self) -> None:
        self.chord = self.prev = self._cand = -1
        self.conf = self.score = 0.0
        self.age_s = 0.0

    def update(self, pc_e: np.ndarray, dt: float, bass_pc: int = -1, key: int = -1, allow: bool = True) -> bool:
        """Advance by ``dt`` with this frame's pc energy, the bass pc (``bass_pitch(..) % 12`` or -1) and the key
        index (for the score method's diatonic bonus; -1 none).  ``allow`` False holds the current chord (a gate,
        e.g. fifths_ladder's 'chord_change > 0.12 or an onset in the last 0.3 s').  Returns ``changed``."""
        dt = _dt(dt)
        self.changed = False
        self.age_s += dt
        self._t_since += dt
        if self._t_since + 1e-9 < self.period_s:
            return False
        step = self._t_since if self._t_since < math.inf else dt
        self._t_since = 0.0
        x = self._x
        x[:] = pc_e
        np.maximum(x, 0.0, out=x)
        top = float(x.max())
        self.n_pcs = int(np.count_nonzero(x > 0.1 * top)) if top > SILENT else 0
        self.bass = bass_pc % 12 if bass_pc >= 0 else -1
        if top <= SILENT:
            self.sounding = False
            self._cand = -1
            self.conf = self.score = self.best_score = 0.0
            return False
        self.sounding = True
        if self.bass >= 0:
            x[self.bass] *= self.bass_gain
        s = self.scores
        if self.method == "cosine":
            np.dot(self._t, x, out=s)
            s /= math.sqrt(float(x @ x))
        else:
            x /= float(x.max())
            np.dot(self._t, x, out=s)
            s += self._const - 0.6 * float(x.sum())
            if self.bass >= 0 and self.bass_bonus:
                s[self._rows_of_root[self.bass]] += self.bass_bonus
            if key >= 0 and self.diatonic_bonus:
                if key != self._dia_key:
                    self._dia_key = key
                    self._dia[:] = SCALE_MASK[key][self._root_of] * self.diatonic_bonus
                s += self._dia
        best = int(np.argmax(s))
        best = self._settle_root(best)
        self.best, self.best_score = best, float(s[best])
        cur = float(s[self.chord]) if self.chord >= 0 else -math.inf
        if allow and best != self.chord and self.best_score >= self.min_score \
                and self.best_score - cur >= self.lead:
            if self._cand != best:
                self._cand, self._cand_s = best, 0.0
            else:
                self._cand_s += step
            if self._cand_s >= self.hold_s - 1e-9:
                self.prev, self.chord, self.changed, self.age_s = self.chord, best, True, 0.0
                self._cand = -1
                cur = self.best_score
        else:
            self._cand = -1
        self.score = cur if cur > -math.inf else 0.0
        lo, hi = self.conf_range
        self.conf = smoothstep(lo, hi, self.score) if self.chord >= 0 else 0.0
        return self.changed

    def _settle_root(self, best: int) -> int:
        """Augmented / diminished seventh: of the rotations scoring within ``lead`` of the best, keep the root
        nearest the previous root on the line of fifths (the current chord's root, else the best's own)."""
        q = self.qualities[best // 12]
        period = _SYMMETRIC.get(q)
        if period is None:
            return best
        ref = self.root if self.chord >= 0 else -1
        if ref < 0:
            return best
        base = best - best % 12
        top = float(self.scores[best])
        pick, pick_d = best, abs(fifths_of(best % 12 - ref))
        for j in range(1, 12 // period):
            c = base + (best % 12 + j * period) % 12
            if float(self.scores[c]) >= top - self.lead:
                d = abs(fifths_of(c % 12 - ref))
                if d < pick_d:
                    pick, pick_d = c, d
        return pick


# Roman numerals ------------------------------------------------------------------------------------------------
_MAJ_DEG = ("I", "bII", "II", "bIII", "III", "IV", "bV", "V", "bVI", "VI", "bVII", "VII")
_MIN_DEG = ("I", "bII", "II", "III", "#III", "IV", "#IV", "V", "VI", "#VI", "VII", "#VII")
_MARK = {"maj": "", "min": "", "dim": "°", "aug": "+", "7": "7", "maj7": "maj7", "m7": "7",
         "hdim7": "ø7", "dim7": "°7", "sus4": "sus4", "5": "5"}
_TARGETS = ({2: "ii", 4: "iii", 5: "IV", 7: "V", 9: "vi"},              # major: secondary-function targets
            {3: "III", 5: "iv", 7: "V", 8: "VI", 10: "VII"})            # minor
FN_T, FN_PD, FN_D, FN_CHROMATIC = 0, 1, 2, 3
FUNCTION_NAMES = ("T", "PD", "D", "chromatic")
_FUNCTIONS = (
    {(0, "M"): FN_T, (4, "m"): FN_T, (9, "m"): FN_T,
     (2, "m"): FN_PD, (5, "M"): FN_PD, (5, "m"): FN_PD, (8, "M"): FN_PD, (2, "d"): FN_PD, (1, "M"): FN_PD,
     (7, "M"): FN_D, (11, "d"): FN_D},
    {(0, "m"): FN_T, (3, "M"): FN_T, (8, "M"): FN_T,
     (5, "m"): FN_PD, (2, "d"): FN_PD, (5, "M"): FN_PD, (1, "M"): FN_PD,
     (7, "M"): FN_D, (7, "m"): FN_D, (11, "d"): FN_D, (10, "M"): FN_D},
)


def _secondary(root: int, quality: str, key: int) -> Optional[tuple[str, int]]:
    """(prefix, target degree) when the chord is a secondary dominant (major / dom7 a fifth above a diatonic target
    other than the tonic, with a non-diatonic tone or a b7) or a secondary leading-tone chord (dim-kind a semitone
    below such a target, with a non-diatonic tone)."""
    minor = key >= 12
    rel = (root - key) % 12
    targets = _TARGETS[minor]
    outside = any(not SCALE_MASK[key, p] for p in chord_pcs(root, quality))
    if quality in ("maj", "7"):
        d = (rel - 7) % 12
        if d in targets and (outside or quality == "7"):
            return ("V7" if quality == "7" else "V"), d
    elif _KIND[quality] == "d":
        d = (rel + 1) % 12
        if d in targets and outside:
            return "vii" + _MARK[quality], d
    return None


def roman(root: int, quality: str, key: int, *, secondary: bool = True, unicode: bool = False) -> str:
    """The Roman numeral of a chord in key index ``key`` (no inversion figure: see :func:`inversion`).

    Degrees by the root's semitones above the tonic; major key: I bII II bIII III IV bV V bVI VI bVII VII (a minor
    or diminished chord on 6 is #iv); minor key: I bII II III #III IV #IV V VI #VI VII #VII (a diminished-kind chord
    on 11 is plain vii, on 9 #vi).  Upper case for major-kind chords, lower for minor and diminished; marks °, +,
    7, maj7, ø7, °7, sus4, 5.  With ``secondary``: V/x, V7/x and vii°/x, vii°7/x, viiø7/x (x = ii, iii, IV, V, vi
    in major; III, iv, V, VI, VII in minor).  ``unicode`` writes ♭ ♯ for b #.  Examples in C: I, ii, V7, vii°,
    V7/V, bVI, iv, bII, V7/IV; in A minor: i, ii°, III, iv, V7, VI, VII, vii°7, V/V."""
    root %= 12
    if secondary:
        sec = _secondary(root, quality, key)
        if sec is not None:
            out = f"{sec[0]}/{_TARGETS[key >= 12][sec[1]]}"
            return out.replace("b", "♭").replace("#", "♯") if unicode else out
    rel = (root - key) % 12
    kind = _KIND[quality]
    if key >= 12:
        deg = _MIN_DEG[rel]
        if kind == "d" and rel in (9, 11):
            deg = deg.lstrip("#") if rel == 11 else deg
    else:
        deg = _MAJ_DEG[rel]
        if rel == 6 and kind != "M":
            deg = "#IV"
    if kind != "M":
        deg = deg.lower()
    out = deg + _MARK[quality]
    return out.replace("b", "♭").replace("#", "♯") if unicode else out


def function_class(root: int, quality: str, key: int) -> int:
    """FN_T, FN_PD, FN_D or FN_CHROMATIC (FUNCTION_NAMES).  Major: T = I, iii, vi; PD = ii, IV, iv, bVI, ii°, bII;
    D = V (any 7th), vii° (any), and every secondary dominant / leading-tone chord.  Minor: T = i, III, VI; PD = iv,
    ii°, IV, bII; D = V, v, vii°, VII.  Anything else is chromatic.  Sevenths take their triad's function."""
    if _secondary(root % 12, quality, key) is not None:
        return FN_D
    return _FUNCTIONS[key >= 12].get(((root - key) % 12, _KIND[quality]), FN_CHROMATIC)


def inversion(quality: str, root: int, bass: int) -> str:
    """The figured-bass figure for ``bass`` (pc, -1 none) under a chord: triads '' / '6' (third in the bass) /
    '6/4' (fifth); sevenths '7'-less: '' / '6/5' / '4/3' / '4/2' (seventh).  '' for a bass outside the chord and for
    sus4 and power chords."""
    if bass < 0 or quality in ("sus4", "5"):
        return ""
    iv = QUALITY_INTERVALS[quality]
    rel = (bass - root) % 12
    if rel == 0 or rel not in iv:
        return ""
    pos = iv.index(rel)
    return (("", "6", "6/4"), ("", "6/5", "4/3", "4/2"))[len(iv) == 4][pos]


RES_NONE, RES_FULFILLED, RES_DECEPTIVE = 0, 1, 2


def resolution(prev_root: int, root: int) -> int:
    """How a dominant-function chord on ``prev_root`` went on to ``root``: RES_FULFILLED when the root fell a fifth
    (V -> I, V/V -> V: one step down the line of fifths), RES_DECEPTIVE when it rose a step (V -> vi, two fifths up;
    V -> VI in minor, a semitone), else RES_NONE.  The caller decides that the previous chord was dominant (function_class)."""
    if prev_root < 0 or root < 0:
        return RES_NONE
    up = (root - prev_root) % 12
    return RES_FULFILLED if up == 5 else (RES_DECEPTIVE if up in (1, 2) else RES_NONE)


# ---------------------------------------------------------------------------------------------------------------
# Pitch-class DFT and symmetry
# ---------------------------------------------------------------------------------------------------------------
DFT12 = np.exp(-2j * np.pi * np.outer(np.arange(7), np.arange(12)) / 12.0)     # (7, 12): coefficients 0..6
SYMMETRY_ORDERS = (2, 3, 4, 6)
SYMMETRY_CHANCE = tuple((12 / n - 1) / 11 for n in SYMMETRY_ORDERS)             # 5/11, 3/11, 2/11, 1/11
# share of non-DC energy on multiples of n, from E_1..E_6 (real input: E_k = E_12-k)
_SYM_W = np.array([[0, 2, 0, 2, 0, 1],      # n = 2: k 2,4,6,8,10
                   [0, 0, 2, 0, 0, 1],      # n = 3: k 3,6,9
                   [0, 0, 0, 2, 0, 0],      # n = 4: k 4,8
                   [0, 0, 0, 0, 0, 1]],     # n = 6: k 6
                  np.float64)
_SYM_ALL = np.array([2, 2, 2, 2, 2, 1], np.float64)
_SYM_B = np.array(SYMMETRY_CHANCE)
_SYM_WS = _SYM_W / (1.0 - _SYM_B)[:, None]                  # sigma = W' e / tot - b', b' = b / (1 - b)
_SYM_BS = _SYM_B / (1.0 - _SYM_B)


def pc_dft(c: np.ndarray, out: Optional[np.ndarray] = None) -> np.ndarray:
    """F_k = sum_n c[n] exp(-2 pi i k n / 12) for k = 0..6, into ``out`` (complex128 [7]).  F_0 = sum c."""
    if out is None:
        out = np.empty(7, np.complex128)
    np.dot(DFT12, np.asarray(c, np.complex128), out=out)
    return out


def symmetry(c: np.ndarray, out: Optional[np.ndarray] = None, F: Optional[np.ndarray] = None) -> np.ndarray:
    """sigma_n for n = 2, 3, 4, 6 (SYMMETRY_ORDERS) into ``out`` (float64 [4]); zeros for silence or a flat chroma.
    ``F``: this chroma's pc_dft if already computed."""
    if out is None:
        out = np.empty(4)
    if F is None:
        F = pc_dft(c)
    e = np.abs(F[1:])                                                          # one tiny temporary
    e *= e                                                                      # E_1..E_6
    tot = float(_SYM_ALL @ e)
    if not tot > SILENT * SILENT * max(1.0, abs(F[0]) ** 2):
        out.fill(0.0)
        return out
    np.dot(_SYM_WS, e, out=out)
    out *= 1.0 / tot
    out -= _SYM_BS
    np.clip(out, 0.0, 1.0, out=out)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Tonnetz
# ---------------------------------------------------------------------------------------------------------------
PLR_OPS = ("P", "L", "R")
_SQRT3_2 = math.sqrt(3.0) / 2.0
FIFTH_CENTS, THIRD_CENTS = 701.955, 386.314                # just 3/2 and 5/4
TONNETZ_PERIODS = ((4, -1), (0, 3))


def plr(tid: int, op: Union[int, str]) -> int:
    """Apply P, L or R (or 0, 1, 2) to triad id ``tid``: P C <-> Cm, L C <-> Em, R C <-> Am."""
    o = PLR_OPS.index(op) if isinstance(op, str) else int(op)
    r = tid % 12
    if tid < 12:
        return 12 + (r, (r + 4) % 12, (r + 9) % 12)[o]
    return (r, (r + 8) % 12, (r + 3) % 12)[o]


def _bfs() -> tuple[np.ndarray, np.ndarray]:
    dist = np.full((24, 24), -1, np.int8)
    nxt = np.full((24, 24), -1, np.int8)
    for s in range(24):
        dist[s, s] = 0
        q = deque([s])
        first = {s: -1}
        while q:
            u = q.popleft()
            for o in range(3):
                v = plr(u, o)
                if dist[s, v] < 0:
                    dist[s, v] = dist[s, u] + 1
                    first[v] = o if u == s else first[u]
                    q.append(v)
        for v, o in first.items():
            nxt[s, v] = o
    return dist, nxt


PLR_DIST, PLR_NEXT = _bfs()      # (24, 24) int8: moves between triads; the first op (0 P, 1 L, 2 R) on a shortest path


def plr_path(a: int, b: int) -> list[int]:
    """The ops (0 P, 1 L, 2 R) of a shortest P/L/R path from triad ``a`` to ``b`` (deterministic; [] when equal)."""
    out = []
    while a != b:
        o = int(PLR_NEXT[a, b])
        out.append(o)
        a = plr(a, o)
    return out


def lattice_pc(a: int, b: int) -> int:
    """The pc of Tonnetz node (a, b): 7a + 4b (mod 12)."""
    return (7 * a + 4 * b) % 12


def lattice_lof(a: int, b: int) -> int:
    """The spelled line-of-fifths index of node (a, b): a + 4b (lof_name gives its name)."""
    return a + 4 * b


def lattice_xy(a: float, b: float) -> tuple[float, float]:
    """Plane position of lattice coordinates, unit edge: a (1, 0) + b (1/2, sqrt3/2)."""
    return a + 0.5 * b, b * _SQRT3_2


def xy_lattice(x: float, y: float) -> tuple[float, float]:
    """Inverse of lattice_xy: (a, b) as floats."""
    b = y / _SQRT3_2
    return x - 0.5 * b, b


def tri_triad(a: int, b: int, up: bool) -> int:
    """Triad id of a triangle: up (a, b) = major on pc(a, b); down (a, b) = minor on pc(a, b) + 4."""
    return lattice_pc(a, b) if up else 12 + (lattice_pc(a, b) + 4) % 12


def triad_tri(tid: int) -> tuple[int, int, bool]:
    """A canonical triangle for triad id ``tid``: its root node at (fifths_of(root), 0) (major: up there; minor: the
    down triangle whose root vertex that is, (a, -1))."""
    a = fifths_of(tid % 12)
    return (a, 0, True) if tid < 12 else (a, -1, False)


def tri_vertices(a: int, b: int, up: bool) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """The triangle's nodes as (root, third, fifth) of its triad."""
    if up:
        return (a, b), (a, b + 1), (a + 1, b)
    return (a, b + 1), (a + 1, b), (a + 1, b + 1)


def tri_move(a: int, b: int, up: bool, op: Union[int, str]) -> tuple[int, int, bool]:
    """The triangle over the shared edge after P, L or R (a flip on the lattice, so walks drift honestly)."""
    o = PLR_OPS.index(op) if isinstance(op, str) else int(op)
    if up:
        return ((a, b - 1), (a, b), (a - 1, b))[o] + (False,)
    return ((a, b + 1), (a, b), (a + 1, b))[o] + (True,)


def tri_centroid(a: int, b: int, up: bool) -> tuple[float, float]:
    """The triangle's centroid in lattice coordinates."""
    d = 1.0 / 3.0 if up else 2.0 / 3.0
    return a + d, b + d


def nearest_copy(tid: int, a_f: float, b_f: float) -> tuple[int, int, bool]:
    """The copy of triad ``tid``'s triangle whose centroid is nearest the lattice point (a_f, b_f) in the plane."""
    a0, b0, up = triad_tri(tid)
    ca, cb = tri_centroid(a0, b0, up)
    # solve (ca, cb) + i (4, -1) + j (0, 3) ~= (a_f, b_f), then search around it
    i0 = (a_f - ca) / 4.0
    j0 = (b_f - cb + i0) / 3.0
    best, best_d = (a0, b0, up), math.inf
    for i in range(math.floor(i0) - 1, math.floor(i0) + 3):
        for j in range(math.floor(j0) - 2, math.floor(j0) + 4):
            x, y = lattice_xy(ca + 4 * i - a_f, cb - i + 3 * j - b_f)
            d = x * x + y * y
            if d < best_d - 1e-12:
                best, best_d = (a0 + 4 * i, b0 - i + 3 * j, up), d
    return best


def lattice_cents(da: int, db: int) -> float:
    """Just-intonation distance (cents) between a triangle and its copy offset by (da, db), reduced into
    (-600, 600]: (-4, 1) -21.506 (syntonic comma), (0, -3) +41.058 (diesis), (4, -1) +21.506."""
    c = da * FIFTH_CENTS + db * THIRD_CENTS
    c -= 1200.0 * math.floor(c / 1200.0 + 0.5)
    return c + 1200.0 if c <= -600.0 else c


# ---------------------------------------------------------------------------------------------------------------
# A fixed-size ring of records
# ---------------------------------------------------------------------------------------------------------------
class RingLog:
    """A preallocated ring of ``capacity`` records of ``columns`` numbers (chords, onsets, tokens).  ``push`` writes
    the newest and overwrites the oldest when full; ``row(0)`` is the newest (a view), ``row(n-1)`` the oldest;
    ``ordered()`` copies them oldest first (into ``out`` when given, returning the filled slice)."""

    def __init__(self, capacity: int, columns: int, dtype=np.float64) -> None:
        self.data = np.zeros((int(capacity), int(columns)), dtype)
        self.capacity = int(capacity)
        self.head = 0                  # the next slot to write
        self.n = 0
        self.pushed = 0                # all-time count (a record's serial number is pushed - 1 at its push)

    def __len__(self) -> int:
        return self.n

    def push(self, *values: float) -> np.ndarray:
        """Write one record (missing trailing columns are 0); returns its row (a view, may be edited in place)."""
        r = self.data[self.head]
        r[:len(values)] = values
        r[len(values):] = 0
        self.head = (self.head + 1) % self.capacity
        self.n = min(self.n + 1, self.capacity)
        self.pushed += 1
        return r

    def row(self, back: int = 0) -> np.ndarray:
        """The record ``back`` places before the newest (0 = newest); IndexError when there are not that many."""
        if not 0 <= back < self.n:
            raise IndexError(back)
        return self.data[(self.head - 1 - back) % self.capacity]

    def ordered(self, out: Optional[np.ndarray] = None) -> np.ndarray:
        n, h = self.n, self.head
        if out is None:
            out = np.empty((n, self.data.shape[1]), self.data.dtype)
        start = (h - n) % self.capacity
        first = min(n, self.capacity - start)
        out[:first] = self.data[start:start + first]
        out[first:n] = self.data[:n - first]
        return out[:n]

    def clear(self) -> None:
        self.head = self.n = 0
