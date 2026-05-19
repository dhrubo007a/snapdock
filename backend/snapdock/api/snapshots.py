"""Snapshot routes — trigger, list, inspect, lock, export."""
from __future__ import annotations

import asyncio
import io
import json
import socket
import tarfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.config import settings
from snapdock.core.classifier import ContainerClassifier
from snapdock.core.health import check_stack_health
from snapdock.core.retention import apply_retention
from snapdock.core.snapshot import SnapshotEngine
from snapdock.database import AuditLog, Snapshot, get_db
from snapdock.docker_client import get_docker_client
from snapdock.events import event_bus
from snapdock.models.manifest import Manifest
from snapdock.models.schemas import (
    SnapshotLabelRequest,
    SnapshotLockRequest,
    SnapshotResponse,
    SnapshotTriggerRequest,
)

router = APIRouter(prefix="/stacks/{stack_name}/snapshots", tags=["snapshots"])


def _snap_to_response(snap: Snapshot) -> SnapshotResponse:
    return SnapshotResponse(
        id=snap.id,
        stack_name=snap.stack_name,
        stack_type=snap.stack_type,
        stack_state=snap.stack_state,
        label=snap.label,
        tags=snap.get_tags(),
        locked=snap.locked,
        trigger_type=snap.trigger_type,
        triggered_by=snap.triggered_by,
        generated_at=snap.generated_at,
        finalized_at=snap.finalized_at,
        complete=snap.complete,
        size_bytes=snap.size_bytes,
        verified=snap.verified,
        verified_at=snap.verified_at,
    )


@router.get("", response_model=list[SnapshotResponse])
def list_snapshots(
    stack_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    snaps = (
        db.query(Snapshot)
        .filter_by(stack_name=stack_name)
        .order_by(Snapshot.generated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_snap_to_response(s) for s in snaps]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def trigger_snapshot(
    stack_name: str,
    body: SnapshotTriggerRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "operator")

    docker = get_docker_client()
    classifier = ContainerClassifier(docker)
    stack = classifier.get_stack(stack_name)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")

    # Block snapshotting the stack that contains the SnapDock daemon itself.
    # The container hostname inside Docker equals the short container ID.
    _self_id = socket.gethostname()
    for _c in stack.containers:
        if _c.short_id == _self_id or _c.id.startswith(_self_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot snapshot the stack that contains the SnapDock daemon. "
                    "This would shut down the running service. "
                    "Use an external backup tool for this stack."
                ),
            )

    # Health pre-check: if DEGRADED/BROKEN, require explicit confirmation
    health = check_stack_health(docker, stack)
    if health.requires_confirmation and not body.confirmed:
        return {
            "requires_confirmation": True,
            "health_state": health.state,
            "stopped": health.stopped,
            "unhealthy": health.unhealthy,
            "message": (
                f"Stack is {health.state}. "
                "Re-submit with confirmed=true to proceed."
            ),
        }

    # Build pre-snapshot detail for the UI confirmation modal
    preview = _build_snapshot_preview(stack, docker)

    def _run_snapshot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            engine = SnapshotEngine(docker, db, event_bus, settings)
            manifest: Manifest = loop.run_until_complete(
                engine.run(
                    stack=stack,
                    triggered_by=current_user.email,
                    trigger_type="manual",
                    label=body.label,
                    tags=body.tags,
                )
            )
            # Persist to DB
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
                trigger_type=manifest.trigger_type,
                triggered_by=manifest.triggered_by,
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
                    actor=current_user.email,
                    action="SNAPSHOT",
                    target_stack=stack_name,
                    target_snapshot=manifest.snapshot_id,
                    outcome="SUCCESS" if manifest.complete else "FAILED",
                )
            )
            db.commit()
            # Apply retention policy
            if manifest.complete:
                apply_retention(db, stack_name)
        except Exception as exc:
            db.add(
                AuditLog(
                    actor=current_user.email,
                    action="SNAPSHOT",
                    target_stack=stack_name,
                    outcome="FAILED",
                    detail=str(exc),
                )
            )
            db.commit()
        finally:
            loop.close()

    background_tasks.add_task(_run_snapshot)
    return {"accepted": True, "stack_name": stack_name, "preview": preview}


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
def get_snapshot(
    stack_name: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return _snap_to_response(snap)


@router.get("/{snapshot_id}/manifest")
def get_manifest(
    stack_name: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    manifest_path = Path(snap.manifest_path)
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest file not found on disk")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@router.patch("/{snapshot_id}/lock")
def set_lock(
    stack_name: str,
    snapshot_id: str,
    body: SnapshotLockRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    snap.locked = body.locked
    db.add(
        AuditLog(
            actor=current_user.email,
            action="LOCK" if body.locked else "UNLOCK",
            target_stack=stack_name,
            target_snapshot=snapshot_id,
            outcome="—",
        )
    )
    db.commit()
    return {"id": snapshot_id, "locked": snap.locked}


@router.patch("/{snapshot_id}/label")
def update_label(
    stack_name: str,
    snapshot_id: str,
    body: SnapshotLabelRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "operator")
    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    if body.label is not None:
        snap.label = body.label
    if body.tags is not None:
        snap.set_tags(body.tags)
    db.commit()
    return {"id": snapshot_id, "label": snap.label, "tags": snap.get_tags()}


@router.delete("/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snapshot(
    stack_name: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    if snap.locked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a locked snapshot. Unlock it first.",
        )
    import shutil

    storage = Path(snap.storage_path)
    if storage.exists():
        shutil.rmtree(storage)
    db.add(
        AuditLog(
            actor=current_user.email,
            action="DELETE_SNAPSHOT",
            target_stack=stack_name,
            target_snapshot=snapshot_id,
            outcome="SUCCESS",
        )
    )
    db.delete(snap)
    db.commit()


# --------------------------------------------------------------------------- #
# Export / Import                                                               #
# --------------------------------------------------------------------------- #

@router.get("/{snapshot_id}/export")
def export_snapshot(
    stack_name: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Stream the entire snapshot directory as a .tar.gz archive for offline transfer."""
    require_role(current_user.role, "admin")

    snap = db.query(Snapshot).filter_by(id=snapshot_id, stack_name=stack_name).first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    storage = Path(snap.storage_path)
    if not storage.exists():
        raise HTTPException(status_code=404, detail="Snapshot data not found on disk")

    def _stream():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(str(storage), arcname=snapshot_id)
        buf.seek(0)
        while True:
            chunk = buf.read(65536)
            if not chunk:
                break
            yield chunk

    filename = f"{snapshot_id}.tar.gz"
    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_snapshot(
    stack_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Accept a .tar.gz exported snapshot archive and register it for this stack."""
    import shutil

    require_role(current_user.role, "admin")

    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="File must be a .tar.gz archive")

    raw = await file.read()

    def _extract() -> tuple[str, Path]:
        buf = io.BytesIO(raw)
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            # The top-level dir in the archive is the snapshot ID
            members = tf.getmembers()
            if not members:
                raise ValueError("Archive is empty")
            snap_id = members[0].name.split("/")[0]
            dest = settings.storage_path / stack_name / snap_id
            if dest.exists():
                raise ValueError(f"Snapshot {snap_id} already exists for stack {stack_name}")
            dest.mkdir(parents=True, exist_ok=True)
            tf.extractall(path=str(settings.storage_path / stack_name))
        return snap_id, dest

    try:
        snap_id, snap_dir = await asyncio.to_thread(_extract)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to extract archive: {exc}")

    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        import shutil as _shutil
        _shutil.rmtree(snap_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Archive is missing manifest.json")

    try:
        from snapdock.models.manifest import Manifest
        manifest = Manifest.load(manifest_path)
    except Exception as exc:
        import shutil as _shutil
        _shutil.rmtree(snap_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Invalid manifest: {exc}")

    # Register in DB if not already present
    existing = db.query(Snapshot).filter_by(id=snap_id).first()
    if not existing:
        size_bytes = sum(f.stat().st_size for f in snap_dir.rglob("*") if f.is_file())
        new_snap = Snapshot(
            id=snap_id,
            stack_name=stack_name,
            stack_type=manifest.stack.type,
            stack_state=manifest.stack.stack_state,
            label=manifest.label or f"Imported from {file.filename}",
            trigger_type=manifest.trigger_type,
            triggered_by=manifest.triggered_by,
            generated_at=manifest.generated_at,
            finalized_at=manifest.finalized_at,
            complete=manifest.complete,
            size_bytes=size_bytes,
            manifest_path=str(manifest_path),
            storage_path=str(snap_dir),
        )
        db.add(new_snap)
        db.add(AuditLog(
            actor=current_user.email,
            action="IMPORT_SNAPSHOT",
            target_stack=stack_name,
            target_snapshot=snap_id,
            outcome="SUCCESS",
        ))
        db.commit()

    return {"imported": True, "snapshot_id": snap_id, "stack_name": stack_name}


# --------------------------------------------------------------------------- #
# Pre-snapshot preview builder                                                  #
# --------------------------------------------------------------------------- #

def _build_snapshot_preview(stack, docker) -> dict:
    """Return a summary of what the snapshot will do — shown in the UI confirmation modal."""
    from snapdock.core.hooks import load_hooks
    import subprocess, shlex

    # Volumes
    volumes = []
    for c in stack.containers:
        try:
            c.reload()
            for m in c.attrs.get("Mounts", []):
                volumes.append({
                    "type": m.get("Type"),
                    "name": m.get("Name") or m.get("Source"),
                    "service": c.labels.get("com.docker.compose.service", c.name),
                    "mount_path": m.get("Destination"),
                })
        except Exception:
            pass

    # Quiesce targets
    quiesce_targets = []
    for c in stack.containers:
        image = c.image.tags[0] if c.image.tags else ""
        for keyword, method in {
            "postgres": "postgresql_checkpoint",
            "mysql": "mysql_flush_tables",
            "mariadb": "mysql_flush_tables",
            "redis": "redis_bgsave",
            "mongo": "mongodb_fsynclock",
        }.items():
            if keyword in image.lower():
                quiesce_targets.append({
                    "service": c.labels.get("com.docker.compose.service", c.name),
                    "method": method,
                })
                break

    # Hooks
    hooks: dict = {}
    if stack.working_dir:
        try:
            hooks = load_hooks(stack.working_dir, stack.name)
        except Exception:
            pass

    pre_hooks = [h.exec for h in hooks.get("pre_snapshot", [])]
    post_hooks = [h.exec for h in hooks.get("post_snapshot", [])]

    return {
        "containers": [c.name for c in stack.containers],
        "volumes": volumes,
        "quiesce_targets": quiesce_targets,
        "pre_hooks": pre_hooks,
        "post_hooks": post_hooks,
        "config_files": stack.compose_files,
    }
