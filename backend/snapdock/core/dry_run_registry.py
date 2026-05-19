"""In-memory registry of active dry-run restore environments.

At most one active dry-run is allowed per stack. Entries expire automatically
after DRY_RUN_TTL_MINUTES if the user never calls the teardown endpoint.

The registry uses a plain threading.Lock so it is safe to access from:
  - background threads that spin up their own asyncio event loop (RestoreEngine)
  - the main FastAPI asyncio event loop (teardown endpoint)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DRY_RUN_TTL_MINUTES = 60


@dataclass
class DryRunEntry:
    snapshot_id: str
    stack_name: str
    restore_suffix: str
    # service name -> list of {host_port, container_port, protocol}
    dry_run_ports: dict[str, list[dict]]
    started_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(
        default_factory=lambda: datetime.utcnow() + timedelta(minutes=DRY_RUN_TTL_MINUTES)
    )


_registry: dict[str, DryRunEntry] = {}
_lock = threading.Lock()


def register(entry: DryRunEntry) -> None:
    """Register a new active dry-run, replacing any previous entry for the stack."""
    with _lock:
        _registry[entry.stack_name] = entry


def get(stack_name: str) -> DryRunEntry | None:
    with _lock:
        return _registry.get(stack_name)


def remove(stack_name: str) -> DryRunEntry | None:
    """Remove and return the entry, or None if not present."""
    with _lock:
        return _registry.pop(stack_name, None)


def list_all() -> list[DryRunEntry]:
    with _lock:
        return list(_registry.values())


def list_expired() -> list[DryRunEntry]:
    now = datetime.utcnow()
    with _lock:
        return [e for e in _registry.values() if e.expires_at <= now]
