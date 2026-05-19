"""Coverage / compliance dashboard route."""
from __future__ import annotations

import io
import socket
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from snapdock.api.deps import get_current_user
from snapdock.core.classifier import ContainerClassifier
from snapdock.database import Schedule, Snapshot, get_db
from snapdock.docker_client import get_docker_client
from snapdock.models.schemas import CoverageDashboard, CoverageRow

router = APIRouter(prefix="/coverage", tags=["coverage"])

_OVERDUE_THRESHOLD_HOURS = 26  # ~1 day + buffer
_SELF_HOSTNAME = socket.gethostname()


@router.get("", response_model=CoverageDashboard)
def get_coverage(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    docker = get_docker_client()
    stacks = ContainerClassifier(docker).classify_all()
    now = datetime.utcnow()
    rows: list[CoverageRow] = []

    protected = overdue = unprotected = 0

    for stack in stacks:
        # Skip the SnapDock stack itself
        if any(c.short_id == _SELF_HOSTNAME or c.id.startswith(_SELF_HOSTNAME) for c in stack.containers):
            continue

        last_clean = (
            db.query(Snapshot)
            .filter_by(stack_name=stack.name, stack_state="CLEAN", complete=True)
            .order_by(Snapshot.generated_at.desc())
            .first()
        )
        last_verified = (
            db.query(Snapshot)
            .filter_by(stack_name=stack.name, verified=True)
            .order_by(Snapshot.verified_at.desc())
            .first()
        )
        sched = db.query(Schedule).filter_by(stack_name=stack.name, is_active=True).first()

        # Determine status
        if last_clean is None:
            status = "unprotected"
            unprotected += 1
        elif sched is None:
            age = now - last_clean.generated_at
            if age > timedelta(hours=_OVERDUE_THRESHOLD_HOURS):
                status = "overdue"
                overdue += 1
            else:
                status = "covered"
                protected += 1
        else:
            age = now - last_clean.generated_at
            if age > timedelta(hours=_OVERDUE_THRESHOLD_HOURS * 2):
                status = "overdue"
                overdue += 1
            else:
                status = "covered"
                protected += 1

        rows.append(
            CoverageRow(
                stack_name=stack.name,
                last_clean_snap_at=last_clean.generated_at if last_clean else None,
                schedule_cron=sched.cron_expression if sched else None,
                last_verified_at=last_verified.verified_at if last_verified else None,
                status=status,
            )
        )

    return CoverageDashboard(
        rows=rows,
        total=len(rows),
        protected=protected,
        overdue=overdue,
        unprotected=unprotected,
    )


@router.get("/export.pdf")
def export_coverage_pdf(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generate a PDF compliance report for auditors."""
    # Reuse the JSON route logic to build the data
    dashboard: CoverageDashboard = get_coverage(db=db, current_user=current_user)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=501,
            detail="reportlab is not installed. Add it to requirements.txt.",
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = []

    # Title
    story.append(Paragraph("SnapDock — Coverage Report", styles["Title"]))
    story.append(Paragraph(
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.5 * cm))

    # Summary
    summary_data = [
        ["Total Stacks", "Protected", "Overdue", "Unprotected"],
        [
            str(dashboard.total),
            str(dashboard.protected),
            str(dashboard.overdue),
            str(dashboard.unprotected),
        ],
    ]
    summary_table = Table(summary_data, hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.7 * cm))

    # Detail table
    story.append(Paragraph("Stack Detail", styles["Heading2"]))
    _status_colors = {
        "covered": colors.HexColor("#d4edda"),
        "overdue": colors.HexColor("#fff3cd"),
        "unprotected": colors.HexColor("#f8d7da"),
    }
    detail_data = [["Stack", "Status", "Last Clean Snap", "Schedule", "Last Verified"]]
    for row in dashboard.rows:
        detail_data.append([
            row.stack_name,
            row.status.capitalize(),
            row.last_clean_snap_at.strftime("%Y-%m-%d %H:%M") if row.last_clean_snap_at else "never",
            row.schedule_cron or "—",
            row.last_verified_at.strftime("%Y-%m-%d") if row.last_verified_at else "—",
        ])

    detail_table = Table(detail_data, hAlign="LEFT", colWidths=[4 * cm, 2.5 * cm, 4 * cm, 3.5 * cm, 3 * cm])
    ts = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]
    for i, row in enumerate(dashboard.rows, start=1):
        bg = _status_colors.get(row.status, colors.white)
        ts.append(("BACKGROUND", (0, i), (-1, i), bg))
    detail_table.setStyle(TableStyle(ts))
    story.append(detail_table)

    doc.build(story)
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=snapdock_coverage.pdf"},
    )
