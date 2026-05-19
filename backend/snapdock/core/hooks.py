"""Pre- and post-snapshot hook execution.

Hooks are defined in ``snapdock.yml`` (alongside ``compose.yml``):

    hooks:
      pre_snapshot:
        - service: web
          exec: "php artisan cache:clear"
      post_snapshot:
        - service: web
          exec: "php artisan queue:restart"

Hooks run inside the named service container via ``docker exec``.
A failed hook is **never** a hard failure — it is logged and flagged in the
manifest, but the snapshot continues.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from docker import DockerClient

if TYPE_CHECKING:
    from snapdock.core.classifier import DetectedStack

logger = logging.getLogger(__name__)

_HOOKS_FILENAME = "snapdock.yml"


@dataclass
class HookSpec:
    service: str
    exec: str


@dataclass
class HookResult:
    service: str
    command: str
    outcome: str   # "ok" | "failed" | "skipped"
    stdout: str = ""
    stderr: str = ""


def load_hooks(working_dir: str | None, stack_name: str) -> dict[str, list[HookSpec]]:
    """Load hooks from ``snapdock.yml`` in *working_dir*.

    Returns ``{"pre_snapshot": [...], "post_snapshot": [...]}``; empty lists
    if the file is absent or has no matching keys.
    """
    if not working_dir:
        return {"pre_snapshot": [], "post_snapshot": []}

    hooks_file = Path(working_dir) / _HOOKS_FILENAME
    if not hooks_file.exists():
        return {"pre_snapshot": [], "post_snapshot": []}

    try:
        raw = yaml.safe_load(hooks_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse %s: %s", hooks_file, exc)
        return {"pre_snapshot": [], "post_snapshot": []}

    hooks_raw = raw.get("hooks", {}) if isinstance(raw, dict) else {}
    result: dict[str, list[HookSpec]] = {"pre_snapshot": [], "post_snapshot": []}

    for phase in ("pre_snapshot", "post_snapshot"):
        for entry in hooks_raw.get(phase, []):
            if isinstance(entry, dict) and "service" in entry and "exec" in entry:
                result[phase].append(HookSpec(service=entry["service"], exec=entry["exec"]))

    return result


async def run_hooks(
    docker_client: DockerClient,
    stack: "DetectedStack",
    hook_specs: list[HookSpec],
    log_dir: Path,
    phase: str,
) -> list[HookResult]:
    """Execute *hook_specs* inside their respective service containers.

    Results are written to *log_dir* (one .log file per service).
    Always returns results — never raises.
    """
    results: list[HookResult] = []
    container_map = {c.name.lstrip("/"): c for c in stack.containers}
    # Compose containers: name is <project>_<service>_<replica>
    # Also try matching by compose service label
    for container in stack.containers:
        svc = container.labels.get("com.docker.compose.service", "")
        if svc:
            container_map[svc] = container

    for spec in hook_specs:
        container = container_map.get(spec.service)
        if container is None:
            result = HookResult(
                service=spec.service,
                command=spec.exec,
                outcome="skipped",
                stderr=f"container for service '{spec.service}' not found",
            )
            logger.warning("Hook skipped: %s", result.stderr)
            results.append(result)
            continue

        result = await _run_single_hook(docker_client, container.id, spec)
        results.append(result)

        # Write log
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{phase}_{spec.service}.log"
        log_file.write_text(
            f"# {phase} hook: {spec.service}\n"
            f"# command: {spec.exec}\n"
            f"# outcome: {result.outcome}\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n",
            encoding="utf-8",
        )

    return results


async def _run_single_hook(
    docker_client: DockerClient,
    container_id: str,
    spec: HookSpec,
) -> HookResult:
    def _do() -> HookResult:
        try:
            container = docker_client.containers.get(container_id)
            exit_code, output = container.exec_run(
                ["sh", "-c", spec.exec],
                demux=True,
            )
            stdout = (output[0] or b"").decode("utf-8", errors="replace")
            stderr = (output[1] or b"").decode("utf-8", errors="replace")
            outcome = "ok" if exit_code == 0 else "failed"
            if outcome == "failed":
                logger.warning(
                    "Hook '%s' on service '%s' exited %d",
                    spec.exec,
                    spec.service,
                    exit_code,
                )
            return HookResult(
                service=spec.service,
                command=spec.exec,
                outcome=outcome,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception as exc:
            logger.warning(
                "Hook '%s' on service '%s' raised: %s",
                spec.exec,
                spec.service,
                exc,
            )
            return HookResult(
                service=spec.service,
                command=spec.exec,
                outcome="failed",
                stderr=str(exc),
            )

    return await asyncio.to_thread(_do)
