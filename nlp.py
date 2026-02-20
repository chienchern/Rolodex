"""Gemini NLP integration — parse SMS text into structured intent."""

import json
import logging
import re

from google import genai

logger = logging.getLogger(__name__)
from google.genai import types

from config import GEMINI_API_KEY
from prompts import CONTEXT_TEMPLATE, SYSTEM_PROMPT

# --- Gemini client (module-level for mocking) ---

genai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- Valid intents ---

VALID_INTENTS = {
    "log_interaction",
    "query",
    "set_reminder",
    "update_contact",
    "archive",
    "onboarding",
    "clarify",
    "unknown",
}


def _build_prompt(sms_text, contact_names, pending_context, current_date_str, contacts_data=None, recent_logs=None):
    """Build the Gemini prompt string."""
    if contacts_data:
        # Include full contact details so Gemini can answer queries
        lines = []
        for c in contacts_data:
            parts = [c.get("name", "")]
            if c.get("last_contact_date"):
                parts.append(f"last contact: {c['last_contact_date']}")
            if c.get("last_interaction_message"):
                parts.append(f"last message: {c['last_interaction_message']}")
            if c.get("reminder_date"):
                parts.append(f"reminder: {c['reminder_date']}")
            lines.append("- " + " | ".join(parts))
        contact_list = "\n".join(lines) if lines else "- (no contacts yet)"
    else:
        contact_list = "\n".join(f"- {name}" for name in contact_names) if contact_names else "- (no contacts yet)"

    if pending_context:
        context_section = CONTEXT_TEMPLATE.format(
            original_message=pending_context.get("original_message", ""),
            pending_intent=pending_context.get("pending_intent", ""),
            candidates=", ".join(pending_context.get("candidates", [])),
        )
    else:
        context_section = "No pending conversation context."

    if recent_logs:
        log_lines = []
        for log in recent_logs:
            contact = log.get("contact_name", "unknown")
            intent = log.get("intent", "unknown")
            raw = log.get("raw_message", "")
            log_lines.append(f'- "{raw}" ({intent}, contact: {contact})')
        recent_logs_section = "Recent messages (most recent first):\n" + "\n".join(log_lines)
    else:
        recent_logs_section = "No recent conversation history."

    return SYSTEM_PROMPT.format(
        current_date=current_date_str,
        contact_list=contact_list,
        context_section=context_section,
        recent_logs_section=recent_logs_section,
        sms_text=sms_text,
    )


def _extract_json(text):
    """Extract JSON from text, handling markdown backticks and surrounding text."""
    if not text or not text.strip():
        return None

    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code block
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Try finding JSON object in the text (first { to last })
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _make_fallback_response(message=None):
    """Return a fallback response dict when parsing fails."""
    return {
        "intent": "unknown",
        "contacts": [],
        "response_message": message or "I couldn't understand that. Try something like 'Had coffee with Sarah'.",
    }


def _normalize_result(parsed):
    """Normalize and validate the parsed result, including only intent-relevant fields."""
    intent = parsed.get("intent")
    if intent not in VALID_INTENTS:
        intent = "unknown"

    contacts = parsed.get("contacts", [])
    if not isinstance(contacts, list):
        contacts = []

    # Common fields for all intents
    result = {
        "intent": intent,
        "contacts": contacts,
        "response_message": parsed.get("response_message"),
    }

    # Intent-specific fields
    if intent == "log_interaction":
        result["interaction_date"] = parsed.get("interaction_date")
        result["follow_up_date"] = parsed.get("follow_up_date")
    elif intent == "set_reminder":
        result["follow_up_date"] = parsed.get("follow_up_date")
    elif intent == "update_contact":
        result["new_name"] = parsed.get("new_name")
    elif intent in ("archive", "clarify"):
        result["needs_clarification"] = parsed.get("needs_clarification", False)
        result["clarification_question"] = parsed.get("clarification_question")
    elif intent == "onboarding":
        result["interaction_date"] = parsed.get("interaction_date")
        result["follow_up_date"] = parsed.get("follow_up_date")
        result["needs_clarification"] = parsed.get("needs_clarification", False)
        result["clarification_question"] = parsed.get("clarification_question")
    # query and unknown: no extra fields

    return result


def parse_sms(sms_text, contact_names, pending_context, current_date_str, contacts_data=None, recent_logs=None):
    """Parse an SMS message using Gemini and return structured intent data.

    Args:
        sms_text: The user's SMS message text.
        contact_names: List of active contact name strings.
        pending_context: Dict with multi-turn context, or None.
        current_date_str: Current date string with day-of-week
                          (e.g., "Friday, February 13, 2026").
        contacts_data: Optional list of full contact dicts for query context.
        recent_logs: Optional list of recent log entry dicts for context.

    Returns:
        Dict with keys: intent, contacts, follow_up_date,
        needs_clarification, clarification_question, response_message.

    Raises:
        Exception: If the Gemini API call itself fails.
    """
    # Handle bot commands (e.g. /start, /help) without calling Gemini
    if sms_text.strip().startswith("/"):
        return _make_fallback_response(
            "Hi! Send me a message like 'Had coffee with Sarah today' "
            "to log an interaction, or 'When did I last talk to John?' "
            "to query a contact."
        )

    prompt = _build_prompt(sms_text, contact_names, pending_context, current_date_str, contacts_data, recent_logs)

    # Call Gemini with structured JSON output
    try:
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw_text = response.text
    except Exception:
        logger.exception("Gemini API call failed")
        return _make_fallback_response()

    # Parse the response
    parsed = _extract_json(raw_text)

    if parsed is None:
        return _make_fallback_response()

    return _normalize_result(parsed)
