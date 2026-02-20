"""Telegram webhook handler — inbound Telegram message orchestration."""

import json
import logging
from datetime import datetime, timezone

import pytz

import context
import nlp
import sheets_client
from config import TELEGRAM_SECRET_TOKEN
from contact_actions import (
    execute_archive,
    execute_log_interaction,
    execute_set_reminder,
    execute_update_contact,
)
from messaging import send_message

logger = logging.getLogger(__name__)


def handle_inbound_telegram(json_data: dict, secret_token_header: str | None) -> str:
    """Process an inbound Telegram update.

    Args:
        json_data: The parsed JSON body from Telegram.
        secret_token_header: The X-Telegram-Bot-Api-Secret-Token header value.

    Returns:
        A string response body (empty string for 200 OK).
    """
    # ---------------------------------------------------------------
    # Step 1: Validate secret token
    # ---------------------------------------------------------------
    if TELEGRAM_SECRET_TOKEN and secret_token_header != TELEGRAM_SECRET_TOKEN:
        logger.warning("Invalid Telegram secret token")
        return "Forbidden"

    # ---------------------------------------------------------------
    # Step 2: Extract message fields — ignore non-message updates
    # ---------------------------------------------------------------
    message = json_data.get("message")
    if not message:
        return ""  # channel posts, edited messages, etc.

    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()
    update_id = str(json_data.get("update_id", ""))

    if not text:
        return ""  # photos, stickers, voice notes, etc.

    try:
        # ---------------------------------------------------------------
        # Step 3: Idempotency check
        # ---------------------------------------------------------------
        if context.is_message_processed(update_id):
            logger.info("Duplicate update %s, skipping", update_id)
            return ""

        context.mark_message_processed(update_id)

        # ---------------------------------------------------------------
        # Step 4: User lookup by Telegram chat ID
        # ---------------------------------------------------------------
        user = sheets_client.get_user_by_telegram_chat_id(chat_id)
        if user is None:
            send_message(
                {"telegram_chat_id": chat_id},
                f"This Telegram account is not registered with Rolodex. "
                f"Your chat ID is: {chat_id}",
            )
            return ""

        sheet_id = user["sheet_id"]

        # ---------------------------------------------------------------
        # Step 4b: Handle Telegram bot commands (e.g. /start, /help)
        # ---------------------------------------------------------------
        if text.startswith("/"):
            send_message(user, (
                "Hi! Send me a message like 'Had coffee with Sarah today' "
                "to log an interaction, or 'When did I last talk to John?' "
                "to query a contact."
            ))
            return ""

        # ---------------------------------------------------------------
        # Step 5: Retrieve multi-turn context
        # ---------------------------------------------------------------
        pending_context = context.get_context(chat_id)

        # ---------------------------------------------------------------
        # Step 6: Read contacts + settings + recent logs from Sheets
        # ---------------------------------------------------------------
        contacts = sheets_client.get_active_contacts(sheet_id)
        settings = sheets_client.get_settings(sheet_id)
        recent_logs = sheets_client.get_recent_logs(sheet_id, limit=5)
        contact_names = [c["name"] for c in contacts]

        tz_name = settings.get("timezone", "America/New_York")
        try:
            user_tz = pytz.timezone(tz_name)
            now_local = datetime.now(user_tz)
        except Exception:
            now_local = datetime.now(timezone.utc)
        current_date_str = now_local.strftime("%A, %B %d, %Y")

        # ---------------------------------------------------------------
        # Step 7: Call Gemini NLP
        # ---------------------------------------------------------------
        nlp_result = nlp.parse_sms(text, contact_names, pending_context, current_date_str, contacts, recent_logs)
        logger.info("NLP reasoning: %s", json.dumps(nlp_result.get("reasoning", {}), indent=2))

        intent = nlp_result.get("intent", "unknown")
        nlp_contacts = nlp_result.get("contacts", [])
        interaction_date = nlp_result.get("interaction_date")
        follow_up_date = nlp_result.get("follow_up_date")
        new_name = nlp_result.get("new_name")
        needs_clarification = nlp_result.get("needs_clarification", False)
        clarification_question = nlp_result.get("clarification_question")
        response_message = nlp_result.get("response_message", "")

        # ---------------------------------------------------------------
        # Step 11: Handle multi-turn resolution
        # ---------------------------------------------------------------
        if pending_context:
            pending_intent = pending_context.get("pending_intent")
            if intent != pending_intent and intent not in ("clarify",):
                context.clear_context(chat_id)
                pending_context = None

        # ---------------------------------------------------------------
        # Step 12: Execute intent
        # ---------------------------------------------------------------
        today_str = now_local.strftime("%Y-%m-%d")
        default_reminder_days = int(settings.get("default_reminder_days", 14))

        if intent == "log_interaction":
            if nlp_contacts:
                execute_log_interaction(
                    sheet_id, nlp_contacts, follow_up_date,
                    today_str, default_reminder_days, text,
                    interaction_date, contacts,
                )
            else:
                needs_clarification = True
                clarification_question = response_message or "Who did you mean? I didn't catch a contact name."

        elif intent == "query":
            for contact in nlp_contacts:
                sheets_client.add_log_entry(sheet_id, {
                    "date": today_str,
                    "contact_name": contact["name"],
                    "intent": "query",
                    "raw_message": text,
                })

        elif intent == "set_reminder":
            if nlp_contacts:
                execute_set_reminder(
                    sheet_id, nlp_contacts, follow_up_date,
                    today_str, default_reminder_days, text,
                )
            else:
                needs_clarification = True
                clarification_question = response_message or "Who would you like to set a reminder for?"

        elif intent == "update_contact":
            if nlp_contacts:
                execute_update_contact(
                    sheet_id, nlp_contacts, new_name, text, today_str,
                )
            else:
                needs_clarification = True
                clarification_question = response_message or "Who would you like to rename?"

        elif intent == "archive":
            if needs_clarification:
                candidates = [c["name"] for c in nlp_contacts]
                context.store_context(chat_id, {
                    "pending_intent": "archive",
                    "original_message": text,
                    "candidates": candidates,
                })
            else:
                execute_archive(sheet_id, nlp_contacts)
                context.clear_context(chat_id)

        elif intent == "clarify":
            candidates = [c["name"] for c in nlp_contacts]
            context.store_context(chat_id, {
                "pending_intent": "clarify",
                "original_message": text,
                "candidates": candidates,
            })

        # ---------------------------------------------------------------
        # Step 10: Clear resolved context
        # ---------------------------------------------------------------
        if pending_context and intent not in ("clarify", "archive"):
            context.clear_context(chat_id)

        # ---------------------------------------------------------------
        # Step 11: Send reply
        # ---------------------------------------------------------------
        reply = clarification_question if needs_clarification else response_message
        if reply:
            send_message(user, reply)

        return ""

    except Exception:
        logger.exception("Error processing Telegram message from chat_id %s", chat_id)
        try:
            send_message({"telegram_chat_id": chat_id}, "Something went wrong. Please try again.")
        except Exception:
            logger.exception("Failed to send error message to chat_id %s", chat_id)
        return ""
