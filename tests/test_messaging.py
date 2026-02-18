"""Tests for messaging.py — channel-agnostic send_message."""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reload_messaging(env_vars):
    """Reload messaging before each test to pick up env var changes."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("messaging", "config"):
            del sys.modules[mod_name]
    yield


class TestSendMessageRouting:
    """send_message routes to the correct channel based on MESSAGING_CHANNEL."""

    def test_routes_to_telegram_when_channel_is_telegram(self, env_vars):
        import messaging

        with patch.object(messaging, "MESSAGING_CHANNEL", "telegram"), \
             patch.object(messaging, "_send_via_telegram") as mock_send:
            messaging.send_message({"telegram_chat_id": "123456"}, "Hello!")

            mock_send.assert_called_once_with("123456", "Hello!")

    def test_routes_to_sms_when_channel_is_sms(self, env_vars):
        import messaging

        with patch.object(messaging, "MESSAGING_CHANNEL", "sms"), \
             patch.object(messaging, "_send_via_sms") as mock_send:
            messaging.send_message({"phone": "+15559999999"}, "Hello!")

            mock_send.assert_called_once_with("+15559999999", "Hello!")

    def test_telegram_uses_telegram_chat_id_field(self, env_vars):
        import messaging

        with patch.object(messaging, "MESSAGING_CHANNEL", "telegram"), \
             patch.object(messaging, "_send_via_telegram") as mock_send:
            messaging.send_message(
                {"telegram_chat_id": 987654321, "phone": "+15559999999"},
                "Hello!",
            )

            # Must use telegram_chat_id, not phone
            mock_send.assert_called_once_with("987654321", "Hello!")


class TestSendViaTelegram:
    """_send_via_telegram calls the Telegram Bot API correctly."""

    def test_posts_to_correct_url(self, env_vars):
        import messaging

        with patch.object(messaging, "TELEGRAM_BOT_TOKEN", "test_token"), \
             patch("messaging.requests.post") as mock_post:
            mock_post.return_value = MagicMock()

            messaging._send_via_telegram("123456", "Hello!")

            url = mock_post.call_args[0][0]
            assert url == "https://api.telegram.org/bottest_token/sendMessage"

    def test_sends_correct_payload(self, env_vars):
        import messaging

        with patch.object(messaging, "TELEGRAM_BOT_TOKEN", "test_token"), \
             patch("messaging.requests.post") as mock_post:
            mock_post.return_value = MagicMock()

            messaging._send_via_telegram("123456", "Hello!")

            kwargs = mock_post.call_args[1]
            assert kwargs["json"] == {"chat_id": "123456", "text": "Hello!"}
            assert kwargs["timeout"] == 10

    def test_raises_on_http_error(self, env_vars):
        import messaging

        with patch.object(messaging, "TELEGRAM_BOT_TOKEN", "test_token"), \
             patch("messaging.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
            mock_post.return_value = mock_response

            with pytest.raises(Exception, match="400"):
                messaging._send_via_telegram("123456", "Hello!")


class TestSendViaSms:
    """_send_via_sms calls Twilio correctly."""

    def test_creates_message_with_correct_params(self, env_vars):
        import messaging

        with patch.object(messaging, "TWILIO_ACCOUNT_SID", "ACtest"), \
             patch.object(messaging, "TWILIO_AUTH_TOKEN", "auth_token"), \
             patch.object(messaging, "TWILIO_PHONE_NUMBER", "+15550001234"), \
             patch("twilio.rest.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            messaging._send_via_sms("+15559999999", "Hello!")

            mock_client.messages.create.assert_called_once_with(
                to="+15559999999",
                from_="+15550001234",
                body="Hello!",
            )

    def test_uses_configured_credentials(self, env_vars):
        import messaging

        with patch.object(messaging, "TWILIO_ACCOUNT_SID", "ACtest123"), \
             patch.object(messaging, "TWILIO_AUTH_TOKEN", "secret456"), \
             patch.object(messaging, "TWILIO_PHONE_NUMBER", "+15550001234"), \
             patch("twilio.rest.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()

            messaging._send_via_sms("+15559999999", "Hello!")

            mock_client_cls.assert_called_once_with("ACtest123", "secret456")
