from paytogether.feedback import FEEDBACK_HEADERS, append_feedback_row, ensure_feedback_user_row, feedback_storage_enabled, has_feedback_for_user


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
        def row_values(self, row_index):
            rows = self.get_all_values()
            return rows[row_index - 1] if row_index <= len(rows) else []

        def get_all_values(self):
            return [
                FEEDBACK_HEADERS,
                ["2026-01-01T00:00:00+00:00", "999", "111", "", "", ""],
                ["2026-01-01T00:00:00+00:00", "888", "222", "", "", "Спасибо!"],
            ]

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: FakeWorksheet())

    assert has_feedback_for_user(user_id=222, chat_id=123) is True
    assert has_feedback_for_user(user_id=333, chat_id=123) is False


def test_has_feedback_for_user_returns_false_for_blank_feedback_text(monkeypatch):
    class FakeWorksheet:
        def row_values(self, row_index):
            rows = self.get_all_values()
            return rows[row_index - 1] if row_index <= len(rows) else []

        def get_all_values(self):
            return [
                FEEDBACK_HEADERS,
                ["2026-01-01T00:00:00+00:00", "555", "", "", "", "   "],
            ]

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: FakeWorksheet())

    assert has_feedback_for_user(user_id=None, chat_id=555) is False


def test_append_feedback_row_appends_new_row_with_updated_schema(monkeypatch):
    class FakeWorksheet:
        def __init__(self):
            self.headers = []
            self.appended_rows = []

        def row_values(self, row_index):
            return self.headers if row_index == 1 else []

        def update(self, cell_range, values, value_input_option="RAW"):
            if cell_range == "A1:F1":
                self.headers = values[0]

        def get_all_values(self):
            return [self.headers]

        def append_row(self, row, value_input_option="RAW"):
            self.appended_rows.append(row)

    worksheet = FakeWorksheet()
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: worksheet)

    append_feedback_row(
        chat_id=123,
        user_id=456,
        username="tester",
        full_name="Test User",
        feedback_text="Очень удобно",
    )

    assert worksheet.headers == FEEDBACK_HEADERS
    assert len(worksheet.appended_rows) == 1
    assert len(worksheet.appended_rows[0]) == 6
    assert worksheet.appended_rows[0][1:] == [
        "123",
        "456",
        "tester",
        "Test User",
        "Очень удобно",
    ]


def test_append_feedback_row_always_appends_new_row_for_existing_user(monkeypatch):
    class FakeWorksheet:
        def __init__(self):
            self.rows = [
                FEEDBACK_HEADERS,
                ["2026-01-01T00:00:00+00:00", "123", "456", "old", "Old User", "Старый отзыв"],
            ]
            self.appended_rows = []

        def row_values(self, row_index):
            return self.rows[row_index - 1] if row_index <= len(self.rows) else []

        def update(self, cell_range, values, value_input_option="RAW"):
            if cell_range == "A1:F1":
                self.rows[0] = values[0]

        def get_all_values(self):
            return self.rows

        def append_row(self, row, value_input_option="RAW"):
            self.appended_rows.append(row)

    worksheet = FakeWorksheet()
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: worksheet)

    append_feedback_row(
        chat_id=123,
        user_id=456,
        username="tester",
        full_name="Test User",
        feedback_text="Новый отзыв",
    )

    assert len(worksheet.appended_rows) == 1
    assert worksheet.appended_rows[0][1:] == [
        "123",
        "456",
        "tester",
        "Test User",
        "Новый отзыв",
    ]


def test_ensure_feedback_user_row_always_appends_new_row(monkeypatch):
    class FakeWorksheet:
        def __init__(self):
            self.rows = [
                FEEDBACK_HEADERS,
                ["2026-01-01T00:00:00+00:00", "123", "456", "old", "Old User", "Старый отзыв"],
            ]
            self.appended_rows = []

        def row_values(self, row_index):
            return self.rows[row_index - 1] if row_index <= len(self.rows) else []

        def update(self, cell_range, values, value_input_option="RAW"):
            if cell_range == "A1:F1":
                self.rows[0] = values[0]

        def get_all_values(self):
            return self.rows

        def append_row(self, row, value_input_option="RAW"):
            self.appended_rows.append(row)

    worksheet = FakeWorksheet()
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id")
    monkeypatch.setenv("GOOGLE_SHEETS_WORKSHEET_NAME", "feedback")
    monkeypatch.setattr("paytogether.feedback._open_worksheet", lambda *_args: worksheet)

    ensure_feedback_user_row(
        chat_id=123,
        user_id=456,
        username="new_username",
        full_name="New Name",
    )

    assert len(worksheet.appended_rows) == 1
    assert worksheet.appended_rows[0][1:] == [
        "123",
        "456",
        "new_username",
        "New Name",
        "",
    ]
