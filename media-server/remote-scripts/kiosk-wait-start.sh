#!/usr/bin/env bash
# Wait until Xorg :0 has remained alive for ≥3 seconds,
# then start the kiosk loop as user gjbm2.

MAX_WAIT=10    # total seconds to wait for Xorg

for i in $(seq 1 "$MAX_WAIT"); do
    sleep 1
    if pgrep -f "Xorg :0" >/dev/null; then
        sleep 3                     # make sure X stays up
        runuser -u gjbm2 -- /usr/local/bin/kiosk-loop.sh &
        exit 0
    fi
done

echo "kiosk-wait-start: Xorg never became stable within $MAX_WAIT s" >&2
exit 1



