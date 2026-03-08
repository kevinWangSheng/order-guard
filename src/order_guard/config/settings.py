"""Pydantic Settings for OrderGuard configuration."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Sub-models (not BaseSettings — nested under the root)
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    name: str = "OrderGuard"
    debug: bool = False
    log_level: str = "INFO"
    log_dir: str = "logs"


class LLMConfig(BaseModel):
    model: str = "openai/gpt-4o"
    api_key: SecretStr = SecretStr("")
    api_base: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.1


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/orderguard.db"


class ConnectorConfig(BaseModel):
    name: str
    type: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class AlertChannelConfig(BaseModel):
    name: str
    type: str = "webhook"
    url: str = ""
    enabled: bool = True


class AlertsConfig(BaseModel):
    channels: list[AlertChannelConfig] = Field(default_factory=list)
    silence_minutes: int = 30  # 0 = disabled; skip duplicate alerts within this window


class SchedulerJobConfig(BaseModel):
    name: str
    cron: str
    rule_ids: list[str] = Field(default_factory=list)
    connector: str = "mock"


class MCPDBHubDatabaseConfig(BaseModel):
    alias: str
    dsn: str
    query_timeout: int | None = None


class MCPDBHubSecurityConfig(BaseModel):
    readonly: bool = True
    max_rows: int = 1000


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str
    type: str = "generic"            # "generic" | "dbhub"
    transport: str = "stdio"         # "stdio" or "sse"
    command: str | None = None       # stdio mode (generic)
    args: list[str] = Field(default_factory=list)  # stdio mode (generic)
    url: str | None = None           # sse mode
    headers: dict[str, str] = Field(default_factory=dict)  # sse mode
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    # DBHub-specific fields
    databases: list[MCPDBHubDatabaseConfig] = Field(default_factory=list)
    security: MCPDBHubSecurityConfig = Field(default_factory=MCPDBHubSecurityConfig)


class SchedulerConfig(BaseModel):
    enabled: bool = True
    jobs: list[SchedulerJobConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Environment variable reference resolver (${VAR_NAME} syntax)
# ---------------------------------------------------------------------------

_ENV_REF_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_refs(data: Any) -> Any:
    """Recursively resolve ${VAR} references in string values."""
    if isinstance(data, str):
        def _replace(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _ENV_REF_PATTERN.sub(_replace, data)
    if isinstance(data, dict):
        return {k: _resolve_env_refs(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_env_refs(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Root configuration — merges YAML file + environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="OG_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    @model_validator(mode="before")
    @classmethod
    def load_yaml_config(cls, data: Any) -> Any:
        """Load config.yaml and merge with env-provided values."""
        if not isinstance(data, dict):
            data = {}

        # Determine YAML path: env var or default
        yaml_path = os.environ.get("OG_CONFIG_FILE", "config.yaml")
        path = Path(yaml_path)

        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
            # Resolve ${VAR} references in YAML values
            yaml_data = _resolve_env_refs(yaml_data)
            # YAML is the base, env-provided values override
            _deep_merge(yaml_data, data)
            data = yaml_data

        return data


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base (in-place). Override wins for leaf values."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Return the global Settings singleton. Call with reload=True to force re-read."""
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings
