"""MCP Manager — manage multiple MCP server connections."""

from __future__ import annotations

from loguru import logger

from order_guard.mcp.client import MCPConnection
from order_guard.mcp.models import MCPServerConfig, ToolInfo


class MCPManager:
    """Manage all MCP server connections."""

    def __init__(self, configs: list[MCPServerConfig] | None = None):
        self._connections: dict[str, MCPConnection] = {}
        if configs:
            for config in configs:
                if not config.enabled:
                    continue
                resolved = self._resolve_config(config)
                self._connections[config.name] = MCPConnection(resolved)

    @staticmethod
    def _resolve_config(config: MCPServerConfig) -> MCPServerConfig:
        """Resolve DBHub configs into runnable stdio configs."""
        if config.type == "dbhub":
            from order_guard.mcp.dbhub import prepare_dbhub_config
            return prepare_dbhub_config(config)
        return config

    async def connect_all(self) -> None:
        """Connect to all configured MCP servers. Individual failures don't block others."""
        for name, conn in self._connections.items():
            try:
                await conn.connect()
            except Exception as e:
                logger.error("Failed to connect MCP server '{}': {}", name, e)

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name, conn in self._connections.items():
            try:
                await conn.disconnect()
            except BaseException as e:
                logger.warning("Error disconnecting MCP server '{}': {}", name, e)

    def get_connection(self, name: str) -> MCPConnection:
        """Get a connection by server name."""
        if name not in self._connections:
            available = ", ".join(self._connections.keys()) or "(none)"
            raise KeyError(f"MCP server '{name}' not found. Available: {available}")
        return self._connections[name]

    def list_connections(self) -> list[str]:
        """List all configured server names."""
        return list(self._connections.keys())

    async def get_tools(self, server_name: str) -> list[ToolInfo]:
        """Get all tools from a specific MCP server."""
        conn = self.get_connection(server_name)
        return await conn.list_tools()

    async def get_all_tools(self) -> list[ToolInfo]:
        """Get tools from all connected MCP servers."""
        all_tools: list[ToolInfo] = []
        for name, conn in self._connections.items():
            if conn.is_connected():
                try:
                    tools = await conn.list_tools()
                    all_tools.extend(tools)
                except Exception as e:
                    logger.warning("Failed to list tools from '{}': {}", name, e)
        return all_tools
