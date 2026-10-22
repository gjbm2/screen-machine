#!/usr/bin/env python3
"""Read-only health check of the WSL2 kernel clock (see docs/KIOSK_INFRASTRUCTURE_NOTES.md, "Workstation clock").

Background: WSL runs its own chronyd (hidden, in WSL's root namespace) that steers the shared kernel clock to
the Windows host clock (PHC0). Until 2026-10-11 Ubuntu's systemd-timesyncd fought it, chrony learned a
-21,000 ppm correction and the WSL clock ran ~2 % fast. timesyncd is now masked. This script shows whether
the fight is back.

Prints:
  1. kernel adjtimex tick/freq        healthy: tick 9999-10001, |freq| < ~200 ppm
  2. WSL chrony tracking (127.0.0.1:323, chronyc cmdmon protocol v6)
                                       healthy: ref PHC0, |freq_ppm| in the tens to low hundreds
  3. systemd-timesyncd state           healthy: "inactive / masked"
  4. CLOCK_MONOTONIC vs MONOTONIC_RAW  healthy: within about +-200 ppm over 10 s
Needs no root. Exit status 1 if the clock rate is off by more than 1000 ppm.
"""
import ctypes
import random
import socket
import struct
import subprocess
import sys
import time


def adjtimex():
    buf = ctypes.create_string_buffer(208)  # struct timex on x86_64
    ctypes.CDLL(None).adjtimex(buf)
    freq = struct.unpack_from("l", buf, 16)[0] / 65536  # scaled ppm
    tick = struct.unpack_from("l", buf, 88)[0]
    return tick, freq


def rate_vs_raw(seconds=10.0):
    r0, m0 = time.clock_gettime(time.CLOCK_MONOTONIC_RAW), time.monotonic()
    time.sleep(seconds)
    r1, m1 = time.clock_gettime(time.CLOCK_MONOTONIC_RAW), time.monotonic()
    return ((m1 - m0) / (r1 - r0) - 1) * 1e6


def _chrony_float(x):
    exp = x >> 25
    if exp >= 64:
        exp -= 128
    exp -= 25
    coef = x % (1 << 25)
    if coef >= (1 << 24):
        coef -= 1 << 25
    return coef * 2.0 ** exp


def chrony_tracking():
    hdr = struct.pack("!BBBBHHIII", 6, 1, 0, 0, 33, 0, random.getrandbits(32), 0, 0)  # REQ_TRACKING
    pkt = hdr + b"\0" * (112 - len(hdr))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        s.sendto(pkt, ("127.0.0.1", 323))
        data, _ = s.recvfrom(4096)
    status = struct.unpack("!H", data[8:10])[0]
    if status != 0:
        raise RuntimeError(f"chrony replied status {status}")
    b = data[28:]
    ref = struct.pack("!I", struct.unpack("!I", b[0:4])[0]).decode(errors="replace")
    names = ["current_correction_s", "last_offset_s", "rms_offset_s", "freq_ppm", "resid_freq_ppm",
             "skew_ppm", "root_delay_s", "root_dispersion_s", "last_update_interval_s"]
    vals = [_chrony_float(v) for v in struct.unpack("!9I", b[40:76])]
    return ref, dict(zip(names, vals))


def main():
    tick, freq = adjtimex()
    print(f"1. adjtimex: tick={tick} (nominal 10000)  freq={freq:+.1f} ppm")
    try:
        ref, t = chrony_tracking()
        print(f"2. WSL chrony: ref={ref}  freq={t['freq_ppm']:+.1f} ppm  last_offset={t['last_offset_s'] * 1e3:+.2f} ms"
              f"  update every {t['last_update_interval_s']:.1f} s")
    except Exception as e:  # not on WSL, or chrony not answering
        print(f"2. WSL chrony: no answer on 127.0.0.1:323 ({e})")
    st = subprocess.run(["systemctl", "is-active", "systemd-timesyncd"], capture_output=True, text=True).stdout.strip()
    en = subprocess.run(["systemctl", "is-enabled", "systemd-timesyncd"], capture_output=True, text=True).stdout.strip()
    print(f"3. systemd-timesyncd: {st} / {en}")
    ppm = rate_vs_raw()
    print(f"4. CLOCK_MONOTONIC vs MONOTONIC_RAW over 10 s: {ppm:+.0f} ppm")
    ok = abs(ppm) < 1000
    print("OK" if ok else "CLOCK RATE IS OFF: see docs/KIOSK_INFRASTRUCTURE_NOTES.md, 'Workstation clock'")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
