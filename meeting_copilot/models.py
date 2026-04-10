from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class TranscriptSegment:
    item_id: str
    timestamp: datetime
    english: str
    chinese: str = ""
    translation_status: str = "pending"
