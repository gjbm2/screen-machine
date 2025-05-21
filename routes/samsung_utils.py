"""
Utility functions for controlling Samsung displays.
"""

from samsungtvws import SamsungTVWS
from utils.logger import info, error, debug, warning
from routes.utils import _load_json_once, seq_to_filenames
import json, time, threading, os
from typing import Literal
from wakeonlan import send_magic_packet
from contextlib import suppress
from pathlib import Path

_tv_cache = {}

def _get_tv(publish_destination: str, timeout: int = 5) -> SamsungTVWS:
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
                try:
                    tv = SamsungTVWS(
                        host=ip_address,
                        name="screen-machine",
                        port=8002,
                        token_file=token_file,
                        timeout=timeout  # Use the passed timeout value
                    )
                    debug(f"[get_tv] Created and cached TV object for {publish_destination}")
                    _tv_cache[publish_destination] = tv
                    return tv
                except Exception as e:
                    if "token" in str(e).lower():
                        warning(f"[get_tv] Token expired for {publish_destination}, attempting re-auth")
                        # Clear any existing token file
                        try:
                            os.remove(token_file)
                            debug(f"[get_tv] Removed expired token file: {token_file}")
                        except OSError:
                            pass
                        # Try one more time with fresh auth
                        tv = SamsungTVWS(
                            host=ip_address,
                            name="screen-machine",
                            port=8002,
                            token_file=token_file,
                            timeout=timeout
                        )
                        debug(f"[get_tv] Successfully re-authenticated {publish_destination}")
                        _tv_cache[publish_destination] = tv
                        return tv
                    else:
                        error(f"[get_tv] Failed to create TV object: {e}")
                        return None
            else:
                error(f"Missing IP address or token file for {publish_destination}")
                return None

    error(f"No destination config found for {publish_destination}")
    return None

def device_info(publish_destination: str, timeout: int = 5):
    tv = _get_tv(publish_destination, timeout)
    if tv == None:
        print("No TV found.")
        return

    device_info = tv.rest_device_info()
    #app_info = tv.rest_app_status()
    art_status = tv.art(timeout).get_artmode()

    # Retrieve information about the currently selected art
    info = tv.art(timeout).get_current()
    #info('current art: {}'.format(info))
    #content_id = info['content_id']

    print(f"DEVICE INFO\n{json.dumps(device_info, indent=2)}")
    print(f"\n\nART MODE\n{json.dumps(art_status, indent=2)}")
    print(f"\n\nART\n{json.dumps(info, indent=2)}")
    #print(f"APP INFO\n{json.dumps(app_info, indent=2)}")

PowerState = Literal["off", "art-mode", "on", "standby"]
def get_power_state(publish_destination: str, timeout: int = 5) -> PowerState:
    tv = _get_tv(publish_destination, timeout)
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
        if str(tv.art(timeout).get_artmode()).lower() == "on":
            return "art-mode"
    except Exception:
        pass

    return "on"

def device_sleep(publish_destination: str, timeout: int = 5) -> None:
    state = get_power_state(publish_destination, timeout)

    if state in ("on", "standby"):
        tv = _get_tv(publish_destination, timeout)
        tv.send_key("KEY_POWER")  # toggles to Art Mode
        info(f"{publish_destination} sent to sleep (Art Mode)")
        return True
    elif state == "art-mode":
        info(f"{publish_destination} already in Art Mode — no action taken")
        return False
    else:
        info(f"{publish_destination} appears to be off — cannot sleep it")
        return False

def device_wake(publish_destination: str, timeout: int = 5) -> None:
    state = get_power_state(publish_destination, timeout)

    if state == "off":
        # Fallback to Wake-on-LAN
        destinations = _load_json_once("destination", "publish-destinations.json")
        config = next((d for d in destinations if d["id"] == publish_destination), None)
        mac = config.get("mac") if config else None

        if mac:
            send_magic_packet(mac)
            info(f"{publish_destination} appears off — sent Wake-on-LAN packet")
            time.sleep(2)  # give TV a moment to power up
            state = get_power_state(publish_destination, timeout)
        else:
            error(f"{publish_destination} is off and no MAC address is available for WoL")
            return False

    if state == "standby":
        # Wake with KEY_POWER, then re-check
        tv = _get_tv(publish_destination, timeout)
        if not tv:
            error(f"Could not retrieve TV object to wake {publish_destination}")
            return False
        tv.send_key("KEY_POWER")
        info(f"{publish_destination} sent KEY_POWER to exit standby")
        time.sleep(1.5)
        state = get_power_state(publish_destination, timeout)

    if state == "art-mode":
        # Exit Art Mode
        tv = _get_tv(publish_destination, timeout)
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

def device_sync(publish_destination: str, debug_mode: bool = False, timeout: int = 5) -> None:
    """
    Synchronise the Frame TV's "My Photos" folder so that it contains *exactly*
    the bucket favourites (JPG only) for *publish_destination* in the *same*
    order as the bucket sequence.  Order on the TV cannot be changed directly
    so we delete everything first then upload in the desired order.

    The work is performed in a background thread so that a long-running sync
    does not block the scheduler.  If the TV is unreachable, the function
    returns immediately and logs an error.
    """

    def log_debug(msg):
        if debug_mode:
            from utils.logger import debug as _debug
            _debug(msg)

    # ----- async wrapper ---------------------------------------------------
    def _sync_task():
        try:
            info(f"[device_sync] Starting image sync for {publish_destination}")
            log_debug(f"Loading meta for {publish_destination}")

            # 1) Collect JPG favourites in bucket order --------------------
            try:
                from routes.bucketer import load_meta, bucket_path  # lazy import – heavy modules
                meta = load_meta(publish_destination)
                log_debug(f"Loaded meta: {meta}")
            except Exception as e:
                error(f"[device_sync] Failed to load bucket meta: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
                return

            fav_set = set(meta.get("favorites", []))
            seq_raw = meta.get("sequence", [])
            log_debug(f"fav_set: {fav_set}")
            log_debug(f"seq_raw: {seq_raw}")

            # Use the utility function to normalize sequence entries
            filenames = seq_to_filenames(seq_raw)
            
            # Filter for favorites and JPG files only
            ordered_jpgs = [
                fname for fname in filenames
                if fname in fav_set and Path(fname).suffix.lower() in {".jpg", ".jpeg"}
            ]

            log_debug(f"Ordered JPGs before limit: {ordered_jpgs}")
            # --- TEMPORARY TEST LIMIT ------------------------------------
            # Limit to first 3 images so test runs quickly
            #ordered_jpgs = ordered_jpgs[:3]
            #log_debug(f"Ordered JPGs after limit: {ordered_jpgs}")

            if not ordered_jpgs:
                info(f"[device_sync] No favourite JPGs in bucket {publish_destination}; nothing to sync")
                return

            # 2) Resolve absolute paths ------------------------------------
            bucket_dir = bucket_path(publish_destination)
            desired_paths = [bucket_dir / fname for fname in ordered_jpgs]
            for p in desired_paths:
                if not p.exists():
                    warning(f"[device_sync] Expected file missing in bucket: {p}")
                log_debug(f"Resolved path: {p}")

            # 3) Connect to TV ---------------------------------------------
            tv = _get_tv(publish_destination, timeout)
            if not tv:
                error(f"[device_sync] Unable to get TV object for {publish_destination}")
                return
            log_debug(f"Connected to TV: {tv}")

            # Ensure Art Mode supported
            try:
                if not tv.art(timeout).supported():
                    error(f"[device_sync] Art Mode not supported on device {publish_destination}")
                    return
                log_debug(f"Art Mode supported on device {publish_destination}")
            except Exception as e:
                error(f"[device_sync] Error checking Art Mode support: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
                return

            # 4) Retrieve existing uploads in My Photos --------------------
            try:
                existing_items = tv.art(timeout).available("MY-C0002")  # category for uploads
                existing_ids = [item.get("content_id") for item in existing_items if item.get("content_id")]
                log_debug(f"Existing items: {existing_items}")
                log_debug(f"Existing IDs: {existing_ids}")
            except Exception as e:
                error(f"[device_sync] Failed to list existing art: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
                existing_ids = []

            # 5) Upload the first image immediately so TV shows something -----
            uploaded_ids = []
            first_content_id = None
            try:
                first_path = desired_paths[0]
                log_debug(f"Uploading first image early: {first_path}")
                first_content_id = tv.art(timeout).upload(
                    str(first_path),
                    matte="none",
                    portrait_matte="none",
                )
                if first_content_id:
                    uploaded_ids.append(first_content_id)
                    info(f"[device_sync] (1/{len(desired_paths)}) Uploaded first image {first_path.name} as {first_content_id}")
                    # Immediately display it so default image is avoided
                    try:
                        tv.art(timeout).select_image(first_content_id)
                        log_debug("Displayed first uploaded image to avoid default art")
                    except Exception as e:
                        warning(f"[device_sync] Failed to display first image: {e}")
                else:
                    warning(f"[device_sync] First upload returned no content_id for {first_path.name}")
            except Exception as e:
                error(f"[device_sync] Failed initial upload for first image: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()

            # 6) Delete existing images (excluding the first one if it existed) ----
            if existing_ids:
                try:
                    tv.art().delete_list(existing_ids)
                    info(f"[device_sync] Deleted {len(existing_ids)} existing images from TV")
                    log_debug(f"Deleted IDs: {existing_ids}")
                    time.sleep(1)
                except Exception as e:
                    warning(f"[device_sync] Failed to delete some images: {e}")
                    if debug_mode:
                        import traceback
                        traceback.print_exc()

            # 7) Upload the remaining desired images ----------------------------
            for idx, path in enumerate(desired_paths[1:], start=2):
                try:
                    log_debug(f"Uploading {path} (index {idx})")
                    content_id = tv.art(timeout).upload(
                        str(path),
                        matte="none",
                        portrait_matte="none",
                    )
                    if content_id:
                        uploaded_ids.append(content_id)
                        info(f"[device_sync] ({idx}/{len(desired_paths)}) Uploaded {path.name} as {content_id}")
                        log_debug(f"Upload success: {path} as {content_id}")
                    else:
                        warning(f"[device_sync] ({idx}/{len(desired_paths)}) Upload returned no content_id for {path.name}")
                        log_debug(f"Upload returned no content_id: {path}")
                except Exception as e:
                    error(f"[device_sync] ({idx}/{len(desired_paths)}) Failed to upload {path.name}: {e}")
                    log_debug(f"Upload failed: {path} error: {e}")
                    if debug_mode:
                        import traceback
                        traceback.print_exc()

            # 8) Ensure slideshow is enabled -----------------------------------
            try:
                tv.art(timeout).set_slideshow_status(duration=2, type=True, category=2)  # 2-minute shuffle slideshow of My Photos
                log_debug("Enabled slideshow (2-minute shuffle) on TV")
            except Exception as e:
                warning(f"[device_sync] Unable to enable slideshow: {e}")

            info(f"[device_sync] Sync complete for {publish_destination}: {len(uploaded_ids)} images uploaded")
            log_debug(f"Uploaded IDs: {uploaded_ids}")

        except Exception as e:
            error(f"[device_sync] Unexpected error: {e}")
            if debug_mode:
                import traceback
                traceback.print_exc()

    # ----------------------------------------------------------------------
    # Launch the sync in the background ------------------------------------
    sync_thread = threading.Thread(target=_sync_task, name=f"samsung-sync-{publish_destination}", daemon=True)
    sync_thread.start()
    debug(f"[device_sync] Spawned sync thread for {publish_destination} (id={sync_thread.ident})")

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

def pair_device(publish_destination: str) -> bool:
    """Delete the stored token so the next connection forces a fresh pairing.

    Returns True if a token file was removed, False otherwise.
    """
    # Remove from cache so that _get_tv creates a fresh instance next time
    _tv_cache.pop(publish_destination, None)

    destinations = _load_json_once("destination", "publish-destinations.json")
    config = next((d for d in destinations if d["id"] == publish_destination), None)
    if not config:
        error(f"[pair_device] No destination config for {publish_destination}")
        return False
    token_file = config.get("samsung-token")
    if not token_file:
        warning(f"[pair_device] No token_file configured for {publish_destination}")
        return False
    try:
        Path(token_file).unlink(missing_ok=True)
        info(f"[pair_device] Removed token file for {publish_destination}: {token_file}")
        return True
    except Exception as e:
        error(f"[pair_device] Failed to remove token file {token_file}: {e}")
        return False

