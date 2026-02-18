"""Messaging — channel-agnostic send_message with Telegram and SMS implementations."""

import logging

import requests

from config import (
    MESSAGING_CHANNEL,
    TELEGRAM_BOT_TOKEN,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
)

logger = logging.getLogger(__name__)


def send_message(user: dict, text: str) -> None:
    """Send a message to a user via the configured channel.

    Routes to Telegram or SMS based on the MESSAGING_CHANNEL env var.
    The user dict must contain 'telegram_chat_id' (Telegram) or 'phone' (SMS).
    """
    if MESSAGING_CHANNEL == "telegram":
        _send_via_telegram(str(user["telegram_chat_id"]), text)
    else:
        _send_via_sms(user["phone"], text)


def _send_via_telegram(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    response.raise_for_status()
    logger.info("Telegram message sent to chat_id %s", chat_id)


def _send_via_sms(phone: str, text: str) -> None:
    from twilio.rest import Client
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(to=phone, from_=TWILIO_PHONE_NUMBER, body=text)
    logger.info("SMS sent to %s", phone)
