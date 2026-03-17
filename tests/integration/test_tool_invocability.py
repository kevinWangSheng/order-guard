"""L2 — Tool invocability tests.

Verifies that:
1. All 19 expected tools are registered with correct definitions + executors.
2. Each tool category can be invoked with seed data and returns valid structure.
"""
from __future__ import annotations

import pytest

from tests.integration.conftest import (
    EXPECTED_TOOLS,
    assemble_all_tools,
    seed_alerts,
    seed_business_context,
    seed_health_logs,
    seed_report_config,
    seed_rules,
    seed_usage_logs,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestToolRegistration:
    """Verify tool manifest matches production assembly."""

    def test_all_expected_tools_registered(self):
        tools, executors = assemble_all_tools()
        tool_names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - tool_names
        assert not missing, f"Missing tools: {missing}"

    def test_no_unexpected_tools(self):
        tools, executors = assemble_all_tools()
        tool_names = {t.name for t in tools}
        extra = tool_names - EXPECTED_TOOLS
        assert not extra, f"Unexpected tools: {extra}"

    def test_tool_count_matches(self):
        tools, executors = assemble_all_tools()
        assert len(tools) == len(EXPECTED_TOOLS)

    def test_every_tool_has_executor(self):
        tools, executors = assemble_all_tools()
        for tool in tools:
            assert tool.name in executors, f"Tool '{tool.name}' has no executor"

    def test_every_executor_has_definition(self):
        tools, executors = assemble_all_tools()
        tool_names = {t.name for t in tools}
        for name in executors:
            assert name in tool_names, f"Executor '{name}' has no tool definition"

    def test_tool_definitions_have_input_schema(self):
        tools, _ = assemble_all_tools()
        for tool in tools:
            assert tool.input_schema, f"Tool '{tool.name}' has no input_schema"
            assert "type" in tool.input_schema, f"Tool '{tool.name}' schema missing 'type'"


# ---------------------------------------------------------------------------
# Invocability tests — each tool category with seed data
# ---------------------------------------------------------------------------

class TestToolInvocation:
    """Invoke each tool category with real seed data and verify return shape."""

    async def test_list_rules_invocable(self):
        await seed_rules(2)
        from order_guard.tools.rule_tools import list_rules
        result = await list_rules()
        assert "data" in result
        assert len(result["data"]) == 2

    async def test_list_alerts_invocable(self):
        rules = await seed_rules(1)
        await seed_alerts(rules[0].id, 3)
        from order_guard.tools.alert_tools import list_alerts
        result = await list_alerts()
        assert "data" in result
        assert len(result["data"]) == 3

    async def test_get_alert_stats_invocable(self):
        rules = await seed_rules(1)
        await seed_alerts(rules[0].id, 5)
        from order_guard.tools.alert_tools import get_alert_stats
        result = await get_alert_stats(time_range="7d")
        assert "data" in result
        assert result["data"]["total"] == 5

    async def test_handle_alert_invocable(self):
        rules = await seed_rules(1)
        alerts = await seed_alerts(rules[0].id, 1)
        from order_guard.tools.alert_tools import handle_alert
        result = await handle_alert(alert_id=alerts[0].id, resolution="handled")
        assert "data" in result
        assert result["data"]["affected"] == 1

    async def test_list_context_invocable(self):
        await seed_business_context("知识A")
        await seed_business_context("知识B")
        from order_guard.tools.context_tools import list_context
        result = await list_context()
        assert "data" in result
        assert len(result["data"]) == 2

    async def test_add_context_invocable(self):
        from order_guard.tools.context_tools import add_context
        result = await add_context(content="新知识", category="strategy")
        assert "data" in result
        assert result["data"]["content"] == "新知识"

    async def test_get_usage_stats_invocable(self):
        await seed_usage_logs(3)
        from order_guard.tools.usage_tools import get_usage_stats
        result = await get_usage_stats(time_range="7d")
        assert "data" in result
        assert result["data"]["count"] == 3

    async def test_manage_report_list_invocable(self):
        await seed_report_config("rpt-1")
        from order_guard.tools.report_tools import manage_report
        result = await manage_report(action="list")
        assert "data" in result
        assert len(result["data"]) >= 1

    async def test_create_rule_validation(self):
        """create_rule should validate required fields."""
        from order_guard.tools.rule_tools import create_rule
        result = await create_rule(name="", mcp_server="x", prompt_template="p", schedule="0 9 * * *")
        assert "error" in result

    async def test_delete_context_invocable(self):
        ctx = await seed_business_context("要删除的知识")
        from order_guard.tools.context_tools import delete_context
        result = await delete_context(context_id=ctx.id)
        assert "data" in result
        assert result["data"]["deleted"] is True
