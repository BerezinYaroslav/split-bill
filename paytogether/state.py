from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

from .models import Receipt


@dataclass
class SessionState:
    receipt: Receipt | None = None
    participants: List[str] = field(default_factory=list)
    payer_name: str = ""
    selected_item_index: int | None = None
    raw_ocr_text: str = ""
    tip_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    tip_participants: List[str] = field(default_factory=list)
    awaiting_tip_amount: bool = False


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[int, SessionState] = {}

    def get(self, chat_id: int) -> SessionState:
        return self._sessions.setdefault(chat_id, SessionState())

    def reset(self, chat_id: int) -> None:
        self._sessions[chat_id] = SessionState()
