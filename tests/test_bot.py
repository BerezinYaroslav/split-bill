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
    build_item_keyboard,
    donat_command,
    handle_final_callback,
    normalize_receipt_totals,
    parse_participants_input,
    receipt_is_consistent,
    render_item_prompt,
    render_recognized_receipt_summary,
    render_final_review,
    set_people,
    store,
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


def test_review_uses_bulleted_lists_with_spacing():
    session = SessionState(participants=["Ярик", "Влад"])
    append_receipt_to_session(session, build_receipt("Бургер", "100", "Ярик"), "ocr")
    session.receipt_segments[0].payer_name = "Ярик"

    review = render_final_review(session, with_tips=False)

    assert "Ярик:\n- Бургер\n\nВлад:\nничего не выбрано" in review


def test_album_summary_forces_first_place_label():
    session = SessionState(participants=["Ярослав"])
    receipt = build_receipt("Бургер", "100", "Ярослав")
    segment = append_receipt_to_session(session, receipt, "ocr")

    summary = render_recognized_receipt_summary(
        session,
        segment,
        receipt,
        force_segment_title=True,
    )

    assert summary.startswith("Место 1\nГотово! Вот что я нашёл 👇\n\n")
    assert summary.endswith("Всё выглядит верно?")


def test_item_keyboard_shows_next_only_after_selection():
    keyboard_without_selection = build_item_keyboard(0, ["Ярослав", "Лера"])
    texts_without_selection = [button.text for row in keyboard_without_selection.inline_keyboard for button in row]
    keyboard_with_selection = build_item_keyboard(0, ["Ярослав", "Лера"], ["Ярослав"])
    texts_with_selection = [button.text for row in keyboard_with_selection.inline_keyboard for button in row]

    assert "Дальше" not in texts_without_selection
    assert "Дальше" in texts_with_selection
    assert "Готово" not in texts_with_selection
    assert "✓ Ярослав" in texts_with_selection
    assert "Лера" in texts_with_selection
    assert "✓ Лера" not in texts_with_selection
    assert "Выберите участников для этой позиции:" in render_item_prompt(
        0,
        build_receipt("Бургер", "100", "Ярослав").items[0],
        "Ярослав",
        1,
    )


class FakeMessage:
    def __init__(self):
        self.chat = types.SimpleNamespace(id=123)
        self.reply_calls = []

    async def reply_text(self, text, reply_markup=None):
        self.reply_calls.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, text=""):
        self.message = FakeMessage()
        self.message.text = text
        self.effective_chat = types.SimpleNamespace(id=self.message.chat.id)


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


def test_parse_participants_input_requires_comma_and_two_people():
    assert parse_participants_input("Ярослав") is None
    assert parse_participants_input("Ярослав Лера") is None
    assert parse_participants_input("Ярослав, , Лера, Ярослав") == ["Ярослав", "Лера"]


def test_set_people_rejects_single_participant():
    update = FakeUpdate("Ярослав")
    store.reset(update.effective_chat.id)

    asyncio.run(set_people(update, None))

    assert update.message.reply_calls == [
        (
            "Напишите минимум двух участников через запятую",
            None,
        )
    ]
    assert store.get(update.effective_chat.id).participants == []


def test_set_people_rejects_space_separated_participants():
    update = FakeUpdate("Ярослав Лера")
    store.reset(update.effective_chat.id)

    asyncio.run(set_people(update, None))

    assert update.message.reply_calls == [
        (
            "Напишите минимум двух участников через запятую",
            None,
        )
    ]
    assert store.get(update.effective_chat.id).participants == []


class FakeApplication:
    def __init__(self):
        self.scheduled = []

    def create_task(self, coro):
        self.scheduled.append(coro)
        coro.close()


class FakeContext:
    def __init__(self):
        self.application = FakeApplication()


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


def test_handle_final_callback_schedules_feedback_offer_on_first_success(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    scheduled_calls = []
    monkeypatch.setattr("paytogether.bot.schedule_feedback_offer", lambda *args: scheduled_calls.append(args))

    session = build_finalized_session()
    query = FakeQuery()
    context = FakeContext()

    asyncio.run(handle_final_callback(query, session, context))

    assert session.successful_calculation_count == 1
    assert len(scheduled_calls) == 1
    assert scheduled_calls[0][1] == query.message.chat.id
    assert query.edited_text[0].startswith("Готово! Вот итог расчётов 👇")
    assert "Бургер" not in query.edited_text[0]
    buttons = [button.text for row in query.edited_text[1].inline_keyboard for button in row]
    assert buttons == ["Показать детали"]


def test_handle_final_callback_skips_feedback_on_second_success(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    scheduled_calls = []
    monkeypatch.setattr("paytogether.bot.schedule_feedback_offer", lambda *args: scheduled_calls.append(args))

    session = build_finalized_session()
    session.successful_calculation_count = 1
    query = FakeQuery()
    context = FakeContext()

    asyncio.run(handle_final_callback(query, session, context))

    assert session.successful_calculation_count == 2
    assert scheduled_calls == []


def test_handle_final_callback_recalculate_increments_generation(monkeypatch):
    monkeypatch.setattr("paytogether.bot.feedback_storage_enabled", lambda: True)
    scheduled_calls = []
    monkeypatch.setattr("paytogether.bot.schedule_feedback_offer", lambda *args: scheduled_calls.append(args))

    session = build_finalized_session()
    session.successful_calculation_count = 1
    prev_generation = session.feedback_offer_generation
    query = FakeQuery(data="final_recalculate")
    context = FakeContext()

    asyncio.run(handle_final_callback(query, session, context))

    assert session.feedback_offer_generation == prev_generation + 1
    assert scheduled_calls == []


def test_normalize_receipt_totals_spreads_missing_discount_across_discounted_items():
    receipt = Receipt(
        items=[
            ReceiptItem(
                name="Бургер",
                quantity=Decimal("1"),
                total_price=Decimal("450.12"),
                discount=Decimal("39.88"),
            ),
            ReceiptItem(
                name="Скин",
                quantity=Decimal("1"),
                total_price=Decimal("450.12"),
                discount=Decimal("39.88"),
            ),
            ReceiptItem(
                name="Бургер 2",
                quantity=Decimal("1"),
                total_price=Decimal("450.12"),
                discount=Decimal("39.88"),
            ),
            ReceiptItem(
                name="Картофель фри",
                quantity=Decimal("1"),
                total_price=Decimal("174.54"),
                discount=Decimal("15.46"),
            ),
            ReceiptItem(
                name="Сидр сладкий фруктовый ФЕНР (розлив) бокал",
                quantity=Decimal("1.5"),
                total_price=Decimal("992.10"),
                discount=Decimal("87.90"),
            ),
        ],
        subtotal=Decimal("2512.00"),
        total=Decimal("2512.00"),
    )

    assert not receipt_is_consistent(receipt)
    assert normalize_receipt_totals(receipt) is True
    assert receipt_is_consistent(receipt)
    assert sum((item.net_total for item in receipt.items), Decimal("0")) == Decimal("2512.00")
