"""Daily reminder cron handler — checks contacts and sends reminder SMS."""

import logging
import os
from datetime import datetime, timedelta

import pytz
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

import sheets_client
from config import send_sms

logger = logging.getLogger(__name__)


def handle_reminder_cron(authorization_header: str | None) -> tuple[str, int]:
    """Process daily reminder cron.

    Args:
        authorization_header: The Authorization header value (e.g. "Bearer <token>").

    Returns:
        Tuple of (response_body, status_code).
    """
    # 1. Validate OIDC token (skip if env var set for local testing)
    skip_oidc = os.environ.get("SKIP_OIDC_VALIDATION")
    if not skip_oidc:
        if not authorization_header:
            return ("Unauthorized", 401)
        try:
            token = authorization_header.split("Bearer ")[-1]
            id_token.verify_oauth2_token(token, google_requests.Request())
        except (ValueError, Exception) as e:
            logger.warning("OIDC validation failed: %s", e)
            return ("Unauthorized", 401)

    # 2. Read all users from master sheet
    users = sheets_client.get_all_users()

    # 3. For each user, compute reminders
    for user in users:
        phone = user["phone"]
        sheet_id = user["sheet_id"]

        try:
            # 3a. Get user settings (timezone)
            settings = sheets_client.get_settings(sheet_id)
            tz_name = settings.get("timezone", "America/New_York")
            tz = pytz.timezone(tz_name)

            # 3b. Compute today in the user's timezone
            today = datetime.now(tz).date()

            # 3c. Get active contacts
            contacts = sheets_client.get_active_contacts(sheet_id)

            # 3d. Find contacts due for reminders
            reminders = []
            for contact in contacts:
                reminder_date_str = contact.get("reminder_date", "")
                if not reminder_date_str:
                    continue

                try:
                    reminder_date = datetime.strptime(reminder_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                # Day-of reminder
                if reminder_date == today:
                    notes = contact.get("last_contact_notes", "")
                    reminders.append(
                        f"- {contact['name']} (last: {notes})"
                    )
                    continue

                # 1-week-before reminder
                if reminder_date == today + timedelta(days=7):
                    last_contact_str = contact.get("last_contact_date", "")
                    if last_contact_str:
                        try:
                            last_contact_date = datetime.strptime(
                                last_contact_str, "%Y-%m-%d"
                            ).date()
                            # Only send if reminder_date > last_contact_date + 7 days
                            if reminder_date > last_contact_date + timedelta(days=7):
                                notes = contact.get("last_contact_notes", "")
                                reminders.append(
                                    f"- {contact['name']} in 1 week (last: {notes})"
                                )
                        except (ValueError, TypeError):
                            pass

            # 4. Combine reminders into single SMS and send
            if reminders:
                body = "Rolodex reminders:\n" + "\n".join(reminders)
                send_sms(to=phone, body=body)

        except Exception:
            logger.exception("Error processing reminders for user %s", phone)
            continue

    return ("OK", 200)
