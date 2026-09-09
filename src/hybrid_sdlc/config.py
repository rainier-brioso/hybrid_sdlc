"""Configuration discovery, precedence, and validation."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.errors import ConfigurationError

DEFAULT_SERVER_CANDIDATES = [
    {"url": "http://127.0.0.1:8090/v1"},
    {"url": "http://127.0.0.1:8089/v1"},
]
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_MAX_RETRIES = 3
DEFAULT_TASK_TIMEOUT_SECONDS = 600
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 180
DEFAULT_LOG_BUFFER_CAP_BYTES = 500 * 1024


class ServerCandidateConfig(BaseModel):
    """Configuration for an inference server candidate."""

    model_config = ConfigDict(extra="forbid")

    url: str
    model_alias: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'; must be http or https")
        if not parsed.netloc:
            raise ValueError("URL must include host and port (e.g. http://127.0.0.1:8090/v1)")
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            raise ValueError(f"Port {parsed.port} out of range (1-65535)")
        return v.rstrip("/")


class ToolkitConfig(BaseModel):
    """Top-level toolkit configuration."""

    model_config = ConfigDict(extra="forbid")

    server_candidates: list[ServerCandidateConfig] = Field(
        default_factory=lambda: [ServerCandidateConfig(**c) for c in DEFAULT_SERVER_CANDIDATES]
    )
    selected_model: str = DEFAULT_MODEL
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=1, le=10)
    task_timeout_seconds: int = Field(default=DEFAULT_TASK_TIMEOUT_SECONDS, ge=10, le=3600)
    attempt_timeout_seconds: int = Field(default=DEFAULT_ATTEMPT_TIMEOUT_SECONDS, ge=5, le=1800)
    log_buffer_cap_bytes: int = Field(
        default=DEFAULT_LOG_BUFFER_CAP_BYTES, ge=1024, le=10 * 1024 * 1024
    )
    command_profiles: dict[str, CommandProfile] = Field(default_factory=dict)


def load_config(
    repo_root: Path,
    config_file: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ToolkitConfig:
    """Discover, load, and validate configuration with strict precedence.

    Precedence: CLI arguments > repository hybrid_sdlc.toml > defaults.
    Loading never searches above the verified repo_root.
    """
    repo_root = repo_root.resolve()
    raw_data: dict[str, Any] = {}

    target_path = config_file.resolve() if config_file else (repo_root / "hybrid_sdlc.toml")

    # Ensure config file is not above repository root
    try:
        target_path.relative_to(repo_root)
    except ValueError as e:
        raise ConfigurationError(
            f"Configuration file '{target_path}' is outside repository root '{repo_root}'",
            code="CONFIG_OUTSIDE_REPO",
            details={"config_path": str(target_path), "repo_root": str(repo_root)},
        ) from e

    if target_path.is_file():
        try:
            with target_path.open("rb") as f:
                raw_data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigurationError(
                f"Failed to parse TOML configuration at '{target_path}': {e}",
                code="CONFIG_SYNTAX_ERROR",
                details={"error": str(e), "path": str(target_path)},
            ) from e
        except Exception as e:
            raise ConfigurationError(
                f"Failed to read configuration file at '{target_path}': {e}",
                code="CONFIG_READ_ERROR",
                details={"error": str(e), "path": str(target_path)},
            ) from e
    elif config_file is not None:
        raise ConfigurationError(
            f"Explicitly specified configuration file '{config_file}' does not exist",
            code="CONFIG_NOT_FOUND",
            details={"path": str(config_file)},
        )

    # Process command profiles if present in raw_data
    if "command_profiles" in raw_data:
        profiles_dict = raw_data["command_profiles"]
        if not isinstance(profiles_dict, dict):
            raise ConfigurationError(
                "'command_profiles' must be a mapping of profile names to profile definitions",
                code="CONFIG_INVALID_TYPE",
            )
        processed_profiles = {}
        for name, p_data in profiles_dict.items():
            if not isinstance(p_data, dict):
                raise ConfigurationError(
                    f"Profile '{name}' must be a dictionary",
                    code="CONFIG_INVALID_PROFILE",
                )
            p_data_copy = dict(p_data)
            p_data_copy["name"] = name
            try:
                processed_profiles[name] = CommandProfile(**p_data_copy)
            except Exception as e:
                raise ConfigurationError(
                    f"Invalid configuration for profile '{name}': {e}",
                    code="CONFIG_INVALID_PROFILE",
                    details={"profile": name, "error": str(e)},
                ) from e
        raw_data["command_profiles"] = processed_profiles

    # Apply CLI overrides
    if cli_overrides:
        for k, v in cli_overrides.items():
            if v is not None:
                raw_data[k] = v

    try:
        return ToolkitConfig(**raw_data)
    except Exception as e:
        raise ConfigurationError(
            f"Invalid toolkit configuration: {e}",
            code="CONFIG_VALIDATION_ERROR",
            details={"error": str(e)},
        ) from e
