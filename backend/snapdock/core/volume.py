"""Volume backup and restore using a temporary Alpine container.

All volume data passes through an ``alpine:3.19`` sidecar so the operation
is storage-driver agnostic and requires no host-level root access.

Backup strategy:  the Alpine container writes the tar stream to ``/tmp``
inside the container (tmpfs) and the file is retrieved with
``container.get_archive()``.  This avoids the log-capture path that
docker-py uses for stdout, which re-encodes bytes > 0x7F through
Latin-1 → UTF-8, corrupting binary data such as gzip archives.

Restore strategy:  the .tar.gz archive is injected into the sidecar via
``container.put_archive()`` (wrapped in an in-memory tar envelope) so the
archive directory never needs to be mounted as a host path.

Backup layout inside the snapshot directory:
  volumes/
    <archive_filename>.tar.gz      ← plaintext (encrypted later by crypto module)
"""
from __future__ import annotations

import asyncio
import io
import logging
import tarfile
from pathlib import Path

from docker import DockerClient
import docker.errors

logger = logging.getLogger(__name__)

_ALPINE_IMAGE = "alpine:3.19"


def _ensure_image(docker_client: DockerClient, image: str) -> None:
    """Pull *image* if it is not present locally."""
    try:
        docker_client.images.get(image)
    except docker.errors.ImageNotFound:
        logger.info("Pulling image %s…", image)
        docker_client.images.pull(image)


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #

async def backup_named_volume(
    docker_client: DockerClient,
    volume_name: str,
    output_dir: Path,
    archive_filename: str,
) -> int:
    """Archive a named Docker volume to *output_dir/archive_filename*.tar.gz.

    Returns the size in bytes of the created archive.
    """
    return await _backup_volume_source(
        docker_client,
        volume_spec={volume_name: {"bind": "/src", "mode": "ro"}},
        output_dir=output_dir,
        archive_filename=archive_filename,
    )


async def backup_anonymous_volume(
    docker_client: DockerClient,
    volume_id: str,
    output_dir: Path,
    archive_filename: str,
) -> int:
    """Archive an anonymous Docker volume (by full ID) to *output_dir*."""
    return await _backup_volume_source(
        docker_client,
        volume_spec={volume_id: {"bind": "/src", "mode": "ro"}},
        output_dir=output_dir,
        archive_filename=archive_filename,
    )


async def backup_bind_mount(
    docker_client: DockerClient,
    host_path: str,
    output_dir: Path,
    archive_filename: str,
) -> int:
    """Archive a bind-mount host directory to *output_dir*."""
    return await _backup_volume_source(
        docker_client,
        volume_spec={host_path: {"bind": "/src", "mode": "ro"}},
        output_dir=output_dir,
        archive_filename=archive_filename,
    )


async def restore_named_volume(
    docker_client: DockerClient,
    volume_name: str,
    archive_path: Path,
) -> None:
    """Restore a named Docker volume from *archive_path* (a .tar.gz file).

    The existing volume (if any) is removed and recreated before restoration.
    """
    await asyncio.to_thread(
        _restore_volume_sync, docker_client, volume_name, archive_path, create_volume=True
    )


async def restore_anonymous_volume(
    docker_client: DockerClient,
    new_volume_name: str,
    archive_path: Path,
) -> None:
    """Create a new named volume and restore content from *archive_path*."""
    await asyncio.to_thread(
        _restore_volume_sync,
        docker_client,
        new_volume_name,
        archive_path,
        create_volume=True,
    )


async def restore_bind_mount(
    docker_client: DockerClient,
    host_path: str,
    archive_path: Path,
) -> None:
    """Restore a bind-mount directory from *archive_path*.

    The host directory is overwritten.  The archive is injected via
    ``put_archive`` so the archive's parent directory is never mounted as a
    host path (it lives on the daemon's named storage volume).
    """

    def _do() -> None:
        _ensure_image(docker_client, _ALPINE_IMAGE)
        container = docker_client.containers.create(
            _ALPINE_IMAGE,
            command=[
                "sh", "-c",
                "rm -rf /dst/* && tar -C /dst -xzf /tmp/restore.tar.gz",
            ],
            volumes={host_path: {"bind": "/dst", "mode": "rw"}},
        )
        try:
            envelope = io.BytesIO()
            with tarfile.open(fileobj=envelope, mode="w") as tf:
                info = tarfile.TarInfo(name="restore.tar.gz")
                info.size = archive_path.stat().st_size
                with archive_path.open("rb") as fh:
                    tf.addfile(info, fh)
            container.put_archive("/tmp", envelope.getvalue())

            container.start()
            result = container.wait()
            if result["StatusCode"] != 0:
                raise RuntimeError(
                    f"Bind-mount restore container exited {result['StatusCode']} "
                    f"for path '{host_path}'"
                )
        finally:
            container.remove(force=True)

    await asyncio.to_thread(_do)


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #

async def _backup_volume_source(
    docker_client: DockerClient,
    volume_spec: dict,
    output_dir: Path,
    archive_filename: str,
) -> int:
    """Run an Alpine sidecar that tars the source volume into *output_dir*.

    The archive is written to ``/tmp/backup.tar.gz`` inside the sidecar's
    tmpfs and then retrieved via ``container.get_archive()``.  This avoids the
    docker-py log-capture path (used by ``containers.run(stdout=True)``) which
    re-encodes bytes > 0x7F as Latin-1 → UTF-8, corrupting binary data.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = (
        archive_filename
        if archive_filename.endswith(".tar.gz")
        else archive_filename + ".tar.gz"
    )
    archive_path = output_dir / archive_name

    def _do() -> int:
        _ensure_image(docker_client, _ALPINE_IMAGE)
        container = docker_client.containers.create(
            _ALPINE_IMAGE,
            command=["tar", "-C", "/src", "-czf", "/tmp/backup.tar.gz", "."],
            volumes=volume_spec,
        )
        try:
            container.start()
            result = container.wait()
            if result["StatusCode"] != 0:
                stderr = (
                    container.logs(stdout=False, stderr=True)
                    .decode("utf-8", errors="replace")
                    .strip()
                )
                raise RuntimeError(
                    f"Volume backup container exited {result['StatusCode']}: {stderr}"
                )
            # get_archive returns a pure binary tar stream — no log encoding.
            # The target file is wrapped in an outer tar layer; unwrap it.
            chunks, _ = container.get_archive("/tmp/backup.tar.gz")
            wrapper_bytes = b"".join(chunks)
            with tarfile.open(fileobj=io.BytesIO(wrapper_bytes)) as tf:
                member = tf.getmembers()[0]
                fobj = tf.extractfile(member)
                if fobj is None:
                    raise RuntimeError(
                        "Could not extract backup archive from sidecar container"
                    )
                archive_path.write_bytes(fobj.read())
        finally:
            container.remove(force=True)
        return archive_path.stat().st_size

    return await asyncio.to_thread(_do)


def _restore_volume_sync(
    docker_client: DockerClient,
    volume_name: str,
    archive_path: Path,
    create_volume: bool,
) -> None:
    """Synchronous restore helper (runs inside a thread).

    The archive is injected into the Alpine sidecar via ``put_archive`` so
    that the archive directory (which lives on a named Docker volume) never
    needs to be mounted as a host path into the sidecar.
    """
    if create_volume:
        # Remove existing volume if present.
        # Stopped containers still hold volume references and prevent removal,
        # so prune them first before deleting the volume.
        try:
            existing = docker_client.volumes.get(volume_name)
            for c in docker_client.containers.list(
                all=True, filters={"volume": volume_name}
            ):
                try:
                    c.remove(force=True)
                    logger.debug("Removed container %s holding volume '%s'", c.name, volume_name)
                except Exception as rm_exc:
                    logger.warning("Could not remove container %s: %s", c.name, rm_exc)
            existing.remove()
            logger.debug("Removed existing volume '%s' before restore", volume_name)
        except docker.errors.NotFound:
            pass
        docker_client.volumes.create(volume_name)

    _ensure_image(docker_client, _ALPINE_IMAGE)
    # Create a stopped container so we can inject the archive via put_archive
    # before running it.
    container = docker_client.containers.create(
        _ALPINE_IMAGE,
        command=["tar", "-C", "/dst", "-xzf", "/tmp/restore.tar.gz"],
        volumes={volume_name: {"bind": "/dst", "mode": "rw"}},
    )
    try:
        # Wrap the .tar.gz in a plain tar envelope so put_archive can copy it
        # to /tmp/restore.tar.gz inside the container.
        envelope = io.BytesIO()
        with tarfile.open(fileobj=envelope, mode="w") as tf:
            info = tarfile.TarInfo(name="restore.tar.gz")
            info.size = archive_path.stat().st_size
            with archive_path.open("rb") as fh:
                tf.addfile(info, fh)
        container.put_archive("/tmp", envelope.getvalue())

        container.start()
        result = container.wait()
        if result["StatusCode"] != 0:
            try:
                err_log = (
                    container.logs(stdout=False, stderr=True)
                    .decode("utf-8", errors="replace")
                    .strip()
                )
            except Exception:
                err_log = "(could not capture logs)"
            raise RuntimeError(
                f"Volume restore container exited {result['StatusCode']} "
                f"for volume '{volume_name}': {err_log}"
            )
        logger.debug("Restored volume '%s' from %s", volume_name, archive_path)
    finally:
        container.remove(force=True)
