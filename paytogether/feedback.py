from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

class FeedbackStorageError(RuntimeError):
    pass


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]
FEEDBACK_HEADERS = [
    "timestamp_utc",
    "chat_id",
    "user_id",
    "username",
    "full_name",
    "participants",
    "feedback_text",
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
    feedback_text: str,
) -> None:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
    if not spreadsheet_id or not worksheet_name:
        raise FeedbackStorageError("Google Sheets не настроены.")

    worksheet = _open_worksheet(spreadsheet_id, worksheet_name)
    _ensure_feedback_headers(worksheet)
    _upsert_feedback_row(
        worksheet=worksheet,
        chat_id=chat_id,
        user_id=user_id,
        username=username,
        full_name=full_name,
        participants=participants,
        feedback_text=feedback_text,
        overwrite_feedback_text=True,
    )


def ensure_feedback_user_row(
    *,
    chat_id: int,
    user_id: int | None,
    username: str,
    full_name: str,
    participants: list[str],
) -> None:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
    if not spreadsheet_id or not worksheet_name:
        raise FeedbackStorageError("Google Sheets не настроены.")

    worksheet = _open_worksheet(spreadsheet_id, worksheet_name)
    _ensure_feedback_headers(worksheet)
    _upsert_feedback_row(
        worksheet=worksheet,
        chat_id=chat_id,
        user_id=user_id,
        username=username,
        full_name=full_name,
        participants=participants,
        feedback_text="",
        overwrite_feedback_text=False,
    )


def has_feedback_for_user(*, user_id: int | None, chat_id: int) -> bool:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_name = os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME")
    if not spreadsheet_id or not worksheet_name:
        return False

    worksheet = _open_worksheet(spreadsheet_id, worksheet_name)
    return _find_feedback_row_index(worksheet=worksheet, user_id=user_id, chat_id=chat_id) is not None


def _ensure_feedback_headers(worksheet) -> None:
    try:
        first_row = worksheet.row_values(1)
    except Exception as exc:
        raise FeedbackStorageError("Не удалось прочитать заголовки Google Sheets.") from exc

    if first_row[: len(FEEDBACK_HEADERS)] == FEEDBACK_HEADERS:
        return

    try:
        worksheet.update("A1:G1", [FEEDBACK_HEADERS], value_input_option="USER_ENTERED")
    except Exception as exc:
        raise FeedbackStorageError("Не удалось обновить заголовки Google Sheets.") from exc


def _upsert_feedback_row(
    *,
    worksheet,
    chat_id: int,
    user_id: int | None,
    username: str,
    full_name: str,
    participants: list[str],
    feedback_text: str,
    overwrite_feedback_text: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    row_index = _find_feedback_row_index(worksheet=worksheet, user_id=user_id, chat_id=chat_id)
    existing_feedback_text = ""
    if row_index is not None:
        existing_row = _get_row_values(worksheet, row_index)
        if len(existing_row) >= 7:
            existing_feedback_text = existing_row[6]

    row: list[Any] = [
        now,
        str(chat_id),
        str(user_id or ""),
        username,
        full_name,
        ", ".join(participants),
        feedback_text if overwrite_feedback_text else existing_feedback_text,
    ]

    if row_index is None:
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        return

    worksheet.update(
        f"A{row_index}:G{row_index}",
        [row],
        value_input_option="USER_ENTERED",
    )


def _find_feedback_row_index(*, worksheet, user_id: int | None, chat_id: int) -> int | None:
    target_user_id = str(user_id) if user_id is not None else ""
    target_chat_id = str(chat_id)

    try:
        rows = worksheet.get_all_values()
    except Exception as exc:
        raise FeedbackStorageError("Не удалось прочитать строки из Google Sheets.") from exc

    if not rows:
        return None

    headers = rows[0]
    header_index = {header: index for index, header in enumerate(headers)}
    user_id_index = header_index.get("user_id")
    chat_id_index = header_index.get("chat_id")

    for row_index, row in enumerate(rows[1:], start=2):
        record_user_id = row[user_id_index].strip() if user_id_index is not None and user_id_index < len(row) else ""
        record_chat_id = row[chat_id_index].strip() if chat_id_index is not None and chat_id_index < len(row) else ""
        if target_user_id and record_user_id == target_user_id:
            return row_index
        if not target_user_id and record_chat_id == target_chat_id:
            return row_index
    return None


def _get_row_values(worksheet, row_index: int) -> list[str]:
    try:
        return worksheet.row_values(row_index)
    except Exception as exc:
        raise FeedbackStorageError("Не удалось прочитать строку из Google Sheets.") from exc


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
