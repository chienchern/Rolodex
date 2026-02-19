"""Firestore state management — idempotency and multi-turn context."""

from datetime import datetime, timedelta, timezone

from google.cloud import firestore

IDEMPOTENCY_TTL_HOURS = 1
CONTEXT_TTL_MINUTES = 10

# --- Firestore client (lazy singleton) ---

_firestore_client = None


def _get_firestore_client():
    """Return a cached Firestore client instance."""
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client()
    return _firestore_client


# ============================================================
# Idempotency — processed_messages
# ============================================================


def is_message_processed(message_sid: str) -> bool:
    """Check if a message has already been processed.

    Returns False if the document doesn't exist or its expire_at is in the past.
    """
    client = _get_firestore_client()
    doc_ref = client.collection("processed_messages").document(message_sid)
    doc = doc_ref.get()

    if not doc.exists:
        return False

    data = doc.to_dict()
    expire_at = data.get("expire_at")
    if expire_at is None:
        return True

    # Application-level TTL check
    now = datetime.now(timezone.utc)
    if hasattr(expire_at, "tzinfo") and expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return expire_at > now


def mark_message_processed(message_sid: str) -> None:
    """Mark a message as processed with a 1-hour TTL."""
    client = _get_firestore_client()
    now = datetime.now(timezone.utc)
    doc_ref = client.collection("processed_messages").document(message_sid)
    doc_ref.set({
        "processed_at": now,
        "expire_at": now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
    })


# ============================================================
# Multi-turn context
# ============================================================


def get_context(user_phone: str) -> dict | None:
    """Get multi-turn context for a user.

    Returns the context dict if it exists and hasn't expired, else None.
    """
    client = _get_firestore_client()
    doc_ref = client.collection("context").document(user_phone)
    doc = doc_ref.get()

    if not doc.exists:
        return None

    data = doc.to_dict()
    expire_at = data.get("expire_at")
    if expire_at is None:
        return data

    # Application-level TTL check
    now = datetime.now(timezone.utc)
    if hasattr(expire_at, "tzinfo") and expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    if expire_at <= now:
        return None

    return data


def store_context(user_phone: str, context_data: dict) -> None:
    """Store multi-turn context with a 10-minute TTL.

    The context_data dict is stored as-is, with created_at and expire_at added.
    """
    client = _get_firestore_client()
    now = datetime.now(timezone.utc)
    doc_ref = client.collection("context").document(user_phone)
    doc_ref.set({
        **context_data,
        "created_at": now,
        "expire_at": now + timedelta(minutes=CONTEXT_TTL_MINUTES),
    })


def clear_context(user_phone: str) -> None:
    """Delete multi-turn context for a user."""
    client = _get_firestore_client()
    doc_ref = client.collection("context").document(user_phone)
    doc_ref.delete()
