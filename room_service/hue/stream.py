"""HueStream frames in the xy+brightness colour space, shared by room_service's HueOutput and show_service's driver.

No room_service dependencies beyond room_service.hue.colour, so another process can import this read-only:
    from room_service.hue.stream import xy_bri, xy_streamer, GAMMA

In HueStream's RGB colour space the bridge takes brightness from the colour's luminance, so saturated colours (blue,
red) came out dim. Here brightness = max(r, g, b) ** gamma (below 1 lifts the mid-levels) and the colour is the xy of
the full-level rgb.
"""

from __future__ import annotations

from .colour import rgb_to_xy

GAMMA = 0.6
COLOR_SPACE_BYTE = 14       # HueStream v2 header: "HueStream", version(2), seq, reserved(2), colour space, reserved


def xy_bri(rgb, gamma: float = GAMMA) -> tuple[int, int, int]:
    """rgb 0-1 (already dimmed) -> 16-bit (x, y, brightness): xy of the full-level colour, brightness from the
    brightest channel."""
    level = max(0.0, min(1.0, max(rgb)))
    x, y = rgb_to_xy(*(max(0.0, min(1.0, c / level)) for c in rgb)) if level > 0 else rgb_to_xy(0, 0, 0)
    return round(x * 0xFFFF), round(y * 0xFFFF), round(level ** gamma * 0xFFFF)


def xy_streamer():
    """The pinned hue-entertainment 0.1.2 HueDtlsStreamer, sending (channel_id, x, y, bri) tuples (16-bit values from
    xy_bri) in the xy+brightness colour space. connect(host, app_key, clientkey, area_id) / send_colors / disconnect
    as the base class."""
    from hue_entertainment import HueDtlsStreamer
    from hue_entertainment.constants import COLOR_SPACE_XY

    class XyStreamer(HueDtlsStreamer):
        def _build_huestream_message(self, commands) -> bytes:
            msg = bytearray(super()._build_huestream_message([]))     # header + area id, sequence advanced
            msg[COLOR_SPACE_BYTE] = COLOR_SPACE_XY
            for ch, x, y, bri in commands:
                msg += bytes([ch & 0xFF]) + x.to_bytes(2, "big") + y.to_bytes(2, "big") + bri.to_bytes(2, "big")
            return bytes(msg)

    return XyStreamer()
