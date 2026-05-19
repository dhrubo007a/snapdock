"""Auth routes — login, logout, and /me."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from sqlalchemy import or_

from snapdock.api.deps import get_current_user, oauth2_scheme
from snapdock.auth.jwt import create_access_token, decode_access_token
from snapdock.config import settings
from snapdock.database import AuditLog, RevokedToken, User, get_db
from snapdock.limiter import limiter
from snapdock.models.schemas import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

_LOCKOUT_THRESHOLD = 5
_LOCKOUT_MINUTES = 15


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    identifier = body.username.strip()
    user = (
        db.query(User)
        .filter(
            or_(User.username == identifier, User.email == identifier),
            User.is_active == True,  # noqa: E712
        )
        .first()
    )

    # Account lockout check
    if user and user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account locked until {user.locked_until.strftime('%Y-%m-%d %H:%M')} UTC",
        )

    # Credential verification
    if not user or not _pwd_ctx.verify(body.password, user.hashed_password):
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= _LOCKOUT_THRESHOLD:
                user.locked_until = datetime.utcnow() + timedelta(minutes=_LOCKOUT_MINUTES)
                user.failed_logins = 0
            db.commit()
        db.add(AuditLog(actor=identifier, action="auth.login", outcome="failure", detail="Invalid credentials"))
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
        )

    # Success — reset lockout counters
    if user.failed_logins or user.locked_until:
        user.failed_logins = 0
        user.locked_until = None
        db.commit()

    token = create_access_token(user.id, user.email, user.role)
    return TokenResponse(access_token=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Revoke the current bearer token so it can no longer be used."""
    if token:
        try:
            token_data = decode_access_token(token)
            if token_data.jti:
                expires_at = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
                db.merge(RevokedToken(jti=token_data.jti, expires_at=expires_at))
                db.commit()
        except Exception:
            pass  # Malformed token — nothing to revoke


@router.get("/me", response_model=UserResponse)
def get_me(current_user=Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user
