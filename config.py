"""Configuration — env vars and constants."""

import base64
import json
import os

# --- Required environment variables ---

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MASTER_SHEET_ID = os.environ["MASTER_SHEET_ID"]

_creds_b64 = os.environ["GSPREAD_CREDENTIALS_B64"]
GSPREAD_CREDENTIALS = json.loads(base64.b64decode(_creds_b64))

# --- Messaging channel ("telegram" or "sms") ---

MESSAGING_CHANNEL = os.environ.get("MESSAGING_CHANNEL", "telegram")

# --- Telegram credentials (required when MESSAGING_CHANNEL=telegram) ---

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_SECRET_TOKEN = os.environ.get("TELEGRAM_SECRET_TOKEN", "")

# --- Twilio credentials (required when MESSAGING_CHANNEL=sms) ---

TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")

# --- Constants ---

BATCH_WINDOW_SECONDS = 5
CONTEXT_TTL_MINUTES = 10
IDEMPOTENCY_TTL_HOURS = 1

