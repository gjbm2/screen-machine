#!/usr/bin/env python3
"""samsung_control.py

Command-line helper to control Samsung Frame TVs and sync images.

Usage
-----
python3 samsung_control.py <publish_destination> [--wait 180] [--action {info,sync,sleep,wake}] [--timeout 30]

• <publish_destination> is the ID from *publish-destinations.json* (e.g.
  "north-screen", "south-screen", ...).
• The script spawns the background sync thread and then waits a configurable
  number of seconds so the uploads can finish and you can observe the log
  output.  Set --wait 0 if you just want to fire-and-forget.
• --action: Choose the action to perform. Default is 'sync'.
• --timeout: Timeout in seconds for TV operations (default: 30).
"""

import argparse
import os
import sys
import time
import logging

# Ensure project root is on sys.path when executed from anywhere
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import after path tweak
from routes.samsung_utils import device_sync, device_info, device_sleep, device_wake, pair_device
from utils.logger import info, debug, warning, error


def main() -> None:
    parser = argparse.ArgumentParser(description="Control Samsung Frame TVs and sync images")
    parser.add_argument("destination", help="publish-destination ID (as seen in publish-destinations.json)")
    parser.add_argument("--wait", type=int, default=180, help="seconds to keep the process alive while the background sync runs (0 = exit immediately)")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    parser.add_argument("--action", choices=["info", "sync", "sleep", "wake"], default="sync", help="action to perform (default: sync)")
    parser.add_argument("--timeout", type=int, default=30, help="timeout in seconds for TV operations (default: 30)")
    parser.add_argument("--pair", action="store_true", help="delete stored token first to force pairing")
    parser.add_argument("--delay", type=int, default=0, help="delay execution by specified seconds before proceeding (default: 0)")
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")
    info(f"Starting {args.action} for destination: {args.destination}")

    # Delay execution if requested
    if args.delay > 0:
        info(f"Delaying execution by {args.delay} seconds...")
        time.sleep(args.delay)

    # Pair first if requested so subsequent actions use fresh token
    if args.pair:
        removed = pair_device(args.destination)
        if removed:
            info("Token cleared; please accept the pairing prompt on the TV then re-run the command if it fails the first time.")
        else:
            warning("Pair flag set but no token file was removed (maybe it didn't exist).")

    # Perform the requested action
    if args.action == "info":
        device_info(args.destination, timeout=args.timeout)
    elif args.action == "sync":
        device_sync(args.destination, debug_mode=args.debug, timeout=args.timeout)
    elif args.action == "sleep":
        device_sleep(args.destination, timeout=args.timeout)
    elif args.action == "wake":
        device_wake(args.destination, timeout=args.timeout)

    # Optionally wait so we can watch progress in logs
    if args.wait > 0:
        info(f"Waiting {args.wait}s for sync thread to complete … (Ctrl-C to abort)")
        try:
            time.sleep(args.wait)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
