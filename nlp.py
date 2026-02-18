"""Gemini NLP integration — parse SMS text into structured intent."""

import json
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY

# --- Gemini client (module-level for mocking) ---

genai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- Valid intents ---

VALID_INTENTS = {
    "log_interaction",
    "query",
    "set_reminder",
    "archive",
    "onboarding",
    "clarify",
    "unknown",
}

# --- Prompt template ---

SYSTEM_PROMPT = """\
You are the NLP engine for Rolodex, an SMS-based personal CRM. Your job is to \
parse the user's SMS message and return structured JSON.

Current date: {current_date}

The user's active contacts are:
{contact_list}

{context_section}

The user sent this SMS:
\"\"\"{sms_text}\"\"\"

Classify the intent and return a JSON object with EXACTLY these fields:
- "intent": one of "log_interaction", "query", "set_reminder", "archive", "onboarding", "clarify", "unknown"
- "contacts": array of objects with "name" (string) and "match_type" ("exact", "fuzzy", "new", "ambiguous")
- "notes": string with relevant context (what was discussed, reason for reminder, etc.) or null
- "interaction_date": ISO date string (YYYY-MM-DD) for when the interaction occurred, or null. Only for "log_interaction" intent. If the user references a past date/day (e.g., "met John yesterday", "saw Sarah on Friday"), parse it relative to the current date. If no date is referenced, set to the current date.
- "follow_up_date": ISO date string (YYYY-MM-DD) for the next follow-up, or null. Only set when user explicitly mentions timing (e.g., "follow up in 3 weeks"). Always relative to today's date, not the interaction date. For "set_reminder" with no timing specified, set to null.
- "needs_clarification": boolean — true if the message is ambiguous
- "clarification_question": string question to ask the user, or null
- "response_message": string SMS reply to send to the user

Rules:
- Match contact names from the provided list. Use "fuzzy" match_type for partial/nickname matches.
- If a name matches multiple contacts, set intent to "clarify", needs_clarification to true, and list all candidates.
- If a contact name is not in the list, use match_type "new" and set intent to "onboarding" with needs_clarification true. Ask the user to confirm adding the new contact.
- For "log_interaction": always include notes about what was discussed. Parse interaction_date from the message. Compute follow_up_date only if the user explicitly mentions timing.
- For "query": return information from the contact list in response_message. No sheet updates needed. If last_contact_notes is empty, omit the "Discussed:" part.
- For "set_reminder": set follow_up_date to the requested date. If no timing specified, set follow_up_date to null.
- For "archive": set needs_clarification to true to confirm with the user.
- For "onboarding": used when a contact name is not found in the list. Ask for confirmation to add a new contact.
- If the message is just "YES" or "NO" with no pending context, set intent to "unknown" and respond with a helpful message.
- Include day-of-week in response_message dates (e.g., "Monday, Feb 24, 2026").
- Return ONLY the JSON object, no other text.

Multi-turn confirmation rules (OVERRIDE the rules above when pending context exists):
- If the pending intent is "onboarding" and the user CONFIRMS (e.g., yes, sure, yeah, go ahead, add them, do it, sounds good), set intent to "onboarding", needs_clarification to false, use the candidates from the pending context as contacts with match_type "new", and write a confirmation response_message. Preserve the notes from the original message.
- If the pending intent is "archive" and the user CONFIRMS, set intent to "archive", needs_clarification to false, use the candidates as contacts, and confirm archival.
- If the pending intent is "clarify" and the user specifies which candidate they meant, set intent to "log_interaction" (or whatever the original intent was), needs_clarification to false, with the chosen contact.
- If the user DECLINES (e.g., no, cancel, nevermind, nope), set intent to "unknown" with response_message "OK, cancelled."
- If the user sends a completely new message unrelated to the pending clarification, ignore the pending context and classify as a new intent.
"""

CONTEXT_TEMPLATE = """\
There is a pending conversation. The user previously sent:
\"\"\"{original_message}\"\"\"
The system asked for clarification. The pending intent was: {pending_intent}
Candidates: {candidates}
The current message may be a response to that clarification, or it may be a completely new intent. \
If it's a new intent, classify it as such and ignore the pending context."""


def _build_prompt(sms_text, contact_names, pending_context, current_date_str, contacts_data=None):
    """Build the Gemini prompt string."""
    if contacts_data:
        # Include full contact details so Gemini can answer queries
        lines = []
        for c in contacts_data:
            parts = [c.get("name", "")]
            if c.get("last_contact_date"):
                parts.append(f"last contact: {c['last_contact_date']}")
            if c.get("last_contact_notes"):
                parts.append(f"notes: {c['last_contact_notes']}")
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

    return SYSTEM_PROMPT.format(
        current_date=current_date_str,
        contact_list=contact_list,
        context_section=context_section,
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
        "notes": None,
        "interaction_date": None,
        "follow_up_date": None,
        "needs_clarification": False,
        "clarification_question": None,
        "response_message": message or "I couldn't understand that. Try something like 'Had coffee with Sarah'.",
    }


def _normalize_result(parsed):
    """Normalize and validate the parsed result, filling in defaults."""
    # Ensure all expected fields exist with defaults
    result = {
        "intent": parsed.get("intent"),
        "contacts": parsed.get("contacts", []),
        "notes": parsed.get("notes"),
        "interaction_date": parsed.get("interaction_date"),
        "follow_up_date": parsed.get("follow_up_date"),
        "needs_clarification": parsed.get("needs_clarification", False),
        "clarification_question": parsed.get("clarification_question"),
        "response_message": parsed.get("response_message"),
    }

    # Validate intent
    if result["intent"] not in VALID_INTENTS:
        result["intent"] = "unknown"

    # Ensure contacts is a list
    if not isinstance(result["contacts"], list):
        result["contacts"] = []

    return result


def parse_sms(sms_text, contact_names, pending_context, current_date_str, contacts_data=None):
    """Parse an SMS message using Gemini and return structured intent data.

    Args:
        sms_text: The user's SMS message text.
        contact_names: List of active contact name strings.
        pending_context: Dict with multi-turn context, or None.
        current_date_str: Current date string with day-of-week
                          (e.g., "Friday, February 13, 2026").
        contacts_data: Optional list of full contact dicts for query context.

    Returns:
        Dict with keys: intent, contacts, notes, follow_up_date,
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

    prompt = _build_prompt(sms_text, contact_names, pending_context, current_date_str, contacts_data)

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
