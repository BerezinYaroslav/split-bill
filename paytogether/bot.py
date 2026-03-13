from __future__ import annotations

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
from .parser import serialize_receipt
from .splitter import add_shared_amount, assign_item, calculate_allocations, render_allocations
from .state import SessionStore

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)

store = SessionStore()
RETRY_PROMPT_SUFFIX = """
Re-check all numeric amounts carefully.
Important: if a price looks like 256, 290, 110 or 175 but the receipt total suggests a missing thousands digit,
recover the full amount such as 1 256, 1 290, 1 110, 5 175.
Make sure the sum of item totals minus discounts matches subtotal or final total as closely as possible.
""".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store.reset(update.effective_chat.id)
    await update.message.reply_text(
        "Пожалуйста, пришлите фотографию чека. Я распознаю её через OpenAI API и затем помогу распределить позиции."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n"
        "/start - Новый чек\n"
        "/ocr - Показать сырой JSON распознавания OpenAI\n"
        "/summary - Текущий итог\n"
        "/reset - Сбросить сессию"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store.reset(update.effective_chat.id)
    await update.message.reply_text("Сессия очищена. Пожалуйста, пришлите новый чек.")


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
        await update.message.reply_text("Сначала, пожалуйста, пришлите фотографию чека.")
        return

    allocations = build_session_allocations(session)
    await update.message.reply_text(render_allocations(allocations))


async def show_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    if not session.raw_ocr_text:
        await update.message.reply_text("Сырого JSON распознавания пока нет. Сначала, пожалуйста, пришлите фотографию чека.")
        return

    chunks = [
        session.raw_ocr_text[index : index + 3500]
        for index in range(0, len(session.raw_ocr_text), 3500)
    ]
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    photo = update.message.photo[-1]
    session.participants = []
    session.selected_item_index = None
    session.tip_amount = Decimal("0")
    session.tip_participants = []
    session.awaiting_tip_amount = False
    await update.message.reply_text("Фотография получена, чек обрабатывается. Пожалуйста, подождите.")

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
        session.raw_ocr_text = raw_text
    except TimedOut:
        await update.message.reply_text(
            "Telegram слишком долго передавал файл. Пожалуйста, отправьте фотографию ещё раз или используйте изображение меньшего размера."
        )
        return
    except NetworkError:
        await update.message.reply_text(
            "Не удалось скачать фотографию из Telegram из-за сетевой ошибки. Пожалуйста, повторите попытку через несколько секунд и отправьте фотографию ещё раз."
        )
        return
    except AIReceiptError as exc:
        await update.message.reply_text(f"{exc} Пожалуйста, исправьте проблему и отправьте фотографию чека ещё раз.")
        return

    session.receipt = receipt
    session.selected_item_index = None
    if not receipt.items and receipt.total == 0:
        await update.message.reply_text(
            "OpenAI не смог выделить позиции из чека. Пожалуйста, отправьте команду /ocr, чтобы посмотреть сырой JSON распознавания."
        )
    await update.message.reply_text("Чек распознан:\n\n" + serialize_receipt(receipt))
    await update.message.reply_text(
        "Теперь, пожалуйста, пришлите участников новым сообщением через запятую: Иван, Полина, Денис"
    )


async def recognize_receipt(image_bytes: bytes):
    if not os.getenv("OPENAI_API_KEY"):
        raise AIReceiptError(
            "OpenAI API не настроено: переменная OPENAI_API_KEY не задана."
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
                "OpenAI API вернуло неконсистентный чек: сумма позиций не сходится с итогом."
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
        await context.bot.send_message(
            chat_id=chat_id,
            text="Все позиции распределены. Нужно ли добавить чаевые?",
            reply_markup=build_tip_decision_keyboard(),
        )
        return

    item = session.receipt.items[session.selected_item_index]
    selected = ", ".join(item.participants) if item.participants else "никто"
    await context.bot.send_message(
        chat_id=chat_id,
        text=render_item_prompt(session.selected_item_index, item, selected, len(session.receipt.items)),
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
    return InlineKeyboardMarkup(rows)


def render_item_prompt(index: int, item, selected: str, total_items: int) -> str:
    return (
        f"Позиция {index + 1} из {total_items}\n"
        f"{item.name}\n"
        f"Кол-во: {item.quantity}\n"
        f"Сумма: {item.net_total:.2f} RUB\n"
        f"Сейчас выбрано: {selected}\n"
        "Пожалуйста, выберите участников для этой позиции, затем нажмите «Дальше»."
    )


def next_displayable_item_index(items, start_index: int) -> int:
    index = start_index
    while index < len(items) and items[index].net_total <= 0:
        index += 1
    return index


def build_tip_decision_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, добавить чаевые", callback_data="tips_yes")],
            [InlineKeyboardButton("Нет, без чаевых", callback_data="tips_no")],
        ]
    )


def build_tip_split_keyboard(participants: List[str], selected_participants: List[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Разделить на всех", callback_data="tips_all")],
    ]
    for participant in participants:
        marker = "✓ " if participant in selected_participants else ""
        rows.append(
            [InlineKeyboardButton(f"{marker}{participant}", callback_data=f"tips_toggle:{participant}")]
        )
    rows.append([InlineKeyboardButton("Готово", callback_data="tips_done")])
    return InlineKeyboardMarkup(rows)


def build_final_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подтвердить", callback_data="final_confirm")],
            [InlineKeyboardButton("Пересчитать", callback_data="final_recalculate")],
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
    return add_shared_amount(allocations, session.tip_participants, session.tip_amount)


def render_final_review(session, with_tips: bool) -> str:
    title = "Итог с чаевыми:" if with_tips else "Итог без чаевых:"
    return (
        f"{title}\n\n"
        f"{render_review_assignments(session)}\n\n"
        f"{render_allocations(build_session_allocations(session))}\n\n"
        "Пожалуйста, проверьте, кто какие позиции ел, и подтвердите расчёт."
    )


def render_review_assignments(session) -> str:
    if not session.receipt:
        return "Позиции не найдены."

    lines = []
    for participant in session.participants:
        participant_items = [
            item.name
            for item in session.receipt.items
            if participant in item.participants and item.net_total > 0
        ]
        lines.append(f"{participant}:")
        if participant_items:
            for item_name in participant_items:
                lines.append(f"{item_name}")
        else:
            lines.append("ничего не выбрано")
        lines.append("")

    if session.tip_amount > 0 and session.tip_participants:
        lines.append(f"Чаевые {session.tip_amount:.2f} RUB: " + ", ".join(session.tip_participants))

    return "\n".join(lines).strip()


def restart_allocation(session) -> None:
    if not session.receipt:
        return
    for item in session.receipt.items:
        item.participants = []
    session.selected_item_index = next_displayable_item_index(session.receipt.items, 0)
    session.tip_amount = Decimal("0")
    session.tip_participants = []
    session.awaiting_tip_amount = False


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = store.get(update.effective_chat.id)
    message_text = update.message.text.strip()

    if session.awaiting_tip_amount:
        tip_amount = parse_money_input(message_text)
        if tip_amount is None:
            await update.message.reply_text("Не удалось распознать сумму чаевых. Пожалуйста, пришлите число, например: 500")
            return

        session.tip_amount = tip_amount
        session.tip_participants = []
        session.awaiting_tip_amount = False
        await update.message.reply_text(
            f"Чаевые сохранены: {tip_amount:.2f} RUB\nПожалуйста, выберите, как их распределить.",
            reply_markup=build_tip_split_keyboard(session.participants, session.tip_participants),
        )
        return

    await set_people(update, context)


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
        await query.edit_message_text("Сессия устарела. Пожалуйста, пришлите чек заново.")
        return

    if query.data.startswith("tips_"):
        await handle_tip_callback(query, session)
        return

    if query.data.startswith("final_"):
        await handle_final_callback(query, session)
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
        session.selected_item_index = next_displayable_item_index(
            session.receipt.items,
            item_index + 1,
        )
        if session.selected_item_index >= len(session.receipt.items):
            await query.edit_message_text(
                "Все позиции распределены. Нужно ли добавить чаевые?",
                reply_markup=build_tip_decision_keyboard(),
            )
            return

        next_item = session.receipt.items[session.selected_item_index]
        next_selected = ", ".join(next_item.participants) if next_item.participants else "никто"
        await query.edit_message_text(
            render_item_prompt(
                session.selected_item_index,
                next_item,
                next_selected,
                len(session.receipt.items),
            ),
            reply_markup=build_item_keyboard(session.selected_item_index, session.participants),
        )
        return
    elif action == "next":
        if not item.participants:
            await query.edit_message_text(
                render_item_prompt(item_index, item, "никто", len(session.receipt.items))
                + "\n\nПожалуйста, сначала укажите, кто ел эту позицию.",
                reply_markup=build_item_keyboard(item_index, session.participants),
            )
            return

        session.selected_item_index = next_displayable_item_index(
            session.receipt.items,
            item_index + 1,
        )
        if session.selected_item_index >= len(session.receipt.items):
            await query.edit_message_text(
                "Все позиции распределены. Нужно ли добавить чаевые?",
                reply_markup=build_tip_decision_keyboard(),
            )
            return

        next_item = session.receipt.items[session.selected_item_index]
        next_selected = ", ".join(next_item.participants) if next_item.participants else "никто"
        await query.edit_message_text(
            render_item_prompt(
                session.selected_item_index,
                next_item,
                next_selected,
                len(session.receipt.items),
            ),
            reply_markup=build_item_keyboard(session.selected_item_index, session.participants),
        )
        return

    selected = ", ".join(item.participants) if item.participants else "никто"
    await query.edit_message_text(
        render_item_prompt(item_index, item, selected, len(session.receipt.items)),
        reply_markup=build_item_keyboard(item_index, session.participants),
    )


async def handle_tip_callback(query, session) -> None:
    if query.data == "tips_yes":
        session.awaiting_tip_amount = True
        session.tip_amount = Decimal("0")
        session.tip_participants = []
        await query.edit_message_text(
            "Пожалуйста, введите сумму чаевых одним сообщением, например: 500"
        )
        return

    if query.data == "tips_no":
        session.tip_amount = Decimal("0")
        session.tip_participants = []
        await query.edit_message_text(
            render_final_review(session, with_tips=False),
            reply_markup=build_final_review_keyboard(),
        )
        return

    if query.data == "tips_all":
        session.tip_participants = list(session.participants)
        await query.edit_message_text(
            render_final_review(session, with_tips=True),
            reply_markup=build_final_review_keyboard(),
        )
        return

    if query.data.startswith("tips_toggle:"):
        participant = query.data.split(":", 1)[1]
        if participant in session.tip_participants:
            session.tip_participants.remove(participant)
        else:
            session.tip_participants.append(participant)
        await query.edit_message_text(
            f"Чаевые: {session.tip_amount:.2f} RUB\nПожалуйста, выберите участников для чаевых.",
            reply_markup=build_tip_split_keyboard(session.participants, session.tip_participants),
        )
        return

    if query.data == "tips_done":
        if not session.tip_participants:
            await query.edit_message_text(
                f"Пожалуйста, сначала выберите хотя бы одного участника для чаевых.\nЧаевые: {session.tip_amount:.2f} RUB",
                reply_markup=build_tip_split_keyboard(session.participants, session.tip_participants),
            )
            return
        await query.edit_message_text(
            render_final_review(session, with_tips=True),
            reply_markup=build_final_review_keyboard(),
        )
        return


async def handle_final_callback(query, session) -> None:
    if query.data == "final_confirm":
        tips_title = "Итог с чаевыми:" if session.tip_amount > 0 else "Итог без чаевых:"
        await query.edit_message_text(
            f"{tips_title}\n\n{render_review_assignments(session)}\n\n"
            f"{render_allocations(build_session_allocations(session))}\n\n"
            "Расчёт подтверждён."
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
        await query.edit_message_text(
            render_item_prompt(
                session.selected_item_index,
                item,
                "никто",
                len(session.receipt.items),
            ),
            reply_markup=build_item_keyboard(session.selected_item_index, session.participants),
        )
        return


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await update.effective_chat.send_message(
                "Произошла внутренняя ошибка. Пожалуйста, повторите действие ещё раз. Если проблема сохранится, отправьте фотографию чека заново."
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
