"""Synthetic NV12 frame source with in-band instrumentation.

Every frame carries
  * a 40-bit barcode (32-bit frame id + CRC-8) along the top-left edge, which
    the browser can read back to prove which frame is on screen,
  * a large human-readable capture clock (server time, UTC, ms) for phone-camera
    glass-to-glass checks,
  * a white vertical sweep bar that moves one fixed step per frame (makes
    drops, repeats and tearing visible on the TV / in photos).

Content complexity is adjustable because encoded frame size (and therefore
network transfer time) depends on it:
  0 flat     - moving bars on grey (tiny frames)
  1 smooth   - scrolling gradients
  2 textured - scrolling natural-ish texture (typical)
  3 noise    - scrolling random noise (worst case for the encoder)
"""
from __future__ import annotations

import time

import cv2
import numpy as np

BARCODE_BITS = 40  # 32-bit frame id + 8-bit CRC


def crc8(value: int) -> int:
    crc = 0
    for byte in value.to_bytes(4, "big"):
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def barcode_bits(frame_id: int) -> list[int]:
    word = (frame_id & 0xFFFFFFFF) << 8 | crc8(frame_id & 0xFFFFFFFF)
    return [(word >> (BARCODE_BITS - 1 - i)) & 1 for i in range(BARCODE_BITS)]


def barcode_geometry(width: int, height: int) -> dict:
    """Block size and layout, shared with the client (sent in the config message)."""
    block = max(16, (height // 45) & ~1)
    # two calibration blocks (black, white) precede the data bits
    return {"block": block, "x0": 0, "y0": 0, "count": BARCODE_BITS + 2}


class Stamper:
    """In-band instrumentation shared by every frame source (CPU or GPU): sweep bar, barcode, clock."""

    def __init__(self, width: int, height: int, fps: int):
        self.w, self.h = width, height
        self.geom = barcode_geometry(width, height)
        self.speed = max(2, (width // (fps * 4)) & ~1)  # sweep bar crosses a screen-width in ~2 s
        self.font_scale = height / 360
        self.font_thick = max(2, height // 270)

    def stamp(self, Y: np.ndarray, UV: np.ndarray, frame_id: int, capture_ms: float) -> None:
        w, h = self.w, self.h
        # sweep bar
        bar_w = max(8, w // 160) & ~1
        bx = (frame_id * self.speed * 2) % (w - bar_w)
        bx -= bx % 2
        Y[:, bx:bx + bar_w] = 235
        UV[:, bx:bx + bar_w] = 128
        # barcode: calibration black, white, then 40 data bits
        b = self.geom["block"]
        bits = [0, 1] + barcode_bits(frame_id)
        for i, bit in enumerate(bits):
            Y[0:b, i * b:(i + 1) * b] = 235 if bit else 16
        UV[0:b // 2, 0:len(bits) * b] = 128
        # human-readable capture clock (server time, UTC, ms) + frame id
        t = time.gmtime(capture_ms / 1000)  # UTC, matches the browser clock overlays
        label = time.strftime("%H:%M:%S", t) + f".{int(capture_ms % 1000):03d}"
        y0 = h - int(h * 0.06)
        x0 = int(w * 0.02)
        box_w = int(self.font_scale * 260)
        box_h = int(self.font_scale * 42)
        Y[y0 - box_h:y0 + box_h // 3, x0 - 10:x0 + box_w] = 16
        UV[(y0 - box_h) // 2:(y0 + box_h // 3) // 2, x0 - 10:x0 + box_w] = 128
        cv2.putText(Y, label, (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, self.font_scale, 235,
                    self.font_thick, cv2.LINE_8)
        cv2.putText(Y, f"#{frame_id}", (x0, y0 - box_h - int(self.font_scale * 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, self.font_scale * 0.5, 235, max(1, self.font_thick // 2), cv2.LINE_8)


class PatternSource:
    def __init__(self, width: int, height: int, fps: int, complexity: int = 2, seed: int = 7):
        if width % 2 or height % 2:
            raise ValueError("NV12 needs even dimensions")
        self.w, self.h, self.fps = width, height, fps
        self.complexity = complexity
        self.geom = barcode_geometry(width, height)
        rng = np.random.default_rng(seed)
        tw = width * 2  # texture twice as wide; frames are sliding windows into it
        x = np.linspace(0, 4 * np.pi, tw, dtype=np.float32)
        y = np.linspace(0, 2 * np.pi, height, dtype=np.float32)[:, None]
        if complexity == 0:
            tex = np.full((height, tw), 110, np.uint8)
            tex[:, (np.arange(tw) // (width // 16)) % 2 == 0] = 140
        elif complexity == 1:
            tex = (128 + 90 * np.sin(x + y)).astype(np.uint8)
        elif complexity == 2:
            base = rng.integers(0, 255, (height // 8 + 1, tw // 8 + 1), dtype=np.uint8)
            base = cv2.resize(cv2.GaussianBlur(base, (0, 0), 1.2), (tw, height), interpolation=cv2.INTER_CUBIC)
            detail = rng.integers(0, 40, (height, tw), dtype=np.uint8)
            grad = (60 * np.sin(x / 2 + y)).astype(np.int16)
            tex = np.clip(base.astype(np.int16) // 2 + 50 + detail + grad, 16, 235).astype(np.uint8)
        else:
            tex = rng.integers(16, 236, (height, tw), dtype=np.uint8)
        self.tex_y = np.ascontiguousarray(tex)
        # chroma: slowly varying colour field, also slid
        cx = np.linspace(0, 2 * np.pi, tw // 2, dtype=np.float32)
        cy = np.linspace(0, np.pi, height // 2, dtype=np.float32)[:, None]
        u = (128 + 40 * np.sin(cx + cy)).astype(np.uint8)
        v = (128 + 40 * np.cos(cx * 1.3 - cy)).astype(np.uint8)
        self.tex_uv = np.ascontiguousarray(np.stack([u, v], axis=-1).reshape(height // 2, tw))  # interleaved
        self.frame = np.empty(width * height * 3 // 2, np.uint8)
        self.Y = self.frame[: width * height].reshape(height, width)
        self.UV = self.frame[width * height:].reshape(height // 2, width)
        self.speed = max(2, (width // (fps * 4)) & ~1)  # texture scroll: a screen-width in ~4 s
        self.stamper = Stamper(width, height, fps)

    def render(self, frame_id: int, capture_ms: float) -> memoryview:
        w = self.w
        off = (frame_id * self.speed) % w
        off -= off % 2
        np.copyto(self.Y, self.tex_y[:, off:off + w])
        np.copyto(self.UV, self.tex_uv[:, off:off + w])
        self.stamper.stamp(self.Y, self.UV, frame_id, capture_ms)
        return memoryview(self.frame)
