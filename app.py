"""Flask app entry point — thin routing layer."""

import logging

from flask import Flask, request

logging.basicConfig(level=logging.INFO)
from werkzeug.middleware.proxy_fix import ProxyFix

import reminder_handler
import sms_handler
import telegram_handler

app = Flask(__name__)
# Cloud Run terminates TLS — trust X-Forwarded-Proto so request.url uses https://
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    result = telegram_handler.handle_inbound_telegram(
        json_data=request.get_json(force=True) or {},
        secret_token_header=request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
    )
    return result, 200


@app.route("/sms-webhook", methods=["POST"])
def sms_webhook():
    result = sms_handler.handle_inbound_sms(
        form_data=request.form.to_dict(),
        request_url=request.url,
        twilio_signature=request.headers.get("X-Twilio-Signature", ""),
    )
    return result, 200


@app.route("/reminder-cron", methods=["POST"])
def reminder_cron():
    body, status_code = reminder_handler.handle_reminder_cron(
        authorization_header=request.headers.get("Authorization"),
    )
    return body, status_code


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200
