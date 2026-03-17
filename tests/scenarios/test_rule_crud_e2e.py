"""L3 E2E — Rule CRUD with real LLM.

Tests the full rule lifecycle: create → DB verify, delete flow.
Tools execute directly without system-level interception.

Requires LLM API Key in .env.
"""
from __future__ import annotations

import pytest

from order_guard.models import AlertRule
from order_guard.storage.database import get_session
from order_guard.storage.crud import list_all
from tests.scenarios.conftest import build_e2e_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class TestRuleCrudE2E:
    """E2E rule CRUD tests with real LLM."""

    async def test_create_rule_executes_directly(self, seeded_data):
        """User requests rule creation → tool executes directly → rule in DB."""
        agent = build_e2e_agent()
        result = await agent.run_unified(
            "创建一个监控规则，名称叫'库存日检'，数据源 test-db，"
            "每天9点检查，prompt 是'检查库存低于安全线的SKU'"
        )

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "create_rule" in tool_names, f"Expected create_rule in {tool_names}"

        # Rule should be in DB (executed directly, no interception)
        async with get_session() as session:
            all_rules = await list_all(session, AlertRule)
            rule_names = [r.name for r in all_rules]
            assert "库存日检" in rule_names, (
                "Rule should be created directly without interception"
            )

    async def test_delete_rule_flow(self, seeded_data):
        """User requests rule deletion → LLM decides whether to confirm via prompt."""
        agent = build_e2e_agent()
        result = await agent.run_unified("删除规则 rule-disabled")

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        # LLM should at least list rules or attempt delete
        assert "delete_rule" in tool_names or "list_rules" in tool_names, (
            f"Expected delete_rule or list_rules in {tool_names}"
        )
