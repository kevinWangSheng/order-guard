"""End-to-end tests for DBHub integration (T20).

Tests DBHub connecting to SQLite via MCP stdio transport.
Requires: data/test_warehouse.db + Node.js (npx).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from order_guard.mcp.models import (
    DBHubDatabaseConfig,
    DBHubSecurityConfig,
    MCPServerConfig,
)
from order_guard.mcp.manager import MCPManager

pytestmark = pytest.mark.e2e

DB_PATH = Path("data/test_warehouse.db").resolve()
HAS_DB = DB_PATH.exists()
HAS_NPX = shutil.which("npx") is not None

skip_no_db = pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
skip_no_npx = pytest.mark.skipif(not HAS_NPX, reason="npx not found")


def _make_dbhub_config(readonly: bool = True, max_rows: int = 1000) -> MCPServerConfig:
    # DBHub SQLite DSN: sqlite:// + absolute path (/Users/...) = sqlite:///Users/...
    return MCPServerConfig(
        name="dbhub-test",
        type="dbhub",
        databases=[
            DBHubDatabaseConfig(
                alias="warehouse",
                dsn=f"sqlite://{DB_PATH}",
                query_timeout=5,
            ),
        ],
        security=DBHubSecurityConfig(readonly=readonly, max_rows=max_rows),
    )


@skip_no_db
@skip_no_npx
class TestDBHubConnection:
    """Test DBHub MCP Server connection via MCPManager."""

    @pytest_asyncio.fixture
    async def manager(self):
        config = _make_dbhub_config()
        mgr = MCPManager([config])
        await mgr.connect_all()
        yield mgr
        await mgr.disconnect_all()

    @pytest.mark.asyncio
    async def test_connect_and_list_tools(self, manager: MCPManager) -> None:
        """DBHub should connect and expose execute_sql + search_objects."""
        conn = manager.get_connection("dbhub-test")
        assert conn.is_connected()
        tools = await conn.list_tools()
        tool_names = [t.name for t in tools]
        assert "execute_sql" in tool_names

    @pytest.mark.asyncio
    async def test_search_objects(self, manager: MCPManager) -> None:
        """search_objects should return table list."""
        conn = manager.get_connection("dbhub-test")
        tools = await conn.list_tools()
        tool_names = [t.name for t in tools]
        if "search_objects" not in tool_names:
            pytest.skip("search_objects not available in this DBHub version")
        result = await conn.call_tool("search_objects", {"search_term": "inventory"})
        assert "inventory" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_sql_select(self, manager: MCPManager) -> None:
        """execute_sql should return query results."""
        conn = manager.get_connection("dbhub-test")
        result = await conn.call_tool("execute_sql", {
            "sql": "SELECT sku, warehouse, quantity FROM inventory LIMIT 3"
        })
        assert "SKU" in result

    @pytest.mark.asyncio
    async def test_readonly_rejects_write(self, manager: MCPManager) -> None:
        """readonly mode should reject INSERT/UPDATE/DELETE."""
        conn = manager.get_connection("dbhub-test")
        result = await conn.call_tool("execute_sql", {
            "sql": "DELETE FROM inventory WHERE sku_code = 'SKU-999'"
        })
        # DBHub should return an error for write operations in readonly mode
        result_lower = result.lower()
        assert any(kw in result_lower for kw in ["error", "read", "denied", "not allowed", "readonly"])


@skip_no_db
@skip_no_npx
class TestDBHubMaxRows:
    """Test max_rows limiting."""

    @pytest.mark.asyncio
    async def test_max_rows_limits_results(self) -> None:
        """max_rows=2 should limit query results."""
        config = _make_dbhub_config(max_rows=2)
        mgr = MCPManager([config])
        await mgr.connect_all()
        try:
            conn = mgr.get_connection("dbhub-test")
            result = await conn.call_tool("execute_sql", {
                "sql": "SELECT sku_code FROM inventory"
            })
            # DBHub returns JSON with rows array — count SKU occurrences
            sku_count = result.count("SKU-")
            assert sku_count <= 2, f"Expected max 2 rows but got {sku_count} SKU entries"
        finally:
            await mgr.disconnect_all()
