"""Export and import snapshot archives.

Export
------
``GET /stacks/{stack_name}/snapshots/{snapshot_id}/export``

Streams a .tar.gz of the entire snapshot directory (manifest + volumes +
config + diagnostics, all already AES-encrypted). The archive is safe to
transfer or store anywhere without further encryption.

Import
------
``POST /snapshots/import``

Accepts a multipart file upload of a previously exported archive, extracts
it into the storage path, reads the embedded manifest, and registers the
snapshot in the database.  Admin only.
"""
from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.config import settings
from snapdock.database import AuditLog, Snapshot, get_db
from snapdock.models.manifest import Manifest
from snapdock.models.schemas import SnapshotResponse

router = APIRouter(tags=["export-import"])


# --------------------------------------------------------------------------- #
# All snapshots (global)                                                        #
# --------------------------------------------------------------------------- #

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


@router.get("/snapshots", response_model=list[SnapshotResponse], tags=["snapshots"])
def list_all_snapshots(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """List every snapshot across all stacks, newest first."""
    snaps = db.query(Snapshot).order_by(Snapshot.generated_at.desc()).offset(skip).limit(limit).all()
    return [_snap_to_response(s) for s in snaps]


# --------------------------------------------------------------------------- #
# Export                                                                        #
# --------------------------------------------------------------------------- #

@router.get("/stacks/{stack_name}/snapshots/{snapshot_id}/export")
def export_snapshot(
    stack_name: str,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    snap = db.query(Snapshot).filter_by(
        id=snapshot_id, stack_name=stack_name
    ).first()
    if snap is None or not snap.complete:
        raise HTTPException(status_code=404, detail="Snapshot not found or incomplete")

    storage = Path(snap.storage_path) if snap.storage_path else None
    if storage is None or not storage.exists():
        raise HTTPException(status_code=404, detail="Snapshot data directory not found")

    archive_name = f"{stack_name}_{snapshot_id}.tar.gz"

    def _generate():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(str(storage), arcname=snapshot_id)
        buf.seek(0)
        yield from _chunked(buf)

    db.add(AuditLog(
        actor=current_user.email,
        action="EXPORT",
        target_stack=stack_name,
        target_snapshot=snapshot_id,
        outcome="SUCCESS",
    ))
    db.commit()

    return StreamingResponse(
        _generate(),
        media_type="application/x-tar",
        headers={"Content-Disposition": f'attachment; filename="{archive_name}"'},
    )


def _chunked(fileobj: io.BytesIO, chunk: int = 65536):
    while True:
        data = fileobj.read(chunk)
        if not data:
            break
        yield data


# --------------------------------------------------------------------------- #
# Import                                                                        #
# --------------------------------------------------------------------------- #

@router.post("/snapshots/import", status_code=status.HTTP_201_CREATED)
async def import_snapshot(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")

    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File must be a .tar.gz snapshot archive",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="snapdock_import_"))
    try:
        # Write upload to temp file
        tmp_archive = tmp_dir / "upload.tar.gz"
        with tmp_archive.open("wb") as fh:
            while chunk := await file.read(65536):
                fh.write(chunk)

        # Peek at the archive to find the manifest
        with tarfile.open(tmp_archive, "r:gz") as tf:
            members = tf.getnames()
            # Expect structure: <snapshot_id>/manifest.json
            manifest_members = [m for m in members if m.endswith("/manifest.json") or m == "manifest.json"]
            if not manifest_members:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Archive does not contain a manifest.json",
                )
            manifest_member = sorted(manifest_members, key=len)[0]
            snap_id = manifest_member.split("/")[0] if "/" in manifest_member else "imported"

            # Check for duplicate
            if db.query(Snapshot).filter_by(id=snap_id).first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Snapshot {snap_id} already exists",
                )

            # Extract into storage path
            stack_name_guess = snap_id  # fallback before manifest is read
            extract_root = settings.storage_path
            tf.extractall(path=str(extract_root))  # noqa: S202 — admin-only, controlled archive

        # Read the extracted manifest
        manifest_path = extract_root / manifest_member
        if not manifest_path.exists():
            raise HTTPException(status_code=500, detail="Manifest extraction failed")

        manifest = Manifest.load(manifest_path)
        stack_name_guess = manifest.stack.name
        snap_storage = extract_root / snap_id

        size = sum(
            f.stat().st_size for f in snap_storage.rglob("*") if f.is_file()
        )

        # Move to correct per-stack location if needed
        correct_dir = settings.storage_path / stack_name_guess / snap_id
        if snap_storage != correct_dir:
            correct_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(snap_storage), str(correct_dir))

        # Register in DB
        snap_row = Snapshot(
            id=manifest.snapshot_id,
            stack_name=manifest.stack.name,
            stack_type=manifest.stack.type,
            stack_state=manifest.stack.stack_state,
            label=manifest.label,
            trigger_type=getattr(manifest, "trigger_type", "imported"),
            triggered_by=getattr(manifest, "triggered_by", current_user.email),
            generated_at=manifest.generated_at,
            finalized_at=manifest.finalized_at,
            complete=manifest.complete,
            manifest_path=str(correct_dir / "manifest.json"),
            storage_path=str(correct_dir),
            size_bytes=size,
            verified=False,
        )
        snap_row.set_tags(manifest.tags or [])
        db.add(snap_row)
        db.add(AuditLog(
            actor=current_user.email,
            action="IMPORT",
            target_stack=manifest.stack.name,
            target_snapshot=manifest.snapshot_id,
            outcome="SUCCESS",
        ))
        db.commit()

        return {
            "snapshot_id": manifest.snapshot_id,
            "stack_name": manifest.stack.name,
            "size_bytes": size,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
