"""Tests for the unified data access layer (N1)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from order_guard.data_access.layer import DataAccessLayer, FIXED_TOOLS
from order_guard.data_access.models import (
    DataSourceInfo,
    QueryResult,
    SchemaResult,
    TableInfo,
    TableDetail,
    ColumnDetail,
)
from order_guard.data_access.sql_adapter import SQLAdapter
from order_guard.data_access.mcp_adapter import MCPAdapter
from order_guard.mcp.models import (
    DBHubDatabaseConfig,
    MCPServerConfig,
    ToolInfo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mcp_conn(name: str = "test-db", connected: bool = True) -> MagicMock:
    """Create a mock MCPConnection."""
    conn = MagicMock()
    conn.name = name
    conn.is_connected.return_value = connected
    conn.list_tools = AsyncMock(return_value=[
        ToolInfo(name="execute_sql", description="Run SQL", input_schema={"type": "object"}, server_name=name),
    ])
    conn.call_tool = AsyncMock(return_value="[]")
    return conn


def _make_mcp_manager(configs: list[MCPServerConfig] | None = None) -> MagicMock:
    """Create a mock MCPManager."""
    mgr = MagicMock()
    connections = {}
    for cfg in (configs or []):
        conn = _make_mcp_conn(cfg.name)
        connections[cfg.name] = conn
    mgr.get_connection = MagicMock(side_effect=lambda name: connections.get(name, _make_mcp_conn(name, False)))
    mgr.list_connections.return_value = list(connections.keys())
    return mgr


def _dbhub_config(name: str = "erp-mysql") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        type="dbhub",
        transport="stdio",
        databases=[DBHubDatabaseConfig(alias="erp", dsn="mysql://localhost/erp")],
    )


def _generic_config(name: str = "custom-mcp") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        type="generic",
        transport="stdio",
        command="python",
        args=["-m", "some_mcp_server"],
    )


# ---------------------------------------------------------------------------
# DataAccessLayer — Tool definitions
# ---------------------------------------------------------------------------

class TestFixedTools:
    """Test that DataAccessLayer exposes exactly 3 fixed tools."""

    def test_fixed_tools_count(self):
        assert len(FIXED_TOOLS) == 3

    def test_fixed_tool_names(self):
        names = {t.name for t in FIXED_TOOLS}
        assert names == {"list_datasources", "get_schema", "query"}

    def test_get_tools_returns_fixed_set(self):
        mgr = _make_mcp_manager()
        dal = DataAccessLayer(mgr, [])
        tools = dal.get_tools()
        assert len(tools) == 3
        assert all(isinstance(t, ToolInfo) for t in tools)

    def test_tool_count_independent_of_datasources(self):
        """Tool count should NOT increase with more data sources."""
        configs = [_dbhub_config("db1"), _dbhub_config("db2"), _generic_config("mcp1")]
        mgr = _make_mcp_manager(configs)
        dal = DataAccessLayer(mgr, configs)
        tools = dal.get_tools()
        assert len(tools) == 3

    def test_tool_schemas_valid(self):
        for tool in FIXED_TOOLS:
            assert tool.input_schema.get("type") == "object"
            assert "properties" in tool.input_schema

    def test_query_tool_description_has_limits(self):
        """query tool description should mention LIMIT and SELECT-only."""
        query_tool = [t for t in FIXED_TOOLS if t.name == "query"][0]
        assert "LIMIT" in query_tool.description
        assert "SELECT" in query_tool.description

    def test_query_tool_has_examples(self):
        """query tool input schema should include examples."""
        query_tool = [t for t in FIXED_TOOLS if t.name == "query"][0]
        sql_prop = query_tool.input_schema["properties"]["sql"]
        assert "examples" in sql_prop


# ---------------------------------------------------------------------------
# DataAccessLayer — Initialization
# ---------------------------------------------------------------------------

class TestInitialization:
    @pytest.mark.asyncio
    async def test_initialize_dbhub_creates_sql_adapter(self):
        configs = [_dbhub_config("erp")]
        mgr = _make_mcp_manager(configs)
        dal = DataAccessLayer(mgr, configs)
        await dal.initialize()
        assert "erp" in dal.list_datasource_ids()
        assert isinstance(dal.get_adapter("erp"), SQLAdapter)

    @pytest.mark.asyncio
    async def test_initialize_generic_creates_mcp_adapter(self):
        configs = [_generic_config("custom")]
        mgr = _make_mcp_manager(configs)
        dal = DataAccessLayer(mgr, configs)
        await dal.initialize()
        assert "custom" in dal.list_datasource_ids()
        assert isinstance(dal.get_adapter("custom"), MCPAdapter)

    @pytest.mark.asyncio
    async def test_initialize_skips_disabled(self):
        cfg = _dbhub_config("disabled-db")
        cfg.enabled = False
        mgr = _make_mcp_manager([cfg])
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()
        assert dal.list_datasource_ids() == []

    @pytest.mark.asyncio
    async def test_initialize_skips_disconnected(self):
        cfg = _dbhub_config("disconnected-db")
        mgr = MagicMock()
        conn = _make_mcp_conn(cfg.name, connected=False)
        mgr.get_connection = MagicMock(return_value=conn)
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()
        assert dal.list_datasource_ids() == []

    @pytest.mark.asyncio
    async def test_initialize_multiple_mixed(self):
        configs = [_dbhub_config("db1"), _generic_config("mcp1")]
        mgr = _make_mcp_manager(configs)
        dal = DataAccessLayer(mgr, configs)
        await dal.initialize()
        ids = dal.list_datasource_ids()
        assert "db1" in ids
        assert "mcp1" in ids


# ---------------------------------------------------------------------------
# DataAccessLayer — list_datasources routing
# ---------------------------------------------------------------------------

class TestListDatasources:
    @pytest.mark.asyncio
    async def test_list_datasources_returns_envelope(self):
        configs = [_dbhub_config("db1"), _generic_config("mcp1")]
        mgr = _make_mcp_manager(configs)
        dal = DataAccessLayer(mgr, configs)
        await dal.initialize()

        result = await dal.call_tool("list_datasources", {})
        data = json.loads(result)
        assert "datasources" in data
        assert data["count"] == 2
        ids = {d["id"] for d in data["datasources"]}
        assert ids == {"db1", "mcp1"}
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_list_datasources_empty(self):
        mgr = _make_mcp_manager()
        dal = DataAccessLayer(mgr, [])
        await dal.initialize()

        result = await dal.call_tool("list_datasources", {})
        data = json.loads(result)
        assert data["count"] == 0
        assert data["datasources"] == []
        assert "没有" in data["hint"]


# ---------------------------------------------------------------------------
# DataAccessLayer — get_schema routing
# ---------------------------------------------------------------------------

class TestGetSchema:
    @pytest.mark.asyncio
    async def test_get_schema_invalid_datasource(self):
        mgr = _make_mcp_manager()
        dal = DataAccessLayer(mgr, [])
        await dal.initialize()

        result = await dal.call_tool("get_schema", {"datasource_id": "nonexistent"})
        data = json.loads(result)
        assert "error" in data
        assert "nonexistent" in data["error"]
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_get_schema_list_tables(self):
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        conn = mgr.get_connection("db1")
        conn.call_tool = AsyncMock(side_effect=_mock_sql_calls)
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()

        result = await dal.call_tool("get_schema", {"datasource_id": "db1"})
        data = json.loads(result)
        assert data["datasource_id"] == "db1"
        assert "tables" in data
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_get_schema_table_detail(self):
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        conn = mgr.get_connection("db1")
        conn.call_tool = AsyncMock(side_effect=_mock_sql_calls)
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()

        result = await dal.call_tool("get_schema", {"datasource_id": "db1", "table_name": "orders"})
        data = json.loads(result)
        assert data["datasource_id"] == "db1"
        assert "hint" in data
        assert "query" in data["hint"].lower()  # hints about using query tool next


# ---------------------------------------------------------------------------
# DataAccessLayer — query routing
# ---------------------------------------------------------------------------

class TestQuery:
    @pytest.mark.asyncio
    async def test_query_success_structured_response(self):
        """Query result should include data, row_count, duration_ms, warnings."""
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        conn = mgr.get_connection("db1")
        conn.call_tool = AsyncMock(return_value='[{"id": 1, "name": "test"}]')
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()

        result = await dal.call_tool("query", {
            "datasource_id": "db1",
            "sql": "SELECT id, name FROM orders WHERE id = 1 LIMIT 10",
        })
        data = json.loads(result)
        assert "data" in data
        assert data["data"][0]["id"] == 1
        assert "row_count" in data
        assert "duration_ms" in data
        assert "warnings" in data
        assert isinstance(data["warnings"], list)

    @pytest.mark.asyncio
    async def test_query_invalid_datasource(self):
        mgr = _make_mcp_manager()
        dal = DataAccessLayer(mgr, [])
        await dal.initialize()

        result = await dal.call_tool("query", {"datasource_id": "missing", "sql": "SELECT 1"})
        data = json.loads(result)
        assert "error" in data
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_query_error_with_hint(self):
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        conn = mgr.get_connection("db1")
        conn.call_tool = AsyncMock(side_effect=Exception("connection timeout"))
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()

        result = await dal.call_tool("query", {"datasource_id": "db1", "sql": "SELECT 1"})
        data = json.loads(result)
        assert "error" in data
        assert "hint" in data
        assert "timeout" in data["error"]

    @pytest.mark.asyncio
    async def test_query_empty_sql(self):
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        dal = DataAccessLayer(mgr, [cfg])
        await dal.initialize()

        result = await dal.call_tool("query", {"datasource_id": "db1", "sql": ""})
        data = json.loads(result)
        assert "error" in data
        assert "空" in data["error"]

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        mgr = _make_mcp_manager()
        dal = DataAccessLayer(mgr, [])
        await dal.initialize()

        result = await dal.call_tool("nonexistent_tool", {})
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# SQL Safety Guard
# ---------------------------------------------------------------------------

class TestSQLSafetyGuard:
    """Test SQL validation and normalization in the DAL layer."""

    def _make_dal_with_cache(self, cache: list[str] | None = None) -> DataAccessLayer:
        """Create a DAL with a mock adapter that has a tables cache."""
        cfg = _dbhub_config("db1")
        mgr = _make_mcp_manager([cfg])
        dal = DataAccessLayer(mgr, [cfg])
        # Manually inject an adapter with cache
        adapter = MagicMock(spec=SQLAdapter)
        adapter._tables_cache = cache
        dal._adapters["db1"] = adapter
        return dal

    def test_reject_insert(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行写操作"):
            dal._validate_and_normalize_sql("INSERT INTO orders VALUES (1)", "db1")

    def test_reject_delete(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行写操作"):
            dal._validate_and_normalize_sql("DELETE FROM orders", "db1")

    def test_reject_update(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行写操作"):
            dal._validate_and_normalize_sql("UPDATE orders SET name='x'", "db1")

    def test_reject_drop(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行写操作"):
            dal._validate_and_normalize_sql("DROP TABLE orders", "db1")

    def test_reject_truncate(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行写操作"):
            dal._validate_and_normalize_sql("TRUNCATE TABLE orders", "db1")

    def test_reject_multi_statement(self):
        dal = self._make_dal_with_cache()
        with pytest.raises(ValueError, match="禁止执行多条"):
            dal._validate_and_normalize_sql("SELECT 1; DROP TABLE orders", "db1")

    def test_auto_limit_when_missing(self):
        dal = self._make_dal_with_cache()
        sql, warnings = dal._validate_and_normalize_sql("SELECT id FROM orders WHERE id=1", "db1")
        assert "LIMIT 1000" in sql
        assert any("自动添加" in w for w in warnings)

    def test_no_auto_limit_when_present(self):
        dal = self._make_dal_with_cache()
        sql, warnings = dal._validate_and_normalize_sql("SELECT id FROM orders WHERE id=1 LIMIT 50", "db1")
        assert "LIMIT 50" in sql
        assert not any("自动添加" in w for w in warnings)

    def test_warn_select_star(self):
        dal = self._make_dal_with_cache()
        _, warnings = dal._validate_and_normalize_sql("SELECT * FROM orders WHERE id=1 LIMIT 10", "db1")
        assert any("SELECT *" in w for w in warnings)

    def test_warn_no_where(self):
        dal = self._make_dal_with_cache()
        _, warnings = dal._validate_and_normalize_sql("SELECT id FROM orders LIMIT 10", "db1")
        assert any("WHERE" in w for w in warnings)

    def test_warn_no_where_no_limit(self):
        dal = self._make_dal_with_cache()
        _, warnings = dal._validate_and_normalize_sql("SELECT id FROM orders", "db1")
        # Both WHERE warning and auto-LIMIT warning
        assert any("WHERE" in w for w in warnings)
        assert any("自动添加" in w for w in warnings)

    def test_table_not_found_with_cache(self):
        dal = self._make_dal_with_cache(["orders", "products"])
        with pytest.raises(ValueError, match="不存在"):
            dal._validate_and_normalize_sql("SELECT id FROM nonexistent LIMIT 10", "db1")

    def test_table_found_with_cache(self):
        dal = self._make_dal_with_cache(["orders", "products"])
        sql, _ = dal._validate_and_normalize_sql("SELECT id FROM orders WHERE id=1 LIMIT 10", "db1")
        assert "orders" in sql

    def test_no_table_check_without_cache(self):
        """When cache is None, table check is skipped (no error)."""
        dal = self._make_dal_with_cache(None)
        sql, _ = dal._validate_and_normalize_sql("SELECT id FROM anything WHERE id=1 LIMIT 10", "db1")
        assert "anything" in sql

    def test_valid_query_no_extra_warnings(self):
        dal = self._make_dal_with_cache()
        _, warnings = dal._validate_and_normalize_sql(
            "SELECT id, name FROM orders WHERE id = 1 LIMIT 10", "db1",
        )
        # Should have no warnings (has WHERE, has LIMIT, no SELECT *, no issues)
        assert len(warnings) == 0

    def test_strips_trailing_semicolon(self):
        dal = self._make_dal_with_cache()
        sql, _ = dal._validate_and_normalize_sql("SELECT id FROM orders WHERE id=1 LIMIT 10;", "db1")
        assert not sql.endswith(";")

    def test_information_schema_not_blocked(self):
        """Queries against information_schema should not trigger table-not-found."""
        dal = self._make_dal_with_cache(["orders"])
        sql, _ = dal._validate_and_normalize_sql(
            "SELECT table_name FROM information_schema WHERE table_schema='public' LIMIT 10",
            "db1",
        )
        assert "information_schema" in sql


# ---------------------------------------------------------------------------
# Enhanced Error Messages
# ---------------------------------------------------------------------------

class TestEnhancedErrorMessages:
    def test_table_not_found_hint(self):
        dal = DataAccessLayer(MagicMock(), [])
        adapter = MagicMock(spec=SQLAdapter)
        adapter._tables_cache = ["orders", "products"]
        dal._adapters["db1"] = adapter

        error, hint = dal._build_error_response("Table 'foobar' doesn't exist", "db1")
        assert "orders" in hint
        assert "products" in hint

    def test_column_not_found_hint(self):
        dal = DataAccessLayer(MagicMock(), [])
        error, hint = dal._build_error_response("Unknown column 'foo' in orders", "db1")
        assert "get_schema" in hint

    def test_timeout_hint(self):
        dal = DataAccessLayer(MagicMock(), [])
        error, hint = dal._build_error_response("Query execution timeout", "db1")
        assert "WHERE" in hint or "LIMIT" in hint

    def test_generic_error_hint(self):
        dal = DataAccessLayer(MagicMock(), [])
        _, hint = dal._build_error_response("Some random error", "db1")
        assert "SQL" in hint

    def test_datasource_not_found_hint(self):
        """Query to non-existent datasource should suggest list_datasources."""
        dal = DataAccessLayer(MagicMock(), [])
        result_str = '{"error": "数据源 \'xxx\' 不存在。可用数据源: (无)", "hint": "请先调用 list_datasources 获取可用数据源列表。"}'
        # Parse to verify structure
        data = json.loads(result_str)
        assert "list_datasources" in data["hint"]


# ---------------------------------------------------------------------------
# Query Hints
# ---------------------------------------------------------------------------

class TestQueryHints:
    def test_hint_truncated(self):
        result = QueryResult(datasource_id="db1", success=True, rows_count=1000)
        hint = DataAccessLayer._build_query_hint(result)
        assert "截断" in hint

    def test_hint_empty_result(self):
        result = QueryResult(datasource_id="db1", success=True, rows_count=0)
        hint = DataAccessLayer._build_query_hint(result)
        assert "无结果" in hint

    def test_hint_slow_query(self):
        result = QueryResult(datasource_id="db1", success=True, rows_count=10, duration_ms=6000)
        hint = DataAccessLayer._build_query_hint(result)
        assert "慢" in hint or "耗时" in hint

    def test_no_hint_normal_query(self):
        result = QueryResult(datasource_id="db1", success=True, rows_count=10, duration_ms=50)
        hint = DataAccessLayer._build_query_hint(result)
        assert hint == ""


# ---------------------------------------------------------------------------
# SQLAdapter
# ---------------------------------------------------------------------------

class TestSQLAdapter:
    @pytest.mark.asyncio
    async def test_get_info(self):
        conn = _make_mcp_conn("mydb")
        conn.call_tool = AsyncMock(side_effect=_mock_sql_calls)
        cfg = _dbhub_config("mydb")
        adapter = SQLAdapter(conn, cfg)
        info = await adapter.get_info()
        assert info.id == "mydb"
        assert info.type == "sql"

    @pytest.mark.asyncio
    async def test_query_success(self):
        conn = _make_mcp_conn("mydb")
        conn.call_tool = AsyncMock(return_value='[{"id": 1}]')
        cfg = _dbhub_config("mydb")
        adapter = SQLAdapter(conn, cfg)
        result = await adapter.query("SELECT 1")
        assert result.success
        assert result.rows_count == 1
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_query_failure(self):
        conn = _make_mcp_conn("mydb")
        conn.call_tool = AsyncMock(side_effect=Exception("denied"))
        cfg = _dbhub_config("mydb")
        adapter = SQLAdapter(conn, cfg)
        result = await adapter.query("DROP TABLE x")
        assert not result.success
        assert "denied" in result.error
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_test_connection(self):
        conn = _make_mcp_conn("mydb", connected=True)
        cfg = _dbhub_config("mydb")
        adapter = SQLAdapter(conn, cfg)
        assert await adapter.test_connection() is True


# ---------------------------------------------------------------------------
# MCPAdapter
# ---------------------------------------------------------------------------

class TestMCPAdapter:
    @pytest.mark.asyncio
    async def test_get_info(self):
        conn = _make_mcp_conn("custom")
        cfg = _generic_config("custom")
        adapter = MCPAdapter(conn, cfg)
        info = await adapter.get_info()
        assert info.id == "custom"
        assert info.type == "mcp"
        assert "execute_sql" in info.description

    @pytest.mark.asyncio
    async def test_get_schema_returns_tools_as_tables(self):
        conn = _make_mcp_conn("custom")
        cfg = _generic_config("custom")
        adapter = MCPAdapter(conn, cfg)
        result = await adapter.get_schema()
        assert result.datasource_id == "custom"
        assert result.tables is not None
        assert len(result.tables) == 1
        assert result.tables[0].name == "execute_sql"

    @pytest.mark.asyncio
    async def test_query_with_execute_sql(self):
        conn = _make_mcp_conn("custom")
        conn.call_tool = AsyncMock(return_value='[{"x": 1}]')
        cfg = _generic_config("custom")
        adapter = MCPAdapter(conn, cfg)
        result = await adapter.query("SELECT 1")
        assert result.success
        assert result.rows_count == 1
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_test_connection(self):
        conn = _make_mcp_conn("custom", connected=True)
        cfg = _generic_config("custom")
        adapter = MCPAdapter(conn, cfg)
        assert await adapter.test_connection() is True


# ---------------------------------------------------------------------------
# Agent integration — DataAccessLayer backend
# ---------------------------------------------------------------------------

class TestAgentWithDAL:
    @pytest.mark.asyncio
    async def test_agent_uses_dal_tools(self):
        """Agent should use DataAccessLayer's 3 fixed tools."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        # Mock LLM that returns final output immediately
        llm = MagicMock(spec=LLMClient)
        response = MagicMock()
        response.content = json.dumps({
            "alerts": [],
            "summary": "No issues found",
            "has_alerts": False,
        })
        response.tool_calls = []
        response.token_usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        llm.completion = AsyncMock(return_value=response)

        # Mock DAL
        dal = MagicMock()
        dal.get_tools.return_value = FIXED_TOOLS
        dal.call_tool = AsyncMock(return_value="[]")

        agent = Agent(
            llm_client=llm,
            data_access_layer=dal,
            config=AgentConfig(inject_schema=False),
        )

        result = await agent.run("Check inventory")
        assert result.summary == "No issues found"
        # Verify DAL tools were used
        dal.get_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_routes_tool_calls_to_dal(self):
        """When Agent makes tool calls, they should go to DAL.call_tool."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        # First call: LLM returns a tool call
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.name = "list_datasources"
        tool_call.arguments = {}

        response1 = MagicMock()
        response1.content = ""
        response1.tool_calls = [tool_call]
        response1.token_usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        # Second call: LLM returns final output
        response2 = MagicMock()
        response2.content = json.dumps({
            "alerts": [], "summary": "All good", "has_alerts": False,
        })
        response2.tool_calls = []
        response2.token_usage = MagicMock(prompt_tokens=20, completion_tokens=10, total_tokens=30)

        llm = MagicMock(spec=LLMClient)
        llm.completion = AsyncMock(side_effect=[response1, response2])

        dal = MagicMock()
        dal.get_tools.return_value = FIXED_TOOLS
        dal.call_tool = AsyncMock(return_value=json.dumps({
            "datasources": [{"id": "db1", "name": "DB1", "type": "sql"}],
            "count": 1,
            "hint": "选择一个 datasource_id",
        }))

        agent = Agent(
            llm_client=llm,
            data_access_layer=dal,
            config=AgentConfig(inject_schema=False),
        )

        result = await agent.run("Check inventory")
        assert result.summary == "All good"
        # Verify call was routed to DAL
        dal.call_tool.assert_called_once_with("list_datasources", {})


# ---------------------------------------------------------------------------
# Multi-datasource routing
# ---------------------------------------------------------------------------

class TestMultiDatasourceRouting:
    @pytest.mark.asyncio
    async def test_query_routes_to_correct_adapter(self):
        """Query for 'db1' should go to db1's adapter, not db2's."""
        configs = [_dbhub_config("db1"), _dbhub_config("db2")]
        mgr = _make_mcp_manager(configs)

        conn1 = mgr.get_connection("db1")
        conn1.call_tool = AsyncMock(return_value='[{"source": "db1"}]')
        conn2 = mgr.get_connection("db2")
        conn2.call_tool = AsyncMock(return_value='[{"source": "db2"}]')

        dal = DataAccessLayer(mgr, configs)
        await dal.initialize()

        # Query db1
        result1 = await dal.call_tool("query", {
            "datasource_id": "db1",
            "sql": "SELECT source FROM t WHERE id=1 LIMIT 10",
        })
        data1 = json.loads(result1)
        assert data1["data"][0]["source"] == "db1"

        # Query db2
        result2 = await dal.call_tool("query", {
            "datasource_id": "db2",
            "sql": "SELECT source FROM t WHERE id=2 LIMIT 10",
        })
        data2 = json.loads(result2)
        assert data2["data"][0]["source"] == "db2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mock_sql_calls(tool_name: str, arguments: dict) -> str:
    """Mock MCP tool calls for SQL operations."""
    sql = arguments.get("sql", "").lower() if arguments else ""

    if tool_name == "execute_sql":
        if "information_schema.tables" in sql:
            return json.dumps([
                {"table_name": "orders"},
                {"table_name": "products"},
            ])
        if "sqlite_master" in sql:
            return json.dumps([
                {"name": "orders"},
                {"name": "products"},
            ])
        if "information_schema.columns" in sql or "pragma table_info" in sql:
            return json.dumps([
                {"column_name": "id", "data_type": "int", "column_comment": "Primary key"},
                {"column_name": "name", "data_type": "varchar", "column_comment": "Product name"},
            ])
        if "pragma index_list" in sql:
            return json.dumps([])
        if "pragma foreign_key_list" in sql:
            return json.dumps([])
        if "select *" in sql and "limit" in sql:
            return json.dumps([{"id": 1, "name": "Sample"}])
        return json.dumps([])

    if tool_name == "search_objects":
        return json.dumps(["orders", "products"])

    return json.dumps([])
