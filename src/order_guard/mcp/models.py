"""MCP configuration and tool info models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str
    transport: Literal["stdio", "sse"]
    command: str | None = None       # stdio mode
    args: list[str] = Field(default_factory=list)  # stdio mode
    url: str | None = None           # sse mode
    headers: dict[str, str] = Field(default_factory=dict)  # sse mode
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class ToolInfo(BaseModel):
    """MCP tool information in LLM-compatible format."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""
