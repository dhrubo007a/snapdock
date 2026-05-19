"""Pydantic model for the snapshot manifest.

The manifest is both a *plan* (generated before any action) and a *record*
(finalized after the snapshot completes).  It is the authoritative source of
truth for what a snapshot contains and how to restore it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Sub-models                                                                    #
# --------------------------------------------------------------------------- #

class ServiceManifest(BaseModel):
    name: str
    image: str
    image_digest: str | None = None
    restart_policy_original: str | None = None
    restart_policy_overridden: bool = False
    quiesce: str | None = None          # method used, e.g. "postgresql_checkpoint"
    quiesce_outcome: str | None = None  # ok | failed | skipped
    pre_hook: str | None = None
    pre_hook_outcome: str | None = None   # ok | failed | skipped
    post_hook: str | None = None
    post_hook_outcome: str | None = None


class VolumeManifest(BaseModel):
    type: Literal["named", "anonymous", "bind", "tmpfs"]
    service: str
    mount_path: str
    captured: bool = False
    archive_filename: str | None = None   # relative to snapshot volumes/ dir
    size_bytes: int | None = None
    checksum: str | None = None
    # Named volume
    name: str | None = None
    # Anonymous volume
    id: str | None = None
    # Bind mount
    host_path: str | None = None
    # tmpfs
    note: str | None = None


class StackManifest(BaseModel):
    name: str
    type: Literal["compose", "interconnected", "solo"]
    project_name: str | None = None
    stack_state: Literal["CLEAN", "DEGRADED", "BROKEN"] = "CLEAN"
    compose_files: list[str] = Field(default_factory=list)
    config_hash_match: bool = True
    inferred_reason: str | None = None   # for interconnected groups


class ConfigManifest(BaseModel):
    compose_files: list[str] = Field(default_factory=list)
    env_files: list[str] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    startup_order: list[str] = Field(default_factory=list)
    captured: bool = False
    encrypted: bool = True


class DiagnosticsManifest(BaseModel):
    logs_per_container: int = 1000
    log_files: list[str] = Field(default_factory=list)   # relative paths
    inspect_files: list[str] = Field(default_factory=list)
    events_window_minutes: int = 10
    hook_log_files: list[str] = Field(default_factory=list)
    captured: bool = False


class ImageManifest(BaseModel):
    tag: str               # e.g. "postgres:16-alpine"
    digest: str | None     # repo digest, if available
    archive_filename: str  # relative to snapshot images/ dir, e.g. "postgres_16-alpine.tar"
    size_bytes: int | None = None
    captured: bool = False


class StorageManifest(BaseModel):
    backend: str = "local"
    path: str = ""


# --------------------------------------------------------------------------- #
# Root manifest                                                                 #
# --------------------------------------------------------------------------- #

class Manifest(BaseModel):
    manifest_version: str = "1.0"
    snapshot_id: str
    label: str | None = None
    tags: list[str] = Field(default_factory=list)
    locked: bool = False
    generated_at: datetime
    finalized_at: datetime | None = None
    complete: bool = False

    stack: StackManifest
    services: list[ServiceManifest] = Field(default_factory=list)
    volumes: list[VolumeManifest] = Field(default_factory=list)
    images: list[ImageManifest] = Field(default_factory=list)
    config: ConfigManifest
    diagnostics: DiagnosticsManifest
    storage: StorageManifest

    triggered_by: str
    trigger_type: Literal["manual", "auto"]

    # ------------------------------------------------------------------ #
    # Persistence helpers                                                   #
    # ------------------------------------------------------------------ #

    def save(self, path: Path) -> None:
        """Write the manifest as pretty-printed JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2, exclude_none=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load a manifest from disk."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
