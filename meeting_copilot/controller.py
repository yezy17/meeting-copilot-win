from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Signal

from meeting_copilot.audio import LoopbackAudioSource
from meeting_copilot.config import AppConfig, ROOT_DIR, load_config
from meeting_copilot.llm import MeetingAssistantService, TranslatorService
from meeting_copilot.models import TranscriptSegment
from meeting_copilot.realtime import RealtimeTranscriber


class ControllerSignals(QObject):
    status = Signal(str)
    error = Signal(str)
    saved = Signal(str)
    partial_english = Signal(str)
    partial_chinese = Signal(str)
    segment_changed = Signal(object)
    assistant = Signal(object)
    running = Signal(bool)


class MeetingAssistantController:
    LONG_TURN_FORCE_COMMIT_SECONDS = 3.2
    LONG_TURN_MIN_CHARS = 24

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.signals = ControllerSignals()
        self._segments: list[TranscriptSegment] = []
        self._segments_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._audio_source: Optional[LoopbackAudioSource] = None
        self._transcriber: Optional[RealtimeTranscriber] = None
        self._assistant_thread: Optional[threading.Thread] = None
        self._session_started_at: Optional[datetime] = None
        self._active_partial_item_id: Optional[str] = None
        self._active_partial_started_at: Optional[float] = None
        self._active_partial_text = ""
        self._awaiting_forced_commit = False

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
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def clear(self) -> None:
        with self._segments_lock:
            self._segments.clear()
        self._session_started_at = None
        self.signals.partial_english.emit("")
        self.signals.partial_chinese.emit("")
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
        self._run_assistant_action(
            kind="summary",
            status_message="正在生成会议总结...",
        )

    def suggest_questions(self) -> None:
        self._run_assistant_action(
            kind="question",
            status_message="正在草拟可追问的问题...",
        )

    def _run_assistant_action(self, kind: str, status_message: str) -> None:
        with self._segments_lock:
            snapshot = list(self._segments)
        if not snapshot:
            if kind == "summary":
                self.signals.error.emit("当前还没有可总结的会议内容。")
            else:
                self.signals.error.emit("当前还没有足够内容来草拟问题。")
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

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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
            self._transcriber = None

    async def _session_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._audio_source = LoopbackAudioSource(
            device_index=self.config.loopback_device_index,
            chunk_ms=self.config.audio_chunk_ms,
        )
        self._transcriber = RealtimeTranscriber(
            api_key=self.config.openai_api_key,
            model=self.config.transcription_model,
            language=self.config.source_language,
        )
        translator = TranslatorService(
            api_key=self.config.openai_api_key,
            model=self.config.translation_model,
            target_language=self.config.target_language,
        )

        audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=24)
        transcript_queue: asyncio.Queue[Optional[TranscriptSegment]] = asyncio.Queue()
        partial_translation_queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=1)
        translation_tasks: set[asyncio.Task[None]] = set()
        translation_semaphore = asyncio.Semaphore(2)

        self.signals.running.emit(True)
        self.signals.status.emit("正在连接 OpenAI Realtime...")

        await self._transcriber.connect()
        device = self._audio_source.start(
            on_chunk=lambda chunk: self._enqueue_audio(audio_queue, chunk),
            on_error=self._handle_audio_error,
        )
        self.signals.status.emit(f"已开始监听：{device.name}")

        send_task = asyncio.create_task(self._audio_sender(audio_queue))
        recv_task = asyncio.create_task(self._event_receiver(transcript_queue, partial_translation_queue))
        preview_task = asyncio.create_task(
            self._partial_translation_loop(translator, partial_translation_queue)
        )
        force_commit_task = asyncio.create_task(self._forced_commit_loop())
        translate_task = asyncio.create_task(
            self._translator_dispatcher(
                translator,
                transcript_queue,
                translation_tasks,
                translation_semaphore,
            )
        )

        await self._stop_event.wait()
        self.signals.status.emit("正在停止会话...")
        self._audio_source.stop()

        with suppress(Exception):
            await self._transcriber.commit()
        await asyncio.sleep(0.25)

        with suppress(asyncio.CancelledError):
            send_task.cancel()
            await send_task

        await transcript_queue.put(None)
        await partial_translation_queue.put(None)
        await translate_task
        await preview_task
        with suppress(asyncio.CancelledError):
            force_commit_task.cancel()
            await force_commit_task
        if translation_tasks:
            await asyncio.gather(*translation_tasks, return_exceptions=True)

        with suppress(Exception):
            await self._transcriber.close()

        with suppress(asyncio.CancelledError):
            recv_task.cancel()
            await recv_task

        self.signals.partial_english.emit("")
        self.signals.partial_chinese.emit("")
        self.signals.running.emit(False)
        self.signals.status.emit("会话已停止。")
        self._loop = None
        self._stop_event = None

    async def _audio_sender(self, audio_queue: asyncio.Queue[bytes]) -> None:
        while True:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                if self._stop_event is not None and self._stop_event.is_set():
                    return
                continue
            if self._transcriber is None:
                return
            await self._transcriber.append_audio(chunk)

    async def _event_receiver(
        self,
        transcript_queue: asyncio.Queue[Optional[TranscriptSegment]],
        partial_translation_queue: asyncio.Queue[Optional[str]],
    ) -> None:
        if self._transcriber is None:
            return

        partials: dict[str, str] = {}
        async for event in self._transcriber.events():
            event_type = event.get("type")

            if event_type == "conversation.item.input_audio_transcription.delta":
                item_id = str(event.get("item_id", "partial"))
                partials[item_id] = partials.get(item_id, "") + str(event.get("delta", ""))
                current_partial = self._sanitize_transcript_text(partials[item_id])
                if current_partial:
                    if item_id != self._active_partial_item_id:
                        self._active_partial_item_id = item_id
                        self._active_partial_started_at = time.monotonic()
                        self._awaiting_forced_commit = False
                    elif self._active_partial_started_at is None:
                        self._active_partial_started_at = time.monotonic()
                    self._active_partial_text = current_partial
                elif item_id == self._active_partial_item_id:
                    self._active_partial_text = ""
                self.signals.partial_english.emit(current_partial)
                await self._replace_queue_item(partial_translation_queue, current_partial)
                continue

            if event_type == "conversation.item.input_audio_transcription.completed":
                item_id = str(event.get("item_id", "segment"))
                english = self._sanitize_transcript_text(str(event.get("transcript", "")))
                partials.pop(item_id, None)
                if item_id == self._active_partial_item_id:
                    self._active_partial_item_id = None
                self._active_partial_started_at = None
                self._active_partial_text = ""
                self._awaiting_forced_commit = False
                self.signals.partial_english.emit("")
                self.signals.partial_chinese.emit("")
                await self._replace_queue_item(partial_translation_queue, "")
                if english:
                    segment = TranscriptSegment(
                        item_id=item_id,
                        timestamp=datetime.now(),
                        english=english,
                        chinese="翻译中...",
                        translation_status="pending",
                    )
                    with self._segments_lock:
                        self._segments.append(segment)
                    self.signals.segment_changed.emit(segment)
                    await transcript_queue.put(segment)
                continue

            if event_type == "error":
                message = event.get("error", {}).get("message", "未知 Realtime 错误")
                self.signals.error.emit(f"Realtime 错误：{message}")
                if self._stop_event is not None:
                    self._stop_event.set()
                return

    async def _translator_dispatcher(
        self,
        translator: TranslatorService,
        transcript_queue: asyncio.Queue[Optional[TranscriptSegment]],
        translation_tasks: set[asyncio.Task[None]],
        translation_semaphore: asyncio.Semaphore,
    ) -> None:
        while True:
            segment = await transcript_queue.get()
            if segment is None:
                return
            task = asyncio.create_task(
                self._translate_segment(translator, segment, translation_semaphore)
            )
            translation_tasks.add(task)
            task.add_done_callback(lambda finished: translation_tasks.discard(finished))

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

    async def _partial_translation_loop(
        self,
        translator: TranslatorService,
        partial_translation_queue: asyncio.Queue[Optional[str]],
    ) -> None:
        last_started = ""
        active_stream_task: Optional[asyncio.Task[None]] = None
        while True:
            partial = await partial_translation_queue.get()
            if partial is None:
                if active_stream_task is not None:
                    active_stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await active_stream_task
                return

            latest = partial
            while True:
                try:
                    update = await asyncio.wait_for(partial_translation_queue.get(), timeout=0.18)
                except asyncio.TimeoutError:
                    break
                if update is None:
                    if active_stream_task is not None:
                        active_stream_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await active_stream_task
                    return
                latest = update

            latest = latest.strip()
            if not latest:
                last_started = ""
                if active_stream_task is not None:
                    active_stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await active_stream_task
                    active_stream_task = None
                self.signals.partial_chinese.emit("")
                continue

            if len(latest) < 10:
                continue

            if latest == last_started:
                continue

            last_started = latest
            if active_stream_task is not None:
                active_stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await active_stream_task
            active_stream_task = asyncio.create_task(
                self._stream_live_partial_translation(translator, latest)
            )

    async def _stream_live_partial_translation(
        self,
        translator: TranslatorService,
        english_text: str,
    ) -> None:
        rendered = ""
        try:
            async for delta in translator.stream_live_translation(english_text):
                rendered += delta
                self.signals.partial_chinese.emit(rendered.strip())
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _forced_commit_loop(self) -> None:
        while True:
            await asyncio.sleep(0.18)
            if self._stop_event is not None and self._stop_event.is_set():
                return
            if self._transcriber is None:
                return
            if self._awaiting_forced_commit:
                continue
            if self._active_partial_started_at is None:
                continue
            if len(self._active_partial_text) < self.LONG_TURN_MIN_CHARS:
                continue
            if time.monotonic() - self._active_partial_started_at < self.LONG_TURN_FORCE_COMMIT_SECONDS:
                continue

            self._awaiting_forced_commit = True
            self._active_partial_started_at = None
            try:
                await self._transcriber.commit()
            except Exception:
                self._awaiting_forced_commit = False

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

            self.signals.assistant.emit(
                {
                    "title": title,
                    "body": body,
                    "kind": kind,
                }
            )
            self.signals.status.emit(done_message)
        except Exception as exc:
            if kind == "summary":
                self.signals.error.emit(f"生成摘要失败：{exc}")
            else:
                self.signals.error.emit(f"生成问题建议失败：{exc}")

    def _enqueue_audio(self, queue: asyncio.Queue[bytes], chunk: bytes) -> None:
        if self._loop is None:
            return

        def put_chunk() -> None:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(chunk)

        self._loop.call_soon_threadsafe(put_chunk)

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

    @staticmethod
    async def _replace_queue_item(
        queue: asyncio.Queue[Optional[str]],
        value: str,
    ) -> None:
        while not queue.empty():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        await queue.put(value)
