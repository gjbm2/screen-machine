import sys
from routes.samsung_utils import device_sleep, device_wake, device_sync
from pathlib import Path

# Add the project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from routes.samsung_utils import device_sleep, device_wake, device_sync, device_info

COMMANDS = {
    "sleep": device_sleep,
    "wake": device_wake,
    "sync": device_sync,
    "info": device_info,
}

def main():
    if len(sys.argv) != 3:
        print("Usage: python run_display_command.py <sleep|wake|sync> <publish_destination>")
        sys.exit(1)

    command_name = sys.argv[1].lower()
    publish_destination = sys.argv[2]

    if command_name not in COMMANDS:
        print(f"Unknown command: {command_name}")
        print(f"Valid commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[command_name](publish_destination)

if __name__ == "__main__":
    main()
