"""Container classifier — groups Docker containers into stacks.

Detection hierarchy (from the plan):
  1. Has ``com.docker.compose.project`` label  → Compose stack
  2. Shares a user-defined network with others → Interconnected group
  3. Shares a named volume with others         → Interconnected group
  4. Neither                                   → Solo container
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal

from docker import DockerClient
from docker.models.containers import Container

logger = logging.getLogger(__name__)

StackType = Literal["compose", "interconnected", "solo"]


def read_file_from_portainer(docker_client: DockerClient, file_path: str) -> str | None:
    """Read a file from the Portainer container filesystem via the Docker socket.

    Portainer stores stack compose files inside its own container at
    ``/data/compose/<id>/v<n>/docker-compose.yml``.  Because the SnapDock
    container only has a Docker socket mount (not a bind-mount of Portainer's
    data directory) we use ``container.get_archive()`` — the same mechanism
    as ``docker cp`` — to pull the file out without any extra privileges.
    """
    try:
        for container in docker_client.containers.list():
            image = container.attrs.get("Config", {}).get("Image", "").lower()
            if "portainer" not in image:
                continue
            try:
                chunks, _ = container.get_archive(file_path)
                raw = b"".join(chunks)
                with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                    members = tf.getmembers()
                    if not members:
                        continue
                    fobj = tf.extractfile(members[0])
                    if fobj:
                        return fobj.read().decode("utf-8", errors="replace")
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Portainer compose read failed for %s: %s", file_path, exc)
    return None


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


@dataclass
class DetectedStack:
    name: str
    type: StackType
    containers: list[Container]
    compose_files: list[str] = field(default_factory=list)
    working_dir: str | None = None
    # Merged compose config (from `docker compose config`); None if unavailable
    compose_config_yaml: str | None = None
    config_hash_match: bool = True
    inferred_reason: str | None = None  # for interconnected groups
    # Names of other compose stacks that share a network or volume with this one
    coupled_stacks: list[str] = field(default_factory=list)


class ContainerClassifier:
    def __init__(self, docker_client: DockerClient) -> None:
        self._docker = docker_client

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    def classify_all(self) -> list[DetectedStack]:
        """Classify all running containers and return a list of DetectedStacks."""
        containers: list[Container] = self._docker.containers.list()

        compose_groups: dict[str, list[Container]] = {}
        standalone: list[Container] = []

        for container in containers:
            project = container.labels.get("com.docker.compose.project")
            if project:
                compose_groups.setdefault(project, []).append(container)
            else:
                standalone.append(container)

        stacks: list[DetectedStack] = []

        for project_name, project_containers in compose_groups.items():
            stacks.append(self._build_compose_stack(project_name, project_containers))

        stacks.extend(self._classify_standalone(standalone))

        # Detect cross-project coupling between compose stacks
        _annotate_cross_project_coupling(stacks)

        return stacks

    def get_stack(self, name: str) -> DetectedStack | None:
        """Return the DetectedStack with the given name, or None."""
        for stack in self.classify_all():
            if stack.name == name:
                return stack
        return None

    # ------------------------------------------------------------------ #
    # Compose stacks                                                        #
    # ------------------------------------------------------------------ #

    def _build_compose_stack(
        self, project_name: str, containers: list[Container]
    ) -> DetectedStack:
        sample = containers[0]
        raw_config_files = sample.labels.get(
            "com.docker.compose.project.config_files", ""
        )
        compose_files = [f.strip() for f in raw_config_files.split(",") if f.strip()]
        working_dir = sample.labels.get("com.docker.compose.project.working_dir")

        # Detect config-hash mismatch
        stored_hash = sample.labels.get("com.docker.compose.config-hash", "")
        config_hash_match = True
        compose_config_yaml: str | None = None

        if compose_files:
            compose_config_yaml, config_hash_match = self._run_compose_config(
                compose_files, working_dir, stored_hash
            )

        return DetectedStack(
            name=project_name,
            type="compose",
            containers=containers,
            compose_files=compose_files,
            working_dir=working_dir,
            compose_config_yaml=compose_config_yaml,
            config_hash_match=config_hash_match,
        )

    def _read_from_portainer(self, file_path: str) -> str | None:
        """Read a file from the Portainer container filesystem via the Docker socket."""
        return read_file_from_portainer(self._docker, file_path)

    def _run_compose_config(
        self,
        compose_files: list[str],
        working_dir: str | None,
        stored_hash: str,
    ) -> tuple[str | None, bool]:
        """Run ``docker compose config`` and return (yaml_output, hash_match).

        Falls back gracefully if the compose files are missing or the command fails.
        Resolves file paths in priority order:
          1. Direct filesystem access (Linux host or same filesystem)
          2. Docker Desktop WSL2 host mount translation (D:\\... → /run/desktop/mnt/host/...)
          3. Docker cp from the Portainer container (/data/compose/... paths)
        """
        tmp_dir: Path | None = None
        effective_files: list[str] = []

        try:
            for cf in compose_files:
                p = Path(cf)
                if p.exists():
                    effective_files.append(cf)
                    continue
                # Try Docker Desktop WSL2 translation
                t = _win_to_docker_desktop_path(cf)
                if t is not None and t.exists():
                    effective_files.append(str(t))
                    continue
                # Try reading from the Portainer container filesystem
                content = self._read_from_portainer(cf)
                if content is not None:
                    if tmp_dir is None:
                        tmp_dir = Path(tempfile.mkdtemp(prefix="snapdock_cc_"))
                    tmp_path = tmp_dir / Path(cf).name
                    tmp_path.write_text(content, encoding="utf-8")
                    effective_files.append(str(tmp_path))
                    continue
                # Not resolvable; keep original path so the warning is informative
                effective_files.append(cf)

            # Resolve cwd: default to None (current dir) when inaccessible
            effective_cwd: str | None = None
            if working_dir:
                wd = Path(working_dir)
                if wd.exists():
                    effective_cwd = working_dir
                else:
                    t = _win_to_docker_desktop_path(working_dir)
                    if t is not None and t.exists():
                        effective_cwd = str(t)
            # If we extracted files into a temp dir, use that as cwd so relative
            # references inside the compose file resolve correctly
            if effective_cwd is None and tmp_dir is not None:
                effective_cwd = str(tmp_dir)

            cmd = ["docker", "compose"]
            for cf in effective_files:
                cmd += ["-f", cf]
            cmd.append("config")

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=effective_cwd,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning(
                    "docker compose config failed for files %s: %s", compose_files, exc
                )
                return None, True

            # If interpolation failed due to missing env vars (common for
            # Portainer-managed stacks where vars are injected at deploy time
            # and are not present in the SnapDock container), retry without
            # variable substitution so we still capture the raw compose YAML.
            if result.returncode != 0:
                stderr = result.stderr
                missing_var = (
                    "missing a value" in stderr
                    or "required variable" in stderr
                    or "is not set" in stderr
                )
                if missing_var:
                    try:
                        result = subprocess.run(
                            cmd + ["--no-interpolate"],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            cwd=effective_cwd,
                        )
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

            if result.returncode != 0:
                logger.warning(
                    "docker compose config exited %d: %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return None, True

            yaml_output = result.stdout

            # Note: com.docker.compose.config-hash is Docker's SHA256 of the
            # canonical JSON config, which differs from SHA256(yaml_output).
            # A comparison here always produces false positives, so we skip it.

            return yaml_output, True

        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Standalone containers                                                 #
    # ------------------------------------------------------------------ #

    def _classify_standalone(
        self, containers: list[Container]
    ) -> list[DetectedStack]:
        if not containers:
            return []

        nodes = [c.id for c in containers]
        edges: list[tuple[str, str, str]] = []  # (id_a, id_b, reason)

        # Collect shared user-defined networks
        net_containers: dict[str, list[str]] = {}
        for c in containers:
            for net_name in c.attrs.get("NetworkSettings", {}).get("Networks", {}).keys():
                if net_name not in ("bridge", "host", "none"):
                    net_containers.setdefault(net_name, []).append(c.id)

        for net_name, ids in net_containers.items():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    edges.append((ids[i], ids[j], f"shares network `{net_name}`"))

        # Collect shared named volumes
        vol_containers: dict[str, list[str]] = {}
        for c in containers:
            for mount in c.attrs.get("Mounts", []):
                if mount.get("Type") == "volume" and mount.get("Name"):
                    vol_containers.setdefault(mount["Name"], []).append(c.id)

        for vol_name, ids in vol_containers.items():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    edges.append((ids[i], ids[j], f"shares volume `{vol_name}`"))

        # Union-find connected components
        components = _connected_components(
            nodes, [(a, b) for a, b, _ in edges]
        )

        # Build reason map
        reason_map: dict[frozenset[str], set[str]] = {}
        for a, b, reason in edges:
            key = frozenset([a, b])
            reason_map.setdefault(key, set()).add(reason)

        container_map = {c.id: c for c in containers}
        stacks: list[DetectedStack] = []

        for component in components:
            members = [container_map[cid] for cid in component]
            if len(members) == 1:
                c = members[0]
                stacks.append(
                    DetectedStack(name=c.name, type="solo", containers=[c])
                )
            else:
                reasons: set[str] = set()
                for r in reason_map.values():
                    reasons.update(r)
                group_name = "_".join(
                    sorted(c.name.lstrip("/") for c in members)[:2]
                )
                stacks.append(
                    DetectedStack(
                        name=group_name,
                        type="interconnected",
                        containers=members,
                        inferred_reason=", ".join(sorted(reasons)),
                    )
                )

        return stacks


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _annotate_cross_project_coupling(stacks: list[DetectedStack]) -> None:
    """Detect cross-project coupling between all stacks (compose, interconnected, solo).

    Any two stacks are considered coupled when any container from one stack
    shares a user-defined Docker network or a named volume with any container
    from the other.  Coupling information is stored in each stack's
    ``coupled_stacks`` list so the API can surface it.
    """

    # Pre-compute sets of user-defined networks and named volumes per stack
    def nets(s: DetectedStack) -> set[str]:
        result: set[str] = set()
        for c in s.containers:
            for net in c.attrs.get("NetworkSettings", {}).get("Networks", {}).keys():
                if net not in ("bridge", "host", "none"):
                    result.add(net)
        return result

    def vols(s: DetectedStack) -> set[str]:
        result: set[str] = set()
        for c in s.containers:
            for m in c.attrs.get("Mounts", []):
                if m.get("Type") == "volume" and m.get("Name"):
                    result.add(m["Name"])
        return result

    net_sets = {s.name: nets(s) for s in stacks}
    vol_sets = {s.name: vols(s) for s in stacks}

    for i, a in enumerate(stacks):
        for b in stacks[i + 1:]:
            shared = (net_sets[a.name] & net_sets[b.name]) | (vol_sets[a.name] & vol_sets[b.name])
            if shared:
                if b.name not in a.coupled_stacks:
                    a.coupled_stacks.append(b.name)
                if a.name not in b.coupled_stacks:
                    b.coupled_stacks.append(a.name)


def _connected_components(
    nodes: list[str], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Return connected components via union-find (no external dependencies)."""
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for a, b in edges:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for n in nodes:
        root = find(n)
        groups.setdefault(root, []).append(n)

    return list(groups.values())
