"""Application configuration loaded from environment / snapdock.env."""
from __future__ import annotations

import base64
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SNAPDOCK_",
        env_file="snapdock.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Security                                                             #
    # ------------------------------------------------------------------ #
    encryption_key: str = Field(
        ...,
        description=(
            "Base64url-encoded 32-byte AES-256 key. "
            "Generate: python -c \"import os,base64; "
            "print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
        ),
    )
    jwt_secret: str = Field(..., description="Secret key for JWT signing")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours

    # ------------------------------------------------------------------ #
    # Storage                                                              #
    # ------------------------------------------------------------------ #
    storage_path: Path = Path("/var/lib/snapdock/snapshots")
    database_url: str = "postgresql+psycopg2://snapdock:snapdock@db:5432/snapdock"

    # ------------------------------------------------------------------ #
    # Server                                                               #
    # ------------------------------------------------------------------ #
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    allowed_origins: list[str] = Field(
        default=["*"],
        description="List of allowed CORS origins. Set SNAPDOCK_ALLOWED_ORIGINS='[\"https://example.com\"]' in .env",
    )

    # ------------------------------------------------------------------ #
    # Docker                                                               #
    # ------------------------------------------------------------------ #
    docker_socket: str = "unix:///var/run/docker.sock"

    # ------------------------------------------------------------------ #
    # Timeouts (seconds)                                                   #
    # ------------------------------------------------------------------ #
    quiesce_timeout: int = 30
    health_check_timeout: int = 120
    stop_timeout: int = 30

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #
    @field_validator("encryption_key")
    @classmethod
    def _validate_encryption_key(cls, v: str) -> str:
        # Tolerate missing/extra padding
        padded = v + "==" * ((4 - len(v) % 4) % 4)
        try:
            key_bytes = base64.urlsafe_b64decode(padded)
        except Exception as exc:
            raise ValueError("encryption_key must be base64url-encoded") from exc
        if len(key_bytes) != 32:
            raise ValueError(
                f"encryption_key must decode to exactly 32 bytes, got {len(key_bytes)}"
            )
        return v

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #
    def get_encryption_key_bytes(self) -> bytes:
        """Return the raw 32-byte AES-256 key."""
        padded = self.encryption_key + "==" * ((4 - len(self.encryption_key) % 4) % 4)
        return base64.urlsafe_b64decode(padded)


settings = Settings()
