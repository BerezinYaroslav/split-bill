import sys
import types
import asyncio
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
    donat_command,
    handle_final_callback,
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


class FakeMessage:
    def __init__(self):
        self.chat = types.SimpleNamespace(id=123)
        self.reply_calls = []

    async def reply_text(self, text, reply_markup=None):
        self.reply_calls.append((text, reply_markup))


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()


def test_donat_command_uses_env_link(monkeypatch):
    monkeypatch.setenv("DONAT_URL", "https://example.com/donate")
    update = FakeUpdate()

    asyncio.run(donat_command(update, None))

    assert update.message.reply_calls == [
        (
            "Чаевые официанту оставили, а нам на кофе? ☕😊\n"
            "Помочь в развитии стартапа можно по ссылке: https://example.com/donate",
            None,
        )
    ]


class FakeQuery:
    def __init__(self, data="final_confirm"):
        self.data = data
        self.message = FakeMessage()
        self.from_user = types.SimpleNamespace(id=456)
        self.edited_text = None

    async def edit_message_text(self, text, reply_markup=None):
        self.edited_text = (text, reply_markup)


def build_finalized_session() -> SessionState:
    session = SessionState(participants=["Ярослав"])
    append_receipt_to_session(session, build_receipt("Бургер", "100", "Ярослав"), "ocr")
    session.receipt_segments[0].payer_name = "Ярослав"
    return session


def test_handle_final_callback_asks_feedback_on_first_success(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    monkeypatch.setattr("paytogether.bot.has_feedback_for_user", lambda **_kwargs: False)

    session = build_finalized_session()
    query = FakeQuery()

    asyncio.run(handle_final_callback(query, session))

    assert session.successful_calculation_count == 1
    assert len(query.message.reply_calls) == 1
    assert query.message.reply_calls[0][0] == "Хотите оставить обратную связь?"


def test_handle_final_callback_skips_feedback_on_second_success(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    monkeypatch.setattr("paytogether.bot.has_feedback_for_user", lambda **_kwargs: False)

    session = build_finalized_session()
    session.successful_calculation_count = 1
    query = FakeQuery()

    asyncio.run(handle_final_callback(query, session))

    assert session.successful_calculation_count == 2
    assert query.message.reply_calls == []


def test_handle_final_callback_skips_feedback_when_feedback_exists(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    monkeypatch.setattr("paytogether.bot.has_feedback_for_user", lambda **_kwargs: True)

    session = build_finalized_session()
    query = FakeQuery()

    asyncio.run(handle_final_callback(query, session))

    assert session.successful_calculation_count == 1
    assert query.message.reply_calls == []
