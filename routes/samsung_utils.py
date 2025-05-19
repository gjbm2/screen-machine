"""
Utility functions for controlling Samsung displays.
"""

from samsungtvws import SamsungTVWS
from utils.logger import info, error, debug, warning
from routes.utils import _load_json_once
import json, time, threading
from typing import Literal
from wakeonlan import send_magic_packet
from contextlib import suppress

_tv_cache = {}

def _get_tv(publish_destination: str) -> SamsungTVWS:
    """
    Return a cached SamsungTVWS object for the given display.
    """
    if publish_destination in _tv_cache:
        return _tv_cache[publish_destination]

    destinations = _load_json_once("destination", "publish-destinations.json")
    for dest in destinations:
        if dest['id'] == publish_destination:
            ip_address = dest.get('ip-address')
            token_file = dest.get('samsung-token')
            if ip_address and token_file:
                tv = SamsungTVWS(
                    host=ip_address,
                    name="screen-machine",
                    port=8002,
                    token_file=token_file,
                    timeout=5  # Short network timeout so calls don't hang indefinitely
                )
                debug(f"[get_tv] Created and cached TV object for {publish_destination}")
                _tv_cache[publish_destination] = tv
                return tv
            else:
                error(f"Missing IP address or token file for {publish_destination}")
                return None

    error(f"No destination config found for {publish_destination}")
    return None

def device_info(publish_destination: str):
    tv = _get_tv(publish_destination)
    if tv == None:
        print("No TV found.")
        return

    device_info = tv.rest_device_info()
    #app_info = tv.rest_app_status()
    art_status = tv.art().get_artmode()

    # Retrieve information about the currently selected art
    info = tv.art().get_current()
    #info('current art: {}'.format(info))
    #content_id = info['content_id']

    print(f"DEVICE INFO\n{json.dumps(device_info, indent=2)}")
    print(f"\n\nART MODE\n{json.dumps(art_status, indent=2)}")
    print(f"\n\nART\n{json.dumps(info, indent=2)}")
    #print(f"APP INFO\n{json.dumps(app_info, indent=2)}")

PowerState = Literal["off", "art-mode", "on", "standby"]
def get_power_state(publish_destination: str) -> PowerState:
    tv = _get_tv(publish_destination)
    if not tv:
        return "off"

    # ------------- REST check -------------
    try:
        pstate = (
            tv.rest_device_info()
              .get("device", {})
              .get("PowerState", "unknown")
              .lower()
        )
        if pstate == "standby":
            return "standby"
        if pstate != "on":
            return "off"                 # covers "unknown", "off", etc.
    except Exception:
        return "off"

    # ------------- Art-Mode check -------------
    try:
        if str(tv.art().get_artmode()).lower() == "on":
            return "art-mode"
    except Exception:
        pass

    return "on"

def device_sleep(publish_destination: str) -> None:
    state = get_power_state(publish_destination)

    if state in ("on", "standby"):
        tv = _get_tv(publish_destination)
        tv.send_key("KEY_POWER")  # toggles to Art Mode
        info(f"{publish_destination} sent to sleep (Art Mode)")
        return True
    elif state == "art-mode":
        info(f"{publish_destination} already in Art Mode — no action taken")
        return False
    else:
        info(f"{publish_destination} appears to be off — cannot sleep it")
        return False

def device_wake(publish_destination: str) -> None:
    state = get_power_state(publish_destination)

    if state == "off":
        # Fallback to Wake-on-LAN
        destinations = _load_json_once("destination", "publish-destinations.json")
        config = next((d for d in destinations if d["id"] == publish_destination), None)
        mac = config.get("mac") if config else None

        if mac:
            send_magic_packet(mac)
            info(f"{publish_destination} appears off — sent Wake-on-LAN packet")
            time.sleep(2)  # give TV a moment to power up
            state = get_power_state(publish_destination)
        else:
            error(f"{publish_destination} is off and no MAC address is available for WoL")
            return False

    if state == "standby":
        # Wake with KEY_POWER, then re-check
        tv = _get_tv(publish_destination)
        if not tv:
            error(f"Could not retrieve TV object to wake {publish_destination}")
            return False
        tv.send_key("KEY_POWER")
        info(f"{publish_destination} sent KEY_POWER to exit standby")
        time.sleep(1.5)
        state = get_power_state(publish_destination)

    if state == "art-mode":
        # Exit Art Mode
        tv = _get_tv(publish_destination)
        if tv:
            tv.send_key("KEY_POWER")
            info(f"{publish_destination} was in Art Mode — sent KEY_POWER to exit")
            return True
        else:
            error(f"Could not retrieve TV object to exit Art Mode for {publish_destination}")
            return False
    elif state == "on":
        info(f"{publish_destination} is already on — no action needed")
        return False
    else:
        error(f"{publish_destination} is still off or unresponsive after wake attempt")
        return False

def device_sync(publish_destination: str) -> None:
    """
    Sync the Samsung display state.
    
    Args:
        publish_destination: The ID of the display to control
    """
    # TODO: Implement Samsung display sync command

# --- Device standby helper ----------------------------------------------

def device_standby(publish_destination: str) -> None:
    # Although wacky, this does seem to work if TV is first in wake mode, though I'll be buggered if I know why...
    if device_wake(publish_destination):
        time.sleep(3)	

    # Now send standby
    info(f"{publish_destination} sending to standby (takes 30s).")
    tv = _get_tv(publish_destination)
    tv.hold_key("KEY_POWER", 8)

    #debug("Hold for 10s while daemon completes...")
    #time.sleep(10)

    return True

