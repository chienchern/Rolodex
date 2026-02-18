"""Shared pytest fixtures for Rolodex MVP tests."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest


# --- Environment variables ---

SAMPLE_ENV = {
    "GEMINI_API_KEY": "test_gemini_key",
    "MASTER_SHEET_ID": "master_sheet_id_abc123",
    "MESSAGING_CHANNEL": "telegram",
    "GSPREAD_CREDENTIALS_B64": base64.b64encode(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "test-project",
                "private_key_id": "key123",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJBALRiMLAH\n-----END RSA PRIVATE KEY-----\n",
                "client_email": "test@test-project.iam.gserviceaccount.com",
                "client_id": "123456789",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ).encode()
    ).decode(),
}


@pytest.fixture
def env_vars(monkeypatch):
    """Set all required environment variables."""
    for key, value in SAMPLE_ENV.items():
        monkeypatch.setenv(key, value)
    return SAMPLE_ENV


# --- Sample data ---

SAMPLE_CONTACTS = [
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

SAMPLE_SETTINGS = {
    "timezone": "America/New_York",
    "default_reminder_days": "14",
}

SAMPLE_USER = {
    "phone": "+15550001111",
    "telegram_chat_id": "123456789",
    "name": "Test User",
    "sheet_id": "test_sheet_id_xyz",
}


@pytest.fixture
def sample_contacts():
    return [c.copy() for c in SAMPLE_CONTACTS]


@pytest.fixture
def sample_settings():
    return SAMPLE_SETTINGS.copy()


@pytest.fixture
def sample_user():
    return SAMPLE_USER.copy()


# --- Mock Firestore ---


def _make_mock_document(data=None, exists=True):
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data if exists else None
    doc.id = "mock_doc_id"
    return doc


@pytest.fixture
def mock_firestore_client():
    """Patched Firestore client with configurable document returns."""
    with patch("google.cloud.firestore.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client

        # Helper to configure what documents a collection returns
        def configure_collection(collection_name, documents=None):
            """Set up a collection to return specific documents on .stream()."""
            collection_ref = MagicMock()
            if documents:
                mock_docs = []
                for doc_data in documents:
                    mock_docs.append(_make_mock_document(doc_data))
                collection_ref.stream.return_value = iter(mock_docs)
                collection_ref.where.return_value = collection_ref
                collection_ref.order_by.return_value = collection_ref
            else:
                collection_ref.stream.return_value = iter([])
                collection_ref.where.return_value = collection_ref
                collection_ref.order_by.return_value = collection_ref

            client.collection.return_value = collection_ref
            return collection_ref

        client.configure_collection = configure_collection
        client._make_mock_document = _make_mock_document
        yield client


# --- Mock gspread ---


def _make_mock_worksheet(name, data=None):
    """Create a mock gspread worksheet with data rows."""
    ws = MagicMock()
    ws.title = name
    if data:
        ws.get_all_records.return_value = [row.copy() for row in data]
        ws.get_all_values.return_value = [list(data[0].keys())] + [
            list(row.values()) for row in data
        ]
        ws.row_count = len(data) + 1
        # find() for locating a cell by value
        ws.find.return_value = None
    else:
        ws.get_all_records.return_value = []
        ws.get_all_values.return_value = []
        ws.row_count = 1
        ws.find.return_value = None
    return ws


@pytest.fixture
def mock_gspread_client():
    """Patched gspread client with sample worksheet data."""
    with patch("gspread.service_account_from_dict") as mock_sa:
        client = MagicMock()
        mock_sa.return_value = client

        # Default spreadsheet with standard tabs
        spreadsheet = MagicMock()
        contacts_ws = _make_mock_worksheet("Contacts", SAMPLE_CONTACTS)
        logs_ws = _make_mock_worksheet("Logs", [])
        settings_data = [{"key": k, "value": v} for k, v in SAMPLE_SETTINGS.items()]
        settings_ws = _make_mock_worksheet("Settings", settings_data)
        users_ws = _make_mock_worksheet("Users", [SAMPLE_USER])

        def worksheet_by_title(title):
            return {
                "Contacts": contacts_ws,
                "Logs": logs_ws,
                "Settings": settings_ws,
                "Users": users_ws,
            }.get(title)

        spreadsheet.worksheet.side_effect = worksheet_by_title
        client.open_by_key.return_value = spreadsheet

        client._spreadsheet = spreadsheet
        client._worksheets = {
            "Contacts": contacts_ws,
            "Logs": logs_ws,
            "Settings": settings_ws,
            "Users": users_ws,
        }
        yield client


# --- Mock send_message ---


@pytest.fixture
def mock_send_message():
    """Patched messaging.send_message capturing all outbound messages."""
    with patch("messaging.send_message") as mock_send:
        sent_messages = []

        def capture_send(user, text):
            sent_messages.append({"user": user, "text": text})

        mock_send.side_effect = capture_send
        mock_send._sent_messages = sent_messages
        yield mock_send


# --- Mock Gemini (google.genai) ---

SAMPLE_GEMINI_RESPONSE_LOG = {
    "intent": "log_interaction",
    "contacts": [{"name": "Sarah Chen", "match_type": "fuzzy"}],
    "notes": "had coffee, she's launching her startup next month",
    "follow_up_date": "2026-02-24",
    "needs_clarification": False,
    "clarification_question": None,
    "response_message": "Updated Sarah Chen. I'll remind you to reach out on Tuesday, Feb 24, 2026.",
}

SAMPLE_GEMINI_RESPONSE_QUERY = {
    "intent": "query",
    "contacts": [{"name": "Mike Torres", "match_type": "exact"}],
    "notes": None,
    "follow_up_date": None,
    "needs_clarification": False,
    "clarification_question": None,
    "response_message": "You last talked to Mike Torres on Feb 3, 2026. Notes: lunch, new job at Google.",
}

SAMPLE_GEMINI_RESPONSE_CLARIFY = {
    "intent": "clarify",
    "contacts": [
        {"name": "John Smith", "match_type": "ambiguous"},
        {"name": "John Doe", "match_type": "ambiguous"},
    ],
    "notes": None,
    "follow_up_date": None,
    "needs_clarification": True,
    "clarification_question": "Which John did you mean? John Smith or John Doe?",
    "response_message": "Which John did you mean? John Smith or John Doe?",
}


@pytest.fixture
def mock_genai_client():
    """Patched google.genai client with configurable responses."""
    with patch("google.genai.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client

        def set_response(response_dict):
            """Configure what Gemini returns."""
            mock_response = MagicMock()
            mock_response.text = json.dumps(response_dict)
            client.models.generate_content.return_value = mock_response

        # Default to log interaction response
        set_response(SAMPLE_GEMINI_RESPONSE_LOG)

        client.set_response = set_response
        client._responses = {
            "log": SAMPLE_GEMINI_RESPONSE_LOG,
            "query": SAMPLE_GEMINI_RESPONSE_QUERY,
            "clarify": SAMPLE_GEMINI_RESPONSE_CLARIFY,
        }
        yield client
