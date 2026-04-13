from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

from .models import Receipt


@dataclass
class ReceiptSegment:
    start_index: int
    end_index: int
    total: Decimal
    title: str
    payer_name: str = ""
    tip_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    tip_participants: List[str] = field(default_factory=list)
    tip_payer_name: str = ""


@dataclass
class SessionState:
    receipt: Receipt | None = None
    receipt_segments: List[ReceiptSegment] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)
    is_finalized: bool = False
    active_media_group_id: str = ""
    pending_album_photo_file_ids: List[str] = field(default_factory=list)
    pending_album_generation: int = 0
    selected_item_index: int | None = None
    raw_ocr_text: str = ""
    awaiting_tip_segment_index: int | None = None
    awaiting_feedback: bool = False
    selecting_payer_segment_index: int | None = None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[int, SessionState] = {}

    def get(self, chat_id: int) -> SessionState:
        return self._sessions.setdefault(chat_id, SessionState())

    def reset(self, chat_id: int) -> None:
        self._sessions[chat_id] = SessionState()
