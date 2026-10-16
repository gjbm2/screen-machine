#!/usr/bin/env bash
# Copy the PC22 audio agent (pc22_agent.py, show_sender.py, show_service/vban.py) to Windows, where Spectre's
# audio-agent card runs it with py -3.12 (local-automation scripts/windows/audio-agent/). Restart the card after.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
dest="/mnt/c/Users/Greg/AppData/Local/screen-machine/audio-agent"
mkdir -p "$dest"
cp "$here/pc22_agent.py" "$here/show_sender.py" "$repo/show_service/vban.py" "$dest/"
echo "deployed to $dest"
