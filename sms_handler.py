"""SMS webhook handler — full inbound SMS orchestration."""

import logging
import time
from datetime import datetime, timedelta, timezone

from twilio.request_validator import RequestValidator

import context
import nlp
import sheets_client
from config import BATCH_WINDOW_SECONDS, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, send_sms

logger = logging.getLogger(__name__)


def handle_inbound_sms(form_data: dict, request_url: str, twilio_signature: str) -> str:
    """Process an inbound SMS from Twilio.

    Args:
        form_data: The POST form data from Twilio (Body, From, MessageSid, etc.).
        request_url: The full URL that Twilio posted to.
        twilio_signature: The X-Twilio-Signature header value.

    Returns:
        A string response body (empty string for 200 OK).
    """
    # ---------------------------------------------------------------
    # Step 1: Validate Twilio signature
    # ---------------------------------------------------------------
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    # Handle X-Forwarded-Proto: if behind a proxy, reconstruct URL with https
    if not validator.validate(request_url, form_data, twilio_signature):
        logger.warning("Invalid Twilio signature")
        return "Invalid signature"

    message_sid = form_data.get("MessageSid", "")
    from_phone = form_data.get("From", "")
    body = form_data.get("Body", "")

    try:
        # ---------------------------------------------------------------
        # Step 2: Idempotency check
        # ---------------------------------------------------------------
        if context.is_message_processed(message_sid):
            logger.info("Duplicate message %s, skipping", message_sid)
            return ""

        # Mark as processed immediately
        context.mark_message_processed(message_sid)

        # ---------------------------------------------------------------
        # Step 3: User lookup by phone number
        # ---------------------------------------------------------------
        user = sheets_client.get_user_by_phone(from_phone)
        if user is None:
            send_sms(from_phone, "This phone number is not registered with Rolodex.")
            return ""

        sheet_id = user["sheet_id"]

        # ---------------------------------------------------------------
        # Step 4: Store pending message and sleep for batch window
        # ---------------------------------------------------------------
        context.store_pending_message(from_phone, body, message_sid)
        time.sleep(BATCH_WINDOW_SECONDS)

        # ---------------------------------------------------------------
        # Step 5: Check for newer messages — defer if not the last
        # ---------------------------------------------------------------
        pending = context.get_pending_messages(from_phone)
        # Find the received_at for our message
        my_received_at = None
        for msg in pending:
            if msg.get("message_sid") == message_sid:
                my_received_at = msg.get("received_at")
                break

        if my_received_at and context.has_newer_message(from_phone, my_received_at):
            logger.info("Newer message exists for %s, deferring", from_phone)
            return ""

        # ---------------------------------------------------------------
        # Step 6: Combine batched messages
        # ---------------------------------------------------------------
        combined_text = " ".join(msg["message_text"] for msg in pending)

        # ---------------------------------------------------------------
        # Step 7: Retrieve multi-turn context
        # ---------------------------------------------------------------
        pending_context = context.get_context(from_phone)

        # ---------------------------------------------------------------
        # Step 8: Read contacts + settings from Sheets
        # ---------------------------------------------------------------
        contacts = sheets_client.get_active_contacts(sheet_id)
        settings = sheets_client.get_settings(sheet_id)
        contact_names = [c["name"] for c in contacts]

        # Compute current date string in user timezone
        tz_name = settings.get("timezone", "America/New_York")
        try:
            import pytz
            user_tz = pytz.timezone(tz_name)
            now_local = datetime.now(user_tz)
        except Exception:
            now_local = datetime.now(timezone.utc)
        current_date_str = now_local.strftime("%A, %B %d, %Y")

        # ---------------------------------------------------------------
        # Step 9: Call Gemini NLP
        # ---------------------------------------------------------------
        nlp_result = nlp.parse_sms(combined_text, contact_names, pending_context, current_date_str)

        intent = nlp_result.get("intent", "unknown")
        nlp_contacts = nlp_result.get("contacts", [])
        notes = nlp_result.get("notes")
        follow_up_date = nlp_result.get("follow_up_date")
        needs_clarification = nlp_result.get("needs_clarification", False)
        clarification_question = nlp_result.get("clarification_question")
        response_message = nlp_result.get("response_message", "")

        # ---------------------------------------------------------------
        # Step 10: Handle multi-turn resolution
        # ---------------------------------------------------------------
        if pending_context:
            pending_intent = pending_context.get("pending_intent")
            # If NLP detects a new intent (different from the pending one, and
            # not the resolved version of it), discard stale context
            if intent != pending_intent and intent not in ("clarify",):
                # New intent detected — discard stale context
                context.clear_context(from_phone)
                pending_context = None
            else:
                # Context was used for resolution — will be cleared after execution
                pass

        # ---------------------------------------------------------------
        # Step 11: Execute intent
        # ---------------------------------------------------------------
        today_str = now_local.strftime("%Y-%m-%d")
        default_reminder_days = int(settings.get("default_reminder_days", 14))

        if intent == "log_interaction":
            _execute_log_interaction(
                sheet_id, nlp_contacts, notes, follow_up_date,
                today_str, default_reminder_days, combined_text,
            )

        elif intent == "query":
            # No sheet updates — just send response
            pass

        elif intent == "set_reminder":
            _execute_set_reminder(sheet_id, nlp_contacts, follow_up_date)

        elif intent == "archive":
            if needs_clarification:
                # First call: store context asking confirmation
                candidates = [c["name"] for c in nlp_contacts]
                context.store_context(from_phone, {
                    "pending_intent": "archive",
                    "original_message": combined_text,
                    "candidates": candidates,
                })
            else:
                # Confirmation received: execute archive
                _execute_archive(sheet_id, nlp_contacts)
                context.clear_context(from_phone)

        elif intent == "clarify":
            # Store context with clarification info
            candidates = [c["name"] for c in nlp_contacts]
            context.store_context(from_phone, {
                "pending_intent": "clarify",
                "original_message": combined_text,
                "candidates": candidates,
            })

        elif intent == "onboarding":
            if needs_clarification:
                # First call: store context asking confirmation for new contact
                candidates = [c["name"] for c in nlp_contacts]
                context.store_context(from_phone, {
                    "pending_intent": "onboarding",
                    "original_message": combined_text,
                    "candidates": candidates,
                })
            else:
                # Confirmation: add new contact
                _execute_onboarding(
                    sheet_id, nlp_contacts, notes, follow_up_date,
                    today_str, default_reminder_days, combined_text,
                )
                context.clear_context(from_phone)

        elif intent == "unknown":
            # Just send response_message as-is
            pass

        # ---------------------------------------------------------------
        # Step 12: Clear pending messages + resolved context
        # ---------------------------------------------------------------
        context.clear_pending_messages(from_phone)
        # Clear context if it was used for resolution (not if we just stored new context)
        if pending_context and intent not in ("clarify", "archive", "onboarding"):
            context.clear_context(from_phone)

        # ---------------------------------------------------------------
        # Step 13: Send reply SMS
        # ---------------------------------------------------------------
        reply = clarification_question if needs_clarification else response_message
        if reply:
            send_sms(from_phone, reply)

        return ""

    except Exception:
        logger.exception("Error processing SMS from %s", from_phone)
        try:
            send_sms(from_phone, "Something went wrong. Please try again.")
        except Exception:
            logger.exception("Failed to send error SMS to %s", from_phone)
        return ""


# ---------------------------------------------------------------------------
# Intent executors
# ---------------------------------------------------------------------------

def _execute_log_interaction(sheet_id, nlp_contacts, notes, follow_up_date,
                             today_str, default_reminder_days, raw_message):
    """Update contact, add log entry, set reminder for log_interaction intent."""
    for contact in nlp_contacts:
        contact_name = contact["name"]
        reminder_date = follow_up_date
        if not reminder_date:
            # Compute from default_reminder_days
            today = datetime.strptime(today_str, "%Y-%m-%d")
            reminder_date = (today + timedelta(days=default_reminder_days)).strftime("%Y-%m-%d")

        sheets_client.update_contact(sheet_id, contact_name, {
            "last_contact_date": today_str,
            "last_contact_notes": notes or "",
            "reminder_date": reminder_date,
        })

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": contact_name,
            "intent": "log_interaction",
            "notes": notes or "",
            "raw_message": raw_message,
        })


def _execute_set_reminder(sheet_id, nlp_contacts, follow_up_date):
    """Update reminder_date on contact(s)."""
    for contact in nlp_contacts:
        contact_name = contact["name"]
        sheets_client.update_contact(sheet_id, contact_name, {
            "reminder_date": follow_up_date,
        })


def _execute_archive(sheet_id, nlp_contacts):
    """Archive contact(s)."""
    for contact in nlp_contacts:
        sheets_client.archive_contact(sheet_id, contact["name"])


def _execute_onboarding(sheet_id, nlp_contacts, notes, follow_up_date,
                        today_str, default_reminder_days, raw_message):
    """Add a new contact and log the interaction."""
    for contact in nlp_contacts:
        contact_name = contact["name"]
        reminder_date = follow_up_date
        if not reminder_date:
            today = datetime.strptime(today_str, "%Y-%m-%d")
            reminder_date = (today + timedelta(days=default_reminder_days)).strftime("%Y-%m-%d")

        sheets_client.add_contact(sheet_id, {
            "name": contact_name,
            "status": "active",
            "last_contact_date": today_str,
            "last_contact_notes": notes or "",
            "reminder_date": reminder_date,
        })

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": contact_name,
            "intent": "log_interaction",
            "notes": notes or "",
            "raw_message": raw_message,
        })
