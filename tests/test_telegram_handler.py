"""Tests for telegram_handler.py — Telegram webhook orchestration."""

import importlib
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import SAMPLE_CONTACTS, SAMPLE_SETTINGS, SAMPLE_USER

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHAT_ID = "123456789"
UPDATE_ID = 987654321
SECRET_TOKEN = "test_secret"

SAMPLE_UPDATE = {
    "update_id": UPDATE_ID,
    "message": {
        "message_id": 1234,
        "from": {"id": int(CHAT_ID), "first_name": "Test"},
        "chat": {"id": int(CHAT_ID), "type": "private"},
        "text": "Had coffee with Sarah",
    },
}


def _nlp_response(intent, contacts=None, notes=None, follow_up_date=None,
                  interaction_date=None,
                  needs_clarification=False, clarification_question=None,
                  response_message="Done.", **extra):
    """Build a mock NLP response with only intent-relevant fields."""
    result = {
        "intent": intent,
        "contacts": contacts or [],
        "response_message": response_message,
    }
    if intent == "log_interaction":
        result["notes"] = notes
        result["interaction_date"] = interaction_date
        result["follow_up_date"] = follow_up_date
    elif intent == "set_reminder":
        result["notes"] = notes
        result["follow_up_date"] = follow_up_date
    elif intent in ("archive", "clarify"):
        result["needs_clarification"] = needs_clarification
        result["clarification_question"] = clarification_question
    elif intent == "onboarding":
        result["notes"] = notes
        result["interaction_date"] = interaction_date
        result["follow_up_date"] = follow_up_date
        result["needs_clarification"] = needs_clarification
        result["clarification_question"] = clarification_question
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# Fixture: fully mocked telegram_handler
# ---------------------------------------------------------------------------

@pytest.fixture
def handler(env_vars):
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("telegram_handler", "config", "messaging", "contact_actions",
                        "nlp", "sheets_client", "context"):
            del sys.modules[mod_name]

    mock_genai_client = MagicMock()
    mock_gspread = MagicMock()

    with patch("google.genai.Client", return_value=mock_genai_client), \
         patch("google.cloud.firestore.Client", return_value=MagicMock()), \
         patch("gspread.service_account_from_dict", return_value=mock_gspread):

        import telegram_handler
        import contact_actions
        importlib.reload(telegram_handler)

        mock_sheets = MagicMock()
        mock_sheets.get_user_by_telegram_chat_id.return_value = SAMPLE_USER.copy()
        mock_sheets.get_active_contacts.return_value = [c.copy() for c in SAMPLE_CONTACTS]
        mock_sheets.get_settings.return_value = SAMPLE_SETTINGS.copy()

        with patch.object(telegram_handler, "context") as mock_context, \
             patch.object(telegram_handler, "sheets_client", mock_sheets), \
             patch.object(contact_actions, "sheets_client", mock_sheets), \
             patch.object(telegram_handler, "nlp") as mock_nlp, \
             patch.object(telegram_handler, "send_message") as mock_send_message, \
             patch.object(telegram_handler, "TELEGRAM_SECRET_TOKEN", SECRET_TOKEN), \
             patch.object(telegram_handler, "BATCH_WINDOW_SECONDS", 0), \
             patch.object(telegram_handler, "time") as mock_time:

            mock_context.is_message_processed.return_value = False
            mock_context.has_newer_message.return_value = False
            mock_context.get_context.return_value = None
            mock_context.get_pending_messages.return_value = [
                {"message_text": "Had coffee with Sarah",
                 "message_sid": str(UPDATE_ID),
                 "received_at": datetime.now(timezone.utc)}
            ]

            mock_nlp.parse_sms.return_value = _nlp_response(
                intent="log_interaction",
                contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
                notes="had coffee",
                follow_up_date="2026-03-01",
                response_message="Updated Sarah Chen.",
            )

            mock_time.sleep.return_value = None

            class Ns:
                pass
            ns = Ns()
            ns.mod = telegram_handler
            ns.mock_context = mock_context
            ns.mock_sheets = mock_sheets
            ns.mock_nlp = mock_nlp
            ns.mock_send_message = mock_send_message
            ns.mock_time = mock_time
            yield ns


# ---------------------------------------------------------------------------
# Secret token validation
# ---------------------------------------------------------------------------

class TestSecretTokenValidation:

    def test_invalid_token_returns_forbidden(self, handler):
        result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, "wrong_token")

        assert result == "Forbidden"
        handler.mock_context.is_message_processed.assert_not_called()

    def test_valid_token_proceeds(self, handler):
        result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        assert result == ""
        handler.mock_context.is_message_processed.assert_called()

    def test_no_token_check_when_secret_not_configured(self, handler):
        with patch.object(handler.mod, "TELEGRAM_SECRET_TOKEN", ""):
            result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, None)

        assert result == ""


# ---------------------------------------------------------------------------
# Update parsing
# ---------------------------------------------------------------------------

class TestUpdateParsing:

    def test_update_without_message_key_returns_empty(self, handler):
        update = {"update_id": UPDATE_ID, "channel_post": {"text": "something"}}

        result = handler.mod.handle_inbound_telegram(update, SECRET_TOKEN)

        assert result == ""
        handler.mock_context.is_message_processed.assert_not_called()

    def test_non_text_message_returns_empty(self, handler):
        update = {
            "update_id": UPDATE_ID,
            "message": {
                "message_id": 1,
                "chat": {"id": int(CHAT_ID)},
                # no "text" key — e.g. a photo
            },
        }

        result = handler.mod.handle_inbound_telegram(update, SECRET_TOKEN)

        assert result == ""
        handler.mock_nlp.parse_sms.assert_not_called()

    def test_empty_text_returns_empty(self, handler):
        update = {**SAMPLE_UPDATE, "message": {**SAMPLE_UPDATE["message"], "text": "   "}}

        result = handler.mod.handle_inbound_telegram(update, SECRET_TOKEN)

        assert result == ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:

    def test_duplicate_update_skips_processing(self, handler):
        handler.mock_context.is_message_processed.return_value = True

        result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        assert result == ""
        handler.mock_nlp.parse_sms.assert_not_called()
        handler.mock_sheets.update_contact.assert_not_called()

    def test_new_update_marks_as_processed(self, handler):
        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_context.mark_message_processed.assert_called_once_with(str(UPDATE_ID))


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------

class TestUserLookup:

    def test_unknown_chat_id_sends_not_registered_with_chat_id(self, handler):
        handler.mock_sheets.get_user_by_telegram_chat_id.return_value = None

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_send_message.assert_called_once()
        reply_text = handler.mock_send_message.call_args[0][1]
        assert CHAT_ID in reply_text

    def test_unknown_chat_id_does_not_process_nlp(self, handler):
        handler.mock_sheets.get_user_by_telegram_chat_id.return_value = None

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_nlp.parse_sms.assert_not_called()

    def test_looks_up_user_by_chat_id(self, handler):
        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_sheets.get_user_by_telegram_chat_id.assert_called_once_with(CHAT_ID)


# ---------------------------------------------------------------------------
# Batch window
# ---------------------------------------------------------------------------

class TestBatchWindow:

    def test_sleeps_for_batch_window(self, handler):
        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_time.sleep.assert_called_once_with(0)

    def test_defers_if_newer_message_exists(self, handler):
        handler.mock_context.has_newer_message.return_value = True

        result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        assert result == ""
        handler.mock_nlp.parse_sms.assert_not_called()


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------

class TestLogInteraction:

    def test_updates_contact_adds_log_sends_reply(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            notes="had coffee",
            follow_up_date="2026-03-01",
            response_message="Updated Sarah Chen.",
        )

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_sheets.update_contact.assert_called_once()
        update_args = handler.mock_sheets.update_contact.call_args[0]
        assert update_args[1] == "Sarah Chen"
        assert update_args[2]["reminder_date"] == "2026-03-01"

        handler.mock_sheets.add_log_entry.assert_called_once()
        handler.mock_send_message.assert_called_once()

    def test_reply_contains_response_message(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="log_interaction",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            response_message="Updated Sarah Chen.",
        )

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        reply_text = handler.mock_send_message.call_args[0][1]
        assert "Updated Sarah Chen." in reply_text


class TestQuery:

    def test_no_sheet_updates_sends_response(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="query",
            contacts=[{"name": "Sarah Chen", "match_type": "fuzzy"}],
            response_message="You last talked to Sarah on Jan 15.",
        )

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_sheets.update_contact.assert_not_called()
        handler.mock_sheets.add_log_entry.assert_not_called()
        handler.mock_send_message.assert_called_once()
        reply_text = handler.mock_send_message.call_args[0][1]
        assert "Sarah" in reply_text


class TestClarify:

    def test_stores_context_sends_clarification_question(self, handler):
        handler.mock_nlp.parse_sms.return_value = _nlp_response(
            intent="clarify",
            contacts=[
                {"name": "John Smith", "match_type": "ambiguous"},
                {"name": "John Doe", "match_type": "ambiguous"},
            ],
            needs_clarification=True,
            clarification_question="Which John? Smith or Doe?",
        )

        handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        handler.mock_context.store_context.assert_called_once()
        ctx = handler.mock_context.store_context.call_args[0][1]
        assert ctx["pending_intent"] == "clarify"
        handler.mock_send_message.assert_called_once()
        reply_text = handler.mock_send_message.call_args[0][1]
        assert "John" in reply_text


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_exception_sends_error_message(self, handler):
        handler.mock_nlp.parse_sms.side_effect = Exception("NLP exploded")

        result = handler.mod.handle_inbound_telegram(SAMPLE_UPDATE, SECRET_TOKEN)

        assert result == ""
        handler.mock_send_message.assert_called_once()
        reply_text = handler.mock_send_message.call_args[0][1]
        assert "wrong" in reply_text.lower()
