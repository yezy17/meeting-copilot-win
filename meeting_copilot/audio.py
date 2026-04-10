from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pyaudiowpatch as pyaudio


TARGET_SAMPLE_RATE = 16_000


class LoopbackAudioError(RuntimeError):
    """Raised when loopback capture cannot be started."""


@dataclass(slots=True)
class LoopbackDevice:
    index: int
    name: str
    sample_rate: int
    channels: int


class LoopbackAudioSource:
    def __init__(self, device_index: Optional[int] = None, chunk_ms: int = 200) -> None:
        self._device_index = device_index
        self._chunk_ms = chunk_ms
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self.selected_device: Optional[LoopbackDevice] = None

    @staticmethod
    def list_loopback_devices() -> list[LoopbackDevice]:
        instance = pyaudio.PyAudio()
        try:
            devices: list[LoopbackDevice] = []
            for info in instance.get_loopback_device_info_generator():
                devices.append(
                    LoopbackDevice(
                        index=int(info["index"]),
                        name=str(info["name"]),
                        sample_rate=int(info["defaultSampleRate"]),
                        channels=max(1, int(info["maxInputChannels"])),
                    )
                )
            return devices
        finally:
            instance.terminate()

    def start(
        self,
        on_chunk: Callable[[bytes], None],
        on_error: Callable[[Exception], None],
    ) -> LoopbackDevice:
        if self._thread and self._thread.is_alive():
            raise LoopbackAudioError("Loopback capture is already running.")

        self._stop_event.clear()
        device = self._resolve_device()
        self.selected_device = device
        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(device, on_chunk, on_error),
            name="loopback-capture",
            daemon=True,
        )
        self._thread.start()
        return device

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _resolve_device(self) -> LoopbackDevice:
        instance = pyaudio.PyAudio()
        try:
            if self._device_index is None:
                info = instance.get_default_wasapi_loopback()
            else:
                info = instance.get_device_info_by_index(self._device_index)
                if not info.get("isLoopbackDevice"):
                    info = instance.get_wasapi_loopback_analogue_by_index(self._device_index)
            return LoopbackDevice(
                index=int(info["index"]),
                name=str(info["name"]),
                sample_rate=int(info["defaultSampleRate"]),
                channels=max(1, int(info["maxInputChannels"])),
            )
        except OSError as exc:
            raise LoopbackAudioError("No usable WASAPI loopback device was found.") from exc
        finally:
            instance.terminate()

    def _capture_loop(
        self,
        device: LoopbackDevice,
        on_chunk: Callable[[bytes], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        try:
            self._pyaudio = pyaudio.PyAudio()
            frames_per_buffer = max(256, int(device.sample_rate * self._chunk_ms / 1000))
            self._stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=device.channels,
                rate=device.sample_rate,
                input=True,
                frames_per_buffer=frames_per_buffer,
                input_device_index=device.index,
            )

            while not self._stop_event.is_set():
                raw = self._stream.read(frames_per_buffer, exception_on_overflow=False)
                pcm24 = self._downmix_and_resample(raw, device.sample_rate, device.channels)
                if pcm24:
                    on_chunk(pcm24)
        except Exception as exc:
            on_error(exc)
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._pyaudio is not None:
                self._pyaudio.terminate()
                self._pyaudio = None

    @staticmethod
    def _downmix_and_resample(raw: bytes, source_rate: int, channels: int) -> bytes:
        samples = np.frombuffer(raw, dtype=np.int16)
        if samples.size == 0:
            return b""

        if channels > 1:
            samples = samples.reshape(-1, channels).astype(np.int32)
            mono = np.mean(samples, axis=1)
        else:
            mono = samples.astype(np.int32)

        mono = np.clip(mono, -32768, 32767).astype(np.int16)

        if source_rate == TARGET_SAMPLE_RATE:
            return mono.tobytes()

        if source_rate % TARGET_SAMPLE_RATE == 0:
            step = source_rate // TARGET_SAMPLE_RATE
            return mono[::step].astype(np.int16).tobytes()

        ratio = TARGET_SAMPLE_RATE / float(source_rate)
        new_length = max(1, int(round(len(mono) * ratio)))
        old_positions = np.linspace(0, len(mono) - 1, num=len(mono), dtype=np.float64)
        new_positions = np.linspace(0, len(mono) - 1, num=new_length, dtype=np.float64)
        resampled = np.interp(new_positions, old_positions, mono.astype(np.float64))
        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()

