"""Snapshot diff — compare config, images, and volume file trees between two snapshots.

``GET /stacks/{stack_name}/snapshots/{snap_id}/diff``

Optional query param ``compare_to`` selects the base snapshot.
Defaults to the immediately preceding completed snapshot.

Volume diff is skipped when any single volume archive exceeds 5 GB uncompressed
(too expensive to diff in the general case).

Returns a JSON object with three sections:
  config_diff  — unified diff of every config file that changed
  image_diff   — per-service image changes
  volume_diff  — per-volume file-level change summary (added/removed/modified)
"""
from __future__ import annotations

import io
import json
import tarfile
from difflib import unified_diff
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.config import settings
from snapdock.core.crypto import decrypt_file
from snapdock.database import Snapshot, get_db
from snapdock.models.manifest import Manifest

router = APIRouter(prefix="/stacks/{stack_name}/snapshots", tags=["diff"])

_VOLUME_DIFF_SIZE_LIMIT = 5 * 1024 ** 3  # 5 GB


@router.get("/{snapshot_id}/diff")
def snapshot_diff(
    stack_name: str,
    snapshot_id: str,
    compare_to: str | None = Query(
        default=None,
        description="Base snapshot ID. Defaults to the preceding completed snapshot.",
    ),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    # Resolve the "new" snapshot
    snap_new = db.query(Snapshot).filter_by(
        id=snapshot_id, stack_name=stack_name
    ).first()
    if snap_new is None or not snap_new.complete:
        raise HTTPException(status_code=404, detail="Snapshot not found or incomplete")

    # Resolve the "base" snapshot
    if compare_to:
        snap_old = db.query(Snapshot).filter_by(
            id=compare_to, stack_name=stack_name
        ).first()
        if snap_old is None or not snap_old.complete:
            raise HTTPException(
                status_code=404, detail="Base snapshot not found or incomplete"
            )
    else:
        snap_old = (
            db.query(Snapshot)
            .filter(
                Snapshot.stack_name == stack_name,
                Snapshot.complete == True,  # noqa: E712
                Snapshot.generated_at < snap_new.generated_at,
            )
            .order_by(Snapshot.generated_at.desc())
            .first()
        )
        if snap_old is None:
            raise HTTPException(
                status_code=404,
                detail="No preceding snapshot to compare against — pass compare_to explicitly",
            )

    manifest_new = _load_manifest(snap_new)
    manifest_old = _load_manifest(snap_old)

    return {
        "snapshot_id": snapshot_id,
        "compare_to": snap_old.id,
        "image_diff": _image_diff(manifest_new, manifest_old),
        "config_diff": _config_diff(snap_new, snap_old),
        "volume_diff": _volume_diff(snap_new, snap_old),
    }


# --------------------------------------------------------------------------- #
# Image diff                                                                    #
# --------------------------------------------------------------------------- #

def _image_diff(new: Manifest, old: Manifest) -> list[dict[str, Any]]:
    """Return services where the image or digest changed."""
    old_map = {s.name: s for s in old.services}
    new_map = {s.name: s for s in new.services}
    changes = []

    all_names = set(old_map) | set(new_map)
    for name in sorted(all_names):
        o = old_map.get(name)
        n = new_map.get(name)
        if o is None:
            changes.append({"service": name, "change": "added", "new_image": n.image if n else None})
        elif n is None:
            changes.append({"service": name, "change": "removed", "old_image": o.image})
        elif o.image != n.image or o.image_digest != n.image_digest:
            changes.append({
                "service": name,
                "change": "updated",
                "old_image": o.image,
                "new_image": n.image,
                "old_digest": o.image_digest,
                "new_digest": n.image_digest,
            })

    return changes


# --------------------------------------------------------------------------- #
# Config diff                                                                   #
# --------------------------------------------------------------------------- #

def _config_diff(snap_new: Snapshot, snap_old: Snapshot) -> list[dict[str, Any]]:
    """Unified text diff for every config file that changed between snapshots."""
    key = settings.get_encryption_key_bytes()
    diffs = []

    config_new = _read_config_files(snap_new, key)
    config_old = _read_config_files(snap_old, key)

    all_files = sorted(set(config_new) | set(config_old))
    for filename in all_files:
        old_lines = config_old.get(filename, "").splitlines(keepends=True)
        new_lines = config_new.get(filename, "").splitlines(keepends=True)
        if old_lines == new_lines:
            continue
        diff_lines = list(
            unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
            )
        )
        if diff_lines:
            diffs.append({
                "file": filename,
                "change": "added" if not old_lines else "removed" if not new_lines else "modified",
                "unified_diff": "".join(diff_lines),
            })

    return diffs


def _read_config_files(snap: Snapshot, key: bytes) -> dict[str, str]:
    """Return a {filename: text_content} map for a snapshot's config/ directory."""
    if not snap.storage_path:
        return {}
    config_dir = Path(snap.storage_path) / "config"
    if not config_dir.exists():
        return {}

    result: dict[str, str] = {}
    for path in sorted(config_dir.iterdir()):
        if path.suffix == ".enc":
            try:
                import tempfile
                import shutil
                tmp = Path(tempfile.mkdtemp())
                enc_copy = tmp / path.name
                shutil.copy2(path, enc_copy)
                plaintext_path = decrypt_file(enc_copy, key)
                content = plaintext_path.read_text(errors="replace")
                shutil.rmtree(tmp)
                result[path.stem] = content  # strip .enc suffix for display
            except Exception:
                result[path.name] = "<encrypted — could not decrypt>"
        elif path.is_file():
            try:
                result[path.name] = path.read_text(errors="replace")
            except Exception:
                pass

    return result


# --------------------------------------------------------------------------- #
# Volume diff                                                                   #
# --------------------------------------------------------------------------- #

def _volume_diff(snap_new: Snapshot, snap_old: Snapshot) -> list[dict[str, Any]]:
    """File-level diff for each volume that exists in both snapshots.

    Compares member names + sizes inside the .tar.gz archives (without full
    extraction). Skipped when any archive exceeds 5 GB.
    """
    if not snap_new.storage_path or not snap_old.storage_path:
        return [{"status": "skipped", "reason": "storage_path missing"}]

    key = settings.get_encryption_key_bytes()
    vol_dir_new = Path(snap_new.storage_path) / "volumes"
    vol_dir_old = Path(snap_old.storage_path) / "volumes"

    if not vol_dir_new.exists() or not vol_dir_old.exists():
        return []

    archives_new = {p.name: p for p in vol_dir_new.iterdir() if p.is_file()}
    archives_old = {p.name: p for p in vol_dir_old.iterdir() if p.is_file()}

    # Normalise: strip .enc so we can match by base name
    def _base(name: str) -> str:
        return name.removesuffix(".enc")

    bases_new = {_base(n): p for n, p in archives_new.items()}
    bases_old = {_base(n): p for n, p in archives_old.items()}

    all_bases = sorted(set(bases_new) | set(bases_old))
    result = []

    for base in all_bases:
        pn = bases_new.get(base)
        po = bases_old.get(base)

        if pn is None:
            result.append({"volume": base, "change": "removed"})
            continue
        if po is None:
            result.append({"volume": base, "change": "added"})
            continue

        # Size guard
        if pn.stat().st_size > _VOLUME_DIFF_SIZE_LIMIT or po.stat().st_size > _VOLUME_DIFF_SIZE_LIMIT:
            result.append({
                "volume": base,
                "change": "skipped",
                "reason": "archive exceeds 5 GB limit",
            })
            continue

        try:
            members_new = _tar_members(pn, key)
            members_old = _tar_members(po, key)
        except Exception as exc:
            result.append({"volume": base, "change": "error", "reason": str(exc)})
            continue

        added = sorted(set(members_new) - set(members_old))
        removed = sorted(set(members_old) - set(members_new))
        modified = sorted(
            n for n in set(members_new) & set(members_old)
            if members_new[n] != members_old[n]
        )

        if not added and not removed and not modified:
            result.append({"volume": base, "change": "unchanged"})
        else:
            result.append({
                "volume": base,
                "change": "modified",
                "added": added,
                "removed": removed,
                "modified": modified,
            })

    return result


def _tar_members(archive_path: Path, key: bytes) -> dict[str, int]:
    """Return {member_path: size} for all files inside a (possibly encrypted) tar.gz."""
    import tempfile, shutil

    work: Path | None = None
    try:
        if archive_path.suffix == ".enc":
            tmp_dir = Path(tempfile.mkdtemp())
            enc_copy = tmp_dir / archive_path.name
            shutil.copy2(archive_path, enc_copy)
            tar_path = decrypt_file(enc_copy, key)
            work = tmp_dir
        else:
            tar_path = archive_path
            work = None

        members: dict[str, int] = {}
        with tarfile.open(tar_path, "r:gz") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    members[m.name] = m.size
        return members
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def _load_manifest(snap: Snapshot) -> Manifest:
    if not snap.manifest_path or not Path(snap.manifest_path).exists():
        raise HTTPException(
            status_code=404,
            detail=f"Manifest file missing for snapshot {snap.id}",
        )
    return Manifest.load(snap.manifest_path)
