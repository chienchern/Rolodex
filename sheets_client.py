"""Google Sheets data layer — read/write via gspread."""

import gspread

from config import GSPREAD_CREDENTIALS, MASTER_SHEET_ID

# Lazy-cached gspread client
_client = None


def _get_client():
    """Return a cached gspread client, creating it on first call."""
    global _client
    if _client is None:
        _client = gspread.service_account_from_dict(GSPREAD_CREDENTIALS)
    return _client


def _reset_client():
    """Reset the cached client. Used in tests."""
    global _client
    _client = None


# ---------------------------------------------------------------------------
# Users (master spreadsheet)
# ---------------------------------------------------------------------------

def get_user_by_phone(phone: str) -> dict | None:
    """Look up a user by phone number. Returns user dict or None."""
    client = _get_client()
    spreadsheet = client.open_by_key(MASTER_SHEET_ID)
    users_ws = spreadsheet.worksheet("Users")
    rows = users_ws.get_all_records(numericise_ignore=["all"])
    for row in rows:
        if str(row["phone"]) == phone:
            return row
    return None


def get_user_by_telegram_chat_id(chat_id: str) -> dict | None:
    """Look up a user by Telegram chat ID. Returns user dict or None."""
    client = _get_client()
    spreadsheet = client.open_by_key(MASTER_SHEET_ID)
    users_ws = spreadsheet.worksheet("Users")
    rows = users_ws.get_all_records(numericise_ignore=["all"])
    for row in rows:
        if str(row.get("telegram_chat_id", "")).strip() == chat_id:
            return row
    return None


def get_all_users() -> list[dict]:
    """Return all rows from the master Users tab."""
    client = _get_client()
    spreadsheet = client.open_by_key(MASTER_SHEET_ID)
    users_ws = spreadsheet.worksheet("Users")
    return users_ws.get_all_records(numericise_ignore=["all"])


# ---------------------------------------------------------------------------
# Contacts (per-user spreadsheet)
# ---------------------------------------------------------------------------

def get_active_contacts(sheet_id: str) -> list[dict]:
    """Return contacts with status='active' from the user's Contacts tab."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    contacts_ws = spreadsheet.worksheet("Contacts")
    rows = contacts_ws.get_all_records()
    return [row for row in rows if row.get("status") == "active"]


def update_contact(sheet_id: str, contact_name: str, updates: dict) -> None:
    """Update fields for a contact by name. Raises ValueError if not found."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    contacts_ws = spreadsheet.worksheet("Contacts")

    cell = contacts_ws.find(contact_name)
    if cell is None:
        raise ValueError(f"Contact '{contact_name}' not found")

    row = cell.row
    headers = contacts_ws.row_values(1)
    for field, value in updates.items():
        col = headers.index(field) + 1  # 1-indexed
        contacts_ws.update_cell(row, col, value)


def add_contact(sheet_id: str, contact_data: dict) -> None:
    """Append a new contact row to the Contacts tab."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    contacts_ws = spreadsheet.worksheet("Contacts")

    headers = contacts_ws.row_values(1)
    row = [contact_data.get(h, "") for h in headers]
    contacts_ws.append_row(row)


def rename_contact(sheet_id: str, old_name: str, new_name: str) -> None:
    """Rename a contact. Raises ValueError if not found."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    contacts_ws = spreadsheet.worksheet("Contacts")

    cell = contacts_ws.find(old_name)
    if cell is None:
        raise ValueError(f"Contact '{old_name}' not found")

    headers = contacts_ws.row_values(1)
    name_col = headers.index("name") + 1
    contacts_ws.update_cell(cell.row, name_col, new_name)


def archive_contact(sheet_id: str, contact_name: str) -> None:
    """Set a contact's status to 'archived'. Raises ValueError if not found."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    contacts_ws = spreadsheet.worksheet("Contacts")

    cell = contacts_ws.find(contact_name)
    if cell is None:
        raise ValueError(f"Contact '{contact_name}' not found")

    row = cell.row
    headers = contacts_ws.row_values(1)
    status_col = headers.index("status") + 1
    contacts_ws.update_cell(row, status_col, "archived")


# ---------------------------------------------------------------------------
# Settings (per-user spreadsheet)
# ---------------------------------------------------------------------------

def get_settings(sheet_id: str) -> dict:
    """Return key-value dict from the Settings tab."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    settings_ws = spreadsheet.worksheet("Settings")
    rows = settings_ws.get_all_records()
    return {row["key"]: row["value"] for row in rows}


# ---------------------------------------------------------------------------
# Logs (per-user spreadsheet)
# ---------------------------------------------------------------------------

def add_log_entry(sheet_id: str, log_data: dict) -> None:
    """Append a log entry row to the Logs tab."""
    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    logs_ws = spreadsheet.worksheet("Logs")

    headers = logs_ws.row_values(1)
    row = [log_data.get(h, "") for h in headers]
    logs_ws.append_row(row)
