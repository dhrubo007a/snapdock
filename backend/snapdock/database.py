"""SQLAlchemy engine, session factory, and ORM models."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from snapdock.config import settings


# --------------------------------------------------------------------------- #
# Engine                                                                        #
# --------------------------------------------------------------------------- #

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # detect stale connections
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# --------------------------------------------------------------------------- #
# Base                                                                          #
# --------------------------------------------------------------------------- #

class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# ORM Models                                                                    #
# --------------------------------------------------------------------------- #

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # viewer | operator | admin
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey", back_populates="user", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship("User", back_populates="api_keys")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    stack_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    stack_type: Mapped[str] = mapped_column(String, nullable=False)   # compose | interconnected | solo
    stack_state: Mapped[str] = mapped_column(String, nullable=False)  # CLEAN | DEGRADED | BROKEN
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON array
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trigger_type: Mapped[str] = mapped_column(String, nullable=False)  # manual | auto
    triggered_by: Mapped[str] = mapped_column(String, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manifest_path: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def get_tags(self) -> list[str]:
        return json.loads(self.tags)

    def set_tags(self, tags: list[str]) -> None:
        self.tags = json.dumps(tags)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_stack: Mapped[str | None] = mapped_column(String, nullable=True)
    target_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    stack_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    cron_expression: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retention_manual_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    retention_daily_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    retention_weekly_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RevokedToken(Base):
    """Blocklist of revoked JWT IDs so logout is honoured server-side."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SystemConfig(Base):
    """Single-row-per-key config table.  Used for setup state and one-time tokens."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def init_db() -> None:
    """Create all tables and run lightweight column migrations."""
    Base.metadata.create_all(bind=engine)
    # Inline column migrations for existing installations
    with engine.connect() as conn:
        # username column (added in v0.2)
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR UNIQUE"))
            conn.execute(text("UPDATE users SET username = email WHERE username IS NULL"))
            conn.commit()
        except Exception:
            conn.rollback()
        # failed_logins column (added in v0.3)
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
        except Exception:
            conn.rollback()
        # locked_until column (added in v0.3)
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP"))
            conn.commit()
        except Exception:
            conn.rollback()

        # key_prefix column (added in v1.1)
        try:
            conn.execute(text("ALTER TABLE api_keys ADD COLUMN key_prefix VARCHAR"))
            conn.commit()
        except Exception:
            conn.rollback()


def get_db():
    """FastAPI dependency: yield a database session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
