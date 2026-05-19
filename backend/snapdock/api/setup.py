"""First-boot onboarding API.

Two public endpoints (no auth required):

  GET  /setup/status    → {"required": bool}
  POST /setup/complete  → {"access_token": str}

Security model
--------------
* A cryptographically random 32-byte *setup token* is generated at first boot
  and printed in plaintext to the daemon log.  Its SHA-256 hash is stored in
  the ``system_config`` table under the key ``setup_token_hash``.

* ``POST /setup/complete`` accepts the plaintext token, hashes it with
  ``secrets.compare_digest`` (constant-time), and proceeds only on match.

* After a successful setup the token row is deleted and
  ``setup_complete = true`` is written.  All subsequent calls return **410
  Gone**.

* An in-process attempt counter caps retries at 10 before requiring a daemon
  restart — prevents online brute-force even though the token space is large.

* The endpoint never reveals *why* a token check failed beyond "Invalid setup
  token" to avoid oracle attacks.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

from snapdock.auth.jwt import create_access_token
from snapdock.database import AuditLog, SessionLocal, SystemConfig, User

router = APIRouter(prefix="/setup", tags=["setup"])
logger = logging.getLogger(__name__)

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-process brute-force guard — resets only on daemon restart.
_attempt_count: int = 0
_MAX_ATTEMPTS: int = 10

_MIN_PASSWORD_LEN = 8


# --------------------------------------------------------------------------- #
# Schemas                                                                       #
# --------------------------------------------------------------------------- #

class SetupRequest(BaseModel):
    token: str
    admin_username: str
    admin_email: EmailStr
    admin_password: str


# --------------------------------------------------------------------------- #
# Routes                                                                        #
# --------------------------------------------------------------------------- #

@router.get("/status")
def setup_status() -> dict:
    """Return whether first-boot setup is still required.  Public, no auth."""
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter_by(key="setup_complete").first()
        return {"required": row is None or row.value != "true"}
    finally:
        db.close()


@router.post("/complete", status_code=status.HTTP_201_CREATED)
def setup_complete(body: SetupRequest) -> dict:
    """One-time endpoint to create the first admin and mark setup done.

    Returns a JWT bearer token for the newly created admin account so the
    frontend can immediately proceed to the dashboard.
    """
    global _attempt_count

    db = SessionLocal()
    try:
        # ── Already complete → permanent 410 ──────────────────────────────
        done_row = db.query(SystemConfig).filter_by(key="setup_complete").first()
        if done_row and done_row.value == "true":
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Setup has already been completed",
            )

        # ── Brute-force guard ─────────────────────────────────────────────
        _attempt_count += 1
        if _attempt_count > _MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed setup attempts — restart the daemon to reset",
            )

        # ── Validate token (constant-time compare) ────────────────────────
        token_row = db.query(SystemConfig).filter_by(key="setup_token_hash").first()
        if token_row is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Setup token not available — check daemon logs",
            )

        submitted_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(submitted_hash, token_row.value):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid setup token",
            )

        # ── Validate password strength ─────────────────────────────────────
        if len(body.admin_password) < _MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
            )

        # ── Create admin user ──────────────────────────────────────────────
        if db.query(User).filter_by(email=body.admin_email).first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with that email already exists",
            )

        admin = User(
            id=str(uuid.uuid4()),
            username=str(body.admin_username),
            email=str(body.admin_email),
            hashed_password=_pwd_ctx.hash(body.admin_password),
            role="admin",
        )
        db.add(admin)

        # ── Mark setup complete ────────────────────────────────────────────
        if done_row:
            done_row.value = "true"
            done_row.updated_at = datetime.utcnow()
        else:
            db.add(SystemConfig(key="setup_complete", value="true"))

        # ── Delete one-time token ──────────────────────────────────────────
        db.delete(token_row)

        # ── Audit ──────────────────────────────────────────────────────────
        db.add(AuditLog(
            actor=str(body.admin_email),
            action="SETUP_COMPLETE",
            outcome="SUCCESS",
            detail="First-boot onboarding completed",
        ))

        db.commit()

        # Reset counter on success
        _attempt_count = 0
        logger.info("First-boot setup completed. Admin account: %s", body.admin_email)

        # Issue a JWT so the frontend can go straight to the dashboard
        access_token = create_access_token(admin.id, str(body.admin_email), "admin")
        return {"access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Setup failed unexpectedly: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Setup failed — check daemon logs",
        )
    finally:
        db.close()
