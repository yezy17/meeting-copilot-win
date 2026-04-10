"""Local streaming speech-to-text using faster-whisper with energy-based VAD."""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional

import numpy as np
from faster_whisper import WhisperModel

WHISPER_SAMPLE_RATE = 16_000
_BYTES_PER_SAMPLE = 2  # int16


class WhisperStreamEngine:
    """Streaming speech-to-text engine.

    Audio chunks are fed via ``feed_audio`` (called from capture thread).
    Partial and final transcripts are delivered via callbacks.
    All heavy work runs in a dedicated processing thread.
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        language: str = "en",
        energy_threshold: float = 300.0,
        silence_ms: int = 500,
        partial_interval_ms: int = 300,
        max_speech_s: float = 10.0,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = "float16" if device == "cuda" else "int8"
        self._language = language
        self._energy_threshold = energy_threshold
        self._silence_ms = silence_ms
        self._partial_interval_ms = partial_interval_ms
        self._max_speech_s = max_speech_s

        self._model: Optional[WhisperModel] = None
        self._audio_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=400)
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks — set by controller before start()
        self.on_partial: Optional[Callable[[str], None]] = None
        self.on_final: Optional[Callable[[str], None]] = None

    def load_model(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        if on_status:
            on_status(f"正在加载 Whisper 模型 ({self._model_size})...")
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )
        if on_status:
            on_status(f"Whisper 模型已加载 ({self._model_size} / {self._device})")

    def start(self) -> None:
        if self._model is None:
            self.load_model()
        self._running = True
        self._thread = threading.Thread(
            target=self._processing_loop,
            name="whisper-stream",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._audio_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def feed_audio(self, pcm16k_int16: bytes) -> None:
        """Feed 16 kHz mono int16 PCM. Non-blocking, drops oldest on overflow."""
        try:
            self._audio_queue.put_nowait(pcm16k_int16)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(pcm16k_int16)
            except queue.Full:
                pass

    # ------------------------------------------------------------------ #

    def _processing_loop(self) -> None:
        speech_buf = bytearray()
        is_speaking = False
        silence_start: Optional[float] = None
        last_partial_t = 0.0
        speech_start_t: Optional[float] = None

        while self._running:
            # --- drain audio queue ---
            try:
                chunk = self._audio_queue.get(timeout=0.15)
            except queue.Empty:
                if is_speaking and silence_start is not None:
                    if time.monotonic() - silence_start >= self._silence_ms / 1000:
                        self._emit_final(speech_buf)
                        speech_buf.clear()
                        is_speaking = False
                        silence_start = None
                        speech_start_t = None
                continue

            if chunk is None:
                if is_speaking and speech_buf:
                    self._emit_final(speech_buf)
                return

            # --- energy VAD ---
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) > 0 else 0.0
            is_speech = rms > self._energy_threshold
            now = time.monotonic()

            if is_speech:
                speech_buf.extend(chunk)
                if not is_speaking:
                    is_speaking = True
                    speech_start_t = now
                silence_start = None

                buf_secs = len(speech_buf) / (WHISPER_SAMPLE_RATE * _BYTES_PER_SAMPLE)
                if buf_secs >= 0.8 and now - last_partial_t >= self._partial_interval_ms / 1000:
                    last_partial_t = now
                    self._emit_partial(speech_buf)

            elif is_speaking:
                speech_buf.extend(chunk)
                if silence_start is None:
                    silence_start = now
                elif now - silence_start >= self._silence_ms / 1000:
                    self._emit_final(speech_buf)
                    speech_buf.clear()
                    is_speaking = False
                    silence_start = None
                    speech_start_t = None

            # force-cut very long speech
            if is_speaking and speech_start_t is not None:
                if now - speech_start_t >= self._max_speech_s:
                    self._emit_final(speech_buf)
                    speech_buf.clear()
                    is_speaking = False
                    silence_start = None
                    speech_start_t = None

    def _emit_partial(self, buf: bytearray) -> None:
        # Only transcribe last 10 s to keep partials fast
        max_bytes = WHISPER_SAMPLE_RATE * _BYTES_PER_SAMPLE * 10
        window = buf[-max_bytes:] if len(buf) > max_bytes else buf
        text = self._transcribe(window, beam_size=1)
        if text and self.on_partial:
            self.on_partial(text)

    def _emit_final(self, buf: bytearray) -> None:
        text = self._transcribe(buf, beam_size=5)
        if self.on_partial:
            self.on_partial("")
        if text and self.on_final:
            self.on_final(text)

    def _transcribe(self, audio_bytes: bytearray | bytes, beam_size: int = 1) -> str:
        if len(audio_bytes) < WHISPER_SAMPLE_RATE * _BYTES_PER_SAMPLE // 2:
            return ""
        if self._model is None:
            return ""

        audio = np.frombuffer(bytes(audio_bytes), dtype=np.int16)
        audio_float = audio.astype(np.float32) / 32768.0

        segments, _ = self._model.transcribe(
            audio_float,
            language=self._language,
            beam_size=beam_size,
            without_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(s.text.strip() for s in segments).strip()
