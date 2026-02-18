"""Telegram webhook handler — inbound Telegram message orchestration."""

import logging
import time
from datetime import datetime, timezone

import pytz

import context
import nlp
import sheets_client
from config import BATCH_WINDOW_SECONDS, TELEGRAM_SECRET_TOKEN
from contact_actions import (
    execute_archive,
    execute_log_interaction,
    execute_onboarding,
    execute_set_reminder,
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
        # Step 5: Store pending message and sleep for batch window
        # ---------------------------------------------------------------
        context.store_pending_message(chat_id, text, update_id)
        time.sleep(BATCH_WINDOW_SECONDS)

        # ---------------------------------------------------------------
        # Step 6: Check for newer messages — defer if not the last
        # ---------------------------------------------------------------
        pending = context.get_pending_messages(chat_id)
        my_received_at = None
        for msg in pending:
            if msg.get("message_sid") == update_id:
                my_received_at = msg.get("received_at")
                break

        if my_received_at and context.has_newer_message(chat_id, my_received_at):
            logger.info("Newer message exists for %s, deferring", chat_id)
            return ""

        # ---------------------------------------------------------------
        # Step 7: Combine batched messages
        # ---------------------------------------------------------------
        combined_text = " ".join(msg["message_text"] for msg in pending)

        # ---------------------------------------------------------------
        # Step 8: Retrieve multi-turn context
        # ---------------------------------------------------------------
        pending_context = context.get_context(chat_id)

        # ---------------------------------------------------------------
        # Step 9: Read contacts + settings from Sheets
        # ---------------------------------------------------------------
        contacts = sheets_client.get_active_contacts(sheet_id)
        settings = sheets_client.get_settings(sheet_id)
        contact_names = [c["name"] for c in contacts]

        tz_name = settings.get("timezone", "America/New_York")
        try:
            user_tz = pytz.timezone(tz_name)
            now_local = datetime.now(user_tz)
        except Exception:
            now_local = datetime.now(timezone.utc)
        current_date_str = now_local.strftime("%A, %B %d, %Y")

        # ---------------------------------------------------------------
        # Step 10: Call Gemini NLP
        # ---------------------------------------------------------------
        nlp_result = nlp.parse_sms(combined_text, contact_names, pending_context, current_date_str, contacts)

        intent = nlp_result.get("intent", "unknown")
        nlp_contacts = nlp_result.get("contacts", [])
        notes = nlp_result.get("notes")
        interaction_date = nlp_result.get("interaction_date")
        follow_up_date = nlp_result.get("follow_up_date")
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
            execute_log_interaction(
                sheet_id, nlp_contacts, notes, follow_up_date,
                today_str, default_reminder_days, combined_text,
                interaction_date, contacts,
            )

        elif intent == "query":
            pass

        elif intent == "set_reminder":
            execute_set_reminder(
                sheet_id, nlp_contacts, notes, follow_up_date,
                today_str, default_reminder_days, combined_text,
            )

        elif intent == "archive":
            if needs_clarification:
                candidates = [c["name"] for c in nlp_contacts]
                context.store_context(chat_id, {
                    "pending_intent": "archive",
                    "original_message": combined_text,
                    "candidates": candidates,
                })
            else:
                execute_archive(sheet_id, nlp_contacts)
                context.clear_context(chat_id)

        elif intent == "clarify":
            candidates = [c["name"] for c in nlp_contacts]
            context.store_context(chat_id, {
                "pending_intent": "clarify",
                "original_message": combined_text,
                "candidates": candidates,
            })

        elif intent == "onboarding":
            if needs_clarification:
                candidates = [c["name"] for c in nlp_contacts]
                context.store_context(chat_id, {
                    "pending_intent": "onboarding",
                    "original_message": combined_text,
                    "candidates": candidates,
                })
            else:
                execute_onboarding(
                    sheet_id, nlp_contacts, notes, follow_up_date,
                    today_str, default_reminder_days, combined_text,
                    interaction_date,
                )
                context.clear_context(chat_id)

        # ---------------------------------------------------------------
        # Step 13: Clear pending messages + resolved context
        # ---------------------------------------------------------------
        context.clear_pending_messages(chat_id)
        if pending_context and intent not in ("clarify", "archive", "onboarding"):
            context.clear_context(chat_id)

        # ---------------------------------------------------------------
        # Step 14: Send reply
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
