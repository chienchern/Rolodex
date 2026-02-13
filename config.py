"""Configuration — env vars, constants, shared helpers."""

import base64
import json
import os

from twilio.rest import Client as TwilioClient

# --- Required environment variables (fail-fast) ---

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_PHONE_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MASTER_SHEET_ID = os.environ["MASTER_SHEET_ID"]

# Base64-decoded service account credentials
_creds_b64 = os.environ["GSPREAD_CREDENTIALS_B64"]
GSPREAD_CREDENTIALS = json.loads(base64.b64decode(_creds_b64))

# --- Constants ---

BATCH_WINDOW_SECONDS = 5
CONTEXT_TTL_MINUTES = 10
IDEMPOTENCY_TTL_HOURS = 1

# --- Clients ---

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# --- Helpers ---

def send_sms(to: str, body: str):
    """Send an SMS via Twilio."""
    twilio_client.messages.create(
        body=body,
        from_=TWILIO_PHONE_NUMBER,
        to=to,
    )
