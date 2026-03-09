"""MCP configuration and tool info models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DBHubDatabaseConfig(BaseModel):
    """A single database source for DBHub."""

    alias: str                       # e.g. "warehouse", "erp"
    dsn: str                         # e.g. "sqlite:///data/warehouse.db"
    query_timeout: int | None = None  # seconds, None = no limit


class DBHubSecurityConfig(BaseModel):
    """Security settings applied to DBHub tools."""

    readonly: bool = True
    max_rows: int = 1000


class SchemaFilterConfig(BaseModel):
    """Configuration for filtering sensitive tables/columns from schema context."""

    blocked_tables: list[str] = Field(default_factory=list)
    blocked_columns: list[str] = Field(default_factory=list)
    cold_tables: list[str] = Field(default_factory=list)  # Tables marked as archive/cold


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str
    type: str = "generic"            # "generic" | "dbhub"
    transport: Literal["stdio", "sse"] = "stdio"
    command: str | None = None       # stdio mode (generic)
    args: list[str] = Field(default_factory=list)  # stdio mode (generic)
    url: str | None = None           # sse mode
    headers: dict[str, str] = Field(default_factory=dict)  # sse mode
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    # DBHub-specific fields
    databases: list[DBHubDatabaseConfig] = Field(default_factory=list)
    security: DBHubSecurityConfig = Field(default_factory=DBHubSecurityConfig)
    # Schema anti-hallucination
    schema_filter: SchemaFilterConfig = Field(default_factory=SchemaFilterConfig)
    schema_sample_rows: int = 3      # Number of sample rows to include (0 = disabled)


class ToolInfo(BaseModel):
    """MCP tool information in LLM-compatible format."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""
