import sys
import types
from decimal import Decimal

openai_stub = types.ModuleType("openai")


class _APIStatusError(Exception):
    def __init__(self, status_code=500):
        self.status_code = status_code


class _OpenAI:
    def __init__(self, *args, **kwargs):
        pass


openai_stub.APIStatusError = _APIStatusError
openai_stub.OpenAI = _OpenAI
sys.modules.setdefault("openai", openai_stub)

from paytogether.bot import (
    append_receipt_to_session,
    render_final_review,
)
from paytogether.models import Receipt, ReceiptItem
from paytogether.state import SessionState


def build_receipt(name: str, total: str, participant: str) -> Receipt:
    amount = Decimal(total)
    return Receipt(
        items=[
            ReceiptItem(
                name=name,
                quantity=Decimal("1"),
                total_price=amount,
                participants=[participant],
            )
        ],
        subtotal=amount,
        total=amount,
    )


def test_single_receipt_review_hides_place_label():
    session = SessionState(participants=["Ярослав"])
    append_receipt_to_session(session, build_receipt("Бургер", "100", "Ярослав"), "ocr")
    session.receipt_segments[0].payer_name = "Ярослав"

    review = render_final_review(session, with_tips=False)

    assert "Место 1" not in review
    assert "Оплатил: Ярослав" in review


def test_multiple_receipts_review_keeps_place_labels():
    session = SessionState(participants=["Ярослав", "Лера"])
    append_receipt_to_session(session, build_receipt("Бургер", "100", "Ярослав"), "ocr1")
    append_receipt_to_session(session, build_receipt("Паста", "200", "Лера"), "ocr2")
    session.receipt_segments[0].payer_name = "Ярослав"
    session.receipt_segments[1].payer_name = "Лера"

    review = render_final_review(session, with_tips=False)

    assert "Место 1:" in review
    assert "Место 2:" in review
