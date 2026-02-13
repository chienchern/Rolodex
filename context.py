"""Firestore state management — idempotency, batch window, multi-turn context."""

from datetime import datetime, timedelta, timezone

from google.cloud import firestore

# Import constants from config lazily to avoid circular imports
# and allow tests to mock firestore before config loads
IDEMPOTENCY_TTL_HOURS = 1
CONTEXT_TTL_MINUTES = 10
PENDING_MESSAGE_TTL_MINUTES = 10

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
# Batch window — pending_messages
# ============================================================


def store_pending_message(user_phone: str, message_text: str, message_sid: str) -> None:
    """Store a pending message for batch processing."""
    client = _get_firestore_client()
    now = datetime.now(timezone.utc)
    client.collection("pending_messages").add({
        "user_phone": user_phone,
        "message_text": message_text,
        "message_sid": message_sid,
        "received_at": now,
        "expire_at": now + timedelta(minutes=PENDING_MESSAGE_TTL_MINUTES),
    })


def get_pending_messages(user_phone: str) -> list:
    """Get all pending messages for a user, ordered by received_at.

    Returns a list of message dicts.
    """
    client = _get_firestore_client()
    query = (
        client.collection("pending_messages")
        .where("user_phone", "==", user_phone)
        .order_by("received_at")
    )
    docs = query.stream()
    return [doc.to_dict() for doc in docs]


def has_newer_message(user_phone: str, received_at: datetime) -> bool:
    """Check if there is a newer pending message for this user.

    Returns True if a message with received_at > the given timestamp exists.
    """
    client = _get_firestore_client()
    query = (
        client.collection("pending_messages")
        .where("user_phone", "==", user_phone)
        .where("received_at", ">", received_at)
        .limit(1)
    )
    docs = list(query.stream())
    return len(docs) > 0


def clear_pending_messages(user_phone: str) -> None:
    """Delete all pending messages for a user."""
    client = _get_firestore_client()
    query = client.collection("pending_messages").where("user_phone", "==", user_phone)
    for doc in query.stream():
        doc.reference.delete()


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
