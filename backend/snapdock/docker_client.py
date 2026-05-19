"""Singleton Docker SDK client."""
from __future__ import annotations

import logging
from functools import lru_cache

import docker
from docker import DockerClient
from docker.errors import DockerException

from snapdock.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_docker_client() -> DockerClient:
    """Return a cached, connected Docker client.

    Raises ``DockerException`` if the daemon is unreachable.
    """
    try:
        client = docker.DockerClient(base_url=settings.docker_socket)
        client.ping()
        logger.info("Docker connection established via %s", settings.docker_socket)
        return client
    except DockerException as exc:
        logger.error("Failed to connect to Docker daemon: %s", exc)
        raise
