"""Tests for sheets_client.py — Google Sheets data layer."""

from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import SAMPLE_CONTACTS, SAMPLE_SETTINGS, SAMPLE_USER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cell(row, col, value):
    """Create a mock gspread Cell."""
    c = MagicMock()
    c.row = row
    c.col = col
    c.value = value
    return c


@pytest.fixture(autouse=True)
def reset_sheets_client_cache(env_vars):
    """Reset the cached gspread client before each test."""
    import sheets_client
    sheets_client._reset_client()
    yield
    sheets_client._reset_client()


# ---------------------------------------------------------------------------
# _retry
# ---------------------------------------------------------------------------

def _make_api_error(status_code):
    """Create a gspread APIError with the given HTTP status code."""
    from gspread.exceptions import APIError
    response = MagicMock()
    response.status_code = status_code
    return APIError(response)


class TestRetry:
    """Tests for the _retry helper."""

    def test_retries_on_429(self):
        from sheets_client import _retry
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise _make_api_error(429)
            return "ok"
        with patch("sheets_client.time.sleep"):
            assert _retry(fn) == "ok"
        assert len(calls) == 2

    def test_retries_on_500(self):
        from sheets_client import _retry
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise _make_api_error(500)
            return "ok"
        with patch("sheets_client.time.sleep"):
            assert _retry(fn) == "ok"
        assert len(calls) == 2

    def test_retries_on_503(self):
        from sheets_client import _retry
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise _make_api_error(503)
            return "ok"
        with patch("sheets_client.time.sleep"):
            assert _retry(fn) == "ok"
        assert len(calls) == 2

    def test_raises_immediately_on_non_retriable_error(self):
        from sheets_client import _retry
        from gspread.exceptions import APIError
        calls = []
        def fn():
            calls.append(1)
            raise _make_api_error(403)
        with pytest.raises(APIError):
            _retry(fn)
        assert len(calls) == 1

    def test_raises_after_max_retries_exhausted(self):
        from sheets_client import _retry
        from gspread.exceptions import APIError
        calls = []
        def fn():
            calls.append(1)
            raise _make_api_error(500)
        with patch("sheets_client.time.sleep"):
            with pytest.raises(APIError):
                _retry(fn, retries=3)
        assert len(calls) == 3


# ---------------------------------------------------------------------------
# get_user_by_phone
# ---------------------------------------------------------------------------

class TestGetUserByPhone:
    """Tests for get_user_by_phone(phone)."""

    def test_returns_user_dict_when_found(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_phone

        result = get_user_by_phone("+15550001111")

        assert result is not None
        assert result["phone"] == "+15550001111"
        assert result["name"] == "Test User"
        assert result["sheet_id"] == "test_sheet_id_xyz"

    def test_returns_none_when_not_found(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_phone

        result = get_user_by_phone("+19999999999")

        assert result is None

    def test_uses_master_sheet_id(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_phone

        get_user_by_phone("+15550001111")

        mock_gspread_client.open_by_key.assert_called_with("master_sheet_id_abc123")


# ---------------------------------------------------------------------------
# get_user_by_telegram_chat_id
# ---------------------------------------------------------------------------

class TestGetUserByTelegramChatId:
    """Tests for get_user_by_telegram_chat_id(chat_id)."""

    def test_returns_user_dict_when_found(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_telegram_chat_id

        users_ws = mock_gspread_client._worksheets["Users"]
        users_ws.get_all_records.return_value = [SAMPLE_USER.copy()]

        result = get_user_by_telegram_chat_id("123456789")

        assert result is not None
        assert result["telegram_chat_id"] == "123456789"
        assert result["sheet_id"] == "test_sheet_id_xyz"

    def test_returns_none_when_not_found(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_telegram_chat_id

        result = get_user_by_telegram_chat_id("999999999")

        assert result is None

    def test_matches_numeric_chat_id_stored_as_string(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_telegram_chat_id

        users_ws = mock_gspread_client._worksheets["Users"]
        users_ws.get_all_records.return_value = [
            {**SAMPLE_USER.copy(), "telegram_chat_id": "123456789"}
        ]

        result = get_user_by_telegram_chat_id("123456789")

        assert result is not None

    def test_uses_master_sheet_id(self, env_vars, mock_gspread_client):
        from sheets_client import get_user_by_telegram_chat_id

        users_ws = mock_gspread_client._worksheets["Users"]
        users_ws.get_all_records.return_value = [SAMPLE_USER.copy()]

        get_user_by_telegram_chat_id("123456789")

        mock_gspread_client.open_by_key.assert_called_with("master_sheet_id_abc123")


# ---------------------------------------------------------------------------
# get_all_users
# ---------------------------------------------------------------------------

class TestGetAllUsers:
    """Tests for get_all_users()."""

    def test_returns_all_user_rows(self, env_vars, mock_gspread_client):
        from sheets_client import get_all_users

        users_ws = mock_gspread_client._worksheets["Users"]
        users_ws.get_all_records.return_value = [
            {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet1"},
            {"phone": "+15550002222", "name": "User Two", "sheet_id": "sheet2"},
        ]

        result = get_all_users()

        assert len(result) == 2
        assert result[0]["phone"] == "+15550001111"
        assert result[1]["name"] == "User Two"

    def test_returns_empty_list_when_no_users(self, env_vars, mock_gspread_client):
        from sheets_client import get_all_users

        users_ws = mock_gspread_client._worksheets["Users"]
        users_ws.get_all_records.return_value = []

        result = get_all_users()

        assert result == []


# ---------------------------------------------------------------------------
# get_active_contacts
# ---------------------------------------------------------------------------

class TestGetActiveContacts:
    """Tests for get_active_contacts(sheet_id)."""

    def test_returns_only_active_contacts(self, env_vars, mock_gspread_client):
        from sheets_client import get_active_contacts

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        all_contacts = [c.copy() for c in SAMPLE_CONTACTS]
        archived = {
            "name": "Old Friend",
            "reminder_date": "",
            "last_contact_date": "2025-06-01",
            "last_interaction_message": "haven't talked in a while",
            "status": "archived",
        }
        all_contacts.append(archived)
        contacts_ws.get_all_records.return_value = all_contacts

        result = get_active_contacts("test_sheet_id")

        assert all(c["status"] == "active" for c in result)
        assert len(result) == len(SAMPLE_CONTACTS)
        names = [c["name"] for c in result]
        assert "Old Friend" not in names

    def test_returns_correct_fields(self, env_vars, mock_gspread_client):
        from sheets_client import get_active_contacts

        result = get_active_contacts("test_sheet_id")

        assert len(result) > 0
        contact = result[0]
        assert "name" in contact
        assert "reminder_date" in contact
        assert "last_contact_date" in contact
        assert "last_interaction_message" in contact
        assert "status" in contact

    def test_returns_empty_when_no_active(self, env_vars, mock_gspread_client):
        from sheets_client import get_active_contacts

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.get_all_records.return_value = [
            {
                "name": "Archived",
                "reminder_date": "",
                "last_contact_date": "2025-01-01",
                "last_interaction_message": "",
                "status": "archived",
            }
        ]

        result = get_active_contacts("test_sheet_id")

        assert result == []


# ---------------------------------------------------------------------------
# get_settings
# ---------------------------------------------------------------------------

class TestGetSettings:
    """Tests for get_settings(sheet_id)."""

    def test_returns_key_value_dict(self, env_vars, mock_gspread_client):
        from sheets_client import get_settings

        result = get_settings("test_sheet_id")

        assert result == SAMPLE_SETTINGS

    def test_returns_empty_dict_when_no_settings(self, env_vars, mock_gspread_client):
        from sheets_client import get_settings

        settings_ws = mock_gspread_client._worksheets["Settings"]
        settings_ws.get_all_records.return_value = []

        result = get_settings("test_sheet_id")

        assert result == {}


# ---------------------------------------------------------------------------
# update_contact
# ---------------------------------------------------------------------------

class TestUpdateContact:
    """Tests for update_contact(sheet_id, contact_name, updates)."""

    def test_updates_correct_row(self, env_vars, mock_gspread_client):
        from sheets_client import update_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = _cell(row=2, col=1, value="Sarah Chen")
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        updates = {
            "last_contact_date": "2026-02-13",
            "last_interaction_message": "had coffee",
        }
        update_contact("test_sheet_id", "Sarah Chen", updates)

        assert contacts_ws.update_cell.call_count == 2

    def test_raises_when_contact_not_found(self, env_vars, mock_gspread_client):
        from sheets_client import update_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = None

        with pytest.raises(ValueError, match="not found"):
            update_contact("test_sheet_id", "Nonexistent", {"status": "active"})

    def test_updates_correct_columns(self, env_vars, mock_gspread_client):
        from sheets_client import update_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = _cell(row=3, col=1, value="Dad")
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        update_contact("test_sheet_id", "Dad", {"reminder_date": "2026-03-15"})

        # reminder_date is column 2
        contacts_ws.update_cell.assert_called_once_with(3, 2, "2026-03-15")


# ---------------------------------------------------------------------------
# add_contact
# ---------------------------------------------------------------------------

class TestAddContact:
    """Tests for add_contact(sheet_id, contact_data)."""

    def test_appends_row_with_correct_fields(self, env_vars, mock_gspread_client):
        from sheets_client import add_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        contact_data = {
            "name": "New Person",
            "reminder_date": "2026-03-01",
            "last_contact_date": "2026-02-13",
            "last_interaction_message": "met at conference",
            "status": "active",
        }
        add_contact("test_sheet_id", contact_data)

        contacts_ws.append_row.assert_called_once()
        appended = contacts_ws.append_row.call_args[0][0]
        assert appended[0] == "New Person"
        assert "active" in appended

    def test_appends_fields_in_header_order(self, env_vars, mock_gspread_client):
        from sheets_client import add_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        contact_data = {
            "name": "Alice",
            "status": "active",
            "reminder_date": "2026-04-01",
            "last_contact_date": "2026-02-13",
            "last_interaction_message": "brunch",
        }
        add_contact("test_sheet_id", contact_data)

        appended = contacts_ws.append_row.call_args[0][0]
        assert appended == ["Alice", "2026-04-01", "2026-02-13", "brunch", "active"]


# ---------------------------------------------------------------------------
# add_log_entry
# ---------------------------------------------------------------------------

class TestAddLogEntry:
    """Tests for add_log_entry(sheet_id, log_data)."""

    def test_appends_to_logs_tab(self, env_vars, mock_gspread_client):
        from sheets_client import add_log_entry

        logs_ws = mock_gspread_client._worksheets["Logs"]
        headers = ["date", "contact_name", "intent", "raw_message"]
        logs_ws.row_values.return_value = headers

        log_data = {
            "date": "2026-02-13",
            "contact_name": "Sarah Chen",
            "intent": "log_interaction",
            "raw_message": "Had coffee with Sarah",
        }
        add_log_entry("test_sheet_id", log_data)

        logs_ws.append_row.assert_called_once()
        appended = logs_ws.append_row.call_args[0][0]
        assert appended[0] == "2026-02-13"
        assert appended[1] == "Sarah Chen"

    def test_log_fields_in_header_order(self, env_vars, mock_gspread_client):
        from sheets_client import add_log_entry

        logs_ws = mock_gspread_client._worksheets["Logs"]
        headers = ["date", "contact_name", "intent", "raw_message"]
        logs_ws.row_values.return_value = headers

        log_data = {
            "raw_message": "Had coffee with Sarah",
            "date": "2026-02-13",
            "contact_name": "Sarah Chen",
            "intent": "log_interaction",
        }
        add_log_entry("test_sheet_id", log_data)

        appended = logs_ws.append_row.call_args[0][0]
        assert appended == [
            "2026-02-13", "Sarah Chen", "log_interaction",
            "Had coffee with Sarah",
        ]


# ---------------------------------------------------------------------------
# archive_contact
# ---------------------------------------------------------------------------

class TestArchiveContact:
    """Tests for archive_contact(sheet_id, contact_name)."""

    def test_sets_status_to_archived(self, env_vars, mock_gspread_client):
        from sheets_client import archive_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = _cell(row=2, col=1, value="Sarah Chen")
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        archive_contact("test_sheet_id", "Sarah Chen")

        # status is column 5
        contacts_ws.update_cell.assert_called_once_with(2, 5, "archived")

    def test_raises_when_contact_not_found(self, env_vars, mock_gspread_client):
        from sheets_client import archive_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = None

        with pytest.raises(ValueError, match="not found"):
            archive_contact("test_sheet_id", "Ghost")


# ---------------------------------------------------------------------------
# rename_contact
# ---------------------------------------------------------------------------

class TestRenameContact:
    """Tests for rename_contact(sheet_id, old_name, new_name)."""

    def test_updates_name_cell(self, env_vars, mock_gspread_client):
        from sheets_client import rename_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = _cell(row=2, col=1, value="Becca")
        headers = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
        contacts_ws.row_values.return_value = headers

        rename_contact("test_sheet_id", "Becca", "Becca Zhou")

        # name is column 1
        contacts_ws.update_cell.assert_called_once_with(2, 1, "Becca Zhou")

    def test_raises_when_contact_not_found(self, env_vars, mock_gspread_client):
        from sheets_client import rename_contact

        contacts_ws = mock_gspread_client._worksheets["Contacts"]
        contacts_ws.find.return_value = None

        with pytest.raises(ValueError, match="not found"):
            rename_contact("test_sheet_id", "Ghost", "Ghost Name")


# ---------------------------------------------------------------------------
# get_recent_logs
# ---------------------------------------------------------------------------

class TestGetRecentLogs:
    """Tests for get_recent_logs(sheet_id, limit)."""

    def test_returns_last_n_rows_most_recent_first(self, env_vars, mock_gspread_client):
        from sheets_client import get_recent_logs

        logs_ws = mock_gspread_client._worksheets["Logs"]
        logs_ws.get_all_values.return_value = [
            ["date", "contact_name", "intent", "raw_message"],
            ["2026-02-10", "Sarah Chen", "log_interaction", "Had coffee with Sarah"],
            ["2026-02-12", "Dad", "log_interaction", "Lunch with Dad"],
            ["2026-02-14", "Mike Torres", "query", "When did I last see Mike?"],
        ]

        result = get_recent_logs("test_sheet_id", limit=2)

        assert len(result) == 2
        assert result[0]["contact_name"] == "Mike Torres"  # most recent first
        assert result[1]["contact_name"] == "Dad"

    def test_returns_empty_when_no_logs(self, env_vars, mock_gspread_client):
        from sheets_client import get_recent_logs

        logs_ws = mock_gspread_client._worksheets["Logs"]
        logs_ws.get_all_values.return_value = [
            ["date", "contact_name", "intent", "raw_message"],
        ]

        result = get_recent_logs("test_sheet_id")

        assert result == []

    def test_returns_all_when_fewer_than_limit(self, env_vars, mock_gspread_client):
        from sheets_client import get_recent_logs

        logs_ws = mock_gspread_client._worksheets["Logs"]
        logs_ws.get_all_values.return_value = [
            ["date", "contact_name", "intent", "raw_message"],
            ["2026-02-10", "Sarah Chen", "log_interaction", "Had coffee with Sarah"],
        ]

        result = get_recent_logs("test_sheet_id", limit=5)

        assert len(result) == 1
        assert result[0]["raw_message"] == "Had coffee with Sarah"
