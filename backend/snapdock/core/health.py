"""Stack health check — determines CLEAN / DEGRADED / BROKEN state."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docker import DockerClient

if TYPE_CHECKING:
    from snapdock.core.classifier import DetectedStack

logger = logging.getLogger(__name__)

HealthState = str  # "CLEAN" | "DEGRADED" | "BROKEN"


@dataclass
class HealthReport:
    state: HealthState
    running: list[str]
    stopped: list[str]
    unhealthy: list[str]

    @property
    def is_clean(self) -> bool:
        return self.state == "CLEAN"

    @property
    def requires_confirmation(self) -> bool:
        return self.state in ("DEGRADED", "BROKEN")

    @property
    def requires_checkbox(self) -> bool:
        return self.state == "BROKEN"

    def summary(self) -> str:
        parts = [f"state={self.state}"]
        if self.stopped:
            parts.append(f"stopped=[{', '.join(self.stopped)}]")
        if self.unhealthy:
            parts.append(f"unhealthy=[{', '.join(self.unhealthy)}]")
        return " ".join(parts)


def check_stack_health(
    docker_client: DockerClient,
    stack: "DetectedStack",
) -> HealthReport:
    """Inspect every container in *stack* and return a ``HealthReport``.

    Classification rules (mirrors the plan):
    - CLEAN   — all containers running and healthy
    - DEGRADED — some containers down but at least one running
    - BROKEN  — all containers down OR no containers exist
    """
    running: list[str] = []
    stopped: list[str] = []
    unhealthy: list[str] = []

    for container in stack.containers:
        try:
            # Reload fresh state from Docker
            container.reload()
        except Exception as exc:
            logger.warning("Could not reload container %s: %s", container.name, exc)
            stopped.append(container.name)
            continue

        status = container.status  # "running", "exited", "paused", etc.
        health = (
            container.attrs.get("State", {})
            .get("Health", {})
            .get("Status", "none")
        )  # "healthy" | "unhealthy" | "starting" | "none"

        if status != "running":
            stopped.append(container.name)
        elif health == "unhealthy":
            unhealthy.append(container.name)
            running.append(container.name)
        else:
            running.append(container.name)

    total = len(stack.containers)

    if not stopped and not unhealthy:
        state: HealthState = "CLEAN"
    elif stopped and len(stopped) >= total:
        state = "BROKEN"
    elif stopped or unhealthy:
        state = "DEGRADED"
    else:
        state = "CLEAN"

    report = HealthReport(
        state=state,
        running=running,
        stopped=stopped,
        unhealthy=unhealthy,
    )
    logger.info("Health check for stack '%s': %s", stack.name, report.summary())
    return report
