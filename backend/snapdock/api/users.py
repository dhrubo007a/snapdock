"""User management routes — admin only."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.api_keys import generate_api_key
from snapdock.auth.rbac import require_role
from snapdock.database import AuditLog, ApiKey, User, get_db
from snapdock.models.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyResponse,
    ChangePasswordRequest,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.get("", response_model=list[UserResponse])
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    return db.query(User).offset(skip).limit(limit).all()


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that username already exists",
        )
    if db.query(User).filter_by(email=body.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        )
    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        email=body.email,
        hashed_password=_pwd_ctx.hash(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(AuditLog(actor=current_user.email, action="user.create", outcome="success", detail=user.email))
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.username is not None:
        conflict = db.query(User).filter(User.username == body.username, User.id != user_id).first()
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
        user.username = body.username
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    db.add(AuditLog(actor=current_user.email, action="user.update", outcome="success", detail=user.email))
    db.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_email = user.email
    db.delete(user)
    db.commit()
    db.add(AuditLog(actor=current_user.email, action="user.delete", outcome="success", detail=user_email))
    db.commit()


@router.post("/{user_id}/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    user_id: str,
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Change a user's password.

    * An admin can change any user's password without supplying ``current_password``.
    * A user changing their own password must supply a correct ``current_password``.
    """
    is_self = current_user.id == user_id
    is_admin = current_user.role == "admin"

    if not is_self and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Non-admin self-change requires current password verification
    if is_self and not is_admin:
        if not body.current_password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="current_password is required",
            )
        if not _pwd_ctx.verify(body.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
            )

    user.hashed_password = _pwd_ctx.hash(body.new_password)
    user.updated_at = datetime.utcnow()
    db.commit()
    db.add(AuditLog(actor=current_user.email, action="user.change_password", outcome="success", detail=user.email))
    db.commit()


# ── API Keys ──────────────────────────────────────────────────────────────── #

@router.get("/{user_id}/api-keys", response_model=list[ApiKeyResponse])
def list_api_keys(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Users can view their own keys; admins can view any
    if current_user.id != user_id:
        require_role(current_user.role, "admin")
    keys = db.query(ApiKey).filter_by(user_id=user_id, is_active=True).all()
    return keys


@router.post("/{user_id}/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    user_id: str,
    body: ApiKeyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.id != user_id:
        require_role(current_user.role, "admin")
    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    raw_key, key_hash = generate_api_key()
    api_key = ApiKey(
        id=str(uuid.uuid4()),
        key_hash=key_hash,
        key_prefix=raw_key[:12],
        user_id=user_id,
        name=body.name,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        is_active=api_key.is_active,
        raw_key=raw_key,
    )


@router.delete("/{user_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    user_id: str,
    key_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.id != user_id:
        require_role(current_user.role, "admin")
    key = db.query(ApiKey).filter_by(id=key_id, user_id=user_id).first()
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.is_active = False
    db.commit()
