"""JWT creation and validation for UI session tokens."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from jose import JWTError, jwt
from pydantic import BaseModel

from snapdock.config import settings


class TokenData(BaseModel):
    user_id: str
    email: str
    role: str
    jti: str = ""  # empty string = legacy token without jti


def create_access_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "jti": str(uuid.uuid4()),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenData:
    """Decode and validate a JWT.  Raises ``JWTError`` on invalid/expired token."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    return TokenData(
        user_id=payload["sub"],
        email=payload["email"],
        role=payload["role"],
        jti=payload.get("jti", ""),
    )
