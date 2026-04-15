from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai_extractor import AIReceiptError, PROMPT, extract_receipt_with_ai, extract_receipt_with_ai_prompt
from .feedback import (
    FeedbackStorageError,
    append_feedback_row,
    ensure_feedback_user_row,
    feedback_storage_enabled,
    has_feedback_for_user,
)
from .parser import serialize_receipt
from .splitter import (
    add_shared_amount,
    assign_item,
    calculate_allocations,
    calculate_settlements,
    render_allocations,
    render_settlements,
)
from .state import ReceiptSegment, SessionStore

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)

store = SessionStore()
ALBUM_FLUSH_DELAY_SECONDS = 1.0
FEEDBACK_PROMPT_CALCULATION_COUNTS = {1, 3, 5, 10}
RETRY_PROMPT_SUFFIX = """
Re-check all numeric amounts carefully.
Important: if a price looks like 256, 290, 110 or 175 but the receipt total suggests a missing thousands digit,
recover the full amount such as 1 256, 1 290, 1 110, 5 175.
Make sure the sum of item totals minus discounts matches subtotal or final total as closely as possible.
""".strip()
DEFAULT_DONAT_URL = "https://www.tbank.ru/cf/7EvxbaCsoLS"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store.reset(update.effective_chat.id)
    if feedback_storage_enabled():
        try:
            user = update.effective_user
            ensure_feedback_user_row(
                chat_id=update.effective_chat.id,
                user_id=user.id if user else None,
                username=user.username if user and user.username else "",
                full_name=user.full_name if user else "",
            )
        except FeedbackStorageError as exc:
            logging.exception("Failed to ensure feedback user row: %s", exc)
    await update.message.reply_text(
        "Привет! Пожалуйста, пришлите фотографию одного или нескольких чеков, а я распознаю их и помогу разделить"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n"
        "/start - Новая серия чеков\n"
        "/feedback - Оставить или обновить отзыв\n"
        "/donat - Поддержать команду СплитБил\n"
        "/summary - Текущий итог\n"
        "/reset - Сбросить сессию"
    )


async def donat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    donat_url = os.getenv("DONAT_URL", DEFAULT_DONAT_URL)
    await update.message.reply_text(
        "Чаевые официанту оставили, а нам на кофе? ☕😊\n"
        f"Помочь в развитии стартапа можно по ссылке: {donat_url}"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store.reset(update.effective_chat.id)
    await update.message.reply_text("Сессия очищена. Пожалуйста, пришлите новый чек или несколько чеков подряд")


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    session.awaiting_feedback = True
    prompt = "Напишите, пожалуйста, обратную связь одним сообщением:"

    if feedback_storage_enabled():
        try:
            user = update.effective_user
            already_left_feedback = has_feedback_for_user(
                user_id=user.id if user else None,
                chat_id=update.effective_chat.id,
            )
        except FeedbackStorageError as exc:
            logging.exception("Failed to check feedback history: %s", exc)
            already_left_feedback = False

        if already_left_feedback:
            prompt = "Вы уже оставляли отзыв. Пришлите новый текст одним сообщением, и я перезапишу его:"

    await update.message.reply_text(prompt)


def append_receipt_to_session(session, receipt, raw_text: str) -> ReceiptSegment:
    if not session.receipt:
        session.receipt = receipt
        start_index = 0
    else:
        start_index = len(session.receipt.items)
        session.receipt.items.extend(receipt.items)
        session.receipt.subtotal += receipt.subtotal
        session.receipt.total += receipt.total
        session.receipt.service_charge += receipt.service_charge
        session.receipt.tips += receipt.tips
        session.receipt.metadata.update(
            {
                f"receipt_{len(session.receipt_segments) + 1}_{key}": value
                for key, value in receipt.metadata.items()
            }
        )
        session.raw_ocr_text += "\n\n" + raw_text

    if not session.raw_ocr_text:
        session.raw_ocr_text = raw_text

    end_index = len(session.receipt.items) - 1
    segment = ReceiptSegment(
        start_index=start_index,
        end_index=end_index,
        total=receipt.total,
        title=f"Место {len(session.receipt_segments) + 1}",
    )
    session.receipt_segments.append(segment)
    return segment


def should_show_segment_titles(session) -> bool:
    return len(session.receipt_segments) > 1


def format_segment_title(session, segment: ReceiptSegment | None) -> str:
    if not segment or not should_show_segment_titles(session):
        return ""
    return segment.title


def format_segment_prefix(session, segment: ReceiptSegment | None) -> str:
    title = format_segment_title(session, segment)
    return f"{title}\n" if title else ""


def get_segment_for_item_index(session, item_index: int) -> tuple[int, ReceiptSegment] | tuple[None, None]:
    for index, segment in enumerate(session.receipt_segments):
        if segment.start_index <= item_index <= segment.end_index:
            return index, segment
    return None, None


def build_payments_map(session) -> dict[str, Decimal]:
    payments: dict[str, Decimal] = {}
    for segment in session.receipt_segments:
        if segment.payer_name:
            payments[segment.payer_name] = payments.get(segment.payer_name, Decimal("0")) + segment.total
        if segment.tip_amount > 0 and segment.tip_payer_name:
            payments[segment.tip_payer_name] = payments.get(segment.tip_payer_name, Decimal("0")) + segment.tip_amount
    return payments


def all_receipt_payers_selected(session) -> bool:
    return bool(session.receipt_segments) and all(segment.payer_name for segment in session.receipt_segments)


def all_tip_payers_selected(session) -> bool:
    return all(segment.tip_amount <= 0 or segment.tip_payer_name for segment in session.receipt_segments)


def schedule_album_flush(application: Application, chat_id: int, generation: int) -> None:
    application.create_task(flush_album_receipts_after_delay(application, chat_id, generation))


async def flush_album_receipts_after_delay(application: Application, chat_id: int, generation: int) -> None:
    await asyncio.sleep(ALBUM_FLUSH_DELAY_SECONDS)
    session = store.get(chat_id)
    if generation != session.pending_album_generation:
        return

    photo_file_ids = list(session.pending_album_photo_file_ids)
    if not photo_file_ids:
        session.active_media_group_id = ""
        return

    session.pending_album_photo_file_ids = []
    album_start_index = len(session.receipt.items) if session.receipt else 0
    summaries: List[str] = []

    for photo_file_id in photo_file_ids:
        try:
            telegram_file = await application.bot.get_file(
                photo_file_id,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30,
            )
            image_bytes = await telegram_file.download_as_bytearray(
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30,
                pool_timeout=30,
            )
            receipt, raw_text, warning = await recognize_receipt(bytes(image_bytes))
            segment = append_receipt_to_session(session, receipt, raw_text)
            segment_title = format_segment_title(session, segment)
            summary_prefix = f"{segment_title} распознано:\n\n" if segment_title else ""
            summaries.append(summary_prefix + serialize_receipt(receipt))
        except (TimedOut, NetworkError):
            summaries.append("Не удалось обработать один из чеков из-за ошибки Telegram. Попробуйте отправить альбом ещё раз.")
        except AIReceiptError as exc:
            summaries.append(f"Не удалось распознать один из чеков: {exc}")

    session.active_media_group_id = ""
    if summaries:
        await application.bot.send_message(chat_id=chat_id, text="\n\n".join(summaries))
        await send_post_receipt_message(chat_id, application, session, album_start_index)


async def send_post_receipt_message(
    chat_id: int,
    app_or_context,
    session,
    start_index: int | None,
) -> None:
    bot = getattr(app_or_context, "bot", None) or app_or_context.bot
    if not session.participants:
        await bot.send_message(
            chat_id=chat_id,
            text="Можно прислать ещё чеки, если мест было несколько. Когда все фото будут загружены, пришлите участников через запятую: Иван, Полина, Денис",
        )
        return

    if (
        start_index is not None
        and session.selecting_payer_segment_index is None
        and (session.selected_item_index is None or session.selected_item_index >= start_index)
    ):
        session.selected_item_index = next_displayable_item_index(session.receipt.items, start_index)
        await send_current_item(chat_id, app_or_context, session)


async def set_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    raw = update.message.text.strip()
    participants = [name.strip() for name in raw.split(",") if name.strip()]
    session.participants = participants
    if not participants:
        await update.message.reply_text("Пожалуйста, укажите участников через запятую: Ярослав, Лера")
        return

    await update.message.reply_text(
        "Участники сохранены: " + ", ".join(participants)
    )
    if session.receipt:
        session.selected_item_index = next_displayable_item_index(session.receipt.items, 0)
        await send_current_item(update.effective_chat.id, context, session)


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    if not session.receipt:
        await update.message.reply_text("Сначала, пожалуйста, пришлите фотографию чека")
        return

    allocations = build_session_allocations(session)
    summary_text = (
        f"{render_allocations(allocations)}\n\n"
        f"{render_payments_summary(session)}\n\n"
        f"{render_settlement_summary(session, allocations)}"
    )
    await update.message.reply_text(summary_text)


async def show_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    if not session.raw_ocr_text:
        await update.message.reply_text("Сырого JSON распознавания пока нет. Сначала, пожалуйста, пришлите фотографию чека")
        return

    chunks = [
        session.raw_ocr_text[index : index + 3500]
        for index in range(0, len(session.raw_ocr_text), 3500)
    ]
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    if session.is_finalized:
        store.reset(update.effective_chat.id)
        session = store.get(update.effective_chat.id)

    media_group_id = update.message.media_group_id or ""
    photo = update.message.photo[-1]
    if media_group_id:
        if session.active_media_group_id != media_group_id:
            session.active_media_group_id = media_group_id
            session.pending_album_photo_file_ids = []
            await update.message.reply_text("Фотографии получены, чеки обрабатываются. Пожалуйста, подождите")
        session.pending_album_photo_file_ids.append(photo.file_id)
        session.pending_album_generation += 1
        schedule_album_flush(context.application, update.effective_chat.id, session.pending_album_generation)
        return

    if not media_group_id:
        await update.message.reply_text("Фотография получена, чек обрабатывается. Пожалуйста, подождите")

    try:
        telegram_file = await photo.get_file(
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )
        image_bytes = await telegram_file.download_as_bytearray(
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )
        receipt, raw_text, warning = await recognize_receipt(bytes(image_bytes))
    except TimedOut:
        await update.message.reply_text(
            "Telegram слишком долго передавал файл. Пожалуйста, отправьте фотографию ещё раз или используйте изображение меньшего размера"
        )
        return
    except NetworkError:
        await update.message.reply_text(
            "Не удалось скачать фотографию из Telegram из-за сетевой ошибки. Пожалуйста, повторите попытку через несколько секунд и отправьте фотографию ещё раз"
        )
        return
    except AIReceiptError as exc:
        await update.message.reply_text(f"{exc} Пожалуйста, исправьте проблему и отправьте фотографию чека ещё раз")
        return

    segment = append_receipt_to_session(session, receipt, raw_text)
    if not receipt.items and receipt.total == 0:
        await update.message.reply_text(
            "OpenAI не смог выделить позиции из чека"
        )
    segment_title = format_segment_title(session, segment)
    summary_prefix = f"{segment_title} распознано:\n\n" if segment_title else ""
    summary_text = summary_prefix + serialize_receipt(receipt)

    await update.message.reply_text(summary_text)
    await send_post_receipt_message(
        update.effective_chat.id,
        context,
        session,
        segment.start_index,
    )


async def recognize_receipt(image_bytes: bytes):
    if not os.getenv("OPENAI_API_KEY"):
        raise AIReceiptError(
            "OpenAI API не настроено: переменная OPENAI_API_KEY не задана"
        )

    receipt, ai_json = extract_receipt_with_ai(image_bytes)
    if not receipt_is_consistent(receipt):
        retry_prompt = f"{PROMPT}\n\n{RETRY_PROMPT_SUFFIX}"
        receipt, ai_json = extract_receipt_with_ai_prompt(
            image_bytes,
            retry_prompt,
        )
        if not receipt_is_consistent(receipt):
            raise AIReceiptError(
                "OpenAI API вернуло неконсистентный чек: сумма позиций не сходится с итогом"
            )

    raw_text = f"OpenAI JSON:\n{ai_json}"
    return receipt, raw_text, None


async def send_current_item(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    if not session.receipt or session.selected_item_index is None:
        return

    session.selected_item_index = next_displayable_item_index(
        session.receipt.items, session.selected_item_index
    )
    if session.selected_item_index >= len(session.receipt.items):
        await send_tip_or_final_prompt(chat_id, context, session)
        return

    item = session.receipt.items[session.selected_item_index]
    selected = ", ".join(item.participants) if item.participants else "никто"
    _, segment = get_segment_for_item_index(session, session.selected_item_index)
    place_title = format_segment_prefix(session, segment)
    await context.bot.send_message(
        chat_id=chat_id,
        text=place_title + render_item_prompt(session.selected_item_index, item, selected, len(session.receipt.items)),
        reply_markup=build_item_keyboard(session.selected_item_index, session.participants),
    )


def build_item_keyboard(index: int, participants: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for participant in participants:
        rows.append(
            [InlineKeyboardButton(participant, callback_data=f"toggle:{index}:{participant}")]
        )
    rows.append([InlineKeyboardButton("Поделить на всех", callback_data=f"all:{index}")])
    rows.append([InlineKeyboardButton("Дальше", callback_data=f"next:{index}")])
    rows.append([InlineKeyboardButton("Назад", callback_data=f"back:{index}")])
    return InlineKeyboardMarkup(rows)


def build_payer_keyboard(prefix: str, participants: List[str], selected_payer: str = "") -> InlineKeyboardMarkup:
    rows = []
    for participant in participants:
        marker = "✓ " if participant == selected_payer else ""
        rows.append(
            [InlineKeyboardButton(f"{marker}{participant}", callback_data=f"{prefix}:{participant}")]
        )
    return InlineKeyboardMarkup(rows)


def render_item_prompt(index: int, item, selected: str, total_items: int) -> str:
    return (
        f"Позиция {index + 1} из {total_items}\n"
        f"{item.name}\n"
        f"Кол-во: {item.quantity}\n"
        f"Сумма: {item.net_total:.2f} RUB\n"
        f"Сейчас выбрано: {selected}\n"
        "Пожалуйста, выберите участников для этой позиции, затем нажмите «Дальше»:"
    )


def render_payments_summary(session) -> str:
    lines = []

    for segment in session.receipt_segments:
        payer = segment.payer_name or "пока не выбран"
        segment_title = format_segment_title(session, segment)
        if segment_title:
            lines.append(f"{segment_title}: оплатил {payer}, сумма {segment.total:.2f} RUB")
        else:
            lines.append(f"Оплатил: {payer}, сумма {segment.total:.2f} RUB")
        if segment.tip_amount > 0:
            tip_payer = segment.tip_payer_name or "пока не выбран"
            if segment_title:
                lines.append(f"{segment_title} / чаевые: оплатил {tip_payer}, сумма {segment.tip_amount:.2f} RUB")
            else:
                lines.append(f"Чаевые: оплатил {tip_payer}, сумма {segment.tip_amount:.2f} RUB")

    return "\n".join(lines) if lines else "Оплаты по местам пока не заполнены."


def render_settlement_summary(session, allocations) -> str:
    if not all_receipt_payers_selected(session):
        return "Итоговые взаиморасчеты появятся после выбора плательщика для каждого чека."
    if not all_tip_payers_selected(session):
        return "Итоговые взаиморасчеты появятся после выбора того, кто оплатил чаевые."
    return render_settlements(calculate_settlements(allocations, build_payments_map(session)))


async def send_tip_or_final_prompt(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    await context.bot.send_message(
        chat_id=chat_id,
        text=render_final_review(session, with_tips=has_any_tips(session)),
        reply_markup=build_final_review_keyboard(),
    )


def next_displayable_item_index(items, start_index: int) -> int:
    index = start_index
    while index < len(items) and items[index].net_total <= 0:
        index += 1
    return index


def previous_displayable_item_index(items, start_index: int) -> int:
    index = start_index
    while index >= 0 and items[index].net_total <= 0:
        index -= 1
    return max(index, 0)


def has_any_tips(session) -> bool:
    return any(segment.tip_amount > 0 for segment in session.receipt_segments)


def build_tip_decision_keyboard(segment_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, добавить чаевые", callback_data=f"tips_yes:{segment_index}")],
            [InlineKeyboardButton("Нет, без чаевых", callback_data=f"tips_no:{segment_index}")],
        ]
    )


def build_tip_split_keyboard(segment_index: int, participants: List[str], selected_participants: List[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Разделить на всех", callback_data=f"tips_all:{segment_index}")],
    ]
    for participant in participants:
        marker = "✓ " if participant in selected_participants else ""
        rows.append(
            [InlineKeyboardButton(f"{marker}{participant}", callback_data=f"tips_toggle:{segment_index}:{participant}")]
        )
    rows.append([InlineKeyboardButton("Готово", callback_data=f"tips_done:{segment_index}")])
    return InlineKeyboardMarkup(rows)


def build_final_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подтвердить", callback_data="final_confirm")],
            [InlineKeyboardButton("Пересчитать", callback_data="final_recalculate")],
        ]
    )


def build_feedback_offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Оставить", callback_data="feedback_yes")],
            [InlineKeyboardButton("В другой раз", callback_data="feedback_later")],
        ]
    )


def receipt_items_total(receipt) -> Decimal:
    return sum((item.total_price - item.discount for item in receipt.items), Decimal("0"))


def receipt_consistency_delta(receipt) -> Decimal:
    items_total = receipt_items_total(receipt)
    targets = [value for value in (receipt.subtotal, receipt.total) if value > 0]
    if not targets:
        return Decimal("0")
    return min(abs(items_total - target) for target in targets)


def receipt_is_consistent(receipt) -> bool:
    if not receipt.items:
        return True

    items_total = receipt_items_total(receipt)
    targets = [value for value in (receipt.subtotal, receipt.total) if value > 0]
    if not targets:
        return True

    for target in targets:
        if target == 0:
            continue
        delta = abs(items_total - target)
        if delta <= Decimal("1.00"):
            return True
        if delta / target <= Decimal("0.03"):
            return True
    return False


def build_session_allocations(session) -> List:
    if not session.receipt:
        return []
    allocations = calculate_allocations(session.receipt)
    for segment in session.receipt_segments:
        allocations = add_shared_amount(allocations, segment.tip_participants, segment.tip_amount)
    return allocations


def render_final_review(session, with_tips: bool) -> str:
    title = "Итог с чаевыми:" if with_tips else "Итог без чаевых:"
    allocations = build_session_allocations(session)
    return (
        f"{title}\n\n"
        f"{render_review_assignments(session)}\n\n"
        f"{render_allocations(allocations)}\n\n"
        f"{render_payments_summary(session)}\n\n"
        f"{render_settlement_summary(session, allocations)}\n\n"
        "Пожалуйста, проверьте, кто какие позиции ел, и подтвердите расчёт:"
    )


def render_review_assignments(session) -> str:
    if not session.receipt:
        return "Позиции не найдены"

    lines = []
    for segment in session.receipt_segments:
        segment_title = format_segment_title(session, segment)
        if segment_title:
            lines.append(segment_title + ":")
        payer = segment.payer_name or "пока не выбран"
        lines.append(f"Оплатил: {payer}")
        for participant in session.participants:
            participant_items = [
                item.name
                for item in session.receipt.items[segment.start_index : segment.end_index + 1]
                if participant in item.participants and item.net_total > 0
            ]
            lines.append(f"{participant}:")
            if participant_items:
                lines.extend(participant_items)
            else:
                lines.append("ничего не выбрано")
        if segment.tip_amount > 0 and segment.tip_participants:
            lines.append(f"Чаевые {segment.tip_amount:.2f} RUB: " + ", ".join(segment.tip_participants))
            lines.append(f"Оплатил чаевые: {segment.tip_payer_name or 'пока не выбран'}")
        lines.append("")

    return "\n".join(lines).strip()


def restart_allocation(session) -> None:
    if not session.receipt:
        return
    for item in session.receipt.items:
        item.participants = []
    for segment in session.receipt_segments:
        segment.payer_name = ""
        segment.tip_amount = Decimal("0")
        segment.tip_participants = []
        segment.tip_payer_name = ""
    session.selected_item_index = next_displayable_item_index(session.receipt.items, 0)
    session.awaiting_tip_segment_index = None
    session.selecting_payer_segment_index = None


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    message_text = update.message.text.strip()

    if session.awaiting_feedback:
        await handle_feedback_input(update, session, message_text)
        return

    if session.awaiting_tip_segment_index is not None:
        tip_amount = parse_money_input(message_text)
        if tip_amount is None:
            await update.message.reply_text("Не удалось распознать сумму чаевых. Пожалуйста, пришлите число, например: 500")
            return

        segment_index = session.awaiting_tip_segment_index
        segment = session.receipt_segments[segment_index]
        segment.tip_amount = tip_amount
        segment.tip_participants = []
        segment.tip_payer_name = ""
        session.awaiting_tip_segment_index = None
        await update.message.reply_text(
            (
                f"{format_segment_title(session, segment)}: чаевые сохранены {tip_amount:.2f} RUB\n"
                if format_segment_title(session, segment)
                else f"Чаевые сохранены {tip_amount:.2f} RUB\n"
            )
            + "Пожалуйста, выберите, как их распределить:",
            reply_markup=build_tip_split_keyboard(segment_index, session.participants, segment.tip_participants),
        )
        return

    await set_people(update, context)


async def handle_feedback_input(update: Update, session, message_text: str) -> None:
    if not message_text:
        await update.message.reply_text("Пожалуйста, пришлите обратную связь обычным текстовым сообщением:")
        return

    session.awaiting_feedback = False
    user = update.effective_user
    try:
        if feedback_storage_enabled():
            append_feedback_row(
                chat_id=update.effective_chat.id,
                user_id=user.id if user else None,
                username=user.username if user and user.username else "",
                full_name=user.full_name if user else "",
                feedback_text=message_text,
            )
            await update.message.reply_text("Спасибо! Сохранил вашу обратную связь, чтобы стать еще лучше :)")
            return

        logging.warning("Feedback received, but Google Sheets is not configured.")
        await update.message.reply_text(
            "Спасибо за вашу обратную связь!"
        )
    except FeedbackStorageError as exc:
        logging.exception("Failed to store feedback: %s", exc)
        await update.message.reply_text(
            "Спасибо за вашу обратную связь!"
        )


def parse_money_input(raw_value: str) -> Decimal | None:
    cleaned = raw_value.replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except Exception:
        return None
    if amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    session = store.get(query.message.chat.id)
    if not session.receipt:
        await query.edit_message_text("Сессия устарела. Пожалуйста, пришлите чек заново")
        return

    if query.data.startswith("tips_"):
        await handle_tip_callback(query, session)
        return

    if query.data.startswith("payer:"):
        await handle_receipt_payer_callback(query, session, context)
        return

    if query.data.startswith("tip_payer:"):
        await handle_tip_payer_callback(query, session, context)
        return

    if query.data.startswith("final_"):
        await handle_final_callback(query, session)
        return

    if query.data.startswith("feedback_"):
        await handle_feedback_callback(query, session)
        return

    payload = query.data.split(":")
    action = payload[0]
    item_index = int(payload[1])
    item = session.receipt.items[item_index]

    if action == "toggle":
        participant = payload[2]
        if participant in item.participants:
            item.participants.remove(participant)
        else:
            item.participants.append(participant)
    elif action == "all":
        assign_item(item, session.participants)
        await advance_after_item_selection(query, session, item_index)
        return
    elif action == "next":
        if not item.participants:
            await query.edit_message_text(
                render_item_prompt(item_index, item, "никто", len(session.receipt.items))
                + "\n\nПожалуйста, сначала укажите, кто ел эту позицию:",
                reply_markup=build_item_keyboard(item_index, session.participants),
            )
            return

        await advance_after_item_selection(query, session, item_index)
        return
    elif action == "back":
        previous_index = previous_displayable_item_index(session.receipt.items, item_index - 1)
        session.selected_item_index = previous_index
        previous_item = session.receipt.items[previous_index]
        previous_selected = ", ".join(previous_item.participants) if previous_item.participants else "никто"
        _, previous_segment = get_segment_for_item_index(session, previous_index)
        place_title = format_segment_prefix(session, previous_segment)
        await query.edit_message_text(
            place_title + render_item_prompt(previous_index, previous_item, previous_selected, len(session.receipt.items)),
            reply_markup=build_item_keyboard(previous_index, session.participants),
        )
        return

    selected = ", ".join(item.participants) if item.participants else "никто"
    _, segment = get_segment_for_item_index(session, item_index)
    place_title = format_segment_prefix(session, segment)
    await query.edit_message_text(
        place_title + render_item_prompt(item_index, item, selected, len(session.receipt.items)),
        reply_markup=build_item_keyboard(item_index, session.participants),
    )


async def advance_after_item_selection(query, session, item_index: int) -> None:
    segment_index, segment = get_segment_for_item_index(session, item_index)
    next_index = next_displayable_item_index(session.receipt.items, item_index + 1)

    if segment and next_index > segment.end_index:
        session.selected_item_index = next_index
        session.selecting_payer_segment_index = segment_index
        await query.edit_message_text(
            (
                f"Позиции для {segment.title.lower()} распределены.\n"
                if format_segment_title(session, segment)
                else "Позиции распределены.\n"
            )
            + f"Кто оплатил этот чек на {segment.total:.2f} RUB?",
            reply_markup=build_payer_keyboard("payer", session.participants, segment.payer_name),
        )
        return

    if next_index >= len(session.receipt.items):
        session.selected_item_index = next_index
        await query.edit_message_text(
            render_final_review(session, with_tips=has_any_tips(session)),
            reply_markup=build_final_review_keyboard(),
        )
        return

    session.selected_item_index = next_index
    next_item = session.receipt.items[next_index]
    next_selected = ", ".join(next_item.participants) if next_item.participants else "никто"
    _, next_segment = get_segment_for_item_index(session, next_index)
    place_title = format_segment_prefix(session, next_segment)
    await query.edit_message_text(
        place_title
        + render_item_prompt(
            next_index,
            next_item,
            next_selected,
            len(session.receipt.items),
        ),
        reply_markup=build_item_keyboard(next_index, session.participants),
    )


async def handle_tip_callback(query, session) -> None:
    action, segment_index_raw, *rest = query.data.split(":")
    segment_index = int(segment_index_raw)
    segment = session.receipt_segments[segment_index]

    if action == "tips_yes":
        session.awaiting_tip_segment_index = segment_index
        segment.tip_amount = Decimal("0")
        segment.tip_participants = []
        segment.tip_payer_name = ""
        await query.edit_message_text(
            (
                f"{format_segment_title(session, segment)}: введите сумму чаевых одним сообщением, например: 500"
                if format_segment_title(session, segment)
                else "Введите сумму чаевых одним сообщением, например: 500"
            )
        )
        return

    if action == "tips_no":
        segment.tip_amount = Decimal("0")
        segment.tip_participants = []
        segment.tip_payer_name = ""
        await continue_after_segment_tip_decision(query, session, segment_index)
        return

    if action == "tips_all":
        segment.tip_participants = list(session.participants)
        await query.edit_message_text(
            (
                f"{format_segment_title(session, segment)}: чаевые {segment.tip_amount:.2f} RUB\n"
                if format_segment_title(session, segment)
                else f"Чаевые {segment.tip_amount:.2f} RUB\n"
            )
            + "Кто оплатил чаевые?",
            reply_markup=build_payer_keyboard(f"tip_payer:{segment_index}", session.participants, segment.tip_payer_name),
        )
        return

    if action == "tips_toggle":
        participant = rest[0]
        if participant in segment.tip_participants:
            segment.tip_participants.remove(participant)
        else:
            segment.tip_participants.append(participant)
        await query.edit_message_text(
            (
                f"{format_segment_title(session, segment)}: чаевые {segment.tip_amount:.2f} RUB\n"
                if format_segment_title(session, segment)
                else f"Чаевые {segment.tip_amount:.2f} RUB\n"
            )
            + "Пожалуйста, выберите участников для чаевых.",
            reply_markup=build_tip_split_keyboard(segment_index, session.participants, segment.tip_participants),
        )
        return

    if action == "tips_done":
        if not segment.tip_participants:
            await query.edit_message_text(
                "Пожалуйста, сначала выберите хотя бы одного участника для чаевых.\n"
                + (
                    f"{format_segment_title(session, segment)}: чаевые {segment.tip_amount:.2f} RUB"
                    if format_segment_title(session, segment)
                    else f"Чаевые {segment.tip_amount:.2f} RUB"
                ),
                reply_markup=build_tip_split_keyboard(segment_index, session.participants, segment.tip_participants),
            )
            return
        await query.edit_message_text(
            (
                f"{format_segment_title(session, segment)}: чаевые {segment.tip_amount:.2f} RUB\n"
                if format_segment_title(session, segment)
                else f"Чаевые {segment.tip_amount:.2f} RUB\n"
            )
            + "Кто оплатил чаевые?",
            reply_markup=build_payer_keyboard(f"tip_payer:{segment_index}", session.participants, segment.tip_payer_name),
        )
        return


async def handle_receipt_payer_callback(query, session, context) -> None:
    if session.selecting_payer_segment_index is None:
        await query.edit_message_text("Не удалось определить чек для выбора плательщика. Пожалуйста, начните заново через /reset")
        return

    payer_name = query.data.split(":", 1)[1]
    segment_index = session.selecting_payer_segment_index
    segment = session.receipt_segments[segment_index]
    segment.payer_name = payer_name
    session.selecting_payer_segment_index = None
    await query.edit_message_text(
        (
            f"{format_segment_title(session, segment)}: оплатил {payer_name}. Добавить чаевые для этого места?"
            if format_segment_title(session, segment)
            else f"Оплатил: {payer_name}. Добавить чаевые для этого чека?"
        ),
        reply_markup=build_tip_decision_keyboard(segment_index),
    )


async def handle_tip_payer_callback(query, session, context) -> None:
    _, segment_index_raw, payer_name = query.data.split(":", 2)
    segment_index = int(segment_index_raw)
    session.receipt_segments[segment_index].tip_payer_name = payer_name
    await continue_after_segment_tip_decision(query, session, segment_index)


async def continue_after_segment_tip_decision(query, session, segment_index: int) -> None:
    segment = session.receipt_segments[segment_index]
    next_index = next_displayable_item_index(session.receipt.items, segment.end_index + 1)
    session.selected_item_index = next_index

    if next_index >= len(session.receipt.items):
        await query.edit_message_text(
            render_final_review(session, with_tips=has_any_tips(session)),
            reply_markup=build_final_review_keyboard(),
        )
        return

    next_item = session.receipt.items[next_index]
    next_selected = ", ".join(next_item.participants) if next_item.participants else "никто"
    _, next_segment = get_segment_for_item_index(session, next_index)
    place_title = format_segment_prefix(session, next_segment)
    await query.edit_message_text(
        place_title
        + render_item_prompt(
            next_index,
            next_item,
            next_selected,
            len(session.receipt.items),
        ),
        reply_markup=build_item_keyboard(next_index, session.participants),
    )


async def handle_final_callback(query, session) -> None:
    if query.data == "final_confirm":
        tips_title = "Итог с чаевыми:" if has_any_tips(session) else "Итог без чаевых:"
        allocations = build_session_allocations(session)
        confirmation_text = (
            f"{tips_title}\n\n{render_review_assignments(session)}\n\n"
            f"{render_allocations(allocations)}\n\n"
            f"{render_payments_summary(session)}\n\n"
            f"{render_settlement_summary(session, allocations)}\n\n"
            "Расчёт подтверждён"
        )
        session.is_finalized = True
        session.successful_calculation_count += 1
        await query.edit_message_text(confirmation_text)
        if feedback_storage_enabled():
            try:
                user = query.from_user
                already_left_feedback = has_feedback_for_user(
                    user_id=user.id if user else None,
                    chat_id=query.message.chat.id,
                )
            except FeedbackStorageError as exc:
                logging.exception("Failed to check feedback history: %s", exc)
                already_left_feedback = True

            if (
                not already_left_feedback
                and session.successful_calculation_count in FEEDBACK_PROMPT_CALCULATION_COUNTS
            ):
                await query.message.reply_text(
                    "Хотите оставить обратную связь?",
                    reply_markup=build_feedback_offer_keyboard(),
                )
        return

    if query.data == "final_recalculate":
        restart_allocation(session)
        if session.selected_item_index is None or session.selected_item_index >= len(session.receipt.items):
            await query.edit_message_text(
                "Не удалось запустить пересчёт. Пожалуйста, отправьте фотографию чека ещё раз."
            )
            return

        item = session.receipt.items[session.selected_item_index]
        _, segment = get_segment_for_item_index(session, session.selected_item_index)
        place_title = format_segment_prefix(session, segment)
        await query.edit_message_text(
            place_title + render_item_prompt(
                session.selected_item_index,
                item,
                "никто",
                len(session.receipt.items),
            ),
            reply_markup=build_item_keyboard(session.selected_item_index, session.participants),
        )
        return


async def handle_feedback_callback(query, session) -> None:
    if query.data == "feedback_yes":
        session.awaiting_feedback = True
        await query.edit_message_text(
            "Напишите, пожалуйста, обратную связь одним сообщением:"
        )
        return

    if query.data == "feedback_later":
        session.awaiting_feedback = False
        await query.edit_message_text("Хорошо, в другой раз")
        return


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await update.effective_chat.send_message(
                "Произошла внутренняя ошибка. Пожалуйста, повторите действие ещё раз. Если проблема сохранится, отправьте фотографию чека заново"
            )
        except Exception:
            logging.exception("Failed to notify user about handler error")


def build_application(token: str) -> Application:
    application = (
        Application.builder()
        .token(token)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("feedback", feedback_command))
    application.add_handler(CommandHandler("donat", donat_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ocr", show_ocr))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("summary", summary))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input)
    )
    application.add_error_handler(on_error)
    return application


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before запуск.")

    application = build_application(token)
    application.run_polling()


if __name__ == "__main__":
    main()
