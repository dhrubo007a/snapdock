"""Initial schema — baseline for Alembic tracking.

For fresh installs: creates all tables.
For existing installs (tables created by init_db / create_all):
the entrypoint script stamps this revision so the migration is skipped.

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing = inspector.get_table_names()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("username", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("hashed_password", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("username"),
            sa.UniqueConstraint("email"),
        )

    if "api_keys" not in existing:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("key_hash", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key_hash"),
        )

    if "snapshots" not in existing:
        op.create_table(
            "snapshots",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("stack_name", sa.String(), nullable=False),
            sa.Column("stack_type", sa.String(), nullable=False),
            sa.Column("stack_state", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=True),
            sa.Column("tags", sa.Text(), nullable=False),
            sa.Column("locked", sa.Boolean(), nullable=False),
            sa.Column("trigger_type", sa.String(), nullable=False),
            sa.Column("triggered_by", sa.String(), nullable=False),
            sa.Column("generated_at", sa.DateTime(), nullable=False),
            sa.Column("finalized_at", sa.DateTime(), nullable=True),
            sa.Column("complete", sa.Boolean(), nullable=False),
            sa.Column("manifest_path", sa.String(), nullable=False),
            sa.Column("storage_path", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("verified", sa.Boolean(), nullable=True),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_snapshots_stack_name", "snapshots", ["stack_name"])

    if "audit_log" not in existing:
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("target_stack", sa.String(), nullable=True),
            sa.Column("target_snapshot", sa.String(), nullable=True),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])

    if "schedules" not in existing:
        op.create_table(
            "schedules",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("stack_name", sa.String(), nullable=False),
            sa.Column("cron_expression", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("retention_manual_count", sa.Integer(), nullable=False),
            sa.Column("retention_daily_days", sa.Integer(), nullable=False),
            sa.Column("retention_weekly_weeks", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("stack_name"),
        )

    if "system_config" not in existing:
        op.create_table(
            "system_config",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )

    if "revoked_tokens" not in existing:
        op.create_table(
            "revoked_tokens",
            sa.Column("jti", sa.String(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("jti"),
        )


def downgrade() -> None:
    op.drop_table("revoked_tokens")
    op.drop_table("system_config")
    op.drop_table("schedules")
    op.drop_table("audit_log")
    op.drop_table("snapshots")
    op.drop_table("api_keys")
    op.drop_table("users")
