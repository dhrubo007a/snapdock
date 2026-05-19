"""Restore orchestration engine — implements the 17-step restore sequence.

Mirrors the snapshot engine's structure:
- Steps 6-12 (modify live state) run inside a try/finally so restart policy
  restoration is always attempted.
- Live data written after the snapshot is LOST on restore.  The confirmation
  modal at the API layer surfaces the exact data-loss window before proceeding.
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
import tempfile
import yaml
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from docker import DockerClient
import docker.errors

from snapdock.config import Settings
from snapdock.core import classifier as clf
from snapdock.core.crypto import decrypt_file
from snapdock.core.health import check_stack_health
from snapdock.core.volume import (
    restore_anonymous_volume,
    restore_bind_mount,
    restore_named_volume,
    _ensure_image,
)
from snapdock.events import EventBus, SnapDockEvent
from snapdock.models.manifest import Manifest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PENDING_RESTORE_DIR = Path("/var/lib/snapdock/state/pending_restore")

# Port offset applied to host ports during dry-run restore
_DRY_RUN_PORT_OFFSET = 10000


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


class RestoreEngine:
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
    # Sidecar file reader                                                  #
    # ------------------------------------------------------------------ #

    def _read_file_via_sidecar(self, host_file_path: str) -> bytes | None:
        """Read a host file via a short-lived Alpine sidecar container.

        Docker Desktop's daemon translates Windows paths in bind-mount volume
        keys (e.g. ``D:\\foo\\bar``) so this works even when the path is not
        directly accessible from inside the Linux container.
        """
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
            if "404" in exc_str or "Not Found" in exc_str:
                logger.debug("File not present in sidecar mount %s", host_file_path)
            else:
                logger.warning("Could not read %s via sidecar: %s", host_file_path, exc)
            return None
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        manifest: Manifest,
        triggered_by: str,
        dry_run: bool = False,
    ) -> bool:
        """Execute restore.  Returns True on success."""
        snap_id = manifest.snapshot_id
        stack_name = manifest.stack.name
        snapshot_dir = Path(manifest.storage.path)

        await self._emit(snap_id, stack_name, "restore.step", "Loading manifest…", "running")

        key = self._settings.get_encryption_key_bytes()

        # ── Step 1-4: Setup ──────────────────────────────────────────── #
        # For dry-run: derive isolated names
        if dry_run:
            restore_suffix = f"_dryrun_{int(datetime.utcnow().timestamp())}"
            await self._emit(
                snap_id, stack_name, "restore.step",
                f"Dry-run restore into isolated environment (suffix={restore_suffix})…",
                "running",
            )
        else:
            restore_suffix = ""
            await self._emit(
                snap_id, stack_name, "restore.step",
                f"Restoring stack '{stack_name}' from {snap_id}…", "running",
            )

        # ── Step 5: Write pending_restore flag ───────────────────────── #
        if not dry_run:
            self._write_pending_restore(stack_name)

        # Get currently running stack containers (if any)
        classifier = clf.ContainerClassifier(self._docker)
        live_stack = await asyncio.to_thread(classifier.get_stack, stack_name)

        restart_policies_saved: dict[str, str] = {}
        success = False
        compose_workdir: Path | None = None

        try:
            # Step 6: Override restart policies on live containers
            if live_stack and not dry_run:
                await self._emit(snap_id, stack_name, "restore.step", "Overriding restart policies…", "running")
                restart_policies_saved = await asyncio.to_thread(
                    self._override_restart_policies, live_stack
                )

            # Step 7: Stop live stack
            if live_stack and not dry_run:
                await self._emit(snap_id, stack_name, "restore.step", "Stopping live stack…", "running")
                await asyncio.to_thread(self._stop_stack, live_stack)

            # Step 8: Restore volumes
            await self._emit(snap_id, stack_name, "restore.step", "Restoring volumes…", "running")
            await self._restore_volumes(manifest, snapshot_dir, key, restore_suffix, dry_run)

            # Step 9: Restore config layer
            await self._emit(snap_id, stack_name, "restore.step", "Restoring config layer…", "running")
            await asyncio.to_thread(
                self._restore_config_layer, manifest, snapshot_dir, key, dry_run
            )

            # Step 10: Pull images if digest mismatch, or load from snapshot
            await self._emit(snap_id, stack_name, "restore.step", "Verifying images…", "running")
            await self._load_images_from_snapshot(manifest, snapshot_dir, key)
            await self._pull_images_if_needed(manifest)

            # Step 11: Start stack
            await self._emit(snap_id, stack_name, "restore.step", "Starting stack…", "running")
            compose_workdir = await asyncio.to_thread(
                self._prepare_compose_workdir, manifest, snapshot_dir, key, dry_run
            )
            await asyncio.to_thread(
                self._start_stack_from_manifest, manifest, restore_suffix, compose_workdir, dry_run
            )

            success = True

        except Exception as exc:
            logger.exception("Restore %s failed: %s", snap_id, exc)
            await self._emit(
                snap_id, stack_name, "restore.error",
                f"Restore failed: {exc}", "error",
            )

        finally:
            # Step 12: Restore restart policies
            if restart_policies_saved and live_stack and not dry_run:
                try:
                    await asyncio.to_thread(
                        self._restore_restart_policies, live_stack, restart_policies_saved
                    )
                except Exception as exc:
                    logger.warning("Could not restore restart policies: %s", exc)

            # Step 13: Clear pending_restore flag
            if not dry_run:
                self._clear_pending_restore(stack_name)

            # On failure, clean up the compose workdir so stale files don't
            # accumulate.  On success it is kept intentionally: container
            # labels written by `docker compose up` point to this stable path
            # so the classifier can re-read the compose files at any time.
            # For dry-run the workdir is always a temp dir — always clean it.
            if compose_workdir and compose_workdir.exists():
                if not success or dry_run:
                    shutil.rmtree(compose_workdir, ignore_errors=True)

        if not success:
            return False

        # ── Step 14: Health verify ───────────────────────────────────── #
        await asyncio.sleep(3)
        refreshed_stack = await asyncio.to_thread(classifier.get_stack, stack_name)
        if refreshed_stack:
            health = await asyncio.to_thread(check_stack_health, self._docker, refreshed_stack)
            await self._emit(
                snap_id, stack_name, "restore.step",
                f"Post-restore health: {health.state}",
                "ok" if health.is_clean else "warning",
            )

        # Dry-run: tear down isolated environment
        if dry_run:
            await self._emit(snap_id, stack_name, "restore.step", "Tearing down dry-run environment…", "running")
            await asyncio.to_thread(self._teardown_dry_run, manifest, restore_suffix)

        # ── Steps 15-17: Notify, audit, complete ─────────────────────── #
        await self._emit(
            snap_id, stack_name, "restore.complete",
            f"{'Dry-run restore' if dry_run else 'Restore'} complete", "ok",
            data={"snapshot_id": snap_id, "dry_run": dry_run},
        )
        return True

    # ------------------------------------------------------------------ #
    # Step implementations                                                  #
    # ------------------------------------------------------------------ #

    def _override_restart_policies(self, stack: clf.DetectedStack) -> dict[str, str]:
        saved: dict[str, str] = {}
        for container in stack.containers:
            policy = container.attrs.get("HostConfig", {}).get("RestartPolicy", {})
            saved[container.id] = policy.get("Name", "no")
            try:
                container.update(restart_policy={"Name": "no", "MaximumRetryCount": 0})
            except Exception as exc:
                logger.warning("Could not override restart policy for %s: %s", container.name, exc)
        return saved

    def _restore_restart_policies(
        self, stack: clf.DetectedStack, saved: dict[str, str]
    ) -> None:
        for container in stack.containers:
            policy_name = saved.get(container.id, "no")
            try:
                container.update(restart_policy={"Name": policy_name, "MaximumRetryCount": 0})
            except Exception as exc:
                logger.warning("Could not restore restart policy for %s: %s", container.name, exc)

    def _stop_stack(self, stack: clf.DetectedStack) -> None:
        for container in reversed(stack.containers):
            try:
                container.stop(timeout=self._settings.stop_timeout)
            except Exception as exc:
                logger.warning("Error stopping %s during restore: %s", container.name, exc)

    async def _restore_volumes(
        self,
        manifest: Manifest,
        snapshot_dir: Path,
        key: bytes,
        restore_suffix: str,
        dry_run: bool,
    ) -> None:
        vol_dir = snapshot_dir / "volumes"

        for vol in manifest.volumes:
            if vol.type == "tmpfs":
                continue
            if not vol.captured:
                label = vol.name or vol.id or vol.host_path or "unknown"
                logger.warning(
                    "Volume '%s' (service=%s, mount=%s) was NOT captured in this snapshot "
                    "— skipping restore; a fresh volume will be created when the stack starts.",
                    label, vol.service, vol.mount_path,
                )
                continue

            archive_filename = vol.archive_filename
            if not archive_filename:
                continue

            # Volumes are stored encrypted (.tar.gz.enc); decrypt to a temp copy first
            enc_path = vol_dir / (archive_filename + ".enc")
            plain_path = vol_dir / archive_filename

            if enc_path.exists():
                # Decrypt to a temp file so we don't modify the snapshot.
                # Use suffix=".tar.gz.enc" so that decrypt_file (which strips
                # the last .enc suffix) outputs a file with the correct
                # .tar.gz extension.
                import tempfile, shutil
                with tempfile.NamedTemporaryFile(
                    suffix=".tar.gz.enc", dir=vol_dir, delete=False
                ) as tmp:
                    tmp_enc_path = Path(tmp.name)
                shutil.copy2(enc_path, tmp_enc_path)
                decrypt_file(tmp_enc_path, key)           # writes to tmp_enc_path.with_suffix("") = .tar.gz
                work_path = tmp_enc_path.with_suffix("")  # the decrypted .tar.gz
            elif plain_path.exists():
                work_path = plain_path
            else:
                logger.warning("Volume archive not found in snapshot: %s", archive_filename)
                continue

            try:
                target_name = (vol.name or vol.id or "unknown") + restore_suffix
                archive_mb = work_path.stat().st_size / (1024 * 1024)

                if vol.type == "named" and vol.name:
                    logger.info(
                        "Restoring named volume '%s' → '%s' (%.1f MB archive)",
                        vol.name, target_name, archive_mb,
                    )
                    await restore_named_volume(self._docker, target_name, work_path)
                    logger.info("Named volume '%s' restored OK", target_name)
                elif vol.type == "anonymous" and vol.id:
                    logger.info(
                        "Restoring anonymous volume %s → '%s' (%.1f MB archive)",
                        vol.id[:12], target_name, archive_mb,
                    )
                    await restore_anonymous_volume(self._docker, target_name, work_path)
                    logger.info("Anonymous volume '%s' restored OK", target_name)
                elif vol.type == "bind" and vol.host_path:
                    if not dry_run:
                        logger.info(
                            "Restoring bind mount '%s' (%.1f MB archive)",
                            vol.host_path, archive_mb,
                        )
                        await restore_bind_mount(self._docker, vol.host_path, work_path)
                        logger.info("Bind mount '%s' restored OK", vol.host_path)
                    else:
                        logger.info("Dry-run: skipping bind mount '%s'", vol.host_path)

            finally:
                # Clean up temp decrypted copy
                if enc_path.exists() and work_path != plain_path:
                    try:
                        work_path.unlink(missing_ok=True)
                    except Exception:
                        pass

    def _restore_config_layer(
        self,
        manifest: Manifest,
        snapshot_dir: Path,
        key: bytes,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return  # Don't overwrite production config during dry-run

        config_dir = snapshot_dir / "config"
        if not config_dir.exists():
            return

        # Resolve the working directory from the first compose file path.
        # If the path isn't accessible from this container (e.g. it's inside a
        # Portainer container at /data/compose/…, or on a Windows host), skip
        # writing back — the saved files will be used by _prepare_compose_workdir
        # → Strategy 1 instead.
        working_dir: Path | None = None
        if manifest.stack.compose_files:
            first = Path(manifest.stack.compose_files[0])
            candidate = first.parent if first.is_absolute() else None
            if candidate:
                if candidate.exists():
                    working_dir = candidate
                else:
                    translated = _win_to_docker_desktop_path(str(candidate))
                    if translated and translated.exists():
                        working_dir = translated

        if not working_dir:
            logger.debug(
                "Config restore to original location skipped: path not accessible (%s);"
                " saved compose files will be used from snapshot.",
                manifest.stack.compose_files[0] if manifest.stack.compose_files else "unknown",
            )
            return

        for enc_path in config_dir.glob("*.enc"):
            import tempfile, shutil as _shutil
            tmp = Path(tempfile.mktemp(dir=config_dir))
            _shutil.copy2(enc_path, tmp.with_suffix(enc_path.suffix))
            plain = decrypt_file(tmp.with_suffix(enc_path.suffix), key)
            dest = working_dir / plain.name
            _shutil.copy2(plain, dest)
            plain.unlink(missing_ok=True)

    async def _load_images_from_snapshot(
        self,
        manifest: Manifest,
        snapshot_dir: Path,
        key: bytes,
    ) -> None:
        """Load image archives saved inside the snapshot into the local Docker daemon.

        If an image is already present locally with a matching tag it is skipped.
        If the archive was encrypted it is decrypted to a temp file first.
        """
        if not manifest.images:
            return  # older snapshot format — nothing to load

        img_dir = snapshot_dir / "images"
        for img_m in manifest.images:
            if not img_m.captured:
                continue

            # Check if image already loaded locally with a matching digest
            try:
                local_img = self._docker.images.get(img_m.tag)
                if img_m.digest:
                    # Both sides may be "name@sha256:..." — extract just the sha256 part
                    def _sha(d: str) -> str:
                        return d.split("@", 1)[-1] if "@" in d else d

                    snap_sha = _sha(img_m.digest)
                    local_shas = [_sha(d) for d in local_img.attrs.get("RepoDigests", [])]
                    if snap_sha in local_shas:
                        logger.info(
                            "Image %s matches snapshot digest (%s…); skipping load",
                            img_m.tag, snap_sha[:19],
                        )
                        continue
                    logger.info(
                        "Image %s present locally but digest differs "
                        "(snapshot=%s…, local=%s…); loading from snapshot",
                        img_m.tag,
                        snap_sha[:19],
                        local_shas[0][:19] if local_shas else "none",
                    )
                else:
                    # No digest recorded in snapshot — fall back to tag-only match
                    logger.info(
                        "Image %s present locally (no digest in snapshot); skipping load",
                        img_m.tag,
                    )
                    continue
            except docker.errors.ImageNotFound:
                pass

            archive_path = img_dir / img_m.archive_filename
            enc_path = archive_path.with_suffix(".tar.enc")

            # Decrypt if encrypted copy exists
            plain_path = archive_path
            if enc_path.exists():
                plain_path = archive_path.parent / (img_m.archive_filename + ".tmp")
                await asyncio.to_thread(decrypt_file, enc_path, plain_path, key)

            if not plain_path.exists():
                logger.warning("Image archive %s not found; skipping", img_m.archive_filename)
                continue

            await self._emit(
                manifest.snapshot_id, manifest.stack.name, "restore.step",
                f"Loading image {img_m.tag} from snapshot…", "running",
            )

            def _load(path: Path) -> None:
                with path.open("rb") as fh:
                    self._docker.images.load(fh.read())

            try:
                await asyncio.to_thread(_load, plain_path)
                logger.info("Loaded image %s from snapshot", img_m.tag)
            except Exception as exc:
                logger.warning("Could not load image %s: %s", img_m.tag, exc)
            finally:
                # Remove temp decrypted file if we created one
                if plain_path != archive_path and plain_path.exists():
                    plain_path.unlink(missing_ok=True)

    async def _pull_images_if_needed(self, manifest: Manifest) -> None:
        for svc in manifest.services:
            if not svc.image_digest:
                continue
            try:
                # Check if the image exists locally with the correct digest
                try:
                    self._docker.images.get(svc.image)
                except docker.errors.ImageNotFound:
                    await self._emit(
                        manifest.snapshot_id, manifest.stack.name, "restore.step",
                        f"Pulling image {svc.image}…", "running",
                    )
                    await asyncio.to_thread(self._docker.images.pull, svc.image)
            except Exception as exc:
                logger.warning("Could not pull image %s: %s", svc.image, exc)

    def _prepare_compose_workdir(
        self,
        manifest: Manifest,
        snapshot_dir: Path,
        key: bytes,
        dry_run: bool = False,
    ) -> "Path | None":
        """Decrypt saved compose/env files to a stable working directory.

        Uses a persistent path on the snapshots volume
        (``<storage_path>/.stacks/<stack_name>/``) so that the
        ``com.docker.compose.project.working_directory`` label written to
        containers by ``docker compose up`` continues to point to a valid
        directory after the restore completes.  Returns *None* if the snapshot
        contains no saved config files.

        For dry-run restores a temporary directory is used so the live
        stack's stable compose workdir is not overwritten.
        """
        if not manifest.config.compose_files:
            return None

        config_dir = snapshot_dir / "config"
        if not config_dir.exists():
            return None

        if dry_run:
            # Temp dir for dry-run: avoids overwriting the live stack's stable
            # compose workdir; cleaned up in run()'s finally block.
            stable_dir = Path(tempfile.mkdtemp(prefix=f"sd_dryrun_{manifest.stack.name}_"))
        else:
            # Stable, persistent path on the named snapshots volume so container
            # labels remain valid and the classifier can re-read compose files.
            stable_dir = self._settings.storage_path / ".stacks" / manifest.stack.name
            stable_dir.mkdir(parents=True, exist_ok=True)
        any_extracted = False

        for filename in manifest.config.compose_files + manifest.config.env_files:
            enc_path = config_dir / (filename + ".enc")
            plain_path = config_dir / filename
            if enc_path.exists():
                tmp_enc = stable_dir / (filename + ".enc")
                shutil.copy2(enc_path, tmp_enc)
                decrypt_file(tmp_enc, key)          # output: stable_dir/<filename>
                tmp_enc.unlink(missing_ok=True)     # remove the .enc copy
                any_extracted = True
            elif plain_path.exists():
                shutil.copy2(plain_path, stable_dir / filename)
                any_extracted = True
            else:
                logger.debug("Config file not found in snapshot: %s", filename)

        if not any_extracted:
            shutil.rmtree(stable_dir, ignore_errors=True)
            return None

        return stable_dir

    def _start_stack_from_manifest(
        self,
        manifest: Manifest,
        restore_suffix: str,
        compose_workdir: "Path | None" = None,
        dry_run: bool = False,
    ) -> None:
        """Start containers using their original images in startup_order.

        Strategy (in order of preference):
        1. ``docker compose up -d`` from *compose_workdir* (decrypted config
           files extracted from the snapshot).  Used when the original compose
           files are not accessible from inside the container (e.g. Windows host
           paths on Docker Desktop).
        2. ``docker compose up -d`` using the original host compose file paths
           (works on Linux/macOS where paths are directly accessible).
        3. SDK ``container.start()`` on any stopped containers belonging to the
           project (fallback when compose is unavailable or paths are
           inaccessible).
        4. Emit a warning and return without raising — the volume data has been
           restored; the user can start the stack manually.

        For dry-run restores a suffixed ``--project-name`` isolates the
        temporary stack, and a compose override that clears all port bindings
        prevents conflicts with the running live stack.  Strategy 3 (SDK
        container.start()) is skipped for dry-run.
        """
        startup_order = manifest.config.startup_order or [
            svc.name for svc in manifest.services
        ]
        stack_name = manifest.stack.name
        # Use a suffixed project name for dry-run to isolate from the live stack.
        project_name = (stack_name + restore_suffix) if dry_run else stack_name
        # Generate a no-ports compose override for dry-run; stored inside
        # compose_workdir (itself a temp dir for dry-run) so cleanup is automatic.
        no_ports_override: "Path | None" = None
        if dry_run and compose_workdir and compose_workdir.exists():
            no_ports_override = self._write_no_ports_override(manifest, compose_workdir)

        # ── Strategy 1: docker compose up from saved snapshot config ──── #
        if not (compose_workdir is not None and compose_workdir.exists() and manifest.config.compose_files):
            logger.debug(
                "[%s] Strategy 1 skipped: compose_workdir=%s, saved_files=%s",
                stack_name, compose_workdir, manifest.config.compose_files,
            )
        if (
            compose_workdir is not None
            and compose_workdir.exists()
            and manifest.config.compose_files
        ):
            compose_files = [
                str(compose_workdir / f) for f in manifest.config.compose_files
            ]
            cmd = [
                "docker", "compose",
                "--project-name", project_name,
                "--project-directory", str(compose_workdir),
            ]
            for cf in compose_files:
                cmd += ["-f", cf]
            if no_ports_override:
                cmd += ["-f", str(no_ports_override)]
            cmd += ["up", "-d"]
            # Build subprocess env: inherit current env then overlay vars captured
            # at snapshot time (Portainer-injected values, etc.)
            compose_env = dict(os.environ)
            _SKIP_ENV = frozenset({
                "PATH", "HOSTNAME", "HOME", "TERM", "SHLVL", "_", "PWD", "OLDPWD",
            })
            stack_env_file = compose_workdir / "stack.env.json"
            if stack_env_file.exists():
                try:
                    for k, v in json.loads(stack_env_file.read_text()).items():
                        if k not in _SKIP_ENV:
                            compose_env[k] = v
                except Exception as exc:
                    logger.warning("Could not load stack.env.json: %s", exc)
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(compose_workdir),
                    env=compose_env,
                )
                if result.returncode == 0:
                    logger.info(
                        "[%s] Stack started via saved compose files", stack_name
                    )
                    return
                logger.warning(
                    "docker compose up from saved files failed (exit %d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
            except FileNotFoundError:
                logger.warning("docker CLI not found; falling back to SDK container start")

        # ── Strategy 2: docker compose up using original host paths ───── #
        # For dry-run without a ports override (snapshot has no compose files),
        # skip to avoid port conflicts with the running live stack.
        if manifest.stack.type == "compose" and manifest.stack.compose_files and (
            not dry_run or no_ports_override is not None
        ):
            compose_files = manifest.stack.compose_files
            accessible = all(Path(cf).exists() for cf in compose_files)

            # 2a. Paths not directly visible (Windows host paths on Docker Desktop).
            #     /run/desktop/mnt/host is bind-mounted into this container from
            #     the WSL2 VM — try translating D:\... to that mount point.
            if not accessible:
                translated = [_win_to_docker_desktop_path(cf) for cf in compose_files]
                if all(p is not None and p.exists() for p in translated):
                    compose_files = [str(p) for p in translated]
                    accessible = True
                    logger.info(
                        "[%s] Resolved compose files via Docker Desktop host mount",
                        stack_name,
                    )

            if accessible:
                working_dir = str(Path(compose_files[0]).parent)
                cmd = [
                    "docker", "compose",
                    "--project-name", project_name,
                    "--project-directory", working_dir,
                ]
                for cf in compose_files:
                    cmd += ["-f", cf]
                if no_ports_override:
                    cmd += ["-f", str(no_ports_override)]
                cmd += ["up", "-d"]
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        cwd=working_dir,
                    )
                    if result.returncode == 0:
                        logger.info(
                            "[%s] Stack started via docker compose up", stack_name
                        )
                        return
                    logger.warning(
                        "docker compose up failed (exit %d): %s",
                        result.returncode,
                        result.stderr.strip(),
                    )
                except FileNotFoundError:
                    logger.warning(
                        "docker CLI not found; falling back to SDK container start"
                    )
            else:
                logger.warning(
                    "[%s] Original compose files not accessible from container; "
                    "falling back to SDK container start",
                    stack_name,
                )

        # ── Strategy 3: SDK container.start() on stopped containers ───── #
        # Skipped for dry-run: SDK start would bind the same host ports as the
        # live stack, causing health-check conflicts.
        if dry_run:
            logger.info(
                "[%s] Dry-run: volume data restored to isolated volumes; "
                "skipping SDK container start.",
                stack_name,
            )
            return
        container_map: dict[str, object] = {}
        for container in self._docker.containers.list(all=True):
            proj = container.labels.get("com.docker.compose.project", "")
            svc = container.labels.get(
                "com.docker.compose.service", container.name.lstrip("/")
            )
            name = container.name.lstrip("/")
            if (
                proj == stack_name
                or name.startswith(f"{stack_name}-")
                or name.startswith(f"{stack_name}_")
            ):
                container_map[svc] = container

        if container_map:
            for svc_name in startup_order:
                container = container_map.get(svc_name)
                if container:
                    try:
                        container.start()
                        logger.info("Started container %s", container.name)
                    except Exception as exc:
                        logger.warning("Could not start %s: %s", svc_name, exc)
            return

        # ── Strategy 4: Nothing to start ─────────────────────────────── #
        logger.warning(
            "[%s] Volume data restored but no containers found to start. "
            "Run `docker compose up -d` manually to bring the stack back up.",
            stack_name,
        )

    def _teardown_dry_run(self, manifest: Manifest, restore_suffix: str) -> None:
        """Remove containers and volumes created during a dry-run restore."""
        for vol in manifest.volumes:
            if vol.type in ("named", "anonymous") and (vol.name or vol.id):
                target_name = (vol.name or vol.id or "") + restore_suffix
                try:
                    v = self._docker.volumes.get(target_name)
                    v.remove(force=True)
                    logger.debug("Dry-run cleanup: removed volume %s", target_name)
                except docker.errors.NotFound:
                    pass
                except Exception as exc:
                    logger.warning("Could not remove dry-run volume %s: %s", target_name, exc)

    # ------------------------------------------------------------------ #
    # Pending-restore flag                                                  #
    # ------------------------------------------------------------------ #

    def _write_pending_restore(self, stack_name: str) -> None:
        _PENDING_RESTORE_DIR.mkdir(parents=True, exist_ok=True)
        (_PENDING_RESTORE_DIR / stack_name).touch()

    def _clear_pending_restore(self, stack_name: str) -> None:
        flag = _PENDING_RESTORE_DIR / stack_name
        try:
            flag.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not clear pending_restore for %s: %s", stack_name, exc)

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write_no_ports_override(manifest: Manifest, workdir: Path) -> Path:
        """Write a compose override that clears all port bindings for dry-run.

        Placed in *workdir* and passed to ``docker compose up`` via ``-f`` so
        that dry-run containers start without binding host ports, preventing
        conflicts with the running live stack.
        """
        services = {svc.name: {"ports": []} for svc in manifest.services}
        override = {"services": services}
        override_path = workdir / "docker-compose.no-ports.yml"
        override_path.write_text(yaml.safe_dump(override, default_flow_style=False))
        return override_path

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
