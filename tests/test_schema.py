"""Tests for T21: Schema anti-hallucination — loader, filter, context builder, validator."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from order_guard.mcp.schema import (
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    SchemaFilterConfig,
    SchemaInfo,
    SchemaLoader,
    TableSchema,
    build_schema_context,
    filter_schema,
)
from order_guard.mcp.validator import ValidationResult, validate_query


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_schema() -> SchemaInfo:
    """Create a test schema with multiple tables."""
    return SchemaInfo(
        database="test-db",
        tables={
            "products": TableSchema(
                name="products",
                columns=[
                    ColumnInfo(name="sku", type="VARCHAR(50)", comment="商品编码"),
                    ColumnInfo(name="name", type="VARCHAR(200)", comment="商品名称"),
                    ColumnInfo(name="category", type="VARCHAR(100)", comment="分类"),
                    ColumnInfo(name="unit_cost", type="DECIMAL(10,2)", comment="成本"),
                    ColumnInfo(name="unit_price", type="DECIMAL(10,2)", comment="售价"),
                ],
                indexes=[IndexInfo(name="PRIMARY", columns=["sku"], unique=True)],
                foreign_keys=[],
                sample_rows=[
                    {"sku": "SKU-001", "name": "蓝牙耳机", "category": "电子", "unit_cost": 45, "unit_price": 129},
                ],
            ),
            "orders": TableSchema(
                name="orders",
                columns=[
                    ColumnInfo(name="order_id", type="VARCHAR(50)", comment="订单号"),
                    ColumnInfo(name="sku", type="VARCHAR(50)", comment="商品编码"),
                    ColumnInfo(name="status", type="VARCHAR(20)", comment="状态"),
                    ColumnInfo(name="order_date", type="DATE", comment="下单日期"),
                ],
                indexes=[
                    IndexInfo(name="PRIMARY", columns=["order_id"], unique=True),
                    IndexInfo(name="idx_sku", columns=["sku"]),
                ],
                foreign_keys=[
                    ForeignKeyInfo(column="sku", ref_table="products", ref_column="sku"),
                ],
                sample_rows=[
                    {"order_id": "ORD-001", "sku": "SKU-001", "status": "shipped", "order_date": "2025-01-01"},
                ],
            ),
            "users": TableSchema(
                name="users",
                columns=[
                    ColumnInfo(name="id", type="INT", comment="用户 ID"),
                    ColumnInfo(name="username", type="VARCHAR(50)", comment="用户名"),
                    ColumnInfo(name="password", type="VARCHAR(255)", comment="密码 hash"),
                    ColumnInfo(name="phone", type="VARCHAR(20)", comment="手机号"),
                ],
            ),
        },
    )


def _make_mock_mcp(tables: list[str], columns_by_table: dict[str, list[dict]], samples_by_table: dict[str, list[dict]] | None = None) -> AsyncMock:
    """Create a mock MCP connection."""
    mcp = AsyncMock()
    mcp.name = "mock-db"

    # list_tools returns execute_sql
    tool_info = MagicMock()
    tool_info.name = "execute_sql"
    tool_info.description = "Execute SQL"
    tool_info.input_schema = {}
    tool_info.server_name = "mock-db"
    mcp.list_tools.return_value = [tool_info]

    async def _call_tool(name: str, args: dict[str, Any] | None = None) -> str:
        args = args or {}
        sql = args.get("sql", "")
        if "sqlite_master" in sql:
            return json.dumps([{"name": t} for t in tables])
        if "information_schema" in sql.lower():
            return json.dumps([{"table_name": t} for t in tables])
        if sql.startswith("PRAGMA table_info"):
            tname = sql.split("'")[1]
            cols = columns_by_table.get(tname, [])
            return json.dumps(cols)
        if sql.startswith("PRAGMA index_list"):
            return json.dumps([])
        if sql.startswith("PRAGMA foreign_key_list"):
            return json.dumps([])
        if sql.startswith("SELECT *"):
            for t in tables:
                if t in sql:
                    rows = (samples_by_table or {}).get(t, [])
                    return json.dumps(rows)
            return json.dumps([])
        return json.dumps([])

    mcp.call_tool.side_effect = _call_tool
    return mcp


# ---------------------------------------------------------------------------
# Schema filter tests
# ---------------------------------------------------------------------------

class TestSchemaFilter:
    def test_filter_blocked_tables(self):
        schema = _make_schema()
        config = SchemaFilterConfig(blocked_tables=["users"])
        filtered = filter_schema(schema, config)

        assert "products" in filtered.tables
        assert "orders" in filtered.tables
        assert "users" not in filtered.tables

    def test_filter_blocked_columns(self):
        schema = _make_schema()
        config = SchemaFilterConfig(blocked_columns=["password", "phone"])
        filtered = filter_schema(schema, config)

        # users table still present, but password and phone columns removed
        user_cols = [c.name for c in filtered.tables["users"].columns]
        assert "id" in user_cols
        assert "username" in user_cols
        assert "password" not in user_cols
        assert "phone" not in user_cols

    def test_filter_combined(self):
        schema = _make_schema()
        config = SchemaFilterConfig(
            blocked_tables=["users"],
            blocked_columns=["unit_cost"],
        )
        filtered = filter_schema(schema, config)

        assert "users" not in filtered.tables
        prod_cols = [c.name for c in filtered.tables["products"].columns]
        assert "unit_cost" not in prod_cols
        assert "unit_price" in prod_cols

    def test_filter_case_insensitive(self):
        schema = _make_schema()
        config = SchemaFilterConfig(blocked_tables=["Users"], blocked_columns=["Password"])
        filtered = filter_schema(schema, config)

        assert "users" not in filtered.tables

    def test_filter_sample_rows_blocked_columns(self):
        schema = _make_schema()
        schema.tables["products"].sample_rows = [
            {"sku": "SKU-001", "name": "X", "unit_cost": 10, "unit_price": 20},
        ]
        config = SchemaFilterConfig(blocked_columns=["unit_cost"])
        filtered = filter_schema(schema, config)

        for row in filtered.tables["products"].sample_rows:
            assert "unit_cost" not in row
            assert "sku" in row

    def test_empty_filter_no_change(self):
        schema = _make_schema()
        config = SchemaFilterConfig()
        filtered = filter_schema(schema, config)

        assert set(filtered.tables.keys()) == set(schema.tables.keys())


# ---------------------------------------------------------------------------
# Schema context builder tests
# ---------------------------------------------------------------------------

class TestBuildSchemaContext:
    def test_basic_context(self):
        schema = _make_schema()
        ctx = build_schema_context(schema)

        assert "## 可用数据库: test-db" in ctx
        assert "### 表: products" in ctx
        assert "| sku | VARCHAR(50) | 商品编码 |" in ctx
        assert "### 表: orders" in ctx

    def test_foreign_keys_shown(self):
        schema = _make_schema()
        ctx = build_schema_context(schema)

        assert "sku → products.sku" in ctx

    def test_indexes_shown(self):
        schema = _make_schema()
        ctx = build_schema_context(schema)

        assert "索引:" in ctx
        assert "PRIMARY" in ctx

    def test_sample_data_shown(self):
        schema = _make_schema()
        ctx = build_schema_context(schema)

        assert "样例数据" in ctx
        assert "SKU-001" in ctx

    def test_empty_schema(self):
        schema = SchemaInfo(database="empty")
        ctx = build_schema_context(schema)
        assert ctx == ""


# ---------------------------------------------------------------------------
# Schema loader tests
# ---------------------------------------------------------------------------

class TestSchemaLoader:
    @pytest.mark.asyncio
    async def test_load_tables(self):
        columns = {
            "products": [
                {"name": "sku", "type": "VARCHAR(50)"},
                {"name": "name", "type": "VARCHAR(200)"},
            ],
        }
        mcp = _make_mock_mcp(["products"], columns)
        loader = SchemaLoader(mcp, sample_rows=0)
        schema = await loader.load()

        assert "products" in schema.tables
        col_names = [c.name for c in schema.tables["products"].columns]
        assert "sku" in col_names
        assert "name" in col_names

    @pytest.mark.asyncio
    async def test_load_sample_data(self):
        columns = {
            "orders": [{"name": "order_id", "type": "VARCHAR(50)"}],
        }
        samples = {
            "orders": [{"order_id": "ORD-001"}],
        }
        mcp = _make_mock_mcp(["orders"], columns, samples)
        loader = SchemaLoader(mcp, sample_rows=3)
        schema = await loader.load()

        assert len(schema.tables["orders"].sample_rows) == 1
        assert schema.tables["orders"].sample_rows[0]["order_id"] == "ORD-001"

    @pytest.mark.asyncio
    async def test_load_empty_db(self):
        mcp = _make_mock_mcp([], {})
        loader = SchemaLoader(mcp, sample_rows=0)
        schema = await loader.load()

        assert len(schema.tables) == 0


# ---------------------------------------------------------------------------
# SQL validator tests
# ---------------------------------------------------------------------------

class TestSQLValidator:
    def test_valid_query(self):
        schema = _make_schema()
        result = validate_query("SELECT sku, name FROM products", schema)
        assert result.valid is True

    def test_invalid_table(self):
        schema = _make_schema()
        result = validate_query("SELECT * FROM nonexistent_table", schema)
        assert result.valid is False
        assert "nonexistent_table" in result.error
        assert "不存在" in result.error

    def test_invalid_column_with_table_prefix(self):
        schema = _make_schema()
        result = validate_query("SELECT products.nonexistent FROM products", schema)
        assert result.valid is False
        assert "nonexistent" in result.error

    def test_valid_column_with_table_prefix(self):
        schema = _make_schema()
        result = validate_query("SELECT products.sku FROM products", schema)
        assert result.valid is True

    def test_sql_syntax_error(self):
        schema = _make_schema()
        # Use truly malformed SQL that sqlglot cannot parse
        result = validate_query("SELECT FROM WHERE ,,, (((", schema)
        assert result.valid is False

    def test_join_query_valid(self):
        schema = _make_schema()
        result = validate_query(
            "SELECT o.order_id, p.name FROM orders o JOIN products p ON o.sku = p.sku",
            schema,
        )
        assert result.valid is True

    def test_join_query_invalid_table(self):
        schema = _make_schema()
        result = validate_query(
            "SELECT * FROM orders o JOIN inventory i ON o.sku = i.sku",
            schema,
        )
        assert result.valid is False
        assert "inventory" in result.error

    def test_subquery_valid(self):
        schema = _make_schema()
        result = validate_query(
            "SELECT * FROM products WHERE sku IN (SELECT sku FROM orders)",
            schema,
        )
        assert result.valid is True

    def test_empty_schema(self):
        schema = SchemaInfo(database="empty")
        result = validate_query("SELECT * FROM any_table", schema)
        assert result.valid is False


# ---------------------------------------------------------------------------
# Agent schema integration tests
# ---------------------------------------------------------------------------

class TestAgentSchemaIntegration:
    @pytest.mark.asyncio
    async def test_agent_loads_schema(self):
        """Agent should load schema and inject into system prompt."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient, LLMResponse, TokenUsage, ToolCall

        columns = {
            "products": [{"name": "sku", "type": "VARCHAR(50)"}],
        }
        mcp = _make_mock_mcp(["products"], columns)

        # Mock LLM to return final output immediately
        llm = AsyncMock(spec=LLMClient)
        llm.completion.return_value = LLMResponse(
            content=json.dumps({
                "alerts": [],
                "summary": "No issues found",
                "has_alerts": False,
            }),
            tool_calls=[],
            token_usage=TokenUsage(),
        )

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True, validate_sql=False),
        )
        result = await agent.run("检查库存")

        # Verify schema was injected in system prompt
        call_args = llm.completion.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "products" in system_msg
        assert "sku" in system_msg

    @pytest.mark.asyncio
    async def test_agent_validates_sql(self):
        """Agent should validate SQL and return error for invalid queries."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient, LLMResponse, TokenUsage, ToolCall

        columns = {
            "products": [{"name": "sku", "type": "VARCHAR(50)"}],
        }
        mcp = _make_mock_mcp(["products"], columns)

        call_count = 0

        async def _mock_completion(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: Agent tries to query a nonexistent table
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="execute_sql", arguments={"sql": "SELECT * FROM fake_table"})],
                    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            else:
                # Second call: Agent gets validation error and returns final output
                return LLMResponse(
                    content=json.dumps({
                        "alerts": [],
                        "summary": "Corrected query",
                        "has_alerts": False,
                    }),
                    tool_calls=[],
                    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )

        llm = AsyncMock(spec=LLMClient)
        llm.completion.side_effect = _mock_completion

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True, validate_sql=True),
        )
        result = await agent.run("检查库存")

        # The fake_table query should NOT have been sent to MCP
        for call in mcp.call_tool.call_args_list:
            if call[0][0] == "execute_sql":
                sql = call[0][1].get("sql", "") if len(call[0]) > 1 else call[1].get("sql", "")
                assert "fake_table" not in sql

    @pytest.mark.asyncio
    async def test_agent_schema_filter(self):
        """Agent should filter blocked tables from schema context."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient, LLMResponse, TokenUsage
        from order_guard.mcp.models import SchemaFilterConfig

        columns = {
            "products": [{"name": "sku", "type": "VARCHAR(50)"}],
            "users": [{"name": "id", "type": "INT"}, {"name": "password", "type": "VARCHAR(255)"}],
        }
        mcp = _make_mock_mcp(["products", "users"], columns)

        llm = AsyncMock(spec=LLMClient)
        llm.completion.return_value = LLMResponse(
            content=json.dumps({"alerts": [], "summary": "OK", "has_alerts": False}),
            tool_calls=[],
            token_usage=TokenUsage(),
        )

        schema_filter = SchemaFilterConfig(
            blocked_tables=["users"],
            blocked_columns=["password"],
        )

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True),
            schema_filter=schema_filter,
        )
        result = await agent.run("检查数据")

        # Verify system prompt does NOT contain blocked table/column
        call_args = llm.completion.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "users" not in system_msg.split("可用数据库")[1] if "可用数据库" in system_msg else True
        assert "password" not in system_msg
        # But products should be there
        assert "products" in system_msg
