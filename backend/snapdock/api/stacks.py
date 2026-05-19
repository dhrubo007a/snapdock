"""Stack routes — classification and health summary."""
from __future__ import annotations

import socket
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.core.classifier import ContainerClassifier, DetectedStack
from snapdock.core.health import check_stack_health
from snapdock.database import Schedule, Snapshot, get_db
from snapdock.docker_client import get_docker_client
from snapdock.models.schemas import ContainerSummary, StackResponse

# The container hostname inside Docker equals the short container ID.
# Used to mark the stack containing this daemon as snapshot-protected.
_SELF_HOSTNAME = socket.gethostname()

router = APIRouter(prefix="/stacks", tags=["stacks"])


@router.get("", response_model=list[StackResponse])
def list_stacks(
    db: Session = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    docker = get_docker_client()
    classifier = ContainerClassifier(docker)
    stacks = classifier.classify_all()

    response: list[StackResponse] = []
    for stack in stacks:
        health = check_stack_health(docker, stack)

        # Last snapshot metadata
        last_snap = (
            db.query(Snapshot)
            .filter_by(stack_name=stack.name, complete=True)
            .order_by(Snapshot.generated_at.desc())
            .first()
        )

        # Last verified
        last_verified = (
            db.query(Snapshot)
            .filter_by(stack_name=stack.name, complete=True, verified=True)
            .order_by(Snapshot.verified_at.desc())
            .first()
        )

        # Schedule
        sched = db.query(Schedule).filter_by(stack_name=stack.name).first()

        _protected = any(
            c.short_id == _SELF_HOSTNAME or c.id.startswith(_SELF_HOSTNAME)
            for c in stack.containers
        )
        if _protected:
            continue
        response.append(
            StackResponse(
                name=stack.name,
                type=stack.type,
                containers=[
                    ContainerSummary(
                        id=c.short_id,
                        name=c.name.lstrip("/"),
                        image=c.image.tags[0] if c.image.tags else c.image.short_id,
                        status=c.status,
                    )
                    for c in stack.containers
                ],
                health_state=health.state,
                compose_files=stack.compose_files,
                inferred_reason=stack.inferred_reason,
                last_snapshot_at=last_snap.generated_at if last_snap else None,
                last_snapshot_state=last_snap.stack_state if last_snap else None,
                last_verified_at=last_verified.verified_at if last_verified else None,
                has_schedule=sched is not None and sched.is_active,
                snapshot_protected=_protected,
            )
        )

    return response


@router.get("/{stack_name}", response_model=StackResponse)
def get_stack(
    stack_name: str,
    db: Session = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    from fastapi import HTTPException, status

    docker = get_docker_client()
    classifier = ContainerClassifier(docker)
    stack = classifier.get_stack(stack_name)
    if stack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stack not found")

    health = check_stack_health(docker, stack)
    last_snap = (
        db.query(Snapshot)
        .filter_by(stack_name=stack_name, complete=True)
        .order_by(Snapshot.generated_at.desc())
        .first()
    )
    last_verified = (
        db.query(Snapshot)
        .filter_by(stack_name=stack_name, complete=True, verified=True)
        .order_by(Snapshot.verified_at.desc())
        .first()
    )
    sched = db.query(Schedule).filter_by(stack_name=stack_name).first()

    _protected = any(
        c.short_id == _SELF_HOSTNAME or c.id.startswith(_SELF_HOSTNAME)
        for c in stack.containers
    )
    return StackResponse(
        name=stack.name,
        type=stack.type,
        containers=[
            ContainerSummary(
                id=c.short_id,
                name=c.name.lstrip("/"),
                image=c.image.tags[0] if c.image.tags else c.image.short_id,
                status=c.status,
            )
            for c in stack.containers
        ],
        health_state=health.state,
        compose_files=stack.compose_files,
        inferred_reason=stack.inferred_reason,
        last_snapshot_at=last_snap.generated_at if last_snap else None,
        last_snapshot_state=last_snap.stack_state if last_snap else None,
        last_verified_at=last_verified.verified_at if last_verified else None,
        has_schedule=sched is not None and sched.is_active,
        snapshot_protected=_protected,
    )
