"""Audit log route — admin-only, read-only, exportable as CSV."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.auth.rbac import require_role
from snapdock.database import AuditLog as AuditLogRow, get_db
from snapdock.models.schemas import AuditLogEntry

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogEntry])
def get_audit_log(
    stack_name: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    q = db.query(AuditLogRow).order_by(AuditLogRow.timestamp.desc())
    if stack_name:
        q = q.filter_by(target_stack=stack_name)
    return q.offset(offset).limit(limit).all()


@router.get("/export.csv")
def export_audit_csv(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_role(current_user.role, "admin")
    rows = db.query(AuditLogRow).order_by(AuditLogRow.timestamp.desc()).all()

    lines = ["id,timestamp,actor,action,target_stack,target_snapshot,outcome,detail"]
    for r in rows:
        lines.append(
            f'{r.id},{r.timestamp.isoformat()}Z,"{r.actor}","{r.action}",'
            f'"{r.target_stack or ""}","{r.target_snapshot or ""}","{r.outcome}",'
            f'"{(r.detail or "").replace(chr(34), chr(39))}"'
        )

    return Response(
        content="\n".join(lines),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
