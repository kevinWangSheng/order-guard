"""Tests for AI Agent tool use loop (T17)."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from order_guard.engine.agent import Agent, AgentConfig, _tool_info_to_llm_function
from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall
from order_guard.mcp.models import ToolInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_mcp():
    """Create a mock MCP connection with standard tools."""
    mock_mcp = MagicMock()
    mock_mcp.name = "test-db"
    mock_mcp.list_tools = AsyncMock(return_value=[
        ToolInfo(name="list_tables", description="List all tables", input_schema={"type": "object", "properties": {}}, server_name="test-db"),
        ToolInfo(name="read_query", description="Run SQL SELECT", input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, server_name="test-db"),
    ])
    mock_mcp.call_tool = AsyncMock(return_value="products, orders, inventory")
    return mock_mcp


def _make_tool_call_response(tool_calls: list[dict], content: str = "") -> LLMResponse:
    """Create an LLMResponse with tool calls."""
    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(**tc) for tc in tool_calls],
        token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )


def _make_text_response(content: str) -> LLMResponse:
    """Create an LLMResponse with text only (final answer)."""
    return LLMResponse(
        content=content,
        tool_calls=[],
        token_usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    )


FINAL_JSON = json.dumps({
    "alerts": [
        {
            "sku": "SKU-002",
            "severity": "critical",
            "title": "缺货风险",
            "reason": "库存仅剩 5 件，日均销量 10 件",
            "suggestion": "立即补货 100 件",
        }
    ],
    "summary": "发现 1 个 SKU 存在缺货风险",
    "has_alerts": True,
})


# ---------------------------------------------------------------------------
# Tool conversion tests
# ---------------------------------------------------------------------------

class TestToolConversion:
    def test_tool_info_to_llm_function(self):
        tool = ToolInfo(
            name="read_query",
            description="Execute a SQL query",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            server_name="db1",
        )
        result = _tool_info_to_llm_function(tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "read_query"
        assert result["function"]["description"] == "Execute a SQL query"
        assert "query" in result["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# Agent loop tests
# ---------------------------------------------------------------------------

class TestAgent:
    @pytest.mark.asyncio
    async def test_single_tool_call_then_final(self):
        """Agent calls one tool, then produces final output."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()

        # First call: LLM requests list_tables
        # Second call: LLM returns final JSON
        mock_llm.completion = AsyncMock(side_effect=[
            _make_tool_call_response([{
                "id": "call_1",
                "name": "list_tables",
                "arguments": {},
            }]),
            _make_text_response(FINAL_JSON),
        ])

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存风险")

        assert result.has_alerts is True
        assert len(result.alerts) == 1
        assert result.alerts[0].sku == "SKU-002"
        assert result.alerts[0].severity == "critical"
        assert result.token_usage.total_tokens == 450  # 150 + 300
        mock_mcp.call_tool.assert_called_once_with("list_tables", {})

    @pytest.mark.asyncio
    async def test_multi_tool_calls(self):
        """Agent makes multiple tool calls across iterations."""
        mock_mcp = _make_mock_mcp()
        mock_mcp.call_tool = AsyncMock(side_effect=[
            "products, inventory, orders, daily_sales",
            "sku TEXT, quantity INTEGER, warehouse TEXT",
            "SKU-001: 100, SKU-002: 5, SKU-003: 500",
        ])

        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(side_effect=[
            # Iteration 1: list_tables
            _make_tool_call_response([{
                "id": "call_1", "name": "list_tables", "arguments": {},
            }]),
            # Iteration 2: describe table
            _make_tool_call_response([{
                "id": "call_2", "name": "read_query",
                "arguments": {"query": "PRAGMA table_info(inventory)"},
            }]),
            # Iteration 3: query data
            _make_tool_call_response([{
                "id": "call_3", "name": "read_query",
                "arguments": {"query": "SELECT * FROM inventory"},
            }]),
            # Iteration 4: final output
            _make_text_response(FINAL_JSON),
        ])

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存风险")

        assert result.has_alerts is True
        assert mock_mcp.call_tool.call_count == 3
        assert mock_llm.completion.call_count == 4

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self):
        """Agent stops after max_iterations."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()

        # Always return tool calls, never final text
        mock_llm.completion = AsyncMock(return_value=_make_tool_call_response([{
            "id": "call_x", "name": "list_tables", "arguments": {},
        }]))

        config = AgentConfig(max_iterations=3)
        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp, config=config)
        result = await agent.run("检查库存风险")

        assert "最大迭代次数" in result.summary
        assert mock_llm.completion.call_count == 3

    @pytest.mark.asyncio
    async def test_tool_call_failure_passed_to_llm(self):
        """When a tool call fails, error is sent back to LLM."""
        mock_mcp = _make_mock_mcp()
        mock_mcp.call_tool = AsyncMock(side_effect=RuntimeError("table not found"))

        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(side_effect=[
            _make_tool_call_response([{
                "id": "call_1", "name": "read_query",
                "arguments": {"query": "SELECT * FROM nonexistent"},
            }]),
            _make_text_response(FINAL_JSON),
        ])

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查数据")

        assert result.has_alerts is True
        # Verify the error message was passed to LLM in messages
        second_call_messages = mock_llm.completion.call_args_list[1][0][0]
        tool_msg = [m for m in second_call_messages if m.get("role") == "tool"][0]
        assert "Error" in tool_msg["content"]

    @pytest.mark.asyncio
    async def test_token_usage_accumulated(self):
        """Token usage is accumulated across all LLM calls."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(side_effect=[
            _make_tool_call_response([{
                "id": "call_1", "name": "list_tables", "arguments": {},
            }]),  # 150 tokens
            _make_text_response(FINAL_JSON),  # 300 tokens
        ])

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        assert result.token_usage.prompt_tokens == 200  # 100 + 100
        assert result.token_usage.completion_tokens == 250  # 50 + 200
        assert result.token_usage.total_tokens == 450  # 150 + 300

    @pytest.mark.asyncio
    async def test_direct_text_response(self):
        """LLM returns text without any tool calls."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(return_value=_make_text_response(FINAL_JSON))

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        assert result.has_alerts is True
        assert mock_mcp.call_tool.call_count == 0

    @pytest.mark.asyncio
    async def test_malformed_json_output(self):
        """Agent handles malformed JSON in final output gracefully."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(return_value=_make_text_response("Not valid JSON"))

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        # Should return with text as summary, not crash
        assert result.summary == "Not valid JSON"
        assert len(result.alerts) == 0

    @pytest.mark.asyncio
    async def test_json_in_code_block(self):
        """Agent extracts JSON from markdown code blocks."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()
        wrapped = f"```json\n{FINAL_JSON}\n```"
        mock_llm.completion = AsyncMock(return_value=_make_text_response(wrapped))

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        assert result.has_alerts is True
        assert result.alerts[0].sku == "SKU-002"

    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self):
        """Agent handles multiple tool calls in a single response."""
        mock_mcp = _make_mock_mcp()
        mock_mcp.call_tool = AsyncMock(side_effect=[
            "col1, col2, col3",
            "another_col1, another_col2",
        ])

        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(side_effect=[
            _make_tool_call_response([
                {"id": "call_1", "name": "read_query", "arguments": {"query": "PRAGMA table_info(inventory)"}},
                {"id": "call_2", "name": "read_query", "arguments": {"query": "PRAGMA table_info(orders)"}},
            ]),
            _make_text_response(FINAL_JSON),
        ])

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        assert mock_mcp.call_tool.call_count == 2
        assert result.has_alerts is True

    @pytest.mark.asyncio
    async def test_output_compatible_with_dispatcher(self):
        """AnalyzerOutput from Agent is compatible with existing pipeline."""
        mock_mcp = _make_mock_mcp()
        mock_llm = MagicMock()
        mock_llm.completion = AsyncMock(return_value=_make_text_response(FINAL_JSON))

        agent = Agent(llm_client=mock_llm, mcp_connection=mock_mcp)
        result = await agent.run("检查库存")

        # Verify AnalyzerOutput structure
        assert hasattr(result, "alerts")
        assert hasattr(result, "summary")
        assert hasattr(result, "has_alerts")
        assert hasattr(result, "token_usage")
        assert result.alerts[0].severity in ("critical", "warning", "info")
