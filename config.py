"""Configuration — env vars, constants, shared helpers."""

import base64
import json
import logging
import os

import boto3

# --- Required environment variables (fail-fast) ---

TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
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

logger = logging.getLogger(__name__)
sns_client = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# --- Helpers ---

def send_sms(to: str, body: str):
    """Send an SMS via Amazon SNS."""
    response = sns_client.publish(
        PhoneNumber=to,
        Message=body,
    )
    logger.info("SNS message sent to %s, MessageId: %s", to, response.get("MessageId"))
