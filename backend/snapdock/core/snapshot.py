"""Snapshot orchestration engine — implements the full 25-step sequence.

Steps that modify host/container state (8-20) run inside a try/finally so
restart policy restoration and stack restart are *always* attempted, even if
an earlier step fails.

Progress is published to the ``EventBus`` as ``SnapDockEvent`` objects so the
WebSocket endpoint can stream them to the UI in real time.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from docker import DockerClient

from snapdock.config import Settings
from snapdock.core import classifier as clf
from snapdock.core.classifier import read_file_from_portainer
from snapdock.core.crypto import checksum_file, encrypt_file
from snapdock.core.health import HealthReport, HealthState, check_stack_health
from snapdock.core.hooks import HookResult, load_hooks, run_hooks
from snapdock.core.quiesce import quiesce_container
from snapdock.core.volume import (
    backup_anonymous_volume,
    backup_bind_mount,
    backup_named_volume,
    _ensure_image,
)
from snapdock.events import EventBus, SnapDockEvent
from snapdock.models.manifest import (
    ConfigManifest,
    DiagnosticsManifest,
    ImageManifest,
    Manifest,
    ServiceManifest,
    StackManifest,
    StorageManifest,
    VolumeManifest,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PENDING_RESTART_DIR = Path("/var/lib/snapdock/state/pending_restart")


def _win_to_docker_desktop_path(win_path: str) -> Path | None:
    """Translate a Windows host path to the Docker Desktop Linux mount point.

    Docker Desktop (WSL2 backend) exposes host drives at
    ``/run/desktop/mnt/host/<drive>/<rest>``.
    E.g.  ``D:\\Projects\\foo`` → ``/run/desktop/mnt/host/d/Projects/foo``.
    Returns *None* when *win_path* does not look like a Windows absolute path.
    """
    m = re.match(r'^([A-Za-z]):[/\\](.*)$', win_path)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = m.group(2).replace('\\', '/')
    return Path(f'/run/desktop/mnt/host/{drive}/{rest}')


def _snapshot_id() -> str:
    now = datetime.utcnow()
    suffix = os.urandom(2).hex()
    return now.strftime(f"snap_%Y%m%d_%H%M%S_{suffix}")


class SnapshotEngine:
    def __init__(
        self,
        docker_client: DockerClient,
        db: "Session",
        event_bus: EventBus,
        settings: Settings,
    ) -> None:
        self._docker = docker_client
        self._db = db
        self._bus = event_bus
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        stack: clf.DetectedStack,
        triggered_by: str,
        trigger_type: str,
        label: str | None = None,
        tags: list[str] | None = None,
    ) -> Manifest:
        snap_id = _snapshot_id()
        snapshot_dir = self._settings.storage_path / stack.name / snap_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        await self._emit(snap_id, stack.name, "snapshot.step", "Generating manifest…", "running")

        # ── Step 1-3: Build manifest (plan phase) ────────────────────── #
        manifest = await self._build_manifest(
            stack, snap_id, triggered_by, trigger_type, label, tags or []
        )
        manifest.storage.path = str(snapshot_dir)
        manifest.save(snapshot_dir / "manifest.json")

        # ── Step 4: Health check ──────────────────────────────────────── #
        health = await asyncio.to_thread(check_stack_health, self._docker, stack)
        manifest.stack.stack_state = health.state
        manifest.save(snapshot_dir / "manifest.json")
        await self._emit(
            snap_id, stack.name, "snapshot.step",
            f"Health check: {health.state}", "ok" if health.is_clean else "warning",
        )

        # ── Step 6: Capture diagnostics ──────────────────────────────── #
        await self._emit(snap_id, stack.name, "snapshot.step", "Capturing diagnostics…", "running")
        diag_dir = snapshot_dir / "diagnostics"
        manifest.diagnostics = await self._capture_diagnostics(stack, diag_dir)
        manifest.save(snapshot_dir / "manifest.json")

        # ── Step 7: Pre-snapshot hooks ───────────────────────────────── #
        hooks = load_hooks(stack.working_dir, stack.name)
        pre_specs = hooks.get("pre_snapshot", [])
        if pre_specs:
            await self._emit(snap_id, stack.name, "snapshot.step", "Running pre-snapshot hooks…", "running")
            pre_results = await run_hooks(
                self._docker, stack, pre_specs,
                log_dir=diag_dir / "hooks", phase="pre"
            )
            self._apply_hook_results(manifest, pre_results, phase="pre")
            manifest.save(snapshot_dir / "manifest.json")

        # ── Critical section ─────────────────────────────────────────── #
        restart_policies_overridden = False
        success = False
        key = self._settings.get_encryption_key_bytes()

        try:
            # Step 8: Override restart policies
            await self._emit(snap_id, stack.name, "snapshot.step", "Overriding restart policies…", "running")
            await asyncio.to_thread(self._override_restart_policies, stack, manifest)
            restart_policies_overridden = True

            # Step 9: Quiesce databases
            await self._emit(snap_id, stack.name, "snapshot.step", "Quiescing databases…", "running")
            await self._quiesce_all(stack, manifest)
            manifest.save(snapshot_dir / "manifest.json")

            # Step 10: Write pending_restart flag
            self._write_pending_restart(stack.name)

            # Step 11: Stop stack
            await self._emit(snap_id, stack.name, "snapshot.step", "Stopping stack…", "running")
            await asyncio.to_thread(self._stop_stack, stack)

            # Step 12: Copy volumes
            await self._emit(snap_id, stack.name, "snapshot.step", "Copying volumes…", "running")
            await self._copy_volumes(stack, manifest, snapshot_dir)
            manifest.save(snapshot_dir / "manifest.json")

            # Step 12b: Save container images
            await self._emit(snap_id, stack.name, "snapshot.step", "Saving images…", "running")
            await self._save_images(stack, manifest, snapshot_dir)
            manifest.save(snapshot_dir / "manifest.json")

            # Step 13: Save config layer
            await self._emit(snap_id, stack.name, "snapshot.step", "Saving config layer…", "running")
            await asyncio.to_thread(self._save_config_layer, stack, manifest, snapshot_dir)
            manifest.config.captured = True
            manifest.save(snapshot_dir / "manifest.json")

            # Step 14: Encrypt snapshot data
            await self._emit(snap_id, stack.name, "snapshot.step", "Encrypting snapshot data…", "running")
            await asyncio.to_thread(self._encrypt_snapshot, snapshot_dir, key)

            # Step 15: Finalize (all data on local storage)
            size_bytes = sum(
                f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file()
            )

            # Step 16: Finalize manifest
            manifest.finalized_at = datetime.utcnow()
            manifest.complete = True
            manifest.save(snapshot_dir / "manifest.json")

            success = True

        except Exception as exc:
            logger.exception("Snapshot %s failed: %s", snap_id, exc)
            manifest.complete = False
            manifest.save(snapshot_dir / "manifest.json")
            await self._emit(
                snap_id, stack.name, "snapshot.error",
                f"Snapshot failed: {exc}", "error",
            )

        finally:
            # Step 17: Restart stack — ALWAYS runs
            await self._emit(snap_id, stack.name, "snapshot.step", "Restarting stack…", "running")
            try:
                await asyncio.to_thread(self._start_stack, stack)
            except Exception as exc:
                logger.error("Failed to restart stack '%s': %s", stack.name, exc)
                await self._emit(
                    snap_id, stack.name, "snapshot.step",
                    f"ERROR: failed to restart stack: {exc}", "error",
                )

            # Step 19: Restore restart policies
            if restart_policies_overridden:
                try:
                    await asyncio.to_thread(self._restore_restart_policies, stack, manifest)
                    await self._emit(snap_id, stack.name, "snapshot.step", "Restart policies restored", "ok")
                except Exception as exc:
                    logger.error("Failed to restore restart policies: %s", exc)

            # Step 20: Clear pending_restart flag
            self._clear_pending_restart(stack.name)

        if not success:
            return manifest

        # ── Step 18: Post-snapshot hooks ─────────────────────────────── #
        post_specs = hooks.get("post_snapshot", [])
        if post_specs:
            await self._emit(snap_id, stack.name, "snapshot.step", "Running post-snapshot hooks…", "running")
            post_results = await run_hooks(
                self._docker, stack, post_specs,
                log_dir=diag_dir / "hooks", phase="post"
            )
            self._apply_hook_results(manifest, post_results, phase="post")
            manifest.save(snapshot_dir / "manifest.json")

        # ── Step 21: Health verify ───────────────────────────────────── #
        await asyncio.sleep(2)  # brief pause for containers to stabilize
        post_health = await asyncio.to_thread(check_stack_health, self._docker, stack)
        await self._emit(
            snap_id, stack.name, "snapshot.step",
            f"Post-restart health: {post_health.state}",
            "ok" if post_health.is_clean else "warning",
        )

        # ── Steps 22-25: Retention, audit, notify, complete ─────────── #
        await self._emit(snap_id, stack.name, "snapshot.complete", "Snapshot complete", "ok",
                         data={"snapshot_id": snap_id, "size_bytes": size_bytes if success else None})

        return manifest

    # ------------------------------------------------------------------ #
    # Step implementations                                                  #
    # ------------------------------------------------------------------ #

    async def _build_manifest(
        self,
        stack: clf.DetectedStack,
        snap_id: str,
        triggered_by: str,
        trigger_type: str,
        label: str | None,
        tags: list[str],
    ) -> Manifest:
        services: list[ServiceManifest] = []
        volumes: list[VolumeManifest] = []
        env_files: list[str] = []
        networks: list[str] = []
        startup_order: list[str] = []

        for container in stack.containers:
            image_tag = container.image.tags[0] if container.image.tags else container.image.short_id
            image_digest = (
                container.image.attrs.get("RepoDigests", [None])[0]
                if container.image.attrs.get("RepoDigests")
                else None
            )
            restart_policy = container.attrs.get("HostConfig", {}).get("RestartPolicy", {})
            policy_name = restart_policy.get("Name", "no")

            svc_name = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            exposed_ports = sorted({
                int(p.split("/")[0])
                for p in (container.attrs.get("Config", {}).get("ExposedPorts") or {}).keys()
                if p.split("/")[0].isdigit()
            })
            services.append(
                ServiceManifest(
                    name=svc_name,
                    image=image_tag,
                    image_digest=image_digest,
                    restart_policy_original=policy_name,
                    exposed_ports=exposed_ports,
                )
            )
            startup_order.append(svc_name)

            # Collect volumes
            for mount in container.attrs.get("Mounts", []):
                mtype = mount.get("Type")
                if mtype == "volume":
                    vol_name = mount.get("Name", "")
                    if not vol_name:
                        continue
                    # Determine if named vs anonymous
                    # Anonymous volumes have a 64-char hex ID as name
                    if len(vol_name) == 64 and all(c in "0123456789abcdef" for c in vol_name):
                        volumes.append(
                            VolumeManifest(
                                type="anonymous",
                                service=svc_name,
                                mount_path=mount.get("Destination", ""),
                                id=vol_name,
                                archive_filename=f"anon_{vol_name[:16]}_{svc_name}.tar.gz",
                            )
                        )
                    else:
                        volumes.append(
                            VolumeManifest(
                                type="named",
                                service=svc_name,
                                mount_path=mount.get("Destination", ""),
                                name=vol_name,
                                archive_filename=f"{vol_name}.tar.gz",
                            )
                        )
                elif mtype == "bind":
                    host_path = mount.get("Source", "")
                    safe_name = host_path.replace("/", "_").lstrip("_")
                    volumes.append(
                        VolumeManifest(
                            type="bind",
                            service=svc_name,
                            mount_path=mount.get("Destination", ""),
                            host_path=host_path,
                            archive_filename=f"bind_{svc_name}_{safe_name[:40]}.tar.gz",
                        )
                    )
                elif mtype == "tmpfs":
                    volumes.append(
                        VolumeManifest(
                            type="tmpfs",
                            service=svc_name,
                            mount_path=mount.get("Destination", ""),
                            note="skipped — in-memory only",
                        )
                    )

            # Collect networks
            for net_name in container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys():
                if net_name not in networks:
                    networks.append(net_name)

        return Manifest(
            snapshot_id=snap_id,
            label=label,
            tags=tags,
            generated_at=datetime.utcnow(),
            stack=StackManifest(
                name=stack.name,
                type=stack.type,
                project_name=stack.name if stack.type == "compose" else None,
                stack_state="CLEAN",
                compose_files=stack.compose_files,
                config_hash_match=stack.config_hash_match,
                inferred_reason=stack.inferred_reason,
            ),
            services=services,
            volumes=volumes,
            config=ConfigManifest(
                compose_files=stack.compose_files,
                env_files=env_files,
                networks=networks,
                startup_order=startup_order,
                encrypted=True,
            ),
            diagnostics=DiagnosticsManifest(),
            storage=StorageManifest(backend="local"),
            triggered_by=triggered_by,
            trigger_type=trigger_type,  # type: ignore[arg-type]
        )

    async def _capture_diagnostics(
        self,
        stack: clf.DetectedStack,
        diag_dir: Path,
    ) -> DiagnosticsManifest:
        diag_dir.mkdir(parents=True, exist_ok=True)
        (diag_dir / "logs").mkdir(exist_ok=True)
        (diag_dir / "inspect").mkdir(exist_ok=True)

        log_files: list[str] = []
        inspect_files: list[str] = []

        for container in stack.containers:
            svc = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            try:
                logs = container.logs(tail=1000).decode("utf-8", errors="replace")
                log_path = diag_dir / "logs" / f"{svc}.log"
                log_path.write_text(logs, encoding="utf-8")
                log_files.append(str(log_path.relative_to(diag_dir.parent.parent)))
            except Exception as exc:
                logger.warning("Could not collect logs for %s: %s", svc, exc)

            try:
                inspect_path = diag_dir / "inspect" / f"{svc}.json"
                inspect_path.write_text(
                    json.dumps(container.attrs, indent=2, default=str),
                    encoding="utf-8",
                )
                inspect_files.append(str(inspect_path.relative_to(diag_dir.parent.parent)))
            except Exception as exc:
                logger.warning("Could not collect inspect for %s: %s", svc, exc)

        # Docker events (last 10 minutes)
        try:
            events_data = await asyncio.to_thread(
                self._collect_docker_events, stack
            )
            events_path = diag_dir / "events.json"
            events_path.write_text(
                json.dumps(events_data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Could not collect Docker events: %s", exc)

        return DiagnosticsManifest(
            log_files=log_files,
            inspect_files=inspect_files,
            captured=True,
        )

    def _collect_docker_events(self, stack: clf.DetectedStack) -> list[dict]:
        from datetime import timedelta

        since = datetime.utcnow() - timedelta(minutes=10)
        container_ids = {c.id for c in stack.containers}
        events = []
        for event in self._docker.events(
            since=since, until=datetime.utcnow(), decode=True
        ):
            if event.get("Actor", {}).get("ID") in container_ids:
                events.append(event)
            if len(events) >= 500:
                break
        return events

    def _override_restart_policies(
        self, stack: clf.DetectedStack, manifest: Manifest
    ) -> None:
        for container in stack.containers:
            svc = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            container.update(restart_policy={"Name": "no", "MaximumRetryCount": 0})
            # Mark as overridden in manifest
            for svc_m in manifest.services:
                if svc_m.name == svc:
                    svc_m.restart_policy_overridden = True
            logger.debug("Overrode restart policy for container %s", container.name)

    def _restore_restart_policies(
        self, stack: clf.DetectedStack, manifest: Manifest
    ) -> None:
        svc_map = {svc.name: svc for svc in manifest.services}
        for container in stack.containers:
            svc = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            original = svc_map.get(svc, None)
            policy_name = original.restart_policy_original if original else "no"
            try:
                container.update(
                    restart_policy={"Name": policy_name or "no", "MaximumRetryCount": 0}
                )
                logger.debug("Restored restart policy '%s' for %s", policy_name, container.name)
            except Exception as exc:
                logger.warning(
                    "Could not restore restart policy for %s: %s", container.name, exc
                )

    async def _quiesce_all(
        self, stack: clf.DetectedStack, manifest: Manifest
    ) -> None:
        from snapdock.api.settings import get_quiesce_overrides
        quiesce_overrides = get_quiesce_overrides(self._db)

        svc_map = {svc.name: svc for svc in manifest.services}
        for container in stack.containers:
            svc_name = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            svc_m = svc_map.get(svc_name)
            if svc_m is None:
                continue
            result = await quiesce_container(
                self._docker,
                container.id,
                svc_m.image,
                timeout=self._settings.quiesce_timeout,
                override_method=quiesce_overrides.get(svc_name),
            )
            svc_m.quiesce = result.method
            svc_m.quiesce_outcome = result.outcome
            if result.outcome == "failed":
                logger.warning(
                    "Quiesce failed for service '%s': %s", svc_name, result.message
                )

    def _stop_stack(self, stack: clf.DetectedStack) -> None:
        """Stop all containers in dependency-safe order.

        ``docker.containers.list()`` returns containers newest-first (reverse
        creation order).  For a typical compose stack the main app container
        is created *after* its dependencies (e.g. postgres), so it appears
        first in the list — exactly the right order for stopping (stop
        dependents before dependencies).
        """
        for container in stack.containers:  # newest first = dependents first
            try:
                container.stop(timeout=self._settings.stop_timeout)
                logger.info("Stopped container %s", container.name)
            except Exception as exc:
                logger.warning("Error stopping %s: %s", container.name, exc)

    def _start_stack(self, stack: clf.DetectedStack) -> None:
        """Start all containers in dependency-safe order.

        ``docker.containers.list()`` returns containers newest-first.  We
        reverse that so dependencies (e.g. postgres, redis) start *before* the
        app containers that depend on them.  A brief pause between starts gives
        each container time to become ready before the next one connects.
        """
        import time
        from docker.errors import NotFound, APIError

        for container in reversed(stack.containers):  # oldest first = deps first
            try:
                # Refresh container state so we don't act on stale cached data
                try:
                    container.reload()
                except NotFound:
                    # Container was removed (e.g. --rm flag) — nothing to start
                    logger.warning(
                        "Container %s no longer exists; skipping start", container.name
                    )
                    continue

                if container.status == "running":
                    logger.info("Container %s is already running", container.name)
                    continue

                container.start()
                logger.info("Started container %s", container.name)
                # Give this container a moment before starting the next one so
                # dependencies (postgres, redis, …) are accepting connections
                time.sleep(1)

            except APIError as exc:
                logger.error("Docker API error starting %s: %s", container.name, exc)
            except Exception as exc:
                logger.error("Unexpected error starting %s: %s", container.name, exc)

    async def _copy_volumes(
        self,
        stack: clf.DetectedStack,
        manifest: Manifest,
        snapshot_dir: Path,
    ) -> None:
        vol_dir = snapshot_dir / "volumes"
        vol_dir.mkdir(parents=True, exist_ok=True)

        for vol in manifest.volumes:
            if vol.type == "tmpfs":
                continue

            archive_name = vol.archive_filename or f"{vol.name or vol.id or 'unknown'}.tar.gz"

            try:
                if vol.type == "named" and vol.name:
                    await self._emit(
                        manifest.snapshot_id, stack.name, "snapshot.step",
                        f"Copying volume {vol.name}…", "running",
                    )
                    size = await backup_named_volume(
                        self._docker, vol.name, vol_dir, archive_name
                    )
                elif vol.type == "anonymous" and vol.id:
                    await self._emit(
                        manifest.snapshot_id, stack.name, "snapshot.step",
                        f"Copying anonymous volume {vol.id[:12]}…", "running",
                    )
                    size = await backup_anonymous_volume(
                        self._docker, vol.id, vol_dir, archive_name
                    )
                elif vol.type == "bind" and vol.host_path:
                    await self._emit(
                        manifest.snapshot_id, stack.name, "snapshot.step",
                        f"Copying bind mount {vol.host_path}…", "running",
                    )
                    size = await backup_bind_mount(
                        self._docker, vol.host_path, vol_dir, archive_name
                    )
                else:
                    continue

                vol.captured = True
                vol.size_bytes = size
                vol.checksum = checksum_file(vol_dir / archive_name)

            except Exception as exc:
                logger.error("Failed to copy volume %s: %s", archive_name, exc)
                await self._emit(
                    manifest.snapshot_id, stack.name, "snapshot.step",
                    f"WARNING: failed to copy {archive_name}: {exc}", "warning",
                )

    async def _save_images(
        self,
        stack: clf.DetectedStack,
        manifest: Manifest,
        snapshot_dir: Path,
    ) -> None:
        """Save each unique container image as a Docker image tar archive.

        Images are deduplicated by tag so a multi-container stack that reuses
        the same image only saves it once.  The tar is written directly to the
        daemon's filesystem via the SDK's ``image.save()`` generator.
        """
        img_dir = snapshot_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        seen: set[str] = set()
        for container in stack.containers:
            tag = container.image.tags[0] if container.image.tags else container.image.short_id
            digest = (
                container.image.attrs.get("RepoDigests", [None])[0]
                if container.image.attrs.get("RepoDigests")
                else None
            )
            if tag in seen:
                continue
            seen.add(tag)

            # Build a filesystem-safe filename from the tag
            safe = tag.replace("/", "_").replace(":", "_").replace("@", "_")
            archive_filename = f"{safe}.tar"
            archive_path = img_dir / archive_filename

            image_manifest = ImageManifest(
                tag=tag,
                digest=digest,
                archive_filename=archive_filename,
            )
            manifest.images.append(image_manifest)

            await self._emit(
                manifest.snapshot_id, stack.name, "snapshot.step",
                f"Saving image {tag}…", "running",
            )

            def _save(img_tag: str, dest: Path) -> int:
                try:
                    img = self._docker.images.get(img_tag)
                    with dest.open("wb") as fh:
                        for chunk in img.save(named=True):
                            fh.write(chunk)
                    return dest.stat().st_size
                except Exception as exc:
                    logger.error("Failed to save image %s: %s", img_tag, exc)
                    raise

            try:
                size = await asyncio.to_thread(_save, tag, archive_path)
                image_manifest.size_bytes = size
                image_manifest.captured = True
            except Exception as exc:
                await self._emit(
                    manifest.snapshot_id, stack.name, "snapshot.step",
                    f"WARNING: could not save image {tag}: {exc}", "warning",
                )

    def _read_file_via_sidecar(self, host_file_path: str) -> bytes | None:
        """Read a host file that may not be directly accessible from this container.

        Creates a short-lived Alpine sidecar with the file's parent directory
        bind-mounted (read-only) and retrieves the file via ``get_archive``.
        Docker Desktop for Windows translates Windows paths (``D:\\...``) to the
        correct Linux paths inside the VM automatically.
        """
        # Python's Path() treats backslashes as literal characters on Linux, so
        # 'D:\\foo\\bar.yml' would have parent='.' (the entire string is the
        # "filename").  Use a regex to split any path style correctly.
        m = re.match(r'^(.+)[/\\]([^/\\]+)$', host_file_path)
        if m:
            parent, name = m.group(1), m.group(2)
        else:
            fp = Path(host_file_path)
            parent, name = str(fp.parent), fp.name
        container = None
        try:
            _ensure_image(self._docker, "alpine:3.19")
            container = self._docker.containers.create(
                "alpine:3.19",
                command=["true"],
                volumes={parent: {"bind": "/tmp/_sd_read", "mode": "ro"}},
            )
            container.start()
            container.wait()
            chunks, _ = container.get_archive(f"/tmp/_sd_read/{name}")
            wrapper = b"".join(chunks)
            with tarfile.open(fileobj=io.BytesIO(wrapper)) as tf:
                member = tf.getmembers()[0]
                fobj = tf.extractfile(member)
                return fobj.read() if fobj else None
        except Exception as exc:
            exc_str = str(exc)
            # 404 = file simply doesn't exist in the mounted directory (e.g.
            # optional .env files).  Log at DEBUG to avoid noise.
            if "404" in exc_str or "Not Found" in exc_str:
                logger.debug("File not present in sidecar mount %s: %s", host_file_path, exc)
            else:
                logger.warning("Could not read %s via sidecar: %s", host_file_path, exc)
            return None
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _save_config_layer(
        self,
        stack: clf.DetectedStack,
        manifest: Manifest,
        snapshot_dir: Path,
    ) -> None:
        config_dir = snapshot_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        saved_compose: list[str] = []
        saved_env: list[str] = []

        for cf_path in stack.compose_files:
            # Extract just the bare filename, handling Windows paths on Linux
            # (Path().name treats backslashes as literal chars on Linux, so
            # Path('D:\\foo\\bar.yml').name == 'D:\\foo\\bar.yml', not 'bar.yml').
            m_name = re.search(r'[^/\\]+$', cf_path)
            filename = m_name.group(0) if m_name else Path(cf_path).name
            dst = config_dir / filename

            src = Path(cf_path)
            # 1. Direct access (Linux host or same filesystem)
            if not src.exists():
                # 2. Docker Desktop Windows→Linux host mount translation
                #    (/run/desktop/mnt/host is bind-mounted into this container)
                translated = _win_to_docker_desktop_path(cf_path)
                if translated and translated.exists():
                    src = translated
            if src.exists():
                shutil.copy2(src, dst)
                saved_compose.append(filename)
            else:
                # 3. File lives inside the Portainer container filesystem
                #    (e.g. /data/compose/11/v1/docker-compose.yml)
                portainer_data = read_file_from_portainer(self._docker, cf_path)
                if portainer_data is not None:
                    dst.write_text(portainer_data, encoding="utf-8")
                    saved_compose.append(filename)
                else:
                    logger.warning("Could not capture compose file: %s", cf_path)

        # Save merged config output if available
        if stack.compose_config_yaml:
            merged = config_dir / "compose.merged.yml"
            merged.write_text(stack.compose_config_yaml, encoding="utf-8")
            saved_compose.append("compose.merged.yml")

        # Collect and save env files referenced in compose
        if stack.working_dir:
            working = Path(stack.working_dir)
            # Also try Docker Desktop translation if the working dir is a Windows path
            if not working.exists():
                translated = _win_to_docker_desktop_path(stack.working_dir)
                if translated and translated.exists():
                    working = translated
            for env_filename in (".env", "web.env", "db.env", ".env.local"):
                env_path = working / env_filename
                if env_path.exists():
                    shutil.copy2(env_path, config_dir / env_filename)
                    saved_env.append(env_filename)
                else:
                    data = self._read_file_via_sidecar(str(Path(stack.working_dir) / env_filename))
                    if data is not None:
                        (config_dir / env_filename).write_bytes(data)
                        saved_env.append(env_filename)

        manifest.config.compose_files = saved_compose

        # Capture the effective runtime env from each running container so that
        # env vars injected by Portainer (or .env files) are preserved in the
        # snapshot and can be passed to `docker compose up` on restore.
        # Vars that are Docker-internal or host-specific are excluded.
        _SKIP_ENV = frozenset({
            "PATH", "HOSTNAME", "HOME", "TERM", "SHLVL", "_", "PWD", "OLDPWD",
        })
        stack_env: dict[str, str] = {}
        for container in stack.containers:
            for entry in (container.attrs.get("Config", {}).get("Env") or []):
                if "=" in entry:
                    k, _, v = entry.partition("=")
                    if k not in _SKIP_ENV:
                        stack_env.setdefault(k, v)  # first container's value wins
        if stack_env:
            import json as _json
            (config_dir / "stack.env.json").write_text(
                _json.dumps(stack_env, indent=2), encoding="utf-8"
            )
            saved_env.append("stack.env.json")

        manifest.config.env_files = saved_env

    def _encrypt_snapshot(self, snapshot_dir: Path, key: bytes) -> None:
        """Encrypt config files and volume archives in-place."""
        config_dir = snapshot_dir / "config"
        if config_dir.exists():
            for path in sorted(config_dir.rglob("*")):
                if path.is_file() and not path.name.endswith(".enc"):
                    encrypt_file(path, key)

        vol_dir = snapshot_dir / "volumes"
        if vol_dir.exists():
            for path in sorted(vol_dir.rglob("*.tar.gz")):
                encrypt_file(path, key)

    # ------------------------------------------------------------------ #
    # Pending-restart flag                                                  #
    # ------------------------------------------------------------------ #

    def _write_pending_restart(self, stack_name: str) -> None:
        _PENDING_RESTART_DIR.mkdir(parents=True, exist_ok=True)
        (_PENDING_RESTART_DIR / stack_name).touch()

    def _clear_pending_restart(self, stack_name: str) -> None:
        flag = _PENDING_RESTART_DIR / stack_name
        try:
            flag.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not clear pending_restart for %s: %s", stack_name, exc)

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    def _apply_hook_results(
        self, manifest: Manifest, results: list[HookResult], phase: str
    ) -> None:
        svc_map = {svc.name: svc for svc in manifest.services}
        for r in results:
            svc_m = svc_map.get(r.service)
            if svc_m is None:
                continue
            if phase == "pre":
                svc_m.pre_hook = r.command
                svc_m.pre_hook_outcome = r.outcome
            else:
                svc_m.post_hook = r.command
                svc_m.post_hook_outcome = r.outcome

    async def _emit(
        self,
        snapshot_id: str,
        stack_name: str,
        event_type: str,
        message: str,
        status: str = "running",
        data: dict | None = None,
    ) -> None:
        event = SnapDockEvent(
            event_type=event_type,
            stack_name=stack_name,
            snapshot_id=snapshot_id,
            message=message,
            status=status,
            data=data or {},
        )
        logger.info("[%s] %s", stack_name, message)
        await self._bus.publish(event)
