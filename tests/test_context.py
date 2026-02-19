"""Tests for context.py — Firestore state management."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest


# --- Helpers ---

def _make_doc_snapshot(data=None, exists=True, doc_id="mock_doc_id"):
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data if exists else None
    doc.id = doc_id
    doc.reference = MagicMock()
    return doc


def _future(minutes=30):
    """Return a datetime in the future."""
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _past(minutes=30):
    """Return a datetime in the past."""
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


# ============================================================
# Idempotency — processed_messages
# ============================================================

class TestIsMessageProcessed:
    """Tests for is_message_processed(message_sid)."""

    @patch("context.firestore")
    def test_returns_false_for_new_message(self, mock_firestore_mod):
        """A message_sid not in Firestore returns False."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(exists=False)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        from context import is_message_processed, _get_firestore_client
        # Reset cached client so our mock is used
        import context
        context._firestore_client = None

        result = is_message_processed("SM_new_message")
        assert result is False
        client.collection.assert_called_with("processed_messages")
        client.collection.return_value.document.assert_called_with("SM_new_message")

    @patch("context.firestore")
    def test_returns_true_for_existing_message_not_expired(self, mock_firestore_mod):
        """A message_sid in Firestore with expire_at in future returns True."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_data = {
            "processed_at": datetime.now(timezone.utc),
            "expire_at": _future(minutes=30),
        }
        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(data=doc_data, exists=True)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        result = context.is_message_processed("SM_existing")
        assert result is True

    @patch("context.firestore")
    def test_returns_false_for_expired_message(self, mock_firestore_mod):
        """A message_sid with expire_at in the past is treated as non-existent."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_data = {
            "processed_at": _past(minutes=120),
            "expire_at": _past(minutes=60),
        }
        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(data=doc_data, exists=True)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        result = context.is_message_processed("SM_expired")
        assert result is False


class TestMarkMessageProcessed:
    """Tests for mark_message_processed(message_sid)."""

    @patch("context.firestore")
    def test_stores_doc_with_expire_at(self, mock_firestore_mod):
        """mark_message_processed stores a doc with processed_at and expire_at."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_ref = MagicMock()
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        context.mark_message_processed("SM_abc123")

        client.collection.assert_called_with("processed_messages")
        client.collection.return_value.document.assert_called_with("SM_abc123")
        doc_ref.set.assert_called_once()

        stored_data = doc_ref.set.call_args[0][0]
        assert "processed_at" in stored_data
        assert "expire_at" in stored_data
        # expire_at should be ~1 hour from now
        delta = stored_data["expire_at"] - stored_data["processed_at"]
        assert timedelta(minutes=59) <= delta <= timedelta(minutes=61)


# ============================================================
# Multi-turn context
# ============================================================

class TestGetContext:
    """Tests for get_context(user_phone)."""

    @patch("context.firestore")
    def test_returns_context_when_not_expired(self, mock_firestore_mod):
        """Returns context dict when expire_at is in the future."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        context_data = {
            "original_message": "Met with John for drinks",
            "pending_intent": "log_interaction",
            "candidates": ["John Smith", "John Doe"],
            "created_at": datetime.now(timezone.utc),
            "expire_at": _future(minutes=5),
        }
        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(data=context_data, exists=True)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        result = context.get_context("+15550001111")
        assert result is not None
        assert result["original_message"] == "Met with John for drinks"
        assert result["pending_intent"] == "log_interaction"
        assert result["candidates"] == ["John Smith", "John Doe"]

    @patch("context.firestore")
    def test_returns_none_when_expired(self, mock_firestore_mod):
        """Returns None when expire_at is in the past."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        context_data = {
            "original_message": "old message",
            "pending_intent": "log_interaction",
            "candidates": [],
            "created_at": _past(minutes=20),
            "expire_at": _past(minutes=10),
        }
        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(data=context_data, exists=True)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        result = context.get_context("+15550001111")
        assert result is None

    @patch("context.firestore")
    def test_returns_none_when_no_doc(self, mock_firestore_mod):
        """Returns None when no context document exists."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_ref = MagicMock()
        doc_snapshot = _make_doc_snapshot(exists=False)
        doc_ref.get.return_value = doc_snapshot
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        result = context.get_context("+15550001111")
        assert result is None


class TestStoreContext:
    """Tests for store_context(user_phone, context_data)."""

    @patch("context.firestore")
    def test_stores_context_with_ttl(self, mock_firestore_mod):
        """Stores context with created_at and expire_at (10-min TTL)."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_ref = MagicMock()
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        ctx_data = {
            "original_message": "Met with John for drinks",
            "pending_intent": "log_interaction",
            "candidates": ["John Smith", "John Doe"],
        }
        context.store_context("+15550001111", ctx_data)

        client.collection.assert_called_with("context")
        client.collection.return_value.document.assert_called_with("+15550001111")
        doc_ref.set.assert_called_once()

        stored = doc_ref.set.call_args[0][0]
        assert stored["original_message"] == "Met with John for drinks"
        assert stored["pending_intent"] == "log_interaction"
        assert stored["candidates"] == ["John Smith", "John Doe"]
        assert "created_at" in stored
        assert "expire_at" in stored
        # expire_at should be ~10 minutes from created_at
        delta = stored["expire_at"] - stored["created_at"]
        assert timedelta(minutes=9) <= delta <= timedelta(minutes=11)


class TestClearContext:
    """Tests for clear_context(user_phone)."""

    @patch("context.firestore")
    def test_deletes_context_doc(self, mock_firestore_mod):
        """Deletes the context document for the given user_phone."""
        client = MagicMock()
        mock_firestore_mod.Client.return_value = client

        doc_ref = MagicMock()
        client.collection.return_value.document.return_value = doc_ref

        import context
        context._firestore_client = None

        context.clear_context("+15550001111")

        client.collection.assert_called_with("context")
        client.collection.return_value.document.assert_called_with("+15550001111")
        doc_ref.delete.assert_called_once()
