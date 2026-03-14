from paytogether.feedback import feedback_storage_enabled, has_feedback_for_user


def test_feedback_storage_enabled_requires_sheet_and_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SHEETS_WORKSHEET_NAME", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)

    assert feedback_storage_enabled() is False

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/creds.json")

    assert feedback_storage_enabled() is True


def test_has_feedback_for_user_checks_user_id_first(monkeypatch):
    class FakeWorksheet:
        def get_all_records(self):
            return [
                {"user_id": "111", "chat_id": "999"},
                {"user_id": "222", "chat_id": "888"},
            ]

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: FakeWorksheet())

    assert has_feedback_for_user(user_id=222, chat_id=123) is True
    assert has_feedback_for_user(user_id=333, chat_id=123) is False


def test_has_feedback_for_user_falls_back_to_chat_id(monkeypatch):
    class FakeWorksheet:
        def get_all_records(self):
            return [
                {"user_id": "", "chat_id": "555"},
            ]

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: FakeWorksheet())

    assert has_feedback_for_user(user_id=None, chat_id=555) is True
