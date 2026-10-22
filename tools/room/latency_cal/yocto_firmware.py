#!/usr/bin/env python3
"""Check for, and optionally install, newer firmware on the Yocto-Light sensor.

Runs ON THE KIOSK BOX (~/.venv/bin/python). light-relay.service must be stopped first,
because the Yoctopuce USB API is exclusive to one process.

  yocto_firmware.py check    report the installed and the newest available firmware
  yocto_firmware.py update   install the newest firmware if it is newer, then verify

The firmware comes from www.yoctopuce.com through the Yoctopuce library, which saves the
module's settings before flashing and restores them afterwards. Do not unplug the sensor
during an update. If one is interrupted, run `update` again: the module stays in its
bootloader and can still be flashed.
"""
import json
import sys
import time

from yoctopuce.yocto_api import YAPI, YRefParam
from yoctopuce.yocto_lightsensor import YLightSensor


def emit(**kw):
    print(json.dumps(kw), flush=True)


def find_sensor():
    sensor = YLightSensor.FirstLightSensor()
    if sensor is None:
        sys.exit("no Yoctopuce light sensor found")
    return sensor


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    err = YRefParam()
    if YAPI.RegisterHub("usb", err) != YAPI.SUCCESS:
        sys.exit(f"USB init failed (is light-relay.service still running?): {err.value}")
    try:
        sensor = find_sensor()
        module = sensor.get_module()
        emit(serial=module.get_serialNumber(), product=module.get_productName(),
             firmware=module.get_firmwareRelease(), measureType=sensor.get_measureType())
        newer = module.checkFirmware("www.yoctopuce.com", True)
        emit(newer_firmware=newer or None)
        if mode != "update":
            return
        if not newer:
            emit(result="already up to date")
            return
        emit(status="flashing; do not unplug the sensor")
        upd = module.updateFirmware(newer)
        upd.startUpdate()
        last = None
        while True:
            progress = upd.get_progress()
            if progress != last:
                emit(progress=progress, message=upd.get_progressMessage())
                last = progress
            if progress < 0 or progress >= 100:
                break
            YAPI.Sleep(500, err)
        if progress < 0:
            sys.exit(f"update failed: {upd.get_progressMessage()} (run `update` again)")

        # The module rebooted; find it again and verify.
        for _ in range(20):
            YAPI.UpdateDeviceList(err)
            sensor = YLightSensor.FirstLightSensor()
            if sensor is not None and sensor.isOnline():
                break
            time.sleep(0.5)
        module = sensor.get_module()
        emit(firmware_after=module.get_firmwareRelease(), measureType=sensor.get_measureType())

        # Does the new firmware accept the high-rate mode? Try it, then put the mode back.
        original = sensor.get_measureType()
        sensor.set_measureType(YLightSensor.MEASURETYPE_HIGH_RATE)
        YAPI.Sleep(300, err)
        sensor.load(0)
        high_rate = sensor.get_measureType()
        sensor.set_measureType(original)
        YAPI.Sleep(300, err)
        sensor.load(0)
        emit(high_rate_accepted=high_rate == YLightSensor.MEASURETYPE_HIGH_RATE,
             measureType_restored=sensor.get_measureType() == original)
    finally:
        YAPI.FreeAPI()


if __name__ == "__main__":
    main()
