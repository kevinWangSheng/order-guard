"""Tests for T22: Hot/cold data + query optimization."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from order_guard.engine.agent import Agent, AgentConfig, build_time_constraint
from order_guard.engine.llm_client import LLMResponse, TokenUsage
from order_guard.mcp.models import SchemaFilterConfig
from order_guard.mcp.schema import (
    ColumnInfo,
    SchemaInfo,
    TableSchema,
    build_schema_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_mcp(tables: list[str], columns_by_table: dict | None = None):
    """Create a mock MCP connection."""
    mcp = AsyncMock()
    mcp.name = "test-db"

    tool_info = MagicMock()
    tool_info.name = "execute_sql"
    tool_info.description = "Execute SQL"
    tool_info.input_schema = {}
    tool_info.server_name = "test-db"
    mcp.list_tools.return_value = [tool_info]

    columns_by_table = columns_by_table or {}

    async def _call_tool(name, args=None):
        args = args or {}
        sql = args.get("sql", "")
        if "sqlite_master" in sql:
            return json.dumps([{"name": t} for t in tables])
        if sql.startswith("PRAGMA table_info"):
            tname = sql.split("'")[1]
            return json.dumps(columns_by_table.get(tname, []))
        if sql.startswith("PRAGMA index_list") or sql.startswith("PRAGMA foreign_key_list"):
            return json.dumps([])
        if sql.startswith("SELECT *"):
            return json.dumps([])
        return json.dumps([])

    mcp.call_tool.side_effect = _call_tool
    return mcp


FINAL_JSON = json.dumps({
    "alerts": [],
    "summary": "No issues",
    "has_alerts": False,
})


# ---------------------------------------------------------------------------
# Time constraint tests
# ---------------------------------------------------------------------------

class TestTimeConstraint:
    def test_build_time_constraint_7d(self):
        result = build_time_constraint("7d")
        assert "7d" in result
        assert "时间" in result
        assert "WHERE" in result

    def test_build_time_constraint_90d(self):
        result = build_time_constraint("90d")
        assert "90d" in result

    def test_build_time_constraint_empty(self):
        result = build_time_constraint("")
        assert result == ""

    def test_build_time_constraint_none(self):
        result = build_time_constraint("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_agent_injects_time_constraint(self):
        """Agent should inject time constraint into system prompt."""
        mcp = _make_mock_mcp(["orders"], {
            "orders": [{"name": "order_id", "type": "VARCHAR(50)"}],
        })

        llm = AsyncMock()
        llm.completion.return_value = LLMResponse(
            content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
        )

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True),
            data_window="7d",
        )
        await agent.run("检查订单")

        messages = llm.completion.call_args[0][0]
        system_msg = messages[0]["content"]
        assert "7d" in system_msg
        assert "时间约束" in system_msg

    @pytest.mark.asyncio
    async def test_agent_no_time_constraint_when_empty(self):
        """Agent should NOT inject time constraint when data_window is empty."""
        mcp = _make_mock_mcp(["orders"], {
            "orders": [{"name": "order_id", "type": "VARCHAR(50)"}],
        })

        llm = AsyncMock()
        llm.completion.return_value = LLMResponse(
            content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
        )

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True),
            data_window="",
        )
        await agent.run("检查订单")

        messages = llm.completion.call_args[0][0]
        system_msg = messages[0]["content"]
        assert "时间约束" not in system_msg


# ---------------------------------------------------------------------------
# Large table strategy tests
# ---------------------------------------------------------------------------

class TestLargeTableStrategy:
    def test_system_prompt_includes_query_strategy(self):
        """System prompt should include large table query strategy."""
        from order_guard.engine.agent import AGENT_SYSTEM_PROMPT
        assert "COUNT(*)" in AGENT_SYSTEM_PROMPT
        assert "10000" in AGENT_SYSTEM_PROMPT
        assert "LIMIT" in AGENT_SYSTEM_PROMPT
        assert "GROUP BY" in AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Cold table tests
# ---------------------------------------------------------------------------

class TestColdTables:
    def test_cold_table_marked_in_context(self):
        """Cold tables should be marked as archive in schema context."""
        schema = SchemaInfo(
            database="test",
            tables={
                "orders": TableSchema(name="orders", columns=[
                    ColumnInfo(name="id", type="INT"),
                ]),
                "orders_archive_2024": TableSchema(name="orders_archive_2024", columns=[
                    ColumnInfo(name="id", type="INT"),
                ]),
            },
        )

        ctx = build_schema_context(schema, cold_tables=["orders_archive_2024"])
        assert "### 表: orders\n" in ctx or "### 表: orders" in ctx
        assert "归档表" in ctx
        assert "orders_archive_2024" in ctx

    def test_non_cold_table_not_marked(self):
        """Regular tables should NOT be marked as archive."""
        schema = SchemaInfo(
            database="test",
            tables={
                "orders": TableSchema(name="orders", columns=[
                    ColumnInfo(name="id", type="INT"),
                ]),
            },
        )

        ctx = build_schema_context(schema, cold_tables=["some_other_table"])
        assert "归档表" not in ctx

    def test_cold_tables_case_insensitive(self):
        """Cold table matching should be case insensitive."""
        schema = SchemaInfo(
            database="test",
            tables={
                "Orders_Archive_2024": TableSchema(name="Orders_Archive_2024", columns=[]),
            },
        )

        ctx = build_schema_context(schema, cold_tables=["orders_archive_2024"])
        assert "归档表" in ctx

    @pytest.mark.asyncio
    async def test_agent_passes_cold_tables_to_schema_context(self):
        """Agent should pass cold_tables config when building schema context."""
        mcp = _make_mock_mcp(
            ["orders", "orders_archive"],
            {
                "orders": [{"name": "id", "type": "INT"}],
                "orders_archive": [{"name": "id", "type": "INT"}],
            },
        )

        llm = AsyncMock()
        llm.completion.return_value = LLMResponse(
            content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
        )

        schema_filter = SchemaFilterConfig(cold_tables=["orders_archive"])

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True),
            schema_filter=schema_filter,
        )
        await agent.run("检查订单")

        messages = llm.completion.call_args[0][0]
        system_msg = messages[0]["content"]
        assert "归档表" in system_msg

    def test_empty_cold_tables_no_marking(self):
        """No cold tables = no archive marking."""
        schema = SchemaInfo(
            database="test",
            tables={
                "orders": TableSchema(name="orders", columns=[]),
            },
        )

        ctx = build_schema_context(schema, cold_tables=[])
        assert "归档表" not in ctx

        ctx2 = build_schema_context(schema, cold_tables=None)
        assert "归档表" not in ctx2


# ---------------------------------------------------------------------------
# Data window in AlertRule model
# ---------------------------------------------------------------------------

class TestAlertRuleDataWindow:
    def test_alert_rule_has_data_window_field(self):
        from order_guard.models.tables import AlertRule
        rule = AlertRule(id="test-rule", data_window="7d")
        assert rule.data_window == "7d"

    def test_alert_rule_data_window_default_empty(self):
        from order_guard.models.tables import AlertRule
        rule = AlertRule(id="test-rule")
        assert rule.data_window == ""
