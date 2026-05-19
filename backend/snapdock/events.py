"""In-process WebSocket event bus.

The snapshot and restore engines publish ``SnapDockEvent`` objects here.
WebSocket connections subscribe and receive them in real time.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SnapDockEvent:
    """A single progress or lifecycle event emitted by the daemon."""

    event_type: str          # snapshot.step | snapshot.complete | snapshot.error |
                             # restore.step | restore.complete | restore.error
    stack_name: str
    snapshot_id: str | None = None
    step: str | None = None
    status: str | None = None  # running | ok | error | warning
    message: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "event_type": self.event_type,
                "stack_name": self.stack_name,
                "snapshot_id": self.snapshot_id,
                "step": self.step,
                "status": self.status,
                "message": self.message,
                "timestamp": self.timestamp,
                "data": self.data,
            }
        )


class EventBus:
    """Simple fan-out pub/sub backed by ``asyncio.Queue`` per subscriber."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[SnapDockEvent]] = []
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def subscribe(self) -> asyncio.Queue[SnapDockEvent]:
        """Register a new subscriber and return its queue."""
        q: asyncio.Queue[SnapDockEvent] = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[SnapDockEvent]) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    async def publish(self, event: SnapDockEvent) -> None:
        """Fan-out an event to all current subscribers."""
        async with self._lock:
            dead: list[asyncio.Queue[SnapDockEvent]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "EventBus: subscriber queue full, dropping event '%s'",
                        event.event_type,
                    )
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def publish_sync(
        self, event: SnapDockEvent, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Thread-safe publish from a non-async context."""
        target = loop or self._loop
        if target is None:
            return
        asyncio.run_coroutine_threadsafe(self.publish(event), target)


# Module-level singleton used throughout the daemon
event_bus = EventBus()
