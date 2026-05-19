"""API key generation and validation.

API keys are stored as bcrypt hashes.  The raw key (shown once on creation)
is a URL-safe random token prefixed with ``sdck_`` for easy identification.
"""
from __future__ import annotations

import secrets

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from snapdock.database import ApiKey, User

_KEY_PREFIX = "sdck_"
_KEY_BYTES = 32  # 256 bits of entropy

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash).

    *raw_key* must be shown to the user exactly once and never stored.
    *key_hash* is stored in the database.
    """
    raw = _KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
    key_hash = _ctx.hash(raw)
    return raw, key_hash


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    return _ctx.verify(raw_key, key_hash)


def lookup_api_key(db: Session, raw_key: str) -> ApiKey | None:
    """Iterate active API keys and verify against *raw_key*.

    Intentionally O(N) — expected to be a very small table.
    Returns the matching ``ApiKey`` row or ``None``.
    """
    from datetime import datetime

    candidates = (
        db.query(ApiKey)
        .filter_by(is_active=True)
        .all()
    )
    for candidate in candidates:
        if verify_api_key(raw_key, candidate.key_hash):
            # Check expiry
            if candidate.expires_at and candidate.expires_at < datetime.utcnow():
                return None
            # Record last use
            candidate.last_used_at = datetime.utcnow()
            db.commit()
            return candidate
    return None
