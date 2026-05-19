"""Retention policy — applied per stack after every successful snapshot.

Default policy (configurable per stack via the Schedule table):
  - Keep last 5 manual snapshots (locked snapshots: never deleted)
  - Keep 1 per day for the last 7 days (auto snapshots)
  - Keep 1 per week for the last 4 weeks (auto snapshots)

Locked snapshots are NEVER touched regardless of age or policy.
The cleanup runs AFTER the new snapshot is confirmed complete.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from snapdock.database import AuditLog, Schedule, Snapshot

logger = logging.getLogger(__name__)


def apply_retention(
    db: Session,
    stack_name: str,
) -> list[str]:
    """Apply the retention policy for *stack_name*.

    Returns the list of snapshot IDs that were deleted.
    """
    schedule = db.query(Schedule).filter_by(stack_name=stack_name).first()

    if schedule:
        manual_keep = schedule.retention_manual_count
        daily_days = schedule.retention_daily_days
        weekly_weeks = schedule.retention_weekly_weeks
    else:
        manual_keep = 5
        daily_days = 7
        weekly_weeks = 4

    all_snaps = (
        db.query(Snapshot)
        .filter_by(stack_name=stack_name, complete=True)
        .order_by(Snapshot.generated_at.desc())
        .all()
    )

    to_keep: set[str] = set()

    # ── Manual snapshots: keep last N ───────────────────────────────── #
    manual_snaps = [s for s in all_snaps if s.trigger_type == "manual"]
    for snap in manual_snaps[:manual_keep]:
        to_keep.add(snap.id)

    # ── Auto snapshots: 1-per-day for last N days ────────────────────── #
    now = datetime.utcnow()
    daily_seen: set[str] = set()
    for snap in all_snaps:
        if snap.trigger_type != "auto":
            continue
        age = now - snap.generated_at
        if age > timedelta(days=daily_days):
            break
        day_key = snap.generated_at.strftime("%Y-%m-%d")
        if day_key not in daily_seen:
            daily_seen.add(day_key)
            to_keep.add(snap.id)

    # ── Auto snapshots: 1-per-week for last N weeks ──────────────────── #
    weekly_seen: set[str] = set()
    for snap in all_snaps:
        if snap.trigger_type != "auto":
            continue
        age = now - snap.generated_at
        if age > timedelta(weeks=weekly_weeks):
            break
        week_key = snap.generated_at.strftime("%G-W%V")
        if week_key not in weekly_seen:
            weekly_seen.add(week_key)
            to_keep.add(snap.id)

    # ── Delete everything not in to_keep (except locked) ─────────────── #
    deleted: list[str] = []
    for snap in all_snaps:
        if snap.id in to_keep:
            continue
        if snap.locked:
            continue
        _delete_snapshot(db, snap)
        deleted.append(snap.id)

    if deleted:
        logger.info(
            "Retention cleanup for '%s': deleted %d snapshot(s): %s",
            stack_name,
            len(deleted),
            deleted,
        )

    return deleted


def _delete_snapshot(db: Session, snap: Snapshot) -> None:
    """Remove snapshot files and the database row."""
    storage_path = Path(snap.storage_path)
    if storage_path.exists():
        try:
            shutil.rmtree(storage_path)
        except Exception as exc:
            logger.warning(
                "Could not remove snapshot files at %s: %s", storage_path, exc
            )
    db.delete(snap)
    db.commit()
    logger.debug("Deleted snapshot %s", snap.id)
