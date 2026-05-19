"""Database quiescing via ``docker exec``.

The daemon detects the database engine from the container's image name and
runs the appropriate quiesce command inside the container before stopping.
If the CLI binary is absent or the command fails the quiesce is skipped with
a warning — it is never a hard failure.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from docker import DockerClient

logger = logging.getLogger(__name__)

_IMAGE_CHECKS = [
    ("postgres", "postgresql_checkpoint"),
    ("mysql",    "mysql_flush_tables"),
    ("mariadb",  "mysql_flush_tables"),
    ("redis",    "redis_bgsave"),
    ("mongo",    "mongodb_fsynclock"),
]


@dataclass
class QuiesceResult:
    method: str | None      # e.g. "postgresql_checkpoint" or None for generic
    outcome: str            # "ok" | "failed" | "skipped"
    message: str = ""


async def quiesce_container(
    docker_client: DockerClient,
    container_id: str,
    image_name: str,
    timeout: int = 30,
) -> QuiesceResult:
    """Detect DB type from image name and run the appropriate quiesce command.

    Returns a ``QuiesceResult`` — never raises.  A failed or missing CLI is
    treated as a skipped quiesce (logged as a warning).
    """
    image_lower = image_name.lower()

    for fragment, method in _IMAGE_CHECKS:
        if fragment in image_lower:
            handler = _HANDLERS.get(method)
            if handler:
                return await handler(docker_client, container_id, method, timeout)

    # Generic: no explicit quiesce; the container will receive SIGTERM on stop
    return QuiesceResult(method=None, outcome="skipped", message="generic container — SIGTERM on stop")


# --------------------------------------------------------------------------- #
# Per-engine handlers                                                           #
# --------------------------------------------------------------------------- #

async def _quiesce_postgres(
    docker_client: DockerClient,
    container_id: str,
    method: str,
    timeout: int,
) -> QuiesceResult:
    # Discover the superuser from the container's environment variables.
    # POSTGRES_USER (or POSTGRES_DB as fallback) is set by the official image.
    # Fall back to "postgres" if neither is present.
    pg_user = "postgres"
    pg_db = "postgres"
    try:
        container = docker_client.containers.get(container_id)
        env_list: list[str] = container.attrs.get("Config", {}).get("Env") or []
        env = dict(e.split("=", 1) for e in env_list if "=" in e)
        pg_user = env.get("POSTGRES_USER") or env.get("PGUSER") or "postgres"
        # POSTGRES_DB may differ from the username; fall back to "postgres"
        # (the maintenance database that always exists) rather than the username.
        pg_db = env.get("POSTGRES_DB") or env.get("PGDATABASE") or "postgres"
    except Exception:
        pass  # best-effort; fall back to safe defaults

    exit_code, output = await _exec(
        docker_client,
        container_id,
        ["psql", "-U", pg_user, "-d", pg_db, "-c", "CHECKPOINT"],
        timeout=timeout,
    )
    if exit_code == 0:
        return QuiesceResult(method=method, outcome="ok", message="CHECKPOINT complete")
    return QuiesceResult(
        method=method,
        outcome="failed",
        message=f"psql CHECKPOINT exited {exit_code}: {output.strip()[:200]}",
    )


async def _quiesce_mysql(
    docker_client: DockerClient,
    container_id: str,
    method: str,
    timeout: int,
) -> QuiesceResult:
    # FLUSH TABLES WITH READ LOCK — requires the lock to be held until stop.
    # We rely on the graceful stop (SIGTERM) releasing it automatically.
    exit_code, output = await _exec(
        docker_client,
        container_id,
        ["mysql", "-u", "root", "-e", "FLUSH TABLES WITH READ LOCK"],
        timeout=timeout,
    )
    if exit_code == 0:
        return QuiesceResult(method=method, outcome="ok", message="FLUSH TABLES WITH READ LOCK issued")
    return QuiesceResult(
        method=method,
        outcome="failed",
        message=f"mysql FLUSH exited {exit_code}: {output.strip()[:200]}",
    )


async def _quiesce_redis(
    docker_client: DockerClient,
    container_id: str,
    method: str,
    timeout: int,
) -> QuiesceResult:
    # Trigger BGSAVE, then poll LASTSAVE until it changes
    exit_code, _ = await _exec(
        docker_client, container_id, ["redis-cli", "BGSAVE"], timeout=10
    )
    if exit_code != 0:
        return QuiesceResult(
            method=method, outcome="failed", message="redis-cli BGSAVE failed"
        )

    # Get initial LASTSAVE timestamp
    _, initial_ts = await _exec(
        docker_client, container_id, ["redis-cli", "LASTSAVE"], timeout=5
    )
    initial_ts = initial_ts.strip()

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1)
        _, current_ts = await _exec(
            docker_client, container_id, ["redis-cli", "LASTSAVE"], timeout=5
        )
        current_ts = current_ts.strip()
        if current_ts != initial_ts:
            return QuiesceResult(method=method, outcome="ok", message="BGSAVE complete")

    return QuiesceResult(
        method=method,
        outcome="failed",
        message=f"BGSAVE did not complete within {timeout}s",
    )


async def _quiesce_mongo(
    docker_client: DockerClient,
    container_id: str,
    method: str,
    timeout: int,
) -> QuiesceResult:
    exit_code, output = await _exec(
        docker_client,
        container_id,
        ["mongosh", "--eval", "db.fsyncLock()"],
        timeout=timeout,
    )
    if exit_code == 0:
        return QuiesceResult(method=method, outcome="ok", message="fsyncLock acquired")
    # Older MongoDB uses 'mongo' instead of 'mongosh'
    exit_code2, output2 = await _exec(
        docker_client,
        container_id,
        ["mongo", "--eval", "db.fsyncLock()"],
        timeout=timeout,
    )
    if exit_code2 == 0:
        return QuiesceResult(method=method, outcome="ok", message="fsyncLock acquired (mongo)")
    return QuiesceResult(
        method=method,
        outcome="failed",
        message=f"fsyncLock failed (mongosh exit {exit_code}, mongo exit {exit_code2})",
    )


_HANDLERS = {
    "postgresql_checkpoint": _quiesce_postgres,
    "mysql_flush_tables":    _quiesce_mysql,
    "redis_bgsave":          _quiesce_redis,
    "mongodb_fsynclock":     _quiesce_mongo,
}


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

async def _exec(
    docker_client: DockerClient,
    container_id: str,
    cmd: list[str],
    timeout: int = 30,
) -> tuple[int, str]:
    """Run *cmd* inside *container_id* via docker exec.

    Returns (exit_code, output_str).  Any exception returns exit_code=1.
    """
    def _do():
        try:
            container = docker_client.containers.get(container_id)
            exit_code, output = container.exec_run(cmd, demux=False)
            decoded = output.decode("utf-8", errors="replace") if output else ""
            return exit_code, decoded
        except Exception as exc:
            logger.warning("docker exec failed on %s: %s", container_id, exc)
            return 1, str(exc)

    try:
        return await asyncio.wait_for(asyncio.to_thread(_do), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("docker exec timed out after %ds on %s", timeout, container_id)
        return 1, f"timed out after {timeout}s"
