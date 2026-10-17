"""pc22-synth: plays the test piano out loud on PC22, so PC22's mic hears it (like the boundary mic and the Steinway).

    py -3 pc22_synth.py        (Windows; started by show_sender.py --test-piano through WSL interop, never by hand)

Raw MIDI on stdin, three bytes a message (note on/off, CC 64/66/67 pedals; every channel as it comes), each played at
once on Windows' built-in synth, "Microsoft GS Wavetable Synth" (winmm; Python's standard library only), which sounds
on Windows' default output device. It prints ``ready <device>`` once the synth is open (``error <why>`` and exit 1
if it can't be), and on end of input (show_sender stopped or died) it silences every note and exits.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

SYNTH = "GS Wavetable"                     # only Windows' own synth: never a MIDI device plugged into PC22 (the piano)


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


def main() -> int:
    try:
        winmm = ctypes.windll.winmm
    except AttributeError:
        print("error not Windows", flush=True)
        return 1
    dev, name = None, ""
    for i in range(winmm.midiOutGetNumDevs()):
        caps = MIDIOUTCAPSW()
        if winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0 and SYNTH in caps.szPname:
            dev, name = i, caps.szPname
            break
    if dev is None:
        print(f"error no {SYNTH} synth", flush=True)
        return 1
    h = wintypes.HANDLE()
    rc = winmm.midiOutOpen(ctypes.byref(h), dev, 0, 0, 0)
    if rc != 0:
        print(f"error midiOutOpen failed ({rc})", flush=True)
        return 1
    try:
        for ch in range(16):
            winmm.midiOutShortMsg(h, 0xC0 | ch)                 # program 0: acoustic grand
        print(f"ready {name}", flush=True)
        inp = sys.stdin.buffer
        while True:
            msg = inp.read(3)
            if len(msg) < 3:
                break
            st, d1, d2 = msg
            if st & 0x80:
                winmm.midiOutShortMsg(h, st | (d1 & 0x7F) << 8 | (d2 & 0x7F) << 16)
    finally:
        winmm.midiOutReset(h)                                   # every note off, pedals up
        winmm.midiOutClose(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
