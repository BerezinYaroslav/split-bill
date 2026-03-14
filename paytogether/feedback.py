from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

class FeedbackStorageError(RuntimeError):
    pass


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def feedback_storage_enabled() -> bool:
    return bool(
        os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
        and os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
        and (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        )
    )


def append_feedback_row(
    *,
    chat_id: int,
    user_id: int | None,
    username: str,
    full_name: str,
    participants: list[str],
    receipt_total: Decimal,
    tip_amount: Decimal,
    feedback_text: str,
) -> None:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
    if not spreadsheet_id or not worksheet_name:
        raise FeedbackStorageError("Google Sheets не настроены.")

    worksheet = _open_worksheet(spreadsheet_id, worksheet_name)
    now = datetime.now(timezone.utc).isoformat()
    row: list[Any] = [
        now,
        str(chat_id),
        str(user_id or ""),
        username,
        full_name,
        ", ".join(participants),
        _format_money(receipt_total),
        _format_money(tip_amount),
        feedback_text,
    ]
    worksheet.append_row(row, value_input_option="USER_ENTERED")


def has_feedback_for_user(*, user_id: int | None, chat_id: int) -> bool:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
    if not spreadsheet_id or not worksheet_name:
        return False

    worksheet = _open_worksheet(spreadsheet_id, worksheet_name)
    target_user_id = str(user_id) if user_id is not None else ""
    target_chat_id = str(chat_id)

    try:
        records = worksheet.get_all_records()
    except Exception as exc:
        raise FeedbackStorageError("Не удалось прочитать строки из Google Sheets.") from exc

    for record in records:
        record_user_id = str(record.get("user_id", "")).strip()
        record_chat_id = str(record.get("chat_id", "")).strip()
        if target_user_id and record_user_id == target_user_id:
            return True
        if not target_user_id and record_chat_id == target_chat_id:
            return True
    return False


def _open_worksheet(spreadsheet_id: str, worksheet_name: str):
    try:
        import gspread
    except ImportError as exc:
        raise FeedbackStorageError("Пакет gspread не установлен.") from exc

    credentials = _load_credentials()
    client = gspread.authorize(credentials)
    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        return spreadsheet.worksheet(worksheet_name)
    except Exception as exc:
        raise FeedbackStorageError("Не удалось открыть Google Sheets.") from exc


def _load_credentials():
    try:
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise FeedbackStorageError("Пакет google-auth не установлен.") from exc

    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    try:
        if service_account_json:
            info = json.loads(service_account_json)
            return Credentials.from_service_account_info(info, scopes=SCOPES)
        if service_account_file:
            return Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    except Exception as exc:
        raise FeedbackStorageError("Не удалось загрузить credentials сервисного аккаунта Google.") from exc

    raise FeedbackStorageError("Не заданы credentials сервисного аккаунта Google.")


def _format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"
