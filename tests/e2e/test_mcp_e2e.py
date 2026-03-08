"""End-to-end tests for MCP + SQLite pipeline (T19).

These tests require:
- data/test_warehouse.db created (run: python scripts/create_test_db.py)
- A valid LLM API key for full Agent tests (set OG_LLM_API_KEY env var)

Tests are marked with pytest.mark.e2e.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

DB_PATH = Path("data/test_warehouse.db")
SCRIPTS_DIR = Path("scripts")
# Use Python to run our custom SQLite MCP server
PYTHON_PATH = sys.executable
MCP_SERVER_SCRIPT = str(SCRIPTS_DIR / "sqlite_mcp_server.py")

HAS_DB = DB_PATH.exists()
HAS_API_KEY = bool(os.environ.get("OG_LLM_API_KEY", ""))


# ---------------------------------------------------------------------------
# Database verification tests (no LLM needed)
# ---------------------------------------------------------------------------

class TestDatabaseSetup:
    def test_database_exists(self):
        assert DB_PATH.exists(), f"Database not found: {DB_PATH}"

    def test_tables_exist(self):
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        assert "products" in table_names
        assert "inventory" in table_names
        assert "orders" in table_names
        assert "daily_sales" in table_names

    def test_data_populated(self):
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()

        for table, min_rows in [("products", 5), ("inventory", 5), ("daily_sales", 100), ("orders", 100)]:
            count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count >= min_rows, f"Expected >= {min_rows} {table} rows, got {count}"

        conn.close()

    def test_sku002_is_low_stock(self):
        """SKU-002 should have critically low stock vs demand."""
        conn = sqlite3.connect(str(DB_PATH))
        stock = conn.execute("SELECT quantity FROM inventory WHERE sku='SKU-002'").fetchone()[0]
        avg_sales = conn.execute("SELECT AVG(quantity_sold) FROM daily_sales WHERE sku='SKU-002'").fetchone()[0]
        conn.close()
        days_of_stock = stock / avg_sales if avg_sales > 0 else float("inf")
        assert days_of_stock < 1, f"SKU-002 should be critically low, got {days_of_stock:.1f} days"

    def test_sku003_is_overstocked(self):
        """SKU-003 should be heavily overstocked."""
        conn = sqlite3.connect(str(DB_PATH))
        stock = conn.execute("SELECT quantity FROM inventory WHERE sku='SKU-003'").fetchone()[0]
        avg_sales = conn.execute("SELECT AVG(quantity_sold) FROM daily_sales WHERE sku='SKU-003'").fetchone()[0]
        conn.close()
        days_of_stock = stock / avg_sales if avg_sales > 0 else float("inf")
        assert days_of_stock > 90, f"SKU-003 should be overstocked, got {days_of_stock:.1f} days"


# ---------------------------------------------------------------------------
# MCP Connection test (Python MCP server, no LLM)
# ---------------------------------------------------------------------------

class TestMCPConnection:
    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.asyncio
    async def test_connect_and_list_tools(self):
        """Can connect to Python SQLite MCP server and list tools."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp.client import MCPConnection

        config = MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )

        conn = MCPConnection(config)
        try:
            await conn.connect()
            assert conn.is_connected()

            tools = await conn.list_tools()
            tool_names = [t.name for t in tools]
            assert "list_tables" in tool_names
            assert "describe_table" in tool_names
            assert "read_query" in tool_names

        finally:
            await conn.disconnect()

    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.asyncio
    async def test_call_tool_list_tables(self):
        """Can call list_tables tool and get table names."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp.client import MCPConnection

        config = MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )

        conn = MCPConnection(config)
        try:
            await conn.connect()
            result = await conn.call_tool("list_tables", {})
            assert "products" in result
            assert "inventory" in result
            assert "orders" in result
            assert "daily_sales" in result
        finally:
            await conn.disconnect()

    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.asyncio
    async def test_call_tool_read_query(self):
        """Can execute SQL queries via MCP."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp.client import MCPConnection

        config = MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )

        conn = MCPConnection(config)
        try:
            await conn.connect()
            result = await conn.call_tool("read_query", {"query": "SELECT COUNT(*) as cnt FROM products"})
            assert "5" in result
        finally:
            await conn.disconnect()

    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.asyncio
    async def test_call_tool_describe_table(self):
        """Can describe table schema via MCP."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp.client import MCPConnection

        config = MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )

        conn = MCPConnection(config)
        try:
            await conn.connect()
            result = await conn.call_tool("describe_table", {"table_name": "inventory"})
            assert "sku" in result
            assert "quantity" in result
            assert "warehouse" in result
        finally:
            await conn.disconnect()


# ---------------------------------------------------------------------------
# MCPManager integration test
# ---------------------------------------------------------------------------

class TestMCPManagerIntegration:
    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.asyncio
    async def test_manager_connect_and_query(self):
        """MCPManager can connect to server and execute queries."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp import MCPManager

        configs = [MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )]

        manager = MCPManager(configs)
        try:
            await manager.connect_all()

            conn = manager.get_connection("test-warehouse")
            assert conn.is_connected()

            tools = await manager.get_tools("test-warehouse")
            assert len(tools) == 3

            result = await conn.call_tool("read_query", {
                "query": "SELECT sku, quantity FROM inventory ORDER BY sku",
            })
            assert "SKU-001" in result
            assert "SKU-002" in result

        finally:
            await manager.disconnect_all()


# ---------------------------------------------------------------------------
# Full Agent E2E test (needs LLM API key)
# ---------------------------------------------------------------------------

class TestFullAgentE2E:
    @pytest.mark.skipif(not HAS_DB, reason="test_warehouse.db not found")
    @pytest.mark.skipif(not HAS_API_KEY, reason="OG_LLM_API_KEY not set")
    @pytest.mark.asyncio
    async def test_agent_analyzes_warehouse(self):
        """Full Agent loop: connect → explore → query → analyze."""
        from order_guard.mcp.models import MCPServerConfig
        from order_guard.mcp.client import MCPConnection
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        config = MCPServerConfig(
            name="test-warehouse",
            transport="stdio",
            command=PYTHON_PATH,
            args=[MCP_SERVER_SCRIPT, str(DB_PATH)],
        )

        conn = MCPConnection(config)
        try:
            await conn.connect()

            llm = LLMClient()
            agent = Agent(
                llm_client=llm,
                mcp_connection=conn,
                config=AgentConfig(max_iterations=15),
            )

            result = await agent.run("""
请检查仓库数据库中的库存状况：
1. 查看有哪些商品及其库存数量
2. 结合近期销售数据，计算每个 SKU 的库存可售天数
3. 与安全库存（reorder_point）对比，判断是否需要补货
4. 找出积压商品（库存可售天数 > 90 天）
5. 检查退货率异常的 SKU（从 orders 表中分析）

对每个异常 SKU 输出 severity（critical/warning/info）、原因和建议。
""")

            # Basic output validation
            assert result is not None
            assert result.token_usage.total_tokens > 0
            assert result.summary

            # Agent should find alerts
            if result.has_alerts:
                severity_list = [a.severity for a in result.alerts]
                has_critical = any(s == "critical" for s in severity_list)
                assert has_critical, f"Expected critical alert, got: {severity_list}"

                print(f"\n=== Agent Results ===")
                print(f"Summary: {result.summary}")
                print(f"Alerts ({len(result.alerts)}):")
                for a in result.alerts:
                    print(f"  [{a.severity}] {a.sku}: {a.title} - {a.reason}")
                print(f"Token usage: {result.token_usage.total_tokens}")

        finally:
            await conn.disconnect()
