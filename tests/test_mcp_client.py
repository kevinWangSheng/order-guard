"""Tests for MCP client layer (T16)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.mcp.models import MCPServerConfig, ToolInfo
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.manager import MCPManager


# ---------------------------------------------------------------------------
# MCPServerConfig tests
# ---------------------------------------------------------------------------

class TestMCPServerConfig:
    def test_stdio_config(self):
        config = MCPServerConfig(
            name="test-db",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-sqlite", "test.db"],
        )
        assert config.name == "test-db"
        assert config.transport == "stdio"
        assert config.command == "npx"
        assert len(config.args) == 3
        assert config.enabled is True

    def test_sse_config(self):
        config = MCPServerConfig(
            name="remote-api",
            transport="sse",
            url="https://example.com/sse",
            headers={"Authorization": "Bearer test"},
        )
        assert config.name == "remote-api"
        assert config.transport == "sse"
        assert config.url == "https://example.com/sse"
        assert config.headers["Authorization"] == "Bearer test"

    def test_disabled_config(self):
        config = MCPServerConfig(
            name="disabled-server",
            transport="stdio",
            command="test",
            enabled=False,
        )
        assert config.enabled is False


# ---------------------------------------------------------------------------
# ToolInfo tests
# ---------------------------------------------------------------------------

class TestToolInfo:
    def test_tool_info(self):
        tool = ToolInfo(
            name="read_query",
            description="Execute a SELECT query",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            server_name="test-db",
        )
        assert tool.name == "read_query"
        assert tool.server_name == "test-db"
        assert "query" in tool.input_schema["properties"]


# ---------------------------------------------------------------------------
# MCPConnection tests (mocked)
# ---------------------------------------------------------------------------

class TestMCPConnection:
    def test_initial_state(self):
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        conn = MCPConnection(config)
        assert conn.name == "test"
        assert conn.is_connected() is False

    @pytest.mark.asyncio
    async def test_list_tools_not_connected(self):
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        conn = MCPConnection(config)
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.list_tools()

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        config = MCPServerConfig(name="test", transport="stdio", command="echo")
        conn = MCPConnection(config)
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.call_tool("test_tool", {})

    @pytest.mark.asyncio
    async def test_list_tools_with_mock_session(self):
        """Test list_tools with a mocked MCP session."""
        config = MCPServerConfig(name="test-db", transport="stdio", command="echo")
        conn = MCPConnection(config)

        # Mock a connected session
        mock_tool = MagicMock()
        mock_tool.name = "list_tables"
        mock_tool.description = "List all tables"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_result = MagicMock()
        mock_result.tools = [mock_tool]

        mock_session = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_result)
        conn._session = mock_session

        tools = await conn.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "list_tables"
        assert tools[0].description == "List all tables"
        assert tools[0].server_name == "test-db"

    @pytest.mark.asyncio
    async def test_call_tool_with_mock_session(self):
        """Test call_tool with a mocked MCP session."""
        config = MCPServerConfig(name="test-db", transport="stdio", command="echo")
        conn = MCPConnection(config)

        # Mock a connected session
        mock_content = MagicMock()
        mock_content.text = "products, orders, inventory"

        mock_result = MagicMock()
        mock_result.content = [mock_content]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        conn._session = mock_session

        result = await conn.call_tool("list_tables", {})
        assert "products" in result
        assert "orders" in result
        mock_session.call_tool.assert_called_once_with("list_tables", {})

    @pytest.mark.asyncio
    async def test_call_tool_error_result(self):
        """Test call_tool when MCP returns an error."""
        config = MCPServerConfig(name="test-db", transport="stdio", command="echo")
        conn = MCPConnection(config)

        mock_content = MagicMock()
        mock_content.text = "Error: table not found"

        mock_result = MagicMock()
        mock_result.content = [mock_content]
        mock_result.isError = True

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        conn._session = mock_session

        result = await conn.call_tool("describe_table", {"table": "nonexistent"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# MCPManager tests
# ---------------------------------------------------------------------------

class TestMCPManager:
    def test_create_with_configs(self):
        configs = [
            MCPServerConfig(name="db1", transport="stdio", command="echo"),
            MCPServerConfig(name="db2", transport="stdio", command="echo"),
            MCPServerConfig(name="disabled", transport="stdio", command="echo", enabled=False),
        ]
        manager = MCPManager(configs)
        names = manager.list_connections()
        assert "db1" in names
        assert "db2" in names
        assert "disabled" not in names  # disabled servers are excluded

    def test_get_connection(self):
        configs = [MCPServerConfig(name="db1", transport="stdio", command="echo")]
        manager = MCPManager(configs)
        conn = manager.get_connection("db1")
        assert conn.name == "db1"

    def test_get_connection_not_found(self):
        manager = MCPManager([])
        with pytest.raises(KeyError, match="not found"):
            manager.get_connection("nonexistent")

    @pytest.mark.asyncio
    async def test_connect_all_with_failure(self):
        """Individual connection failures don't block others."""
        configs = [
            MCPServerConfig(name="good", transport="stdio", command="echo"),
            MCPServerConfig(name="bad", transport="stdio", command="echo"),
        ]
        manager = MCPManager(configs)

        # Mock connections
        good_conn = manager.get_connection("good")
        bad_conn = manager.get_connection("bad")

        good_conn.connect = AsyncMock()
        bad_conn.connect = AsyncMock(side_effect=RuntimeError("connection failed"))

        await manager.connect_all()

        good_conn.connect.assert_called_once()
        bad_conn.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        configs = [
            MCPServerConfig(name="db1", transport="stdio", command="echo"),
            MCPServerConfig(name="db2", transport="stdio", command="echo"),
        ]
        manager = MCPManager(configs)

        for name in manager.list_connections():
            conn = manager.get_connection(name)
            conn.disconnect = AsyncMock()

        await manager.disconnect_all()

        for name in manager.list_connections():
            conn = manager.get_connection(name)
            conn.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tools(self):
        configs = [MCPServerConfig(name="db1", transport="stdio", command="echo")]
        manager = MCPManager(configs)

        conn = manager.get_connection("db1")
        mock_tools = [
            ToolInfo(name="list_tables", description="List tables", input_schema={}, server_name="db1"),
            ToolInfo(name="read_query", description="Run SQL", input_schema={}, server_name="db1"),
        ]
        conn.list_tools = AsyncMock(return_value=mock_tools)

        tools = await manager.get_tools("db1")
        assert len(tools) == 2
        assert tools[0].name == "list_tables"
