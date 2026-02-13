"""Tests for app.py — Flask routing layer."""

import importlib
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def setup_env(env_vars):
    """All tests need env vars set before importing app."""
    pass


@pytest.fixture
def client():
    """Flask test client with mocked handlers."""
    with patch("sms_handler.handle_inbound_sms") as mock_sms, \
         patch("reminder_handler.handle_reminder_cron") as mock_reminder:
        import app
        importlib.reload(app)
        app.app.testing = True
        with app.app.test_client() as c:
            c._mock_sms = mock_sms
            c._mock_reminder = mock_reminder
            yield c


class TestSmsWebhook:
    def test_post_delegates_to_handler(self, client):
        client._mock_sms.return_value = "OK"
        resp = client.post("/sms-webhook", data={"Body": "Hello", "From": "+1555"})
        assert resp.status_code == 200
        client._mock_sms.assert_called_once()

    def test_passes_correct_args(self, client):
        client._mock_sms.return_value = "OK"
        client.post(
            "/sms-webhook",
            data={"Body": "Hello"},
            headers={"X-Twilio-Signature": "sig123"},
        )
        args = client._mock_sms.call_args
        form_data = args[1]["form_data"]
        assert "Body" in form_data
        assert args[1]["twilio_signature"] == "sig123"


class TestReminderCron:
    def test_post_delegates_to_handler(self, client):
        client._mock_reminder.return_value = ("OK", 200)
        resp = client.post(
            "/reminder-cron",
            headers={"Authorization": "Bearer token123"},
        )
        assert resp.status_code == 200

    def test_passes_authorization_header(self, client):
        client._mock_reminder.return_value = ("OK", 200)
        client.post(
            "/reminder-cron",
            headers={"Authorization": "Bearer token123"},
        )
        client._mock_reminder.assert_called_once()
        args = client._mock_reminder.call_args
        assert "Bearer token123" in str(args)


class TestHealth:
    def test_returns_200_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert b"OK" in resp.data


class TestUnknownRoute:
    def test_returns_404(self, client):
        resp = client.get("/nonexistent")
        assert resp.status_code == 404
