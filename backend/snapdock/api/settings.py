"""Mutable runtime settings stored in the ``system_config`` table.

GET   /settings   → full config object  (admin only)
PATCH /settings   → update config       (admin only)

Settings keys in the DB use the prefix  ``setting.``  so they live alongside
the onboarding keys without clashing.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.config import settings as app_settings
from snapdock.database import SystemConfig, get_db
from snapdock.models.schemas import (
    SettingsGeneral,
    SettingsNotificationsRead,
    SettingsPatch,
    SettingsResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)

_PREFIX = "setting."


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _get(db: Session, key: str, default: str = "") -> str:
    row = db.query(SystemConfig).filter_by(key=_PREFIX + key).first()
    return row.value if row else default


def _set(db: Session, key: str, value: str) -> None:
    full_key = _PREFIX + key
    row = db.query(SystemConfig).filter_by(key=full_key).first()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(SystemConfig(key=full_key, value=value))


def _build_response(db: Session) -> SettingsResponse:
    general = SettingsGeneral(
        jwt_expire_minutes=int(_get(db, "general.jwt_expire_minutes",
                                    str(app_settings.jwt_expire_minutes))),
        quiesce_timeout=int(_get(db, "general.quiesce_timeout",
                                  str(app_settings.quiesce_timeout))),
        health_check_timeout=int(_get(db, "general.health_check_timeout",
                                      str(app_settings.health_check_timeout))),
        stop_timeout=int(_get(db, "general.stop_timeout",
                               str(app_settings.stop_timeout))),
    )

    wh_raw = _get(db, "notifications.webhook_urls", "")
    to_raw = _get(db, "notifications.smtp_to", "")
    smtp_pwd = _get(db, "notifications.smtp_password", "")

    notifications = SettingsNotificationsRead(
        webhook_urls=[u.strip() for u in wh_raw.split(",") if u.strip()],
        smtp_host=_get(db, "notifications.smtp_host", ""),
        smtp_port=int(_get(db, "notifications.smtp_port", "587")),
        smtp_user=_get(db, "notifications.smtp_user", ""),
        smtp_password_configured=bool(smtp_pwd),
        smtp_from=_get(db, "notifications.smtp_from", ""),
        smtp_to=[r.strip() for r in to_raw.split(",") if r.strip()],
    )

    return SettingsResponse(general=general, notifications=notifications)


# --------------------------------------------------------------------------- #
# Public helper for other modules                                               #
# --------------------------------------------------------------------------- #

def get_notification_config(db: Session) -> dict:
    """Return notification settings, preferring DB values over env vars."""
    import os
    wh_raw = _get(db, "notifications.webhook_urls",
                  os.getenv("SNAPDOCK_WEBHOOK_URL", ""))
    to_raw = _get(db, "notifications.smtp_to",
                  os.getenv("SNAPDOCK_SMTP_TO", ""))
    return {
        "webhook_urls": [u.strip() for u in wh_raw.split(",") if u.strip()],
        "smtp_host":    _get(db, "notifications.smtp_host",
                             os.getenv("SNAPDOCK_SMTP_HOST", "")),
        "smtp_port":    int(_get(db, "notifications.smtp_port",
                                 os.getenv("SNAPDOCK_SMTP_PORT", "587"))),
        "smtp_user":    _get(db, "notifications.smtp_user",
                             os.getenv("SNAPDOCK_SMTP_USER", "")),
        "smtp_password": _get(db, "notifications.smtp_password",
                              os.getenv("SNAPDOCK_SMTP_PASSWORD", "")),
        "smtp_from":    _get(db, "notifications.smtp_from",
                             os.getenv("SNAPDOCK_SMTP_FROM", "")),
        "smtp_to":      [r.strip() for r in to_raw.split(",") if r.strip()],
    }


# --------------------------------------------------------------------------- #
# Routes                                                                        #
# --------------------------------------------------------------------------- #

@router.get("", response_model=SettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SettingsResponse:
    require_role(current_user.role, "admin")
    return _build_response(db)


@router.patch("", response_model=SettingsResponse)
def patch_settings(
    body: SettingsPatch,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SettingsResponse:
    require_role(current_user.role, "admin")

    if body.general:
        g = body.general
        _set(db, "general.jwt_expire_minutes", str(g.jwt_expire_minutes))
        _set(db, "general.quiesce_timeout",    str(g.quiesce_timeout))
        _set(db, "general.health_check_timeout", str(g.health_check_timeout))
        _set(db, "general.stop_timeout",       str(g.stop_timeout))

    if body.notifications:
        n = body.notifications
        _set(db, "notifications.webhook_urls", ",".join(n.webhook_urls))
        _set(db, "notifications.smtp_host",    n.smtp_host)
        _set(db, "notifications.smtp_port",    str(n.smtp_port))
        _set(db, "notifications.smtp_user",    n.smtp_user)
        _set(db, "notifications.smtp_from",    n.smtp_from)
        _set(db, "notifications.smtp_to",      ",".join(n.smtp_to))
        # Only persist password if a new value was supplied
        if n.smtp_password:
            _set(db, "notifications.smtp_password", n.smtp_password)

    db.commit()
    logger.info("Settings updated by %s", current_user.email)
    return _build_response(db)
