"""Schedule management routes."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.core.classifier import ContainerClassifier
from snapdock.core.snapshot import SnapshotEngine
from snapdock.database import Schedule, get_db
from snapdock.docker_client import get_docker_client
from snapdock.models.schemas import ScheduleResponse, ScheduleUpsert
from snapdock.scheduler import get_scheduler

router = APIRouter(tags=["schedule"])

# ── List all schedules ────────────────────────────────────────────────────────

@router.get("/stacks/schedules", response_model=list[ScheduleResponse])
def list_schedules(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Return all configured snapshot schedules across all stacks."""
    return db.query(Schedule).order_by(Schedule.stack_name).all()


# ── Per-stack CRUD ────────────────────────────────────────────────────────────

@router.get("/stacks/{stack_name}/schedule", response_model=ScheduleResponse | None)
def get_schedule(
    stack_name: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return db.query(Schedule).filter_by(stack_name=stack_name).first()


@router.put("/stacks/{stack_name}/schedule", response_model=ScheduleResponse)
def upsert_schedule(
    stack_name: str,
    body: ScheduleUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")

    sched = db.query(Schedule).filter_by(stack_name=stack_name).first()
    if sched is None:
        sched = Schedule(
            id=str(uuid.uuid4()),
            stack_name=stack_name,
        )
        db.add(sched)

    sched.cron_expression = body.cron_expression
    sched.is_active = body.is_active
    sched.retention_manual_count = body.retention_manual_count
    sched.retention_daily_days = body.retention_daily_days
    sched.retention_weekly_weeks = body.retention_weekly_weeks
    sched.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sched)

    # Register / update APScheduler job
    _register_schedule_job(stack_name, body.cron_expression, body.is_active)

    return sched


@router.delete("/stacks/{stack_name}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    stack_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    sched = db.query(Schedule).filter_by(stack_name=stack_name).first()
    if sched:
        db.delete(sched)
        db.commit()
    # Remove APScheduler job
    scheduler = get_scheduler()
    job_id = f"snapshot_{stack_name}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


# --------------------------------------------------------------------------- #
# Scheduler integration                                                         #
# --------------------------------------------------------------------------- #

def _register_schedule_job(stack_name: str, cron_expr: str, is_active: bool) -> None:
    from apscheduler.triggers.cron import CronTrigger

    scheduler = get_scheduler()
    job_id = f"snapshot_{stack_name}"

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if not is_active:
        return

    # Parse cron expression (5-field standard cron)
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cron_expression must be a 5-field cron string (minute hour day month weekday)",
        )

    minute, hour, day, month, day_of_week = parts
    trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="UTC",
    )

    scheduler.add_job(
        _run_scheduled_snapshot,
        trigger=trigger,
        id=job_id,
        args=[stack_name],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


async def _run_scheduled_snapshot(stack_name: str) -> None:
    """Scheduled snapshot job — called by APScheduler."""
    import logging

    from snapdock.config import settings
    from snapdock.database import AuditLog, SessionLocal, Snapshot
    from snapdock.events import event_bus

    logger = logging.getLogger(__name__)
    docker = get_docker_client()
    db = SessionLocal()

    try:
        classifier = ContainerClassifier(docker)
        stack = classifier.get_stack(stack_name)
        if stack is None:
            logger.warning("Scheduled snapshot: stack '%s' not found", stack_name)
            return

        engine = SnapshotEngine(docker, db, event_bus, settings)
        manifest = await engine.run(
            stack=stack,
            triggered_by="system",
            trigger_type="auto",
        )

        from snapdock.core.retention import apply_retention
        from pathlib import Path

        size = (
            sum(
                f.stat().st_size
                for f in Path(manifest.storage.path).rglob("*")
                if f.is_file()
            )
            if manifest.storage.path and Path(manifest.storage.path).exists()
            else None
        )
        snap_row = Snapshot(
            id=manifest.snapshot_id,
            stack_name=manifest.stack.name,
            stack_type=manifest.stack.type,
            stack_state=manifest.stack.stack_state,
            label=manifest.label,
            trigger_type="auto",
            triggered_by="system",
            generated_at=manifest.generated_at,
            finalized_at=manifest.finalized_at,
            complete=manifest.complete,
            manifest_path=str(Path(manifest.storage.path) / "manifest.json"),
            storage_path=manifest.storage.path,
            size_bytes=size,
        )
        snap_row.set_tags(manifest.tags)
        db.add(snap_row)
        db.add(
            AuditLog(
                actor="system",
                action="SCHEDULED_SNAP",
                target_stack=stack_name,
                target_snapshot=manifest.snapshot_id,
                outcome="SUCCESS" if manifest.complete else "FAILED",
            )
        )
        db.commit()
        if manifest.complete:
            apply_retention(db, stack_name)
    except Exception as exc:
        logger.error("Scheduled snapshot for '%s' failed: %s", stack_name, exc)
        db.add(
            AuditLog(
                actor="system",
                action="SCHEDULED_SNAP",
                target_stack=stack_name,
                outcome="FAILED",
                detail=str(exc),
            )
        )
        db.commit()
    finally:
        db.close()
