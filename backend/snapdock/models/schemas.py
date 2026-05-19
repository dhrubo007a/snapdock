"""Pydantic schemas for API request and response bodies."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# --------------------------------------------------------------------------- #
# Auth                                                                          #
# --------------------------------------------------------------------------- #

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


# --------------------------------------------------------------------------- #
# Users                                                                         #
# --------------------------------------------------------------------------- #

class UserCreate(BaseModel):
    username: str
    email: str
    password: str = Field(..., min_length=8)
    role: Literal["viewer", "operator", "admin"] = "viewer"


class UserUpdate(BaseModel):
    username: str | None = None
    role: Literal["viewer", "operator", "admin"] | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: str
    username: str | None
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str | None = None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyResponse):
    """Returned only on creation — includes the raw key (shown once)."""
    raw_key: str


# --------------------------------------------------------------------------- #
# Stacks                                                                        #
# --------------------------------------------------------------------------- #

class ContainerSummary(BaseModel):
    id: str
    name: str
    image: str
    status: str


class StackResponse(BaseModel):
    name: str
    type: str                    # compose | interconnected | solo
    containers: list[ContainerSummary]
    health_state: str            # CLEAN | DEGRADED | BROKEN
    compose_files: list[str]
    inferred_reason: str | None
    last_snapshot_at: datetime | None
    last_snapshot_state: str | None
    last_verified_at: datetime | None
    has_schedule: bool
    snapshot_protected: bool = False  # True when this stack contains the SnapDock daemon itself
    coupled_stacks: list[str] = []   # other compose stacks sharing a network/volume with this one


# --------------------------------------------------------------------------- #
# Snapshots                                                                     #
# --------------------------------------------------------------------------- #

class SnapshotTriggerRequest(BaseModel):
    label: str | None = None
    tags: list[str] = Field(default_factory=list)
    # For DEGRADED/BROKEN: client must pass confirmed=True after seeing the modal
    confirmed: bool = False


class SnapshotResponse(BaseModel):
    id: str
    stack_name: str
    stack_type: str
    stack_state: str
    label: str | None
    tags: list[str]
    locked: bool
    trigger_type: str
    triggered_by: str
    generated_at: datetime
    finalized_at: datetime | None
    complete: bool
    size_bytes: int | None
    verified: bool | None
    verified_at: datetime | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_tags(cls, obj: Any) -> "SnapshotResponse":
        data = {c.key: getattr(obj, c.key) for c in obj.__table__.columns}
        data["tags"] = obj.get_tags()
        return cls(**data)


class SnapshotLockRequest(BaseModel):
    locked: bool


class SnapshotLabelRequest(BaseModel):
    label: str | None = None
    tags: list[str] | None = None


# --------------------------------------------------------------------------- #
# Restore                                                                       #
# --------------------------------------------------------------------------- #

class RestoreRequest(BaseModel):
    # Client must pass confirmed=True after seeing the data-loss modal
    confirmed: bool = False
    dry_run: bool = False


# --------------------------------------------------------------------------- #
# Schedule                                                                      #
# --------------------------------------------------------------------------- #

class ScheduleUpsert(BaseModel):
    cron_expression: str
    is_active: bool = True
    retention_manual_count: int = Field(default=5, ge=1)
    retention_daily_days: int = Field(default=7, ge=0)
    retention_weekly_weeks: int = Field(default=4, ge=0)


class ScheduleResponse(BaseModel):
    id: str
    stack_name: str
    cron_expression: str
    is_active: bool
    retention_manual_count: int
    retention_daily_days: int
    retention_weekly_weeks: int
    updated_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Audit                                                                         #
# --------------------------------------------------------------------------- #

class AuditLogEntry(BaseModel):
    id: int
    timestamp: datetime
    actor: str
    action: str
    target_stack: str | None
    target_snapshot: str | None
    outcome: str
    detail: str | None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Settings                                                                      #
# --------------------------------------------------------------------------- #

class SettingsGeneral(BaseModel):
    jwt_expire_minutes: int = Field(1440, ge=5, le=43200)
    quiesce_timeout: int = Field(30, ge=5, le=300)
    health_check_timeout: int = Field(120, ge=10, le=600)
    stop_timeout: int = Field(30, ge=5, le=300)
    # Per-service quiesce method overrides: {service_name: method}
    # Valid methods: auto | postgresql_checkpoint | mysql_flush_tables |
    #                redis_bgsave | mongodb_fsynclock | skip
    quiesce_overrides: dict[str, str] = Field(default_factory=dict)


class SettingsNotificationsRead(BaseModel):
    webhook_urls: list[str] = []
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password_configured: bool = False
    smtp_from: str = ""
    smtp_to: list[str] = []


class SettingsNotificationsWrite(BaseModel):
    webhook_urls: list[str] = []
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""   # omit or empty = keep existing
    smtp_from: str = ""
    smtp_to: list[str] = []


class SettingsResponse(BaseModel):
    general: SettingsGeneral
    notifications: SettingsNotificationsRead


class SettingsPatch(BaseModel):
    general: SettingsGeneral | None = None
    notifications: SettingsNotificationsWrite | None = None


# --------------------------------------------------------------------------- #
# Change password                                                               #
# --------------------------------------------------------------------------- #

class ChangePasswordRequest(BaseModel):
    current_password: str | None = None   # required when changing own password
    new_password: str = Field(..., min_length=8)


# --------------------------------------------------------------------------- #
# Coverage dashboard                                                            #
# --------------------------------------------------------------------------- #

class CoverageRow(BaseModel):
    stack_name: str
    last_clean_snap_at: datetime | None
    schedule_cron: str | None
    last_verified_at: datetime | None
    status: str   # covered | overdue | unprotected


class CoverageDashboard(BaseModel):
    rows: list[CoverageRow]
    total: int
    protected: int
    overdue: int
    unprotected: int
