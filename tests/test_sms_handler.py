"""Tests for sms_handler.py — SMS webhook orchestration."""

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_FORM_DATA = {
    "MessageSid": "SM_test_abc123",
    "From": "+15550001111",
    "Body": "Had coffee with Sarah",
    "To": "+15550001234",
}

REQUEST_URL = "https://example.com/sms-webhook"
TWILIO_SIGNATURE = "valid_signature_abc"

SAMPLE_USER = {
    "phone": "+15550001111",
    "name": "Test User",
    "sheet_id": "test_sheet_id_xyz",
}

SAMPLE_CONTACTS = [
    {
        "name": "Sarah Chen",
        "reminder_date": "2026-02-20",
        "last_contact_date": "2026-01-15",
        "last_contact_notes": "discussed her startup",
        "status": "active",
    },
    {
        "name": "Dad",
        "reminder_date": "2026-03-01",
        "last_contact_date": "2026-01-20",
        "last_contact_notes": "called him about retirement",
        "status": "active",
    },
]

SAMPLE_SETTINGS = {
    "timezone": "America/New_York",
    "default_reminder_days": "14",
}


def _nlp_response(intent, contacts=None, notes=None, follow_up_date=None,
                  needs_clarification=False, clarification_question=None,
                  response_message="Done."):
    """Build a mock NLP response dict."""
    return {
        "intent": intent,
        "contacts": contacts or [],
        "notes": notes,
        "follow_up_date": follow_up_date,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "response_message": response_message,
    }


# ---------------------------------------------------------------------------
# Fixture: fully mocked sms_handler
# ---------------------------------------------------------------------------

@pytest.fixture
def handler(env_vars):
    """Import sms_handler with all dependencies mocked.

    Uses env_vars fixture from conftest.py to set environment variables
    before any config imports happen.

    Yields a namespace with the module and all mocks for assertions.
    """
    # Remove cached modules so sms_handler re-imports cleanly
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("sms_handler", "config", "nlp", "sheets_client", "context"):
            del sys.modules[mod_name]

    # Now mock external services before importing sms_handler
    mock_sns_client = MagicMock()
    mock_genai_client = MagicMock()
    mock_firestore_client_cls = MagicMock()
    mock_gspread = MagicMock()

    with patch("boto3.client", return_value=mock_sns_client), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("google.cloud.firestore.Client", return_value=MagicMock()), \
         patch("gspread.service_account_from_dict", return_value=mock_gspread):

        # Import sms_handler (triggers config, nlp, sheets_client, context imports)
        import sms_handler
        importlib.reload(sms_handler)

        # Now patch the module-level references within sms_handler
        with patch.object(sms_handler, "RequestValidator") as mock_rv_cls, \
             patch.object(sms_handler, "context") as mock_context, \
             patch.object(sms_handler, "sheets_client") as mock_sheets, \
             patch.object(sms_handler, "nlp") as mock_nlp, \
             patch.object(sms_handler, "send_sms") as mock_send_sms, \
             patch.object(sms_handler, "TWILIO_AUTH_TOKEN", "test_auth_token"), \
             patch.object(sms_handler, "BATCH_WINDOW_SECONDS", 5), \
             patch.object(sms_handler, "time") as mock_time:

            # RequestValidator setup
            mock_validator = MagicMock()
            mock_rv_cls.return_value = mock_validator
            mock_validator.validate.return_value = True

            # Context defaults
            mock_context.is_message_processed.return_value = False
            mock_context.has_newer_message.return_value = False
            mock_context.get_context.return_value = None
            mock_context.get_pending_messages.return_value = [
                {"message_text": "Had coffee with Sarah",
                 "message_sid": "SM_test_abc123",
                 "received_at": datetime.now(timezone.utc)}
            ]

            # Sheets defaults
            mock_sheets.get_user_by_phone.return_value = SAMPLE_USER.copy()
            mock_sheets.get_active_contacts.return_value = [c.copy() for c in SAMPLE_CONTACTS]
            mock_sheets.get_settings.return_value = SAMPLE_SETTINGS.copy()

            # NLP default
            mock_nlp.parse_sms.return_value = _nlp_response(
                intent="log_interaction",
                contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
                notes="had coffee",
                follow_up_date="2026-03-01",
                response_message="Updated Sarah Chen. I'll remind you on Sunday, Mar 1, 2026.",
            )

            # time.sleep is a no-op
            mock_time.sleep.return_value = None

            class Ns:
                pass
            ns = Ns()
            ns.mod = sms_handler
            ns.mock_validator = mock_validator
            ns.mock_context = mock_context
            ns.mock_sheets = mock_sheets
            ns.mock_nlp = mock_nlp
            ns.mock_send_sms = mock_send_sms
            ns.mock_time = mock_time
            ns.mock_rv_cls = mock_rv_cls
            yield ns


# ===================================================================
# Orchestration tests
# ===================================================================

class TestSignatureValidation:
    def test_valid_signature_proceeds(self, handler):
        handler.mock_validator.validate.return_value = True
        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_send_sms.assert_called()

    def test_invalid_signature_returns_error(self, handler):
        handler.mock_validator.validate.return_value = False
        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_context.is_message_processed.assert_not_called()
        assert "error" in result.lower() or "signature" in result.lower() or result == ""


class TestIdempotency:
    def test_duplicate_message_returns_200_without_processing(self, handler):
        handler.mock_context.is_message_processed.return_value = True
        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_nlp.parse_sms.assert_not_called()
        handler.mock_sheets.update_contact.assert_not_called()
        handler.mock_send_sms.assert_not_called()


class TestUserLookup:
    def test_unknown_phone_sends_error_sms(self, handler):
        handler.mock_sheets.get_user_by_phone.return_value = None
        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "not" in sms_body.lower() or "error" in sms_body.lower() or "registered" in sms_body.lower()


class TestBatchWindow:
    def test_defers_if_newer_message_exists(self, handler):
        handler.mock_context.has_newer_message.return_value = True
        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_nlp.parse_sms.assert_not_called()
        handler.mock_send_sms.assert_not_called()
        handler.mock_time.sleep.assert_called()

    def test_sleep_called_with_batch_window(self, handler):
        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)
        handler.mock_time.sleep.assert_called_with(5)


# ===================================================================
# Intent routing tests
# ===================================================================

class TestLogInteraction:
    def test_updates_contact_adds_log_sets_reminder_sends_reply(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            notes="had coffee",
            follow_up_date="2026-03-01",
            response_message="Updated Sarah Chen. Reminder on Mar 1.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        # Update contact
        handler.mock_sheets.update_contact.assert_called_once()
        update_args = handler.mock_sheets.update_contact.call_args
        assert update_args[0][1] == "Sarah Chen"
        updates = update_args[0][2]
        assert "last_contact_date" in updates
        assert "last_contact_notes" in updates
        assert "reminder_date" in updates
        assert updates["reminder_date"] == "2026-03-01"

        # Add log entry
        handler.mock_sheets.add_log_entry.assert_called_once()
        log_args = handler.mock_sheets.add_log_entry.call_args
        log_data = log_args[0][1]
        assert log_data["contact_name"] == "Sarah Chen"
        assert log_data["intent"] == "log_interaction"

        # Send reply
        handler.mock_send_sms.assert_called_once()

    def test_uses_default_reminder_days_when_no_follow_up_date(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            notes="had coffee",
            follow_up_date=None,
            response_message="Updated Sarah Chen.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        assert "reminder_date" in updates
        assert updates["reminder_date"] is not None


class TestLogInteractionPreserveReminder:
    """Bug 2: When no explicit timing, preserve existing reminder_date."""

    def test_preserves_existing_reminder_when_no_follow_up_date(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            notes="had coffee",
            follow_up_date=None,
            response_message="Updated Sarah Chen. Existing reminder unchanged.",
        )
        # Sarah Chen has reminder_date="2026-02-20" in SAMPLE_CONTACTS
        handler.mock_sheets.get_active_contacts.return_value = [c.copy() for c in SAMPLE_CONTACTS]

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        # Should preserve existing reminder, not compute a new one
        assert updates["reminder_date"] == "2026-02-20"

    def test_uses_default_when_no_follow_up_and_no_existing_reminder(self, handler):
        """Contact with no existing reminder gets default interval."""
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Mike Torres", "match_type": "exact"}],
            notes="lunch",
            follow_up_date=None,
            response_message="Updated Mike Torres.",
        )
        handler.mock_sheets.get_active_contacts.return_value = [c.copy() for c in SAMPLE_CONTACTS]

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        # Mike Torres has reminder_date="" so should get default
        assert updates["reminder_date"] is not None
        assert updates["reminder_date"] != ""


class TestLogInteractionDate:
    """Bug 1: last_contact_date should use interaction_date when available."""

    def test_uses_interaction_date_when_provided(self, handler):
        handler.mock_nlp.parse_sms.return_value = {
            **_nlp_response(
                intent="log_interaction",
                contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
                notes="had coffee",
                follow_up_date="2026-03-01",
                response_message="Updated Sarah Chen (met Friday, Feb 13).",
            ),
            "interaction_date": "2026-02-13",
        }

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        assert updates["last_contact_date"] == "2026-02-13"

    def test_uses_today_when_no_interaction_date(self, handler):
        handler.mock_nlp.parse_sms.return_value = {
            **_nlp_response(
                intent="log_interaction",
                contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
                notes="had coffee",
                follow_up_date="2026-03-01",
                response_message="Updated Sarah Chen.",
            ),
            "interaction_date": None,
        }

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        # Should use today_str (computed from now), not a hardcoded date
        assert updates["last_contact_date"] is not None
        assert updates["last_contact_date"] != "2026-02-13"


class TestQuery:
    def test_no_sheet_updates_sends_response(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="query",
            contacts=[{"name": "Mike Torres", "match_type": "exact"}],
            response_message="You last talked to Mike Torres on Feb 3.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.update_contact.assert_not_called()
        handler.mock_sheets.add_log_entry.assert_not_called()
        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "Mike Torres" in sms_body


class TestSetReminder:
    def test_creates_log_entry(self, handler):
        """Bug 3: set_reminder should create a log entry."""
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="set_reminder",
            contacts=[{"name": "Dad", "match_type": "exact"}],
            notes="birthday",
            follow_up_date="2026-04-01",
            response_message="Reminder set for Dad on Wednesday, Apr 1, 2026.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.add_log_entry.assert_called_once()
        log_data = handler.mock_sheets.add_log_entry.call_args[0][1]
        assert log_data["intent"] == "set_reminder"
        assert log_data["contact_name"] == "Dad"

    def test_uses_default_when_no_follow_up_date(self, handler):
        """Bug 4: set_reminder with no timing uses default interval."""
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="set_reminder",
            contacts=[{"name": "Dad", "match_type": "exact"}],
            notes=None,
            follow_up_date=None,
            response_message="Reminder set for Dad.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        update_args = handler.mock_sheets.update_contact.call_args
        updates = update_args[0][2]
        assert updates["reminder_date"] is not None
        assert updates["reminder_date"] != ""

    def test_updates_reminder_date_sends_confirmation(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="set_reminder",
            contacts=[{"name": "Dad", "match_type": "exact"}],
            follow_up_date="2026-04-01",
            response_message="Reminder set for Dad on Wednesday, Apr 1, 2026.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.update_contact.assert_called_once()
        update_args = handler.mock_sheets.update_contact.call_args
        assert update_args[0][1] == "Dad"
        updates = update_args[0][2]
        assert updates["reminder_date"] == "2026-04-01"

        handler.mock_send_sms.assert_called_once()


class TestArchive:
    def test_first_call_stores_context_asking_confirmation(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="archive",
            contacts=[{"name": "Sarah Chen", "match_type": "exact"}],
            needs_clarification=True,
            clarification_question="Archive Sarah Chen? Reply YES to confirm.",
            response_message="Archive Sarah Chen? Reply YES to confirm.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_context.store_context.assert_called_once()
        ctx_data = handler.mock_context.store_context.call_args[0][1]
        assert ctx_data["pending_intent"] == "archive"
        handler.mock_sheets.archive_contact.assert_not_called()
        handler.mock_send_sms.assert_called_once()

    def test_confirmation_executes_archive(self, handler):
        handler.mock_context.get_context.return_value = {
            "pending_intent": "archive",
            "original_message": "Remove Sarah from my rolodex",
            "candidates": ["Sarah Chen"],
        }
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="archive",
            contacts=[{"name": "Sarah Chen", "match_type": "exact"}],
            needs_clarification=False,
            response_message="Sarah Chen has been archived.",
        )

        form_data = {**SAMPLE_FORM_DATA, "Body": "YES", "MessageSid": "SM_confirm_123"}
        handler.mock_context.get_pending_messages.return_value = [
            {"message_text": "YES", "message_sid": "SM_confirm_123",
             "received_at": datetime.now(timezone.utc)}
        ]
        handler.mod.handle_inbound_sms(form_data, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.archive_contact.assert_called_once()
        archive_args = handler.mock_sheets.archive_contact.call_args
        assert archive_args[0][1] == "Sarah Chen"
        handler.mock_send_sms.assert_called_once()


class TestClarify:
    def test_stores_context_and_sends_clarification(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="clarify",
            contacts=[
                {"name": "John Smith", "match_type": "ambiguous"},
                {"name": "John Doe", "match_type": "ambiguous"},
            ],
            needs_clarification=True,
            clarification_question="Which John? John Smith or John Doe?",
            response_message="Which John? John Smith or John Doe?",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_context.store_context.assert_called_once()
        ctx_data = handler.mock_context.store_context.call_args[0][1]
        assert "candidates" in ctx_data
        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "John" in sms_body


class TestOnboarding:
    """Bug 6: onboarding should log with intent='onboarding'."""

    def test_onboarding_logs_with_correct_intent(self, handler):
        handler.mock_nlp.parse_sms.return_value = {
            **_nlp_response(
                intent="onboarding",
                contacts=[{"name": "Priya", "match_type": "new"}],
                notes="dinner",
                follow_up_date=None,
                needs_clarification=False,
                response_message="Added Priya to your rolodex.",
            ),
            "interaction_date": None,
        }
        handler.mock_context.get_context.return_value = {
            "pending_intent": "onboarding",
            "original_message": "Dinner with Priya",
            "candidates": ["Priya"],
        }

        form_data = {**SAMPLE_FORM_DATA, "Body": "YES", "MessageSid": "SM_confirm_onboard"}
        handler.mock_context.get_pending_messages.return_value = [
            {"message_text": "YES", "message_sid": "SM_confirm_onboard",
             "received_at": datetime.now(timezone.utc)}
        ]
        handler.mod.handle_inbound_sms(form_data, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.add_log_entry.assert_called_once()
        log_data = handler.mock_sheets.add_log_entry.call_args[0][1]
        assert log_data["intent"] == "onboarding"


class TestUnknown:
    def test_sends_response_message_as_is(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="unknown",
            response_message="I couldn't understand that. Try something like 'Had coffee with Sarah'.",
        )

        handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_sheets.update_contact.assert_not_called()
        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "couldn't understand" in sms_body.lower() or "coffee" in sms_body.lower()


# ===================================================================
# Multi-turn tests
# ===================================================================

class TestMultiTurn:
    def test_stale_context_discarded_when_new_intent_detected(self, handler):
        """If user ignores clarification and sends a new intent, discard old context."""
        handler.mock_context.get_context.return_value = {
            "pending_intent": "clarify",
            "original_message": "Met with John",
            "candidates": ["John Smith", "John Doe"],
        }
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="query",
            contacts=[{"name": "Mike Torres", "match_type": "exact"}],
            response_message="You last talked to Mike Torres on Feb 3.",
        )

        form_data = {**SAMPLE_FORM_DATA, "Body": "When did I last talk to Mike?", "MessageSid": "SM_new_123"}
        handler.mock_context.get_pending_messages.return_value = [
            {"message_text": "When did I last talk to Mike?", "message_sid": "SM_new_123",
             "received_at": datetime.now(timezone.utc)}
        ]
        handler.mod.handle_inbound_sms(form_data, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_context.clear_context.assert_called()
        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "Mike Torres" in sms_body

    def test_clarify_context_not_discarded_on_resolution(self, handler):
        """Bug 5: When pending_intent is 'clarify' and resolved intent is
        'log_interaction', the context should NOT be discarded as stale."""
        handler.mock_context.get_context.return_value = {
            "pending_intent": "clarify",
            "original_message": "Met with John for drinks",
            "candidates": ["John Smith", "John Doe"],
        }
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "John Doe", "match_type": "exact"}],
            notes="drinks",
            follow_up_date="2026-03-01",
            response_message="Updated John Doe.",
        )

        form_data = {**SAMPLE_FORM_DATA, "Body": "Doe", "MessageSid": "SM_resolve_clarify"}
        handler.mock_context.get_pending_messages.return_value = [
            {"message_text": "Doe", "message_sid": "SM_resolve_clarify",
             "received_at": datetime.now(timezone.utc)}
        ]
        handler.mod.handle_inbound_sms(form_data, REQUEST_URL, TWILIO_SIGNATURE)

        # Should process as log_interaction (not discard context)
        handler.mock_sheets.update_contact.assert_called_once()
        update_args = handler.mock_sheets.update_contact.call_args
        assert update_args[0][1] == "John Doe"

    def test_valid_context_used_for_resolution(self, handler):
        """If user responds to clarification, context is used."""
        handler.mock_context.get_context.return_value = {
            "pending_intent": "log_interaction",
            "original_message": "Met with John for drinks",
            "candidates": ["John Smith", "John Doe"],
        }
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "John Doe", "match_type": "exact"}],
            notes="drinks",
            follow_up_date="2026-03-01",
            response_message="Updated John Doe. Reminder on Mar 1.",
        )

        form_data = {**SAMPLE_FORM_DATA, "Body": "Doe", "MessageSid": "SM_resolve_123"}
        handler.mock_context.get_pending_messages.return_value = [
            {"message_text": "Doe", "message_sid": "SM_resolve_123",
             "received_at": datetime.now(timezone.utc)}
        ]
        handler.mod.handle_inbound_sms(form_data, REQUEST_URL, TWILIO_SIGNATURE)

        # Context should have been passed to NLP
        nlp_call_args = handler.mock_nlp.parse_sms.call_args
        # 3rd positional arg is pending_context
        passed_context = nlp_call_args[0][2]
        assert passed_context is not None

        # Should process as log_interaction
        handler.mock_sheets.update_contact.assert_called_once()
        handler.mock_context.clear_context.assert_called()


# ===================================================================
# Error handling
# ===================================================================

class TestErrorHandling:
    def test_exception_sends_something_went_wrong_sms(self, handler):
        """Exception during processing sends error SMS."""
        handler.mock_nlp.parse_sms.side_effect = Exception("Gemini exploded")

        result = handler.mod.handle_inbound_sms(SAMPLE_FORM_DATA, REQUEST_URL, TWILIO_SIGNATURE)

        handler.mock_send_sms.assert_called_once()
        sms_body = handler.mock_send_sms.call_args[0][1]
        assert "something went wrong" in sms_body.lower()
