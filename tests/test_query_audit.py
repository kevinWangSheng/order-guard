"""Tests for T23: Query audit — logging, CLI, statistics."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from order_guard.engine.agent import Agent, AgentConfig
from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall
from order_guard.models.tables import QueryLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FINAL_JSON = json.dumps({
    "alerts": [],
    "summary": "No issues",
    "has_alerts": False,
})


def _make_mock_mcp(tables=None):
    """Create a mock MCP connection."""
    mcp = AsyncMock()
    mcp.name = "test-db"

    tool = MagicMock()
    tool.name = "execute_sql"
    tool.description = "Execute SQL"
    tool.input_schema = {}
    tool.server_name = "test-db"
    mcp.list_tools.return_value = [tool]

    tables = tables or []

    async def _call_tool(name, args=None):
        args = args or {}
        sql = args.get("sql", "")
        if "sqlite_master" in sql:
            return json.dumps([{"name": t} for t in tables])
        if sql.startswith("PRAGMA"):
            return json.dumps([])
        if sql.startswith("SELECT"):
            return json.dumps([{"id": 1}, {"id": 2}])
        return json.dumps([])

    mcp.call_tool.side_effect = _call_tool
    return mcp


# ---------------------------------------------------------------------------
# QueryLog model tests
# ---------------------------------------------------------------------------

class TestQueryLogModel:
    def test_query_log_fields(self):
        log = QueryLog(
            rule_id="rule-1",
            mcp_server="test-db",
            sql="SELECT * FROM products",
            status="success",
            rows_returned=10,
            duration_ms=42,
            agent_iteration=3,
        )
        assert log.rule_id == "rule-1"
        assert log.mcp_server == "test-db"
        assert log.sql == "SELECT * FROM products"
        assert log.status == "success"
        assert log.rows_returned == 10
        assert log.duration_ms == 42
        assert log.agent_iteration == 3

    def test_query_log_defaults(self):
        log = QueryLog()
        assert log.rule_id == ""
        assert log.status == "success"
        assert log.rows_returned == 0
        assert log.duration_ms == 0
        assert log.error is None

    def test_query_log_error_status(self):
        log = QueryLog(status="error", error="table not found")
        assert log.status == "error"
        assert log.error == "table not found"


# ---------------------------------------------------------------------------
# Agent audit integration tests
# ---------------------------------------------------------------------------

class TestAgentAudit:
    @pytest.mark.asyncio
    async def test_agent_logs_successful_query(self):
        """Agent should log successful execute_sql calls."""
        mcp = _make_mock_mcp()
        logged_queries = []

        async def _mock_log_query(**kwargs):
            logged_queries.append(kwargs)

        llm = AsyncMock()
        call_count = 0

        async def _mock_completion(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="execute_sql", arguments={"sql": "SELECT * FROM products LIMIT 10"})],
                    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            return LLMResponse(
                content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
            )

        llm.completion.side_effect = _mock_completion

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=False, validate_sql=False),
            rule_id="test-rule",
        )
        agent._log_query = _mock_log_query

        await agent.run("检查数据")

        assert len(logged_queries) == 1
        assert logged_queries[0]["sql"] == "SELECT * FROM products LIMIT 10"
        assert logged_queries[0]["status"] == "success"
        assert logged_queries[0]["rows_returned"] == 2  # mock returns 2 rows

    @pytest.mark.asyncio
    async def test_agent_logs_failed_query(self):
        """Agent should log failed execute_sql calls."""
        mcp = AsyncMock()
        mcp.name = "test-db"

        tool = MagicMock()
        tool.name = "execute_sql"
        tool.description = "Execute SQL"
        tool.input_schema = {}
        tool.server_name = "test-db"
        mcp.list_tools.return_value = [tool]
        mcp.call_tool.side_effect = RuntimeError("connection lost")

        logged_queries = []

        async def _mock_log_query(**kwargs):
            logged_queries.append(kwargs)

        llm = AsyncMock()
        call_count = 0

        async def _mock_completion(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="execute_sql", arguments={"sql": "SELECT 1"})],
                    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            return LLMResponse(
                content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
            )

        llm.completion.side_effect = _mock_completion

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=False, validate_sql=False),
        )
        agent._log_query = _mock_log_query

        await agent.run("检查数据")

        assert len(logged_queries) == 1
        assert logged_queries[0]["status"] == "error"
        assert "connection lost" in logged_queries[0]["error"]

    @pytest.mark.asyncio
    async def test_agent_logs_rejected_query(self):
        """Agent should log rejected SQL (validation failure)."""
        mcp = _make_mock_mcp(tables=["products"])
        logged_queries = []

        async def _mock_log_query(**kwargs):
            logged_queries.append(kwargs)

        llm = AsyncMock()
        call_count = 0

        async def _mock_completion(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="execute_sql", arguments={"sql": "SELECT * FROM nonexistent"})],
                    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            return LLMResponse(
                content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
            )

        llm.completion.side_effect = _mock_completion

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=True, validate_sql=True),
        )
        agent._log_query = _mock_log_query

        await agent.run("检查数据")

        # Find the rejected query log
        rejected = [q for q in logged_queries if q["status"] == "rejected"]
        assert len(rejected) == 1
        assert "nonexistent" in rejected[0]["sql"]

    @pytest.mark.asyncio
    async def test_agent_does_not_log_non_sql_tools(self):
        """Agent should NOT log non-execute_sql tool calls."""
        mcp = AsyncMock()
        mcp.name = "test-db"

        tools = [MagicMock() for _ in range(2)]
        tools[0].name = "search_objects"
        tools[0].description = "Search"
        tools[0].input_schema = {}
        tools[0].server_name = "test-db"
        tools[1].name = "execute_sql"
        tools[1].description = "Execute SQL"
        tools[1].input_schema = {}
        tools[1].server_name = "test-db"
        mcp.list_tools.return_value = tools
        mcp.call_tool.return_value = json.dumps([])

        logged_queries = []

        async def _mock_log_query(**kwargs):
            logged_queries.append(kwargs)

        llm = AsyncMock()
        call_count = 0

        async def _mock_completion(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="tc1", name="search_objects", arguments={"query": ""})],
                    token_usage=TokenUsage(),
                )
            return LLMResponse(
                content=FINAL_JSON, tool_calls=[], token_usage=TokenUsage(),
            )

        llm.completion.side_effect = _mock_completion

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=False, validate_sql=False),
        )
        agent._log_query = _mock_log_query

        await agent.run("检查数据")

        assert len(logged_queries) == 0


# ---------------------------------------------------------------------------
# Row counting tests
# ---------------------------------------------------------------------------

class TestCountResultRows:
    def test_count_json_array(self):
        assert Agent._count_result_rows('[{"id": 1}, {"id": 2}]') == 2

    def test_count_empty_array(self):
        assert Agent._count_result_rows('[]') == 0

    def test_count_non_json(self):
        assert Agent._count_result_rows("some text") == 0

    def test_count_json_object(self):
        assert Agent._count_result_rows('{"key": "value"}') == 0
