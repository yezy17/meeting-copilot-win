from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class AppConfig:
    openai_api_key: str
    transcription_model: str
    translation_model: str
    summary_model: str
    source_language: str
    target_language: str
    audio_chunk_ms: int
    loopback_device_index: Optional[int]


def _parse_optional_int(value: str) -> Optional[int]:
    stripped = value.strip()
    return int(stripped) if stripped else None


def load_config() -> AppConfig:
    load_dotenv(ROOT_DIR / ".env", override=True)
    return AppConfig(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        transcription_model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe").strip(),
        translation_model=os.getenv("TRANSLATION_MODEL", "gpt-4.1-nano").strip(),
        summary_model=os.getenv("SUMMARY_MODEL", "gpt-5.4-mini").strip(),
        source_language=os.getenv("SOURCE_LANGUAGE", "en").strip(),
        target_language=os.getenv("TARGET_LANGUAGE", "zh-CN").strip(),
        audio_chunk_ms=max(80, int(os.getenv("AUDIO_CHUNK_MS", "120").strip())),
        loopback_device_index=_parse_optional_int(os.getenv("LOOPBACK_DEVICE_INDEX", "")),
    )
