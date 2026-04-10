from __future__ import annotations

import asyncio
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Signal

from meeting_copilot.audio import LoopbackAudioSource
from meeting_copilot.config import AppConfig, ROOT_DIR, load_config
from meeting_copilot.llm import MeetingAssistantService, TranslatorService
from meeting_copilot.models import TranscriptSegment
from meeting_copilot.whisper_engine import WhisperStreamEngine


class ControllerSignals(QObject):
    status = Signal(str)
    error = Signal(str)
    saved = Signal(str)
    partial_english = Signal(str)
    segment_changed = Signal(object)
    assistant = Signal(object)
    running = Signal(bool)


class MeetingAssistantController:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.signals = ControllerSignals()
        self._segments: list[TranscriptSegment] = []
        self._segments_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._audio_source: Optional[LoopbackAudioSource] = None
        self._engine: Optional[WhisperStreamEngine] = None
        self._assistant_thread: Optional[threading.Thread] = None
        self._session_started_at: Optional[datetime] = None
        self._transcript_queue: Optional[asyncio.Queue] = None

    def start(self) -> None:
        if self.is_running:
            return

        self.config = load_config()
        if not self.config.openai_api_key:
            self.signals.error.emit("请先在 .env 中配置 OPENAI_API_KEY。")
            return

        self._thread = threading.Thread(
            target=self._run_background_session,
            name="meeting-copilot-session",
            daemon=True,
        )
        self._session_started_at = datetime.now()
        self._thread.start()

    def stop(self) -> None:
        if self._audio_source is not None:
            self._audio_source.stop()
        if self._engine is not None:
            self._engine.stop()
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def clear(self) -> None:
        with self._segments_lock:
            self._segments.clear()
        self._session_started_at = None
        self.signals.partial_english.emit("")
        self.signals.assistant.emit({"title": "", "body": "", "kind": ""})
        self.signals.status.emit("已清空当前会话内容。")

    def save_transcript(self) -> None:
        with self._segments_lock:
            snapshot = list(self._segments)
        if not snapshot:
            self.signals.error.emit("当前还没有可保存的会议记录。")
            return

        records_dir = ROOT_DIR / "meeting_records"
        records_dir.mkdir(parents=True, exist_ok=True)
        session_start = self._session_started_at or snapshot[0].timestamp
        session_end = snapshot[-1].timestamp
        filename = f"meeting_{session_start.strftime('%Y%m%d_%H%M%S')}.md"
        output_path = records_dir / filename

        lines = [
            "# Meeting Record",
            "",
            f"- Session start: {session_start.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Session end: {session_end.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Segment count: {len(snapshot)}",
            "",
            "## Transcript",
            "",
        ]
        for segment in snapshot:
            stamp = segment.timestamp.strftime("%H:%M:%S")
            lines.append(f"### {stamp}")
            lines.append("")
            lines.append(f"- EN: {segment.english}")
            lines.append(f"- ZH: {segment.chinese}")
            lines.append("")

        output_path.write_text("\n".join(lines), encoding="utf-8")
        self.signals.status.emit(f"会议记录已保存：{output_path}")
        self.signals.saved.emit(str(output_path))

    def summarize(self) -> None:
        self._run_assistant_action(kind="summary", status_message="正在生成会议总结...")

    def suggest_questions(self) -> None:
        self._run_assistant_action(kind="question", status_message="正在草拟可追问的问题...")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    #  Background session
    # ------------------------------------------------------------------ #

    def _run_background_session(self) -> None:
        try:
            asyncio.run(self._session_main())
        except Exception as exc:
            self.signals.error.emit(f"后台会话异常退出：{exc}")
            self.signals.running.emit(False)
        finally:
            self._loop = None
            self._stop_event = None
            self._audio_source = None
            self._engine = None

    async def _session_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        # --- Load whisper model (may download on first use) ---
        self.signals.status.emit("正在加载 Whisper 模型...")
        self._engine = WhisperStreamEngine(
            model_size=self.config.whisper_model_size,
            device=self.config.whisper_device,
            language=self.config.source_language,
            energy_threshold=self.config.whisper_energy_threshold,
        )
        self._engine.load_model(
            on_status=lambda msg: self.signals.status.emit(msg),
        )

        # --- Wire callbacks ---
        self._engine.on_partial = self._on_whisper_partial
        self._engine.on_final = self._on_whisper_final

        # --- Audio source ---
        self._audio_source = LoopbackAudioSource(
            device_index=self.config.loopback_device_index,
            chunk_ms=self.config.audio_chunk_ms,
        )

        # --- Translator ---
        translator = TranslatorService(
            api_key=self.config.openai_api_key,
            model=self.config.translation_model,
            target_language=self.config.target_language,
        )

        self._transcript_queue: asyncio.Queue[Optional[TranscriptSegment]] = asyncio.Queue()
        translation_tasks: set[asyncio.Task] = set()
        translation_semaphore = asyncio.Semaphore(2)

        # --- Start ---
        self._engine.start()
        device = self._audio_source.start(
            on_chunk=self._engine.feed_audio,
            on_error=self._handle_audio_error,
        )
        self.signals.running.emit(True)
        self.signals.status.emit(f"已开始监听：{device.name}")

        translate_task = asyncio.create_task(
            self._translator_dispatcher(translator, translation_tasks, translation_semaphore)
        )

        # --- Wait for stop ---
        await self._stop_event.wait()
        self.signals.status.emit("正在停止会话...")

        self._audio_source.stop()
        self._engine.stop()

        await self._transcript_queue.put(None)
        await translate_task
        if translation_tasks:
            await asyncio.gather(*translation_tasks, return_exceptions=True)

        self.signals.partial_english.emit("")
        self.signals.running.emit(False)
        self.signals.status.emit("会话已停止。")
        self._loop = None
        self._stop_event = None

    # ------------------------------------------------------------------ #
    #  Whisper callbacks (called from whisper thread)
    # ------------------------------------------------------------------ #

    def _on_whisper_partial(self, text: str) -> None:
        cleaned = self._sanitize_transcript_text(text)
        self.signals.partial_english.emit(cleaned)

    def _on_whisper_final(self, text: str) -> None:
        cleaned = self._sanitize_transcript_text(text)
        if not cleaned:
            return

        self.signals.partial_english.emit("")

        segment = TranscriptSegment(
            item_id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            english=cleaned,
            chinese="翻译中...",
            translation_status="pending",
        )
        with self._segments_lock:
            self._segments.append(segment)
        self.signals.segment_changed.emit(segment)

        if self._loop and self._transcript_queue is not None:
            self._loop.call_soon_threadsafe(self._transcript_queue.put_nowait, segment)

    # ------------------------------------------------------------------ #
    #  Translation
    # ------------------------------------------------------------------ #

    async def _translator_dispatcher(
        self,
        translator: TranslatorService,
        translation_tasks: set[asyncio.Task],
        translation_semaphore: asyncio.Semaphore,
    ) -> None:
        while True:
            segment = await self._transcript_queue.get()
            if segment is None:
                return
            task = asyncio.create_task(
                self._translate_segment(translator, segment, translation_semaphore)
            )
            translation_tasks.add(task)
            task.add_done_callback(lambda t: translation_tasks.discard(t))

    async def _translate_segment(
        self,
        translator: TranslatorService,
        segment: TranscriptSegment,
        translation_semaphore: asyncio.Semaphore,
    ) -> None:
        async with translation_semaphore:
            try:
                chinese = await translator.translate_final(segment.english)
                segment.chinese = chinese or "翻译为空。"
                segment.translation_status = "done"
            except Exception as exc:
                segment.chinese = f"[翻译失败] {exc}"
                segment.translation_status = "error"
            self.signals.segment_changed.emit(segment)

    # ------------------------------------------------------------------ #
    #  Assistant (summary / questions)
    # ------------------------------------------------------------------ #

    def _run_assistant_action(self, kind: str, status_message: str) -> None:
        with self._segments_lock:
            snapshot = list(self._segments)
        if not snapshot:
            msg = "当前还没有可总结的会议内容。" if kind == "summary" else "当前还没有足够内容来草拟问题。"
            self.signals.error.emit(msg)
            return
        if self._assistant_thread and self._assistant_thread.is_alive():
            self.signals.status.emit("已有一个助手任务正在生成中。")
            return

        self.signals.status.emit(status_message)
        self._assistant_thread = threading.Thread(
            target=self._run_assistant_task,
            args=(kind, snapshot),
            name=f"meeting-copilot-{kind}",
            daemon=True,
        )
        self._assistant_thread.start()

    def _run_assistant_task(self, kind: str, segments: list[TranscriptSegment]) -> None:
        try:
            service = MeetingAssistantService(
                api_key=self.config.openai_api_key,
                model=self.config.summary_model,
            )
            if kind == "summary":
                body = asyncio.run(service.summarize(segments))
                title = "Meeting Summary"
                done_message = "会议总结已生成。"
            else:
                body = asyncio.run(service.suggest_questions(segments))
                title = "Suggested Questions"
                done_message = "可追问的问题已生成。"

            self.signals.assistant.emit({"title": title, "body": body, "kind": kind})
            self.signals.status.emit(done_message)
        except Exception as exc:
            msg = f"生成摘要失败：{exc}" if kind == "summary" else f"生成问题建议失败：{exc}"
            self.signals.error.emit(msg)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _handle_audio_error(self, exc: Exception) -> None:
        self.signals.error.emit(f"系统音频采集失败：{exc}")
        self.stop()

    @staticmethod
    def _sanitize_transcript_text(text: str) -> str:
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            candidate = " ".join(raw_line.strip().split())
            normalized = candidate.casefold()
            if not candidate:
                continue
            if "business meeting transcription. preserve names and acronyms." in normalized:
                continue
            if normalized.startswith("context:") and "business meeting transcription" in normalized:
                continue
            cleaned_lines.append(candidate)
        return " ".join(cleaned_lines).strip()
