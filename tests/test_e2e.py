"""End-to-end tests against the deployed Rolodex app.

Tests 1-3: single-turn interactions (log, log with timing, query).
Each test resets seed data, POSTs to the webhook with a valid Twilio signature,
waits for processing, then verifies the reply SMS and sheet state.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

import gspread
import pytest
import requests
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

import os
from dotenv import load_dotenv

load_dotenv()

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_PHONE_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
TEST_USER_PHONE = os.environ["FIRST_USER_PHONE_NUMBER"]
MASTER_SHEET_ID = os.environ["MASTER_SHEET_ID"]
APP_URL = os.environ.get("APP_URL", "https://rolodex-mvp-327059660015.us-central1.run.app")
WEBHOOK_URL = f"{APP_URL}/sms-webhook"
SA_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "sa-key.json")

# How long to wait after posting for the batch window + processing
PROCESSING_WAIT = 15

# Today's date for assertions
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
TODAY = datetime.now().date()

# Seed contacts
SEED_CONTACTS = [
    {
        "name": "Sarah Chen",
        "reminder_date": "2026-02-20",
        "last_contact_date": "2026-01-15",
        "last_contact_notes": "discussed her startup",
        "status": "active",
    },
    {
        "name": "Dad",
        "reminder_date": "2026-03-01",
        "last_contact_date": "2026-01-20",
        "last_contact_notes": "called him about retirement",
        "status": "active",
    },
    {
        "name": "Mike Torres",
        "reminder_date": "",
        "last_contact_date": "2026-02-03",
        "last_contact_notes": "lunch, new job at Google",
        "status": "active",
    },
    {
        "name": "John Smith",
        "reminder_date": "2026-02-25",
        "last_contact_date": "2026-02-01",
        "last_contact_notes": "coffee, discussed travel plans",
        "status": "active",
    },
    {
        "name": "John Doe",
        "reminder_date": "2026-03-10",
        "last_contact_date": "2026-01-30",
        "last_contact_notes": "drinks, he's moving to Austin",
        "status": "active",
    },
]

CONTACTS_HEADERS = ["name", "reminder_date", "last_contact_date", "last_contact_notes", "status"]
LOGS_HEADERS = ["date", "contact_name", "intent", "notes", "raw_message"]

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gc():
    """gspread client from service account key."""
    return gspread.service_account(filename=SA_KEY_PATH)


@pytest.fixture(scope="module")
def twilio_client():
    """Twilio REST client."""
    return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


@pytest.fixture(scope="module")
def validator():
    """Twilio request validator."""
    return RequestValidator(TWILIO_AUTH_TOKEN)


def reset_seed_data(gc):
    """Clear Contacts and Logs tabs, re-seed contacts with known data."""
    spreadsheet = gc.open_by_key(MASTER_SHEET_ID)

    # --- Reset Contacts tab ---
    contacts_ws = spreadsheet.worksheet("Contacts")
    contacts_ws.clear()
    # Write headers + seed rows
    all_rows = [CONTACTS_HEADERS] + [[c[h] for h in CONTACTS_HEADERS] for c in SEED_CONTACTS]
    contacts_ws.update(values=all_rows, range_name=f"A1:E{len(all_rows)}")

    # --- Reset Logs tab ---
    logs_ws = spreadsheet.worksheet("Logs")
    logs_ws.clear()
    logs_ws.update(values=[LOGS_HEADERS], range_name="A1")

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
        "AccountSid": TWILIO_ACCOUNT_SID,
        "NumMedia": "0",
    }

    # Cloud Run terminates TLS, so Flask sees http:// in request.url
    # (no ProxyFix middleware). Compute signature against what the server sees.
    server_url = WEBHOOK_URL.replace("https://", "http://")
    signature = validator.compute_signature(server_url, form_data)

    headers = {
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(WEBHOOK_URL, data=form_data, headers=headers, timeout=60)
    return resp


def get_recent_reply(twilio_client, after_time):
    """Find the most recent outbound SMS to test user sent after `after_time`."""
    messages = twilio_client.messages.list(
        to=TEST_USER_PHONE,
        from_=TWILIO_PHONE_NUMBER,
        date_sent_after=after_time,
        limit=10,
    )
    if not messages:
        return None
    # Return the most recent one
    messages.sort(key=lambda m: m.date_sent, reverse=True)
    return messages[0]


def get_contacts(gc):
    """Read all contacts from the sheet."""
    spreadsheet = gc.open_by_key(MASTER_SHEET_ID)
    contacts_ws = spreadsheet.worksheet("Contacts")
    return contacts_ws.get_all_records()


def get_logs(gc):
    """Read all log entries from the sheet."""
    spreadsheet = gc.open_by_key(MASTER_SHEET_ID)
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

class TestE2ETest1LogInteraction:
    """Test 1: 'Had coffee with Sarah' -- log interaction with existing contact."""

    def test_log_interaction(self, gc, twilio_client, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        test_start = datetime.now(timezone.utc)
        time.sleep(1)

        # --- Act ---
        resp = post_sms(validator, "Had coffee with Sarah")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Reply SMS ---
        reply = get_recent_reply(twilio_client, test_start)
        assert reply is not None, "No reply SMS found"
        reply_body = reply.body
        print(f"  Reply SMS: {reply_body}")

        # The reply should acknowledge Sarah (either "Sarah" or "Sarah Chen")
        assert "Sarah" in reply_body, f"Reply should mention Sarah: {reply_body}"
        # Should NOT be an error message
        assert "went wrong" not in reply_body.lower(), \
            f"Reply should not be an error: {reply_body}"
        assert "not registered" not in reply_body.lower(), \
            f"Reply should not say unregistered: {reply_body}"

        # --- Assert: Contacts tab ---
        contacts = get_contacts(gc)
        sarah = find_contact(contacts, "Sarah Chen")
        assert sarah is not None, "Sarah Chen not found in contacts"
        assert sarah["last_contact_date"] == TODAY_STR, \
            f"last_contact_date should be {TODAY_STR}, got {sarah['last_contact_date']}"
        assert "coffee" in sarah["last_contact_notes"].lower(), \
            f"last_contact_notes should contain 'coffee': {sarah['last_contact_notes']}"

        # reminder_date should be 14 days from today (default)
        expected_reminder = (TODAY + timedelta(days=14)).strftime("%Y-%m-%d")
        assert sarah["reminder_date"] == expected_reminder, \
            f"reminder_date should be {expected_reminder}, got {sarah['reminder_date']}"

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
        assert "coffee" in log["notes"].lower(), \
            f"Log notes should contain 'coffee': {log['notes']}"

        print("  Test 1 PASSED")


class TestE2ETest2LogWithTiming:
    """Test 2: 'Lunch with Dad, follow up in 3 weeks' -- explicit follow-up timing."""

    def test_log_with_explicit_timing(self, gc, twilio_client, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        test_start = datetime.now(timezone.utc)
        time.sleep(1)

        # --- Act ---
        resp = post_sms(validator, "Lunch with Dad, follow up in 3 weeks")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Reply SMS ---
        reply = get_recent_reply(twilio_client, test_start)
        assert reply is not None, "No reply SMS found"
        reply_body = reply.body
        print(f"  Reply SMS: {reply_body}")

        assert "Dad" in reply_body, f"Reply should mention Dad: {reply_body}"
        assert "went wrong" not in reply_body.lower(), \
            f"Reply should not be an error: {reply_body}"

        # --- Assert: Contacts tab ---
        contacts = get_contacts(gc)
        dad = find_contact(contacts, "Dad")
        assert dad is not None, "Dad not found in contacts"
        assert dad["last_contact_date"] == TODAY_STR, \
            f"last_contact_date should be {TODAY_STR}, got {dad['last_contact_date']}"
        assert "lunch" in dad["last_contact_notes"].lower(), \
            f"last_contact_notes should contain 'lunch': {dad['last_contact_notes']}"

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
        assert "lunch" in log["notes"].lower(), \
            f"Log notes should contain 'lunch': {log['notes']}"

        print("  Test 2 PASSED")


class TestE2ETest3Query:
    """Test 3: 'When did I last talk to Mike?' -- query (no sheet changes)."""

    def test_query_last_contact(self, gc, twilio_client, validator):
        # --- Arrange ---
        reset_seed_data(gc)
        test_start = datetime.now(timezone.utc)
        time.sleep(1)

        # Snapshot contacts before the query
        contacts_before = get_contacts(gc)

        # --- Act ---
        resp = post_sms(validator, "When did I last talk to Mike?")
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

        print(f"  Waiting {PROCESSING_WAIT}s for batch window + processing...")
        time.sleep(PROCESSING_WAIT)

        # --- Assert: Reply SMS ---
        reply = get_recent_reply(twilio_client, test_start)
        assert reply is not None, "No reply SMS found"
        reply_body = reply.body
        print(f"  Reply SMS: {reply_body}")

        assert "Mike" in reply_body, f"Reply should mention Mike: {reply_body}"
        assert "went wrong" not in reply_body.lower(), \
            f"Reply should not be an error: {reply_body}"

        # Should reference Feb 3 or the last contact info somehow
        reply_lower = reply_body.lower()
        has_date_ref = ("feb" in reply_lower and "3" in reply_body) or \
                       "february 3" in reply_lower or \
                       "2/3" in reply_body or "02/03" in reply_body or \
                       "2026-02-03" in reply_body
        assert has_date_ref, f"Reply should reference Feb 3 date: {reply_body}"

        # Should ideally mention some context from the last interaction
        # (Gemini may or may not include this -- soft check)
        has_context = ("google" in reply_lower or "new job" in reply_lower or
                       "lunch" in reply_lower)
        if not has_context:
            print(f"  NOTE: Reply did not mention interaction context (Google/new job/lunch)."
                  f" This is a prompt tuning opportunity. Reply was: {reply_body}")

        # --- Assert: Contacts tab -- no changes ---
        contacts_after = get_contacts(gc)
        for seed in SEED_CONTACTS:
            before = find_contact(contacts_before, seed["name"])
            after = find_contact(contacts_after, seed["name"])
            assert before == after, \
                f"Contact '{seed['name']}' should not change. Before: {before}, After: {after}"

        # --- Assert: Logs tab -- no new rows ---
        logs = get_logs(gc)
        assert len(logs) == 0, f"Expected 0 log entries for a query, got {len(logs)}"

        print("  Test 3 PASSED")
