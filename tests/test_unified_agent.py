"""Tests for unified Agent (N12) — write interception, tool registration, AgentResult."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from order_guard.engine.agent import (
    Agent,
    AgentConfig,
    AgentResult,
    _tool_info_to_llm_function,
)
from order_guard.engine.llm_client import TokenUsage
from order_guard.mcp.models import ToolInfo
from order_guard.tools import (
    rule_tools, context_tools, alert_tools, data_tools,
    health_tools, report_tools, usage_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class FakeLLMResponse:
    content: str | None
    tool_calls: list | None
    token_usage: TokenUsage


def make_llm_response(content=None, tool_calls=None):
    return FakeLLMResponse(
        content=content,
        tool_calls=tool_calls,
        token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )


# ---------------------------------------------------------------------------
# Tool definitions tests
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_all_tools(self):
        all_tools = (
            data_tools.TOOL_DEFINITIONS
            + rule_tools.TOOL_DEFINITIONS
            + context_tools.TOOL_DEFINITIONS
            + alert_tools.TOOL_DEFINITIONS
            + health_tools.TOOL_DEFINITIONS
            + report_tools.TOOL_DEFINITIONS
            + usage_tools.TOOL_DEFINITIONS
        )
        assert len(all_tools) == 19

    def test_unique_tool_names(self):
        all_tools = (
            data_tools.TOOL_DEFINITIONS
            + rule_tools.TOOL_DEFINITIONS
            + context_tools.TOOL_DEFINITIONS
            + alert_tools.TOOL_DEFINITIONS
            + health_tools.TOOL_DEFINITIONS
            + report_tools.TOOL_DEFINITIONS
            + usage_tools.TOOL_DEFINITIONS
        )
        names = [t.name for t in all_tools]
        assert len(names) == len(set(names))

    def test_tool_info_to_llm_function(self):
        tool = ToolInfo(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}, "required": []},
            server_name="test",
        )
        result = _tool_info_to_llm_function(tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "test_tool"
        assert result["function"]["description"] == "A test tool"


# ---------------------------------------------------------------------------
# AgentResult structure tests
# ---------------------------------------------------------------------------

class TestAgentResult:
    def test_default_values(self):
        result = AgentResult(response="hello")
        assert result.response == "hello"
        assert result.tool_calls_log == []


# ---------------------------------------------------------------------------
# Tool calls log tests
# ---------------------------------------------------------------------------

class TestToolCallsLog:
    @pytest.mark.asyncio
    async def test_logs_tool_calls(self):
        mock_llm = AsyncMock()
        mock_executor = AsyncMock(return_value={"data": [], "hint": "ok"})

        mock_llm.completion = AsyncMock(side_effect=[
            make_llm_response(tool_calls=[
                FakeToolCall(id="tc1", name="list_rules", arguments={}),
            ]),
            make_llm_response(content="没有规则。"),
        ])

        agent = Agent(
            llm_client=mock_llm,
            config=AgentConfig(inject_business_context=False),
            tools=[ToolInfo(name="list_rules", description="x", input_schema={"type": "object", "properties": {}, "required": []}, server_name="t")],
            tool_executors={"list_rules": mock_executor},
        )

        result = await agent.run_unified("查看规则")
        assert len(result.tool_calls_log) == 1
        assert result.tool_calls_log[0]["tool"] == "list_rules"


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_detection_mode_still_works(self):
        """The `run()` method should still return AnalyzerOutput."""
        mock_llm = AsyncMock()
        mock_llm.completion = AsyncMock(return_value=make_llm_response(
            content='{"alerts": [], "summary": "正常", "has_alerts": false}'
        ))

        mock_dal = MagicMock()
        mock_dal.get_tools.return_value = []

        agent = Agent(
            llm_client=mock_llm,
            data_access_layer=mock_dal,
            config=AgentConfig(inject_business_context=False),
        )

        result = await agent.run("检查库存")
        from order_guard.engine.analyzer import AnalyzerOutput
        assert isinstance(result, AnalyzerOutput)
        assert result.summary == "正常"


# ---------------------------------------------------------------------------
# data_tools migration tests
# ---------------------------------------------------------------------------

class TestDataToolsMigration:
    def test_has_3_tools(self):
        assert len(data_tools.TOOL_DEFINITIONS) == 3

    def test_tool_names(self):
        names = {t.name for t in data_tools.TOOL_DEFINITIONS}
        assert names == {"list_datasources", "get_schema", "query"}

    def test_has_executors(self):
        assert "list_datasources" in data_tools.TOOL_EXECUTORS
        assert "get_schema" in data_tools.TOOL_EXECUTORS
        assert "query" in data_tools.TOOL_EXECUTORS
