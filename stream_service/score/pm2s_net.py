"""PM2S networks and their post-processing, for the analysis worker (analyse.py; needs torch, so it is only imported
there, in ~/.venvs/sm-score-ml).

Source: PM2S, https://github.com/cheriell/PM2S (pm2s/models/*.py, pm2s/models/utils.py, pm2s/features/beat.py's
        post-processing and pm2s/pm2s.py's metre rule), ported with the module and attribute names kept so the
        authors' state dicts load unchanged.  Weights: the authors' release, https://zenodo.org/records/10520196.
        L. Liu, Q. Kong, V. Morfi and E. Benetos, "Performance MIDI-to-Score Conversion by Neural Beat Tracking",
        ISMIR 2022.

MIT License

Copyright (c) 2022 Lele Liu

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Input everywhere: a note sequence, float array (n, 4) of (pitch, onset s, duration s, velocity 0-127) sorted by
onset.  The networks are bidirectional, so a note's predictions use the notes after it too: predictions for the
last second or two of a window are weaker (analyse.py only trusts beats that have notes after them).
"""
from __future__ import annotations

import pathlib

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

RESOLUTION = 0.01                         # onset-shift resolution (s)
TOLERANCE = 0.07                          # beat alignment tolerance (s)
IBI_VOCAB = int(4 / RESOLUTION) + 1
MIN_BPM = 40
KEY_VOCAB = 12                            # key signatures: the major key with that signature, C Db D ... B
# the key-signature classes as fifths (sharps > 0, flats < 0): C Db D Eb E F F# G Ab A Bb B
KEY_FIFTHS = (0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5)
WEIGHTS = {"beat": "RNNJointBeatModel.pth", "hand": "RNNHandPartModel.pth",
           "key": "RNNKeySignatureModel.pth", "ts": "CNNTimeSignatureModel.pth"}


def in_features() -> int:
    return 128 + int(4.0 / RESOLUTION) + 1 + 1 + 1


def encode_note_sequence(note_seq: torch.Tensor) -> torch.Tensor:
    x_pitch, x_onset, x_duration, x_velocity = note_seq[:, :, 0], note_seq[:, :, 1], note_seq[:, :, 2], note_seq[:, :, 3]
    pitch_onehot = F.one_hot(x_pitch.long(), 128).float()
    onset_shift = x_onset[:, 1:] - x_onset[:, :-1]
    onset_shift = torch.cat([torch.zeros_like(onset_shift[:, 0:1]), onset_shift], dim=1)
    onset_shift = torch.clamp(onset_shift, 0, 4)
    onset_shift_onehot = F.one_hot(torch.round(onset_shift / RESOLUTION).long(), int(4 / RESOLUTION + 1)).float()
    duration_raw = x_duration.float().unsqueeze(2)
    velocity_norm = x_velocity.float().unsqueeze(2) / 127.0
    return torch.cat([pitch_onehot, onset_shift_onehot, duration_raw, velocity_norm], dim=2)


class ConvBlock(nn.Module):
    def __init__(self, in_features, hidden_size=512, kernel_size=9, dropout=0.15):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(1, hidden_size // 4, kernel_size=(kernel_size, in_features), padding=(kernel_size // 2, 0)),
            nn.BatchNorm2d(hidden_size // 4), nn.ELU(), nn.Dropout(p=dropout),
            nn.Conv2d(hidden_size // 4, hidden_size // 2, kernel_size=(kernel_size, 1), padding=(kernel_size // 2, 0)),
            nn.BatchNorm2d(hidden_size // 2), nn.ELU(), nn.Dropout(p=dropout),
            nn.Conv2d(hidden_size // 2, hidden_size, kernel_size=(kernel_size, 1), padding=(kernel_size // 2, 0)),
            nn.BatchNorm2d(hidden_size), nn.ELU(), nn.Dropout(p=dropout),
        )

    def forward(self, x):
        return self.conv(x.unsqueeze(1)).squeeze(3).transpose(1, 2)


class GRUBlock(nn.Module):
    def __init__(self, in_features, hidden_size=512, gru_layers=2, dropout=0.15):
        super().__init__()
        self.grus_beat = nn.GRU(input_size=in_features, hidden_size=hidden_size, num_layers=gru_layers,
                                batch_first=True, dropout=dropout, bidirectional=True)
        self.linear = nn.Linear(hidden_size * 2, hidden_size)

    def forward(self, x):
        x, _ = self.grus_beat(x)
        return self.linear(x)


class LinearOutput(nn.Module):
    def __init__(self, in_features, out_features, activation_type="sigmoid", dropout=0.15):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        self.linear = nn.Linear(in_features, out_features)
        self.activation = {"sigmoid": nn.Sigmoid(), "softmax": nn.LogSoftmax(dim=2),
                           "softplus": nn.Softplus()}[activation_type]

    def forward(self, x):
        return self.activation(self.linear(self.dropout(x)))


class RNNJointBeatModel(nn.Module):
    def __init__(self, hidden_size=512):
        super().__init__()
        self.convs = ConvBlock(in_features=in_features())
        self.gru_beat = GRUBlock(in_features=hidden_size)
        self.gru_downbeat = GRUBlock(in_features=hidden_size)
        self.gru_tempo = GRUBlock(in_features=hidden_size)
        self.out_beat = LinearOutput(hidden_size, 1, "sigmoid")
        self.out_downbeat = LinearOutput(hidden_size, 1, "sigmoid")
        self.out_tempo = LinearOutput(hidden_size, IBI_VOCAB, "softmax")

    def forward(self, x):
        x = self.convs(encode_note_sequence(x))
        xb = self.gru_beat(x)
        xd = self.gru_downbeat(xb)
        return self.out_beat(xb).squeeze(2), self.out_downbeat(xd).squeeze(2)     # the tempo head isn't needed


class RNNHandPartModel(nn.Module):
    def __init__(self, hidden_size=512):
        super().__init__()
        self.convs = ConvBlock(in_features=in_features())
        self.gru = GRUBlock(in_features=hidden_size)
        self.out = LinearOutput(hidden_size, 1, "sigmoid")

    def forward(self, x):
        return self.out(self.gru(self.convs(encode_note_sequence(x)))).squeeze(2)


class RNNKeySignatureModel(nn.Module):
    def __init__(self, hidden_size=512):
        super().__init__()
        self.convs = ConvBlock(in_features=in_features())
        self.gru = GRUBlock(in_features=hidden_size)
        self.out = LinearOutput(hidden_size, KEY_VOCAB, "softmax")

    def forward(self, x):
        return self.out(self.gru(self.convs(encode_note_sequence(x)))).transpose(1, 2)


class CNNTimeSignatureModel(nn.Module):
    def __init__(self, hidden_size=256):
        super().__init__()
        self.convs = ConvBlock(in_features=in_features(), hidden_size=hidden_size)
        self.out_ts = LinearOutput(hidden_size, 1, "sigmoid")

    def forward(self, x):
        return self.out_ts(self.convs(encode_note_sequence(x))).squeeze(2)


class PM2S:
    """All four networks, loaded once.  ``__call__(note_seq)`` -> raw per-note outputs (numpy)."""

    def __init__(self, weights_dir: pathlib.Path, threads: int = 2, device: str = "cpu") -> None:
        torch.set_num_threads(threads)
        self.device = device                # "cuda" only for batch work (tools/score/fit_metre.py); live: cpu
        self.nets = {"beat": RNNJointBeatModel(), "hand": RNNHandPartModel(), "key": RNNKeySignatureModel(),
                     "ts": CNNTimeSignatureModel()}
        for name, net in self.nets.items():
            sd = torch.load(weights_dir / WEIGHTS[name], map_location="cpu", weights_only=True)
            net.load_state_dict(sd)
            net.to(device).eval()

    @torch.inference_mode()
    def __call__(self, note_seq: np.ndarray) -> dict:
        x = torch.tensor(note_seq, dtype=torch.float32, device=self.device).unsqueeze(0)
        beat, down = self.nets["beat"](x)
        np_ = lambda t: t.float().cpu().numpy()             # noqa: E731
        return {"beat": np_(beat[0]), "down": np_(down[0]), "hand": np_(self.nets["hand"](x)[0]),
                "key": np_(self.nets["key"](x)[0].exp()).T,                # (n, 12) probabilities
                "ts": np_(self.nets["ts"](x)[0])}


# -- post-processing (pm2s/features/beat.py) -----------------------------------------------------------------------
def beat_pps(beat_probs, downbeat_probs, onsets, prob_thresh=0.5, penalty=1.0):
    """Per-note beat/downbeat probabilities -> (beat times, downbeat times): dynamic thresholds, close onsets
    merged, downbeats merged into beats, then the DP that fills beats between notes."""
    n = len(beat_probs)
    wlen_b, wlen_db = (60.0 / MIN_BPM) * 4, (60.0 / MIN_BPM) * 8
    th_b, th_db = np.full(n, prob_thresh), np.full(n, prob_thresh)
    l_b = r_b = l_db = r_db = 0
    for i, onset in enumerate(onsets):
        while onsets[l_b] < onset - wlen_b / 2:
            l_b += 1
        while r_b < n and onsets[r_b] < onset + wlen_b / 2:
            r_b += 1
        while onsets[l_db] < onset - wlen_db / 2:
            l_db += 1
        while r_db < n and onsets[r_db] < onset + wlen_db / 2:
            r_db += 1
        th_b[i] = np.max(beat_probs[l_b:r_b]) * prob_thresh
        th_db[i] = np.max(downbeat_probs[l_db:r_db]) * prob_thresh
    beats = onsets[beat_probs > th_b]
    downbeats = onsets[downbeat_probs > th_db]
    beats = _merge_close(beats)
    downbeats = _merge_close(downbeats)
    extra = [d for d in downbeats if len(beats) == 0 or np.min(np.abs(beats - d)) > TOLERANCE * 2]
    beats = np.sort(np.concatenate([beats, extra]))
    if len(beats) >= 3:
        beats = beat_complement_dp(beats, penalty)
    return beats, downbeats


def _merge_close(t):
    if len(t) == 0:
        return t
    lo = t[np.concatenate([[True], np.abs(np.diff(t)) > TOLERANCE * 2])]
    hi = t[::-1][np.concatenate([[True], np.abs(np.diff(t[::-1])) > TOLERANCE * 2])][::-1]
    return np.mean([lo, hi], axis=0)


def beat_complement_dp(beats, penalty=1.0):
    wlen = 5
    ibis = np.diff(beats)
    filled = []
    for i in range(len(beats) - 1):
        filled.append(beats[i])
        ibi = ibis[i]
        med = np.median(ibis[max(0, i - wlen):min(len(ibis), i + wlen + 1)])
        for ratio in (2, 3, 4):
            if abs(ibi / med - ratio) / ratio < 0.15:
                for x in range(1, ratio):
                    filled.append(beats[i] + x * ibi / ratio)
    filled.append(beats[-1])
    beats = np.sort(np.array(filled))
    dp = [[beats[0], beats[1]] for _ in range(4)]
    obj = [0.0] * 4
    for i in range(2, len(beats)):
        new_dp, new_obj = [None] * 4, [0.0] * 4
        for x in range(4):
            ibi = (beats[i] - beats[i - 1]) / (x + 1)
            objs = [obj[p] + abs(np.log(ibi / max(1e-6, dp[p][-1] - dp[p][-2]))) + penalty * x for p in range(4)]
            best = int(np.argmin(objs))
            new_dp[x] = dp[best] + [beats[i - 1] + ibi * k for k in range(1, x + 1)] + [beats[i]]
            new_obj[x] = objs[best]
        dp, obj = new_dp, new_obj
    return np.array(dp[int(np.argmin(obj))])


def metre(beats, downbeats, ts_probs) -> tuple[str, int]:
    """pm2s.py's rule: the duple/triple vote and beats per downbeat -> one of 2/4 3/4 4/4 6/8, and the beat index
    the bar lines start from (the phase that best matches the predicted downbeats)."""
    triple = int(np.bincount((ts_probs > 0.5).astype(int), minlength=2).argmax()) == 1
    ratio = len(beats) / max(1, len(downbeats))
    ts = ("6/8" if ratio > 4.5 else "3/4") if triple else ("2/4" if ratio < 3 else "4/4")
    num = int(ts.split("/")[0])
    if len(downbeats) == 0 or len(beats) == 0:
        return ts, 0
    near = [int(np.argmin(np.abs(beats - d))) for d in downbeats]
    near = [i for i, d in zip(near, downbeats) if abs(beats[i] - d) < TOLERANCE * 2]
    scores = [sum(1 for i in near if i % num == phase) for phase in range(num)]
    return ts, int(np.argmax(scores))
