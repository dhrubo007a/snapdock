"""Restore routes — trigger restore and dry-run verification."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.config import settings
from snapdock.core import dry_run_registry
from snapdock.core.restore import RestoreEngine
from snapdock.database import AuditLog, Snapshot, get_db
from snapdock.docker_client import get_docker_client
from snapdock.events import event_bus
from snapdock.models.manifest import Manifest
from snapdock.models.schemas import ActiveDryRunResponse, DryRunPortBinding, RestoreRequest

router = APIRouter(prefix="/stacks/{stack_name}/snapshots", tags=["restore"])


@router.post("/{snapshot_id}/restore", status_code=status.HTTP_202_ACCEPTED)
async def trigger_restore(
    stack_name: str,
    snapshot_id: str,
    body: RestoreRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Only admins can restore; dry-run is operator+
    if body.dry_run:
        require_role(current_user.role, "operator")
    else:
        require_role(current_user.role, "admin")

    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if not snap.complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot restore from an incomplete snapshot",
        )

    manifest_path = Path(snap.manifest_path)
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest file not found on disk")

    manifest = Manifest.load(manifest_path)

    # For DEGRADED/BROKEN snapshots or destructive restore: require confirmation
    needs_confirmation = (
        not body.dry_run
        and not body.confirmed
    )
    if needs_confirmation:
        # Compute approximate data loss window
        loss_since = snap.finalized_at or snap.generated_at
        loss_delta = datetime.utcnow() - loss_since
        hours, remainder = divmod(int(loss_delta.total_seconds()), 3600)
        minutes = remainder // 60
        return {
            "requires_confirmation": True,
            "snapshot_id": snapshot_id,
            "snapshot_state": snap.stack_state,
            "data_loss_window": f"{hours}h {minutes}m",
            "message": (
                f"This will overwrite all data for '{stack_name}' since "
                f"{loss_since.isoformat()}Z (~{hours}h {minutes}m of changes). "
                "Re-submit with confirmed=true to proceed."
            ),
        }

    docker = get_docker_client()

    def _run_restore():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            engine = RestoreEngine(docker, db, event_bus, settings)
            success = loop.run_until_complete(
                engine.run(
                    manifest=manifest,
                    triggered_by=current_user.email,
                    dry_run=body.dry_run,
                )
            )
            action = "RESTORE_DRYRUN" if body.dry_run else "RESTORE"
            db.add(
                AuditLog(
                    actor=current_user.email,
                    action=action,
                    target_stack=stack_name,
                    target_snapshot=snapshot_id,
                    outcome="SUCCESS" if success else "FAILED",
                )
            )
            if body.dry_run:
                snap.verified = success
                snap.verified_at = datetime.utcnow() if success else None
            db.commit()
        except Exception as exc:
            db.add(
                AuditLog(
                    actor=current_user.email,
                    action="RESTORE",
                    target_stack=stack_name,
                    target_snapshot=snapshot_id,
                    outcome="FAILED",
                    detail=str(exc),
                )
            )
            db.commit()
        finally:
            loop.close()

    background_tasks.add_task(_run_restore)
    return {
        "accepted": True,
        "stack_name": stack_name,
        "snapshot_id": snapshot_id,
        "dry_run": body.dry_run,
    }


@router.post("/{snapshot_id}/verify", status_code=status.HTTP_202_ACCEPTED)
async def verify_snapshot(
    stack_name: str,
    snapshot_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Trigger a dry-run restore verification for this snapshot."""
    require_role(current_user.role, "operator")

    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    manifest_path = Path(snap.manifest_path)
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest file not found")

    manifest = Manifest.load(manifest_path)
    docker = get_docker_client()

    def _run_verify():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            engine = RestoreEngine(docker, db, event_bus, settings)
            success = loop.run_until_complete(
                engine.run(manifest=manifest, triggered_by=current_user.email, dry_run=True)
            )
            snap.verified = success
            snap.verified_at = datetime.utcnow()
            db.add(
                AuditLog(
                    actor=current_user.email,
                    action="VERIFY",
                    target_stack=stack_name,
                    target_snapshot=snapshot_id,
                    outcome="SUCCESS" if success else "FAILED",
                )
            )
            db.commit()
        finally:
            loop.close()

    background_tasks.add_task(_run_verify)
    return {"accepted": True, "snapshot_id": snapshot_id}


# --------------------------------------------------------------------------- #
# Active dry-run management                                                     #
# --------------------------------------------------------------------------- #

dry_run_router = APIRouter(tags=["dry-run"])


def _entry_to_response(entry: dry_run_registry.DryRunEntry) -> ActiveDryRunResponse:
    ports = {
        svc: [DryRunPortBinding(**b) for b in bindings]
        for svc, bindings in entry.dry_run_ports.items()
    }
    return ActiveDryRunResponse(
        snapshot_id=entry.snapshot_id,
        stack_name=entry.stack_name,
        restore_suffix=entry.restore_suffix,
        dry_run_ports=ports,
        started_at=entry.started_at,
        expires_at=entry.expires_at,
    )


@dry_run_router.get("/dry-runs", response_model=list[ActiveDryRunResponse])
async def list_dry_runs(current_user=Depends(get_current_user)):
    """List all currently active dry-run environments (operator+)."""
    require_role(current_user.role, "operator")
    return [_entry_to_response(e) for e in dry_run_registry.list_all()]


@dry_run_router.get("/stacks/{stack_name}/dry-run", response_model=ActiveDryRunResponse)
async def get_dry_run(
    stack_name: str,
    current_user=Depends(get_current_user),
):
    """Get the active dry-run environment for a specific stack (operator+)."""
    require_role(current_user.role, "operator")
    entry = dry_run_registry.get(stack_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="No active dry-run for this stack")
    return _entry_to_response(entry)


@dry_run_router.delete(
    "/stacks/{stack_name}/dry-run",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def teardown_dry_run(
    stack_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Tear down the active dry-run environment for a stack (operator+)."""
    require_role(current_user.role, "operator")
    entry = dry_run_registry.remove(stack_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="No active dry-run for this stack")

    snap = db.query(Snapshot).filter_by(id=entry.snapshot_id).first()
    if snap is None or not snap.manifest_path:
        # Snapshot or manifest gone — nothing to clean up in Docker
        return

    manifest_path = Path(snap.manifest_path)
    if not manifest_path.exists():
        return

    manifest = Manifest.load(manifest_path)
    docker = get_docker_client()

    def _do_teardown():
        engine = RestoreEngine(docker, db, event_bus, settings)
        engine._teardown_dry_run(manifest, entry.restore_suffix)

    await asyncio.to_thread(_do_teardown)
    db.add(
        AuditLog(
            actor=current_user.email,
            action="RESTORE_DRYRUN_TEARDOWN",
            target_stack=stack_name,
            target_snapshot=entry.snapshot_id,
            outcome="SUCCESS",
        )
    )
    db.commit()
