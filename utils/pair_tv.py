"""Pair this machine with the deployment's Samsung Frames (accept the prompt on each TV).

The TVs come from the deployment's publish-destinations.json: every destination with an ``ip-address`` and a
``samsung-token`` (a bare file name). The token is written to ~/.config/screen-machine/samsung/<name> (0600).

    python -m utils.pair_tv                 # every Samsung destination
    python -m utils.pair_tv north-screen    # only these ids
"""
import os
import sys

from samsungtvws import SamsungTVWS

import smconfig
from routes.samsung_utils import token_path


def main(ids: list[str]) -> int:
    tvs = [d for d in smconfig.destinations() if d.get("ip-address") and d.get("samsung-token")
           and (not ids or d["id"] in ids)]
    if not tvs:
        print("no Samsung destinations (ip-address + samsung-token) to pair", file=sys.stderr)
        return 1
    for d in tvs:
        token_file = token_path(d["samsung-token"])
        print(f"pairing {d['id']} at {d['ip-address']} (token: {token_file})")
        tv = SamsungTVWS(
            host=d["ip-address"],
            name="screen-machine",
            port=8002,  # port 8002 is for encrypted connection, try 8001 if this fails
            token_file=token_file,
        )
        tv.shortcuts().home()  # Or use .power() to trigger connection
        if os.path.exists(token_file):
            os.chmod(token_file, 0o600)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
