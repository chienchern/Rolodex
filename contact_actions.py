"""Intent executors — business logic for each NLP intent."""

from datetime import datetime, timedelta

import sheets_client


def execute_log_interaction(sheet_id, nlp_contacts, follow_up_date,
                             today_str, default_reminder_days, raw_message,
                             interaction_date=None, active_contacts=None):
    """Update contact, add log entry, set reminder for log_interaction intent."""
    contact_date = interaction_date or today_str

    for contact in nlp_contacts:
        contact_name = contact["name"]

        if follow_up_date:
            reminder_date = follow_up_date
        else:
            existing_reminder = _get_existing_reminder(contact_name, active_contacts)
            if existing_reminder:
                reminder_date = existing_reminder
            else:
                today = datetime.strptime(today_str, "%Y-%m-%d")
                reminder_date = (today + timedelta(days=default_reminder_days)).strftime("%Y-%m-%d")

        try:
            sheets_client.update_contact(sheet_id, contact_name, {
                "last_contact_date": contact_date,
                "last_interaction_message": raw_message,
                "reminder_date": reminder_date,
            })
        except ValueError:
            sheets_client.add_contact(sheet_id, {
                "name": contact_name,
                "status": "active",
                "last_contact_date": contact_date,
                "last_interaction_message": raw_message,
                "reminder_date": reminder_date,
            })

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": contact_name,
            "intent": "log_interaction",
            "raw_message": raw_message,
        })


def _get_existing_reminder(contact_name, active_contacts):
    if not active_contacts:
        return None
    for c in active_contacts:
        if c.get("name") == contact_name:
            return c.get("reminder_date") or None
    return None


def execute_set_reminder(sheet_id, nlp_contacts, follow_up_date,
                          today_str, default_reminder_days, raw_message):
    """Update reminder_date on contact(s) and add log entry."""
    for contact in nlp_contacts:
        contact_name = contact["name"]

        reminder_date = follow_up_date
        if not reminder_date:
            today = datetime.strptime(today_str, "%Y-%m-%d")
            reminder_date = (today + timedelta(days=default_reminder_days)).strftime("%Y-%m-%d")

        sheets_client.update_contact(sheet_id, contact_name, {"reminder_date": reminder_date})

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": contact_name,
            "intent": "set_reminder",
            "raw_message": raw_message,
        })


def execute_update_contact(sheet_id, nlp_contacts, new_name, raw_message, today_str):
    """Rename a contact and add a log entry."""
    for contact in nlp_contacts:
        old_name = contact["name"]
        sheets_client.rename_contact(sheet_id, old_name, new_name)

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": new_name,
            "intent": "update_contact",
            "raw_message": raw_message,
        })


def execute_archive(sheet_id, nlp_contacts):
    """Archive contact(s)."""
    for contact in nlp_contacts:
        sheets_client.archive_contact(sheet_id, contact["name"])


def execute_onboarding(sheet_id, nlp_contacts, follow_up_date,
                        today_str, default_reminder_days, raw_message,
                        interaction_date=None):
    """Add a new contact and log the interaction."""
    contact_date = interaction_date or today_str

    for contact in nlp_contacts:
        contact_name = contact["name"]
        reminder_date = follow_up_date
        if not reminder_date:
            today = datetime.strptime(today_str, "%Y-%m-%d")
            reminder_date = (today + timedelta(days=default_reminder_days)).strftime("%Y-%m-%d")

        sheets_client.add_contact(sheet_id, {
            "name": contact_name,
            "status": "active",
            "last_contact_date": contact_date,
            "last_interaction_message": raw_message,
            "reminder_date": reminder_date,
        })

        sheets_client.add_log_entry(sheet_id, {
            "date": today_str,
            "contact_name": contact_name,
            "intent": "onboarding",
            "raw_message": raw_message,
        })
