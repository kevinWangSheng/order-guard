"""L2 — Detection pipeline integration tests.

Tests the full pipeline: MockLLM drives Agent → tools execute for real →
Alerts saved to DB → TaskRun recorded.
"""
from __future__ import annotations

import json

import pytest

from tests.integration.conftest import (
    MockLLMStep,
    build_mock_agent,
    seed_rules,
)
from order_guard.engine.llm_client import ToolCall

pytestmark = pytest.mark.asyncio


class TestDetectionPipeline:
    """Full detection pipeline with MockLLMClient."""

    async def test_agent_run_produces_analyzer_output(self):
        """Agent.run() should parse final JSON into AnalyzerOutput."""
        rules = await seed_rules(1, id="det-rule-1")

        final_json = json.dumps({
            "alerts": [
                {
                    "sku": "SKU-001",
                    "severity": "critical",
                    "title": "缺货告警",
                    "reason": "库存为0",
                    "suggestion": "立即补货",
                }
            ],
            "summary": "发现1个异常",
            "has_alerts": True,
        })

        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})]),
            MockLLMStep(content=final_json),
        ]
        agent, mock_llm = build_mock_agent(steps)
        result = await agent.run("检查库存数据")

        assert result.has_alerts is True
        assert len(result.alerts) == 1
        assert result.alerts[0].severity == "critical"
        assert result.alerts[0].sku == "SKU-001"
        assert mock_llm.call_count == 2

    async def test_agent_run_no_alerts(self):
        """Agent should handle 'no alerts' case."""
        final_json = json.dumps({
            "alerts": [],
            "summary": "数据正常",
            "has_alerts": False,
        })
        steps = [MockLLMStep(content=final_json)]
        agent, _ = build_mock_agent(steps)
        result = await agent.run("检查数据")

        assert result.has_alerts is False
        assert len(result.alerts) == 0

    async def test_disabled_rule_skipped(self):
        """Disabled rules should be skipped in the pipeline."""
        rules = await seed_rules(1, id="disabled-rule", enabled=False)

        from order_guard.engine.rules import RuleManager
        from order_guard.engine.analyzer import Analyzer
        from order_guard.alerts.dispatcher import AlertDispatcher
        from order_guard.scheduler.jobs import run_detection_job

        rm = RuleManager()
        analyzer = Analyzer()
        dispatcher = AlertDispatcher()

        task_run = await run_detection_job(
            rule_id="disabled-rule",
            job_name="test-disabled",
            rule_manager=rm,
            analyzer=analyzer,
            dispatcher=dispatcher,
        )
        assert task_run is not None

        # Re-fetch from DB to get updated status (in-memory SQLite uses separate sessions)
        from order_guard.storage.database import get_session
        from order_guard.storage.crud import get_by_id
        from order_guard.models import TaskRun
        async with get_session() as session:
            refreshed = await get_by_id(session, TaskRun, task_run.id)
            assert refreshed.status == "success"

    async def test_max_iterations_timeout(self):
        """Agent should stop at max_iterations and return timeout message."""
        from order_guard.engine.agent import Agent, AgentConfig

        # All steps are tool calls — Agent never gets final content
        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})])
            for _ in range(20)
        ]
        agent, mock_llm = build_mock_agent(steps)
        # Override max_iterations to 3
        agent._config.max_iterations = 3

        result = await agent.run("检查数据")
        assert "最大迭代次数" in result.summary
        assert mock_llm.call_count == 3

    async def test_token_usage_accumulated(self):
        """Token usage should be accumulated across iterations."""
        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})]),
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})]),
            MockLLMStep(content='{"alerts":[],"summary":"ok","has_alerts":false}'),
        ]
        agent, _ = build_mock_agent(steps)
        result = await agent.run("检查数据")

        # Each step produces 150 tokens → 3 steps = 450
        assert result.token_usage.total_tokens == 450

    async def test_agent_unified_mode_returns_agent_result(self):
        """Agent.run_unified() should return AgentResult."""
        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})]),
            MockLLMStep(content="已查询规则列表。"),
        ]
        agent, _ = build_mock_agent(steps)
        result = await agent.run_unified("查看规则")

        assert result.response == "已查询规则列表。"
        assert len(result.tool_calls_log) == 1
        assert result.tool_calls_log[0]["tool"] == "list_rules"

    async def test_tool_execution_error_handled(self):
        """Agent should handle tool execution errors gracefully."""
        # Call a non-existent tool — should get error message back
        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="nonexistent_tool", arguments={})]),
            MockLLMStep(content="处理完成。"),
        ]
        agent, _ = build_mock_agent(steps)
        result = await agent.run_unified("测试")

        assert result.response == "处理完成。"

    async def test_multiple_tool_calls_in_one_step(self):
        """Agent should handle multiple tool calls in a single LLM response."""
        await seed_rules(1, id="multi-tc-rule")

        steps = [
            MockLLMStep(tool_calls=[
                ToolCall(name="list_rules", arguments={}),
                ToolCall(name="list_context", arguments={}),
            ]),
            MockLLMStep(content="查询完成。"),
        ]
        agent, _ = build_mock_agent(steps)
        result = await agent.run_unified("查看规则和知识")

        assert len(result.tool_calls_log) == 2
        assert result.tool_calls_log[0]["tool"] == "list_rules"
        assert result.tool_calls_log[1]["tool"] == "list_context"
