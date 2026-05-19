"""WebSocket endpoint — streams live events to connected clients.

Clients connect to ``WS /events`` and receive JSON-encoded ``SnapDockEvent``
objects as they are published by the snapshot/restore engines.

Optional query param ``stack_name`` filters to events for a specific stack.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from snapdock.events import SnapDockEvent, event_bus

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)


@router.websocket("/events")
async def websocket_events(
    websocket: WebSocket,
    stack_name: str | None = None,
):
    await websocket.accept()
    q = await event_bus.subscribe()
    logger.info(
        "WebSocket client connected (filter: %s)",
        stack_name or "all",
    )
    try:
        while True:
            try:
                event: SnapDockEvent = await asyncio.wait_for(q.get(), timeout=30.0)
                if stack_name and event.stack_name != stack_name:
                    continue
                await websocket.send_text(event.to_json())
            except asyncio.TimeoutError:
                # Send a keepalive ping
                try:
                    await websocket.send_text('{"event_type":"ping"}')
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        await event_bus.unsubscribe(q)
