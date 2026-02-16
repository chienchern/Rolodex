"""Tests for config.py — env var loading, constants, send_sms helper."""

import base64
import json
import importlib
from unittest.mock import patch, MagicMock

import pytest


class TestEnvVarLoading:
    """Config loads all required env vars on import."""

    def test_loads_all_env_vars(self, env_vars):
        import config

        importlib.reload(config)
        assert config.TWILIO_AUTH_TOKEN == env_vars["TWILIO_AUTH_TOKEN"]
        assert config.GEMINI_API_KEY == env_vars["GEMINI_API_KEY"]
        assert config.MASTER_SHEET_ID == env_vars["MASTER_SHEET_ID"]

    def test_missing_required_env_var_raises(self, monkeypatch):
        # Set all but one
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "x")
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setenv("MASTER_SHEET_ID", "x")
        # Don't set GSPREAD_CREDENTIALS_B64
        monkeypatch.delenv("GSPREAD_CREDENTIALS_B64", raising=False)
        with pytest.raises(KeyError):
            import config

            importlib.reload(config)

    def test_base64_credentials_decode(self, env_vars):
        import config

        importlib.reload(config)
        creds = config.GSPREAD_CREDENTIALS
        assert isinstance(creds, dict)
        assert creds["type"] == "service_account"
        assert creds["project_id"] == "test-project"
        assert "client_email" in creds


class TestConstants:
    """Config defines expected constants."""

    def test_batch_window_seconds(self, env_vars):
        import config

        importlib.reload(config)
        assert config.BATCH_WINDOW_SECONDS == 5

    def test_context_ttl_minutes(self, env_vars):
        import config

        importlib.reload(config)
        assert config.CONTEXT_TTL_MINUTES == 10

    def test_idempotency_ttl_hours(self, env_vars):
        import config

        importlib.reload(config)
        assert config.IDEMPOTENCY_TTL_HOURS == 1


class TestSendSms:
    """send_sms() calls SNS client correctly."""

    def test_send_sms_calls_sns(self, env_vars):
        import config

        importlib.reload(config)

        mock_sns = MagicMock()
        mock_sns.publish.return_value = {"MessageId": "test-123"}
        config.sns_client = mock_sns

        config.send_sms("+15559999999", "Hello!")

        mock_sns.publish.assert_called_once_with(
            PhoneNumber="+15559999999",
            Message="Hello!",
        )
