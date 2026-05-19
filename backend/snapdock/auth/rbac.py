"""Role-Based Access Control.

Roles (ascending privilege):
  viewer   — read-only: view stacks, history, manifests, audit log
  operator — viewer + trigger snapshots, view diagnostics
  admin    — full access: restore, delete, schedule, config, user management
"""
from __future__ import annotations

from fastapi import HTTPException, status

_ROLE_LEVELS = {"viewer": 0, "operator": 1, "admin": 2}


def require_role(current_role: str, minimum_role: str) -> None:
    """Raise HTTP 403 if *current_role* is below *minimum_role*."""
    current_level = _ROLE_LEVELS.get(current_role, -1)
    required_level = _ROLE_LEVELS.get(minimum_role, 99)
    if current_level < required_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{minimum_role}' required (current: '{current_role}')",
        )


def is_admin(role: str) -> bool:
    return role == "admin"


def is_operator_or_above(role: str) -> bool:
    return _ROLE_LEVELS.get(role, -1) >= _ROLE_LEVELS["operator"]
