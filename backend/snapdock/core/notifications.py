"""Notification dispatch — webhooks and email.

Configurable per stack in a future settings table.  For Phase 1, webhook URLs
are read from the ``SNAPDOCK_WEBHOOK_URL`` environment variable (comma-separated
for multiple targets) and email settings from ``SNAPDOCK_SMTP_*`` variables.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from snapdock.models.manifest import Manifest

logger = logging.getLogger(__name__)


def _default_webhook_urls() -> list[str]:
    raw = os.getenv("SNAPDOCK_WEBHOOK_URL", "")
    return [u.strip() for u in raw.split(",") if u.strip()]


@dataclass
class NotificationPayload:
    event: str          # snapshot.success | snapshot.failure | snapshot.degraded |
                        # restore.success | restore.failure | retention.cleanup
    stack_name: str
    snapshot_id: str | None
    state: str | None   # CLEAN | DEGRADED | BROKEN
    message: str
    details: dict


async def send_notifications(
    payload: NotificationPayload,
    webhook_urls: list[str] | None = None,
) -> None:
    """Dispatch *payload* to all configured webhook and email targets.

    Never raises — errors are logged as warnings.
    """
    await _send_webhooks(payload, webhook_urls)
    await _send_email(payload)


async def _send_webhooks(
    payload: NotificationPayload,
    webhook_urls: list[str] | None,
) -> None:
    targets = webhook_urls or _default_webhook_urls()
    if not targets:
        return

    body = {
        "event": payload.event,
        "stack_name": payload.stack_name,
        "snapshot_id": payload.snapshot_id,
        "state": payload.state,
        "message": payload.message,
        "details": payload.details,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in targets:
            try:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                logger.debug("Webhook notification sent to %s", url)
            except Exception as exc:
                logger.warning("Failed to send webhook to %s: %s", url, exc)


async def _send_email(payload: NotificationPayload) -> None:
    """Send an email notification via SMTP if configured.

    Required env vars:
      SNAPDOCK_SMTP_HOST       e.g. smtp.mailgun.org
      SNAPDOCK_SMTP_PORT       default 587
      SNAPDOCK_SMTP_USER
      SNAPDOCK_SMTP_PASSWORD
      SNAPDOCK_SMTP_FROM       e.g. snapdock@company.com
      SNAPDOCK_SMTP_TO         comma-separated recipient list
    """
    host = os.getenv("SNAPDOCK_SMTP_HOST", "")
    if not host:
        return  # email not configured

    port = int(os.getenv("SNAPDOCK_SMTP_PORT", "587"))
    user = os.getenv("SNAPDOCK_SMTP_USER", "")
    password = os.getenv("SNAPDOCK_SMTP_PASSWORD", "")
    from_addr = os.getenv("SNAPDOCK_SMTP_FROM", user)
    to_raw = os.getenv("SNAPDOCK_SMTP_TO", "")
    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]

    if not recipients:
        return

    subject = f"[SnapDock] {payload.event} — {payload.stack_name}"
    body_lines = [
        f"Event:    {payload.event}",
        f"Stack:    {payload.stack_name}",
        f"Snapshot: {payload.snapshot_id or '—'}",
        f"State:    {payload.state or '—'}",
        "",
        payload.message,
    ]
    if payload.details:
        body_lines += ["", "Details:"]
        for k, v in payload.details.items():
            body_lines.append(f"  {k}: {v}")

    text_body = "\n".join(body_lines)

    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        msg = MIMEText(text_body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)

        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=user or None,
            password=password or None,
            use_tls=False,
            start_tls=port != 465,
        )
        logger.debug("Email notification sent to %s", recipients)
    except Exception as exc:
        logger.warning("Failed to send email notification: %s", exc)


def notify_snapshot_complete(manifest: Manifest, success: bool) -> NotificationPayload:
    event = "snapshot.success" if success else "snapshot.failure"
    if success and manifest.stack.stack_state in ("DEGRADED", "BROKEN"):
        event = "snapshot.degraded"
    return NotificationPayload(
        event=event,
        stack_name=manifest.stack.name,
        snapshot_id=manifest.snapshot_id,
        state=manifest.stack.stack_state,
        message=(
            f"Snapshot {'succeeded' if success else 'failed'}: "
            f"{manifest.snapshot_id} ({manifest.stack.stack_state})"
        ),
        details={
            "trigger_type": manifest.trigger_type,
            "triggered_by": manifest.triggered_by,
            "label": manifest.label,
            "complete": manifest.complete,
        },
    )


def notify_restore_complete(
    manifest: Manifest, success: bool, dry_run: bool
) -> NotificationPayload:
    label = "dry-run restore" if dry_run else "restore"
    event = f"restore.success" if success else "restore.failure"
    return NotificationPayload(
        event=event,
        stack_name=manifest.stack.name,
        snapshot_id=manifest.snapshot_id,
        state=manifest.stack.stack_state,
        message=f"{'Dry-run ' if dry_run else ''}Restore {'succeeded' if success else 'failed'}: {manifest.snapshot_id}",
        details={"dry_run": dry_run},
    )
