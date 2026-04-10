import glob
import os
import sys

# Add NVIDIA CUDA DLL directories to PATH before importing anything that uses CUDA.
# pip-installed nvidia-cublas-cu12 / nvidia-cudnn-cu12 place DLLs in site-packages
# but ctranslate2 searches only the system PATH.
_site = os.path.join(os.path.dirname(sys.executable), "..", "Lib", "site-packages")
for _dll_dir in glob.glob(os.path.join(_site, "nvidia", "*", "bin")):
    _dll_dir = os.path.abspath(_dll_dir)
    if _dll_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")

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
