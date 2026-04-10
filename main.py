import sys

from meeting_copilot.audio import LoopbackAudioSource
from meeting_copilot.config import load_config
from meeting_copilot.controller import MeetingAssistantController
from meeting_copilot.ui import run_app


def main() -> int:
    if "--list-devices" in sys.argv:
        devices = LoopbackAudioSource.list_loopback_devices()
        if not devices:
            print("No loopback devices found.")
            return 1
        for device in devices:
            print(
                f"index={device.index} rate={device.sample_rate} channels={device.channels} "
                f"name={device.name}"
            )
        return 0

    config = load_config()
    controller = MeetingAssistantController(config)
    return run_app(controller)


if __name__ == "__main__":
    raise SystemExit(main())
