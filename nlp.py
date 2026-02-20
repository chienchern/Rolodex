"""Gemini NLP integration — parse SMS text into structured intent."""

import json
import logging
import re

from google import genai

logger = logging.getLogger(__name__)
from google.genai import types

from config import GEMINI_API_KEY
from prompts import SYSTEM_PROMPT

# --- Gemini client (module-level for mocking) ---

genai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- Valid intents (matches new prompt schema; "clarify" derived internally) ---

VALID_INTENTS = {
    "log_interaction",
    "query",
    "set_reminder",
    "update_contact",
    "archive",
    "onboarding",
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

    context_section = json.dumps(pending_context) if pending_context else "null"

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
    """Normalize and validate the parsed result from the new nested schema."""
    # Read intent from nested schema; fall back gracefully for old-format responses
    intent_obj = parsed.get("intent") if isinstance(parsed.get("intent"), dict) else {}
    intent = intent_obj.get("value", "unknown")
    if intent not in VALID_INTENTS:
        intent = "unknown"

    contact_obj = parsed.get("contact") if isinstance(parsed.get("contact"), dict) else {}
    match_type = contact_obj.get("match_type", "none")
    contact_name = contact_obj.get("name")

    # Build contacts list from name (null, string, or array of canonical names)
    if contact_name is None:
        contacts = []
    elif isinstance(contact_name, list):
        contacts = [{"name": n, "match_type": match_type} for n in contact_name if n]
    else:
        contacts = [{"name": contact_name, "match_type": match_type}]

    # Override intent to "clarify" for ambiguous contacts (handler compat)
    if match_type == "ambiguous":
        intent = "clarify"

    # Derive needs_clarification from match_type and intent
    needs_clarification = (
        match_type in ("none", "ambiguous")
        or intent in ("archive", "onboarding")
    )

    fields_obj = parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {}
    response_obj = parsed.get("response") if isinstance(parsed.get("response"), dict) else {}
    response_message = response_obj.get("message")

    # Collect per-step reasoning for logging
    context_obj = parsed.get("context") if isinstance(parsed.get("context"), dict) else {}
    reasoning = {
        "context": context_obj.get("reasoning"),
        "intent": intent_obj.get("reasoning"),
        "contact": contact_obj.get("reasoning"),
        "fields": fields_obj.get("reasoning"),
        "response": response_obj.get("reasoning"),
    }

    # Common fields for all intents
    result = {
        "intent": intent,
        "contacts": contacts,
        "response_message": response_message,
        "reasoning": reasoning,
    }

    # Intent-specific fields
    if intent == "log_interaction":
        result["interaction_date"] = fields_obj.get("interaction_date")
        result["follow_up_date"] = fields_obj.get("follow_up_date")
    elif intent == "set_reminder":
        result["follow_up_date"] = fields_obj.get("follow_up_date")
    elif intent == "update_contact":
        result["new_name"] = fields_obj.get("new_name")
    elif intent in ("archive", "clarify"):
        result["needs_clarification"] = needs_clarification
        result["clarification_question"] = response_message
    elif intent == "onboarding":
        result["interaction_date"] = fields_obj.get("interaction_date")
        result["follow_up_date"] = fields_obj.get("follow_up_date")
        result["needs_clarification"] = needs_clarification
        result["clarification_question"] = response_message
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

    # Call Gemini with structured JSON output at temperature=0 for determinism
    try:
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
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
