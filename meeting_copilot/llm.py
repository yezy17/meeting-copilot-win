from __future__ import annotations

from typing import Iterable

from openai import AsyncOpenAI

from meeting_copilot.models import TranscriptSegment


def _format_transcript(segments: Iterable[TranscriptSegment]) -> str:
    lines: list[str] = []
    for segment in segments:
        stamp = segment.timestamp.strftime("%H:%M:%S")
        lines.append(f"[{stamp}] EN: {segment.english}")
        lines.append(f"[{stamp}] ZH: {segment.chinese}")
    return "\n".join(lines)


class TranslatorService:
    def __init__(self, api_key: str, model: str, target_language: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._target_language = target_language

    async def translate_final(self, english_text: str) -> str:
        if not english_text.strip():
            return ""

        response = await self._client.responses.create(
            model=self._model,
            instructions=(
                "You are a realtime meeting interpreter. Translate the user's English input "
                f"into concise, natural {self._target_language}. Preserve names, acronyms, "
                "product names, code snippets, numbers, and action items. Output Chinese only. "
                "Do not add bullet lists, explanations, or the original English. Keep "
                "uncertainty explicit instead of inventing details."
            ),
            input=english_text,
            max_output_tokens=120,
        )
        return (response.output_text or "").strip()



class MeetingAssistantService:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def summarize(self, segments: Iterable[TranscriptSegment]) -> str:
        transcript = _format_transcript(segments)
        if not transcript:
            return "当前还没有可总结的会议内容。"

        response = await self._client.responses.create(
            model=self._model,
            instructions=(
                "你是一个会议助手。请基于会议转写生成简洁、可执行的中文总结。"
                "输出必须包含以下小节：会议主题、关键结论、行动项、风险与未决问题、建议追问。"
                "如果某项内容没有证据，请明确写“未提及”或“待确认”，不要编造。"
            ),
            input=transcript,
            max_output_tokens=900,
        )
        return (response.output_text or "").strip()

    async def suggest_questions(self, segments: Iterable[TranscriptSegment]) -> str:
        transcript = _format_transcript(segments)
        if not transcript:
            return "当前还没有足够的会议内容来草拟问题。"

        response = await self._client.responses.create(
            model=self._model,
            instructions=(
                "你是一个会议中的中文同传助手。请基于已有会议内容，草拟 4 到 6 个高价值追问。"
                "问题要适合在会议中直接开口提问，优先澄清目标、负责人、时间线、风险、依赖、决策依据。"
                "输出中文，格式紧凑。每条包含两部分："
                "1. 可直接提问的一句话。"
                "2. 用简短括号说明为什么值得问。"
                "如果信息还不够，请明确指出目前最缺什么信息。不要编造会议里没提到的事实。"
            ),
            input=transcript,
            max_output_tokens=700,
        )
        return (response.output_text or "").strip()
