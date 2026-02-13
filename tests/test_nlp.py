"""Tests for nlp.py — Gemini NLP integration."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    SAMPLE_CONTACTS,
    SAMPLE_GEMINI_RESPONSE_CLARIFY,
    SAMPLE_GEMINI_RESPONSE_LOG,
    SAMPLE_GEMINI_RESPONSE_QUERY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONTACT_NAMES = [c["name"] for c in SAMPLE_CONTACTS]
CURRENT_DATE = "Friday, February 13, 2026"
SAMPLE_CONTEXT = {
    "original_message": "Met with John for drinks",
    "pending_intent": "log_interaction",
    "candidates": ["John Smith", "John Doe"],
}


def _mock_genai_response(text):
    """Return a mock Gemini response object with .text attribute."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    """Verify that parse_sms builds a prompt containing the right information."""

    @patch("nlp.genai_client")
    def test_prompt_includes_contact_names(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)
        )

        from nlp import parse_sms

        parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)

        call_args = mock_client.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args[1].get("contents") or call_args[0][1]
        prompt_str = str(prompt)

        for name in CONTACT_NAMES:
            assert name in prompt_str, f"Contact name '{name}' missing from prompt"

    @patch("nlp.genai_client")
    def test_prompt_includes_date(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)
        )

        from nlp import parse_sms

        parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)

        call_args = mock_client.models.generate_content.call_args
        prompt_str = str(call_args)
        assert CURRENT_DATE in prompt_str

    @patch("nlp.genai_client")
    def test_prompt_includes_context(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)
        )

        from nlp import parse_sms

        parse_sms("Doe", CONTACT_NAMES, SAMPLE_CONTEXT, CURRENT_DATE)

        call_args = mock_client.models.generate_content.call_args
        prompt_str = str(call_args)
        assert "Met with John for drinks" in prompt_str

    @patch("nlp.genai_client")
    def test_prompt_includes_sms_text(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)
        )

        from nlp import parse_sms

        parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)

        call_args = mock_client.models.generate_content.call_args
        prompt_str = str(call_args)
        assert "Had coffee with Sarah" in prompt_str


# ---------------------------------------------------------------------------
# Response parsing — happy path
# ---------------------------------------------------------------------------


class TestResponseParsingHappyPath:
    """Verify well-formed JSON responses are parsed correctly."""

    @patch("nlp.genai_client")
    def test_log_interaction_parsed(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)
        )

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)

        assert result["intent"] == "log_interaction"
        assert result["contacts"][0]["name"] == "Sarah Chen"
        assert result["notes"] is not None
        assert result["follow_up_date"] == "2026-02-24"
        assert result["needs_clarification"] is False
        assert result["response_message"] is not None

    @patch("nlp.genai_client")
    def test_query_parsed(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_QUERY)
        )

        from nlp import parse_sms

        result = parse_sms("When did I last talk to Mike?", CONTACT_NAMES, None, CURRENT_DATE)

        assert result["intent"] == "query"
        assert result["contacts"][0]["name"] == "Mike Torres"

    @patch("nlp.genai_client")
    def test_clarify_parsed(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_CLARIFY)
        )

        from nlp import parse_sms

        result = parse_sms("Met with John", CONTACT_NAMES, None, CURRENT_DATE)

        assert result["intent"] == "clarify"
        assert result["needs_clarification"] is True
        assert len(result["contacts"]) == 2
        assert result["clarification_question"] is not None

    @patch("nlp.genai_client")
    def test_set_reminder_parsed(self, mock_client, env_vars):
        response = {
            "intent": "set_reminder",
            "contacts": [{"name": "Dad", "match_type": "exact"}],
            "notes": "birthday",
            "follow_up_date": "2026-03-05",
            "needs_clarification": False,
            "clarification_question": None,
            "response_message": "Reminder set for Dad on March 5.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("Remind me about Dad on March 5", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "set_reminder"
        assert result["follow_up_date"] == "2026-03-05"

    @patch("nlp.genai_client")
    def test_archive_parsed(self, mock_client, env_vars):
        response = {
            "intent": "archive",
            "contacts": [{"name": "Sarah Chen", "match_type": "exact"}],
            "notes": None,
            "follow_up_date": None,
            "needs_clarification": True,
            "clarification_question": "Are you sure you want to archive Sarah Chen?",
            "response_message": "Are you sure you want to archive Sarah Chen? Reply YES to confirm.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("Remove Sarah from my rolodex", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "archive"

    @patch("nlp.genai_client")
    def test_unknown_intent_parsed(self, mock_client, env_vars):
        response = {
            "intent": "unknown",
            "contacts": [],
            "notes": None,
            "follow_up_date": None,
            "needs_clarification": False,
            "clarification_question": None,
            "response_message": "I'm not sure what you mean. Try something like 'Had coffee with Sarah'.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("What's the weather?", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "unknown"
        assert result["response_message"] is not None

    @patch("nlp.genai_client")
    def test_multi_contact_response(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(SAMPLE_GEMINI_RESPONSE_CLARIFY)
        )

        from nlp import parse_sms

        result = parse_sms("Met with John", CONTACT_NAMES, None, CURRENT_DATE)
        assert len(result["contacts"]) == 2
        names = [c["name"] for c in result["contacts"]]
        assert "John Smith" in names
        assert "John Doe" in names


# ---------------------------------------------------------------------------
# Fallback parsing
# ---------------------------------------------------------------------------


class TestFallbackParsing:
    """Verify JSON extraction from imperfect Gemini responses."""

    @patch("nlp.genai_client")
    def test_json_in_markdown_backticks(self, mock_client, env_vars):
        wrapped = f"```json\n{json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)}\n```"
        mock_client.models.generate_content.return_value = _mock_genai_response(wrapped)

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "log_interaction"

    @patch("nlp.genai_client")
    def test_json_with_leading_text(self, mock_client, env_vars):
        text = f"Here is the result:\n{json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)}"
        mock_client.models.generate_content.return_value = _mock_genai_response(text)

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "log_interaction"

    @patch("nlp.genai_client")
    def test_json_with_trailing_text(self, mock_client, env_vars):
        text = f"{json.dumps(SAMPLE_GEMINI_RESPONSE_LOG)}\n\nLet me know if you need anything else!"
        mock_client.models.generate_content.return_value = _mock_genai_response(text)

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "log_interaction"

    @patch("nlp.genai_client")
    def test_missing_optional_fields_default_to_none(self, mock_client, env_vars):
        minimal = {
            "intent": "log_interaction",
            "contacts": [{"name": "Sarah Chen", "match_type": "exact"}],
            "response_message": "Updated Sarah Chen.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(minimal)
        )

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "log_interaction"
        assert result["notes"] is None
        assert result["follow_up_date"] is None
        assert result["needs_clarification"] is False or result["needs_clarification"] is None
        assert result["clarification_question"] is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify graceful behavior on API or parsing failures."""

    @patch("nlp.genai_client")
    def test_api_exception_raises(self, mock_client, env_vars):
        mock_client.models.generate_content.side_effect = Exception("API quota exceeded")

        from nlp import parse_sms

        with pytest.raises(Exception, match="API quota exceeded"):
            parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)

    @patch("nlp.genai_client")
    def test_empty_response_body(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response("")

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        # Should return a dict with intent=unknown or raise — either is acceptable
        assert result["intent"] == "unknown"
        assert result["response_message"] is not None

    @patch("nlp.genai_client")
    def test_non_json_response(self, mock_client, env_vars):
        mock_client.models.generate_content.return_value = _mock_genai_response(
            "I'm sorry, I can't help with that."
        )

        from nlp import parse_sms

        result = parse_sms("Had coffee with Sarah", CONTACT_NAMES, None, CURRENT_DATE)
        assert result["intent"] == "unknown"
        assert result["response_message"] is not None


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


class TestFieldValidation:
    """Verify handling of unexpected or malformed field values."""

    @patch("nlp.genai_client")
    def test_unknown_intent_value_handled(self, mock_client, env_vars):
        response = {
            "intent": "completely_new_intent",
            "contacts": [],
            "notes": None,
            "follow_up_date": None,
            "needs_clarification": False,
            "clarification_question": None,
            "response_message": "Something unexpected.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("Do something weird", CONTACT_NAMES, None, CURRENT_DATE)
        # Should map unknown intents to "unknown"
        assert result["intent"] == "unknown"

    @patch("nlp.genai_client")
    def test_null_contact_name_handled(self, mock_client, env_vars):
        response = {
            "intent": "log_interaction",
            "contacts": [{"name": None, "match_type": "new"}],
            "notes": "had coffee",
            "follow_up_date": None,
            "needs_clarification": False,
            "clarification_question": None,
            "response_message": "Updated.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("Had coffee", CONTACT_NAMES, None, CURRENT_DATE)
        # Should not crash; contacts list should be present
        assert isinstance(result["contacts"], list)

    @patch("nlp.genai_client")
    def test_malformed_date_string_handled(self, mock_client, env_vars):
        response = {
            "intent": "set_reminder",
            "contacts": [{"name": "Dad", "match_type": "exact"}],
            "notes": "birthday",
            "follow_up_date": "not-a-real-date",
            "needs_clarification": False,
            "clarification_question": None,
            "response_message": "Reminder set for Dad.",
        }
        mock_client.models.generate_content.return_value = _mock_genai_response(
            json.dumps(response)
        )

        from nlp import parse_sms

        result = parse_sms("Remind me about Dad", CONTACT_NAMES, None, CURRENT_DATE)
        # Should not crash; date is passed through as-is or set to None
        assert result["intent"] == "set_reminder"
        assert result["follow_up_date"] in ("not-a-real-date", None)
