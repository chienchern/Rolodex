"""Tests for reminder_handler.py — daily reminder cron."""

import importlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytz


# ---------------------------------------------------------------------------
# Fixture: fully mocked reminder_handler
# ---------------------------------------------------------------------------

@pytest.fixture
def handler(env_vars):
    """Import reminder_handler with all dependencies mocked.

    Yields a namespace with the module and all mocks for assertions.
    """
    with patch("reminder_handler.id_token") as mock_id_token, \
         patch("reminder_handler.sheets_client") as mock_sc, \
         patch("reminder_handler.send_sms") as mock_send:

        mock_id_token.verify_oauth2_token.return_value = {"email": "scheduler@gcp.iam"}
        mock_sc.get_all_users.return_value = []
        mock_sc.get_settings.return_value = {
            "timezone": "America/New_York",
            "default_reminder_days": "14",
        }
        mock_sc.get_active_contacts.return_value = []

        import reminder_handler

        class Ns:
            pass
        ns = Ns()
        ns.mod = reminder_handler
        ns.mock_id_token = mock_id_token
        ns.mock_sc = mock_sc
        ns.mock_send = mock_send
        yield ns


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestOIDCAuth:
    """Validate OIDC token handling."""

    def test_valid_oidc_token_proceeds(self, handler):
        """Valid OIDC token should allow the request to proceed (200)."""
        body, status = handler.mod.handle_reminder_cron("Bearer valid_token")
        assert status == 200

    def test_missing_token_returns_401(self, handler):
        """Missing authorization header returns 401."""
        body, status = handler.mod.handle_reminder_cron(None)
        assert status == 401
        handler.mock_id_token.verify_oauth2_token.assert_not_called()

    def test_invalid_token_returns_401(self, handler):
        """Invalid OIDC token returns 401."""
        handler.mock_id_token.verify_oauth2_token.side_effect = ValueError("bad token")
        body, status = handler.mod.handle_reminder_cron("Bearer bad_token")
        assert status == 401

    def test_skip_oidc_env_var(self, handler, monkeypatch):
        """When SKIP_OIDC_VALIDATION is set, auth is skipped."""
        monkeypatch.setenv("SKIP_OIDC_VALIDATION", "1")
        body, status = handler.mod.handle_reminder_cron(None)
        assert status == 200
        handler.mock_id_token.verify_oauth2_token.assert_not_called()


# ---------------------------------------------------------------------------
# Date logic tests
# ---------------------------------------------------------------------------


class TestReminderDateLogic:
    """Verify which contacts trigger reminders."""

    def test_day_of_reminder(self, handler):
        """Contact with reminder_date == today gets a day-of reminder."""
        tz = pytz.timezone("America/New_York")
        today_str = datetime.now(tz).strftime("%Y-%m-%d")

        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Sarah Chen",
                "reminder_date": today_str,
                "last_contact_date": "2026-01-15",
                "last_contact_notes": "discussed her startup",
                "status": "active",
            }
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")
        assert status == 200
        handler.mock_send.assert_called_once()
        sms_body = handler.mock_send.call_args[1]["body"]
        assert "Sarah Chen" in sms_body

    def test_one_week_before_reminder(self, handler):
        """Contact with reminder_date == today + 7, and no recent contact, gets 1-week-before reminder."""
        tz = pytz.timezone("America/New_York")
        today = datetime.now(tz).date()
        reminder_date = today + timedelta(days=7)
        last_contact_date = today - timedelta(days=30)

        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Dad",
                "reminder_date": reminder_date.isoformat(),
                "last_contact_date": last_contact_date.isoformat(),
                "last_contact_notes": "called him about retirement",
                "status": "active",
            }
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")
        assert status == 200
        handler.mock_send.assert_called_once()
        sms_body = handler.mock_send.call_args[1]["body"]
        assert "Dad" in sms_body

    def test_no_one_week_reminder_for_recent_contact(self, handler):
        """Contact with reminder_date == today + 7 but recent interaction gets NO 1-week reminder."""
        tz = pytz.timezone("America/New_York")
        today = datetime.now(tz).date()
        reminder_date = today + timedelta(days=7)
        # last_contact_date is today, so reminder_date (today+7) <= last_contact_date + 7 (today+7)
        last_contact_date = today

        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Mike Torres",
                "reminder_date": reminder_date.isoformat(),
                "last_contact_date": last_contact_date.isoformat(),
                "last_contact_notes": "lunch",
                "status": "active",
            }
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")
        assert status == 200
        handler.mock_send.assert_not_called()

    def test_archived_contacts_excluded(self, handler):
        """Archived contacts should not trigger reminders (get_active_contacts filters them)."""
        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        # get_active_contacts returns empty list (archived contacts already filtered)
        handler.mock_sc.get_active_contacts.return_value = []

        body, status = handler.mod.handle_reminder_cron("Bearer valid")
        assert status == 200
        handler.mock_send.assert_not_called()

    def test_contacts_with_no_reminder_date_excluded(self, handler):
        """Contacts with empty reminder_date are skipped."""
        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Mike Torres",
                "reminder_date": "",
                "last_contact_date": "2026-02-03",
                "last_contact_notes": "lunch",
                "status": "active",
            }
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")
        assert status == 200
        handler.mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# SMS batching tests
# ---------------------------------------------------------------------------


class TestSMSBatching:
    """Multiple reminders for one user are combined into a single SMS."""

    def test_multiple_reminders_single_sms(self, handler):
        """Two contacts due today should produce one SMS with both names."""
        tz = pytz.timezone("America/New_York")
        today_str = datetime.now(tz).strftime("%Y-%m-%d")

        user = {"phone": "+15550001111", "name": "Test User", "sheet_id": "sheet123"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Sarah Chen",
                "reminder_date": today_str,
                "last_contact_date": "2026-01-15",
                "last_contact_notes": "discussed her startup",
                "status": "active",
            },
            {
                "name": "Dad",
                "reminder_date": today_str,
                "last_contact_date": "2026-01-20",
                "last_contact_notes": "called about retirement",
                "status": "active",
            },
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")

        assert status == 200
        # Only one SMS call for the user, not two
        handler.mock_send.assert_called_once()
        sms_body = handler.mock_send.call_args[1]["body"]
        assert "Sarah Chen" in sms_body
        assert "Dad" in sms_body


# ---------------------------------------------------------------------------
# Timezone handling tests
# ---------------------------------------------------------------------------


class TestTimezoneHandling:
    """'Today' is computed per-user using their timezone setting."""

    def test_today_uses_user_timezone(self, handler):
        """A user in a different timezone should have 'today' computed in their tz."""
        tz = pytz.timezone("Pacific/Auckland")  # NZ is far ahead of UTC
        today_str = datetime.now(tz).strftime("%Y-%m-%d")

        user = {"phone": "+15550001111", "name": "NZ User", "sheet_id": "sheet_nz"}
        handler.mock_sc.get_all_users.return_value = [user]
        handler.mock_sc.get_settings.return_value = {
            "timezone": "Pacific/Auckland",
            "default_reminder_days": "14",
        }
        handler.mock_sc.get_active_contacts.return_value = [
            {
                "name": "Kiwi Friend",
                "reminder_date": today_str,
                "last_contact_date": "2026-01-01",
                "last_contact_notes": "catch up",
                "status": "active",
            }
        ]

        body, status = handler.mod.handle_reminder_cron("Bearer valid")

        assert status == 200
        handler.mock_send.assert_called_once()
        sms_body = handler.mock_send.call_args[1]["body"]
        assert "Kiwi Friend" in sms_body
