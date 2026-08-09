"""Bearer-token authentication for the mobile JSON API.

Web/browser auth stays exactly as-is (session cookies + CSRF). Native clients
such as the Android app cannot hold a session cookie in the way a browser
does, so they authenticate with a long-lived opaque bearer token instead.
Only the token's SHA-256 digest is ever stored, matching the existing
PasswordResetToken pattern used elsewhere in this codebase.
"""
import hashlib
import secrets
from datetime import timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, request

from app.extensions import db
from app.models import ApiToken, utcnow

TOKEN_PREFIX = "sulit_at_"
TOKEN_TTL_DAYS = 90


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token(user, device_label="Android device"):
    """Create a new API token for user and return the raw (unhashed) value.

    The raw value is returned exactly once; only its digest is persisted.
    """
    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(40)
    record = ApiToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        device_label=(device_label or "Android device").strip()[:120] or "Android device",
        expires_at=utcnow() + timedelta(days=TOKEN_TTL_DAYS),
    )
    db.session.add(record)
    db.session.flush()
    return raw_token, record


def _extract_bearer_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    candidate = header[len("Bearer "):].strip()
    return candidate or None


def _load_token_record(raw_token):
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    record = db.session.scalar(
        db.select(ApiToken).where(ApiToken.token_hash == _hash_token(raw_token))
    )
    if not record or record.revoked_at is not None:
        return None
    expires_at = record.expires_at
    if expires_at is not None:
        # SQLite (dev only; production uses PostgreSQL) doesn't round-trip
        # tzinfo on DateTime(timezone=True) columns, so a value read back can
        # come back naive even though it was written as UTC-aware.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utcnow():
            return None
    return record


def require_api_token(view):
    """Route decorator: populates g.api_user / g.api_token or returns 401."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        raw_token = _extract_bearer_token()
        record = _load_token_record(raw_token)
        if not record or not record.user or not record.user.is_active_account:
            return jsonify(error="unauthorized", message="Missing or invalid API token."), 401

        g.api_token = record
        g.api_user = record.user
        try:
            return view(*args, **kwargs)
        finally:
            # Best-effort last_used_at bump; never fail the request over this.
            try:
                record.last_used_at = utcnow()
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to update api_token.last_used_at")

    return wrapped
