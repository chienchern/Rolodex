"""Integration tests against the deployed Rolodex app.

Tests 1-3: single-turn interactions (log, log with timing, query).
Each test resets seed data, POSTs to the webhook with a valid Twilio signature,
waits for processing, then verifies sheet state (contacts + logs).
Reply SMS delivery is not verified here — use manual testing for that.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

import gspread
from gspread.exceptions import APIError
import pytest
import requests
from twilio.request_validator import RequestValidator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

import os
from dotenv import load_dotenv

load_dotenv()

TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_PHONE_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
TEST_USER_PHONE = os.environ["FIRST_USER_PHONE_NUMBER"]
MASTER_SHEET_ID = os.environ["MASTER_SHEET_ID"]
APP_URL = os.environ.get("APP_URL", "https://rolodex-mvp-327059660015.us-central1.run.app")
WEBHOOK_URL = f"{APP_URL}/sms-webhook"
SA_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "sa-key.json")

# How long to wait after posting for processing
# 20s covers normal processing (~3s) plus one sheets_client retry cycle (5s backoff)
PROCESSING_WAIT = 20


def _sheets_call_with_retry(fn, *args, retries=5, **kwargs):
    """Call a gspread function, retrying on 429 rate-limit errors with backoff."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  Sheets 429 rate limit — retrying in {wait}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
            else:
                raise

# Today's date for assertions
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
TODAY = datetime.now().date()

# Seed contacts
SEED_CONTACTS = [
    {
        "name": "Sarah Chen",
        "reminder_date": "2026-02-20",
        "last_contact_date": "2026-01-15",
        "last_interaction_message": "discussed her startup",
        "status": "active",
    },
    {
        "name": "Dad",
        "reminder_date": "2026-03-01",
        "last_contact_date": "2026-01-20",
        "last_interaction_message": "called him about retirement",
        "status": "active",
    },
    {
        "name": "Mike Torres",
        "reminder_date": "",
        "last_contact_date": "2026-02-03",
        "last_interaction_message": "lunch, new job at Google",
        "status": "active",
    },
    {
        "name": "John Smith",
        "reminder_date": "2026-02-25",
        "last_contact_date": "2026-02-01",
        "last_interaction_message": "coffee, discussed travel plans",
        "status": "active",
    },
    {
        "name": "John Doe",
        "reminder_date": "2026-03-10",
        "last_contact_date": "2026-01-30",
        "last_interaction_message": "drinks, he's moving to Austin",
        "status": "active",
    },
    {
        "name": "Becca",
        "reminder_date": "2026-03-15",
        "last_contact_date": "2026-02-10",
        "last_interaction_message": "dinner, talked about her new job",
        "status": "active",
    },
]

CONTACTS_HEADERS = ["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]
LOGS_HEADERS = ["date", "contact_name", "intent", "raw_message"]

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gc():
    """gspread client from service account key."""
    return gspread.service_account(filename=SA_KEY_PATH)


@pytest.fixture(scope="module")
def validator():
    """Twilio request validator."""
    return RequestValidator(TWILIO_AUTH_TOKEN)


@pytest.fixture(autouse=True, scope="class")
def inter_test_pause():
    """Pause between test classes to avoid exhausting the shared Sheets API quota."""
    yield
    time.sleep(30)


def reset_seed_data(gc):
    """Clear Contacts and Logs tabs, re-seed contacts with known data."""
    spreadsheet = _sheets_call_with_retry(gc.open_by_key, MASTER_SHEET_ID)

    # --- Reset Contacts tab ---
    contacts_ws = spreadsheet.worksheet("Contacts")
    contacts_ws.clear()
    # Write headers + seed rows
    all_rows = [CONTACTS_HEADERS] + [[c[h] for h in CONTACTS_HEADERS] for c in SEED_CONTACTS]
    contacts_ws.update(values=all_rows, range_name=f"A1:E{len(all_rows)}")

    # --- Reset Logs tab ---
    logs_ws = spreadsheet.worksheet("Logs")
    logs_ws.clear()
    logs_ws.update(values=[LOGS_HEADERS], range_name="A1:D1")

    # Small delay for Sheets API propagation
    time.sleep(2)


def post_sms(validator, body_text, message_sid=None):
    """POST to the webhook with a valid Twilio signature. Returns the response."""
    if message_sid is None:
        message_sid = f"SM_e2e_{uuid.uuid4().hex[:12]}"

    form_data = {
        "Body": body_text,
        "From": TEST_USER_PHONE,
        "To": TWILIO_PHONE_NUMBER,
        "MessageSid": message_sid,
        "AccountSid": "AC_placeholder",
        "NumMedia": "0",
    }

    # ProxyFix is active: Flask reconstructs URL with https:// from X-Forwarded-Proto.
    # Compute signature against the https URL that the server sees.
    server_url = WEBHOOK_URL
    signature = validator.compute_signature(server_url, form_data)

    headers = {
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(WEBHOOK_URL, data=form_data, headers=headers, timeout=60)
    return resp



def get_contacts(gc):
    """Read all contacts from the sheet."""
    spreadsheet = _sheets_call_with_retry(gc.open_by_key, MASTER_SHEET_ID)
    contacts_ws = spreadsheet.worksheet("Contacts")
    return contacts_ws.get_all_records()


def get_logs(gc):
    """Read all log entries from the sheet."""
    spreadsheet = _sheets_call_with_retry(gc.open_by_key, MASTER_SHEET_ID)
    logs_ws = spreadsheet.worksheet("Logs")
    return logs_ws.get_all_records()


def find_contact(contacts, name):
    """Find a contact by name."""
    for c in contacts:
        if c["name"] == name:
            return c
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLogInteraction:
    """Test 1: 'Had coffee with Sarah' -- log interaction with existing contact."""

    def test_log_interaction(self, gc, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        time.sleep(1)

        # --- Act ---
        resp = post_sms(validator, "Had coffee with Sarah")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Contacts tab ---
        contacts = get_contacts(gc)
        sarah = find_contact(contacts, "Sarah Chen")
        assert sarah is not None, "Sarah Chen not found in contacts"
        assert sarah["last_contact_date"] == TODAY_STR, \
            f"last_contact_date should be {TODAY_STR}, got {sarah['last_contact_date']}"
        assert "coffee" in sarah["last_interaction_message"].lower(), \
            f"last_interaction_message should contain 'coffee': {sarah['last_interaction_message']}"

        # reminder_date should be preserved (Sarah had existing reminder_date="2026-02-20")
        # since no explicit timing was specified in the SMS
        assert sarah["reminder_date"] == "2026-02-20", \
            f"reminder_date should be preserved as 2026-02-20, got {sarah['reminder_date']}"

        # Other contacts should be unchanged
        for seed in SEED_CONTACTS:
            if seed["name"] == "Sarah Chen":
                continue
            contact = find_contact(contacts, seed["name"])
            assert contact is not None, f"{seed['name']} missing from contacts"
            assert contact["last_contact_date"] == seed["last_contact_date"], \
                f"{seed['name']} last_contact_date changed unexpectedly"

        # --- Assert: Logs tab ---
        logs = get_logs(gc)
        assert len(logs) >= 1, "Expected at least 1 log entry"
        # Find the log for Sarah Chen
        sarah_logs = [l for l in logs if l["contact_name"] == "Sarah Chen"]
        assert len(sarah_logs) >= 1, "Expected a log entry for Sarah Chen"
        log = sarah_logs[0]
        assert log["intent"] == "log_interaction", \
            f"Log intent should be 'log_interaction', got {log['intent']}"
        assert "coffee" in log["raw_message"].lower(), \
            f"Log raw_message should contain 'coffee': {log['raw_message']}"

        print("  Test 1 PASSED")


class TestLogWithTiming:
    """Test 2: 'Lunch with Dad, follow up in 3 weeks' -- explicit follow-up timing."""

    def test_log_with_explicit_timing(self, gc, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        time.sleep(1)

        # --- Act ---
        resp = post_sms(validator, "Lunch with Dad, follow up in 3 weeks")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Contacts tab ---
        contacts = get_contacts(gc)
        dad = find_contact(contacts, "Dad")
        assert dad is not None, "Dad not found in contacts"
        assert dad["last_contact_date"] == TODAY_STR, \
            f"last_contact_date should be {TODAY_STR}, got {dad['last_contact_date']}"
        assert "lunch" in dad["last_interaction_message"].lower(), \
            f"last_interaction_message should contain 'lunch': {dad['last_interaction_message']}"

        # reminder_date should be ~21 days from today (allow +/- 2 days for timezone/rounding)
        reminder_date = datetime.strptime(dad["reminder_date"], "%Y-%m-%d").date()
        expected_min = TODAY + timedelta(days=19)
        expected_max = TODAY + timedelta(days=23)
        assert expected_min <= reminder_date <= expected_max, \
            f"reminder_date should be ~21 days from today, got {dad['reminder_date']}"

        # Other contacts should be unchanged
        for seed in SEED_CONTACTS:
            if seed["name"] == "Dad":
                continue
            contact = find_contact(contacts, seed["name"])
            assert contact is not None, f"{seed['name']} missing from contacts"
            assert contact["last_contact_date"] == seed["last_contact_date"], \
                f"{seed['name']} last_contact_date changed unexpectedly"

        # --- Assert: Logs tab ---
        logs = get_logs(gc)
        assert len(logs) >= 1, "Expected at least 1 log entry"
        dad_logs = [l for l in logs if l["contact_name"] == "Dad"]
        assert len(dad_logs) >= 1, "Expected a log entry for Dad"
        log = dad_logs[0]
        assert "lunch" in log["raw_message"].lower(), \
            f"Log raw_message should contain 'lunch': {log['raw_message']}"

        print("  Test 2 PASSED")


class TestQuery:
    """Test 3: 'When did I last talk to Mike?' -- query (no sheet changes)."""

    def test_query_last_contact(self, gc, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        time.sleep(1)

        # Snapshot contacts before the query
        contacts_before = get_contacts(gc)

        # --- Act ---
        resp = post_sms(validator, "When did I last talk to Mike?")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Contacts tab -- no changes ---
        contacts_after = get_contacts(gc)
        for seed in SEED_CONTACTS:
            before = find_contact(contacts_before, seed["name"])
            after = find_contact(contacts_after, seed["name"])
            assert before == after, \
                f"Contact '{seed['name']}' should not change. Before: {before}, After: {after}"

        # --- Assert: Contacts tab -- no changes ---
        # (already asserted above)

        # --- Assert: Logs tab -- 1 query entry, no interaction entries ---
        logs = get_logs(gc)
        assert len(logs) >= 1, f"Expected a query log entry, got {len(logs)}"
        query_logs = [l for l in logs if l["intent"] == "query"]
        assert len(query_logs) >= 1, f"Expected a query log entry, got intents: {[l['intent'] for l in logs]}"
        assert all(l["intent"] == "query" for l in logs), \
            f"Only query log entries expected, got: {[l['intent'] for l in logs]}"

        print("  Test 3 PASSED")


class TestLogBasedContext:
    """Test 4: Pronoun resolution via log-based context.

    Send 'Had coffee with Sarah', wait, then send 'Set a reminder for her in 2 weeks'.
    Gemini should resolve 'her' to Sarah Chen using the recent log entry.
    """

    def test_pronoun_resolution_via_logs(self, gc, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        time.sleep(1)

        # --- Act: first message ---
        resp1 = post_sms(validator, "Had coffee with Sarah")
        assert resp1.status_code == 200

        print(f"  Waiting {PROCESSING_WAIT}s for first message processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Act: second message using pronoun ---
        resp2 = post_sms(validator, "Set a reminder for her in 2 weeks")
        assert resp2.status_code == 200

        print(f"  Waiting {PROCESSING_WAIT}s for second message processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Sarah Chen's reminder_date updated ---
        contacts = get_contacts(gc)
        sarah = find_contact(contacts, "Sarah Chen")
        assert sarah is not None, "Sarah Chen not found"

        reminder_date = datetime.strptime(sarah["reminder_date"], "%Y-%m-%d").date()
        expected_min = TODAY + timedelta(days=12)
        expected_max = TODAY + timedelta(days=16)
        assert expected_min <= reminder_date <= expected_max, \
            f"reminder_date should be ~14 days from today, got {sarah['reminder_date']}"

        # --- Assert: Logs tab has set_reminder entry for Sarah ---
        logs = get_logs(gc)
        reminder_logs = [l for l in logs if l["intent"] == "set_reminder" and l["contact_name"] == "Sarah Chen"]
        assert len(reminder_logs) >= 1, \
            f"Expected a set_reminder log for Sarah Chen, got: {[l['intent'] + ':' + l['contact_name'] for l in logs]}"

        print("  Test 4 PASSED")


class TestUpdateContact:
    """Test 5: 'Rename Becca to Becca Zhou' -- update_contact intent."""

    def test_rename_contact(self, gc, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        time.sleep(1)

        # Verify Becca exists before rename
        contacts_before = get_contacts(gc)
        becca = find_contact(contacts_before, "Becca")
        assert becca is not None, "Becca should exist in seed data"
        original_reminder = becca["reminder_date"]
        original_last_contact = becca["last_contact_date"]

        # --- Act ---
        resp = post_sms(validator, "Rename Becca to Becca Zhou")
        assert resp.status_code == 200

        print(f"  Waiting {PROCESSING_WAIT}s for processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Contacts tab ---
        contacts = get_contacts(gc)
        old_becca = find_contact(contacts, "Becca")
        new_becca = find_contact(contacts, "Becca Zhou")
        assert old_becca is None, "Old name 'Becca' should no longer exist"
        assert new_becca is not None, "'Becca Zhou' should exist after rename"

        # Data should be preserved
        assert new_becca["reminder_date"] == original_reminder, \
            f"reminder_date should be preserved: {new_becca['reminder_date']}"
        assert new_becca["last_contact_date"] == original_last_contact, \
            f"last_contact_date should be preserved: {new_becca['last_contact_date']}"

        # --- Assert: Logs tab ---
        logs = get_logs(gc)
        rename_logs = [l for l in logs if l["intent"] == "update_contact" and l["contact_name"] == "Becca Zhou"]
        assert len(rename_logs) >= 1, \
            f"Expected an update_contact log for Becca Zhou, got: {[l['intent'] + ':' + l['contact_name'] for l in logs]}"

        print("  Test 5 PASSED")
