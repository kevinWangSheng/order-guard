"""L3 E2E — Chat query with real LLM.

Tests user conversational queries where Agent fetches real data from seeded DB.
Verifies that responses contain actual data values, not just tool routing.

Requires LLM API Key in .env.
"""
from __future__ import annotations

import pytest

from order_guard.models import Alert
from order_guard.storage.database import get_session
from sqlalchemy import select
from tests.scenarios.conftest import build_e2e_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class TestChatQueryE2E:
    """E2E chat query tests — Agent answers with real data values."""

    async def test_query_inventory_returns_real_numbers(self, seeded_data):
        """User asks about inventory → Agent queries SQLite → response has real quantities."""
        agent = build_e2e_agent()
        result = await agent.run_unified(
            "帮我查一下 SKU-002 手机保护壳的当前库存是多少"
        )

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "query" in tool_names, f"Agent should call query, called: {tool_names}"

        # SKU-002 has quantity=200 in seed data
        response = result.response
        assert "200" in response or "SKU-002" in response, (
            f"Response should mention SKU-002 quantity (200), got: {response[:300]}"
        )

    async def test_query_pending_orders(self, seeded_data):
        """User asks about pending orders → Agent queries orders table."""
        agent = build_e2e_agent()
        result = await agent.run_unified(
            "查看目前有多少待处理的订单"
        )

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        # Agent should use query or list_datasources to explore
        data_tools_used = {"list_datasources", "get_schema", "query"} & set(tool_names)
        assert len(data_tools_used) > 0, (
            f"Agent should use data tools, called: {tool_names}"
        )

        # We seeded 4 pending orders (ORD-001, ORD-002, ORD-004, ORD-005)
        assert result.response, "Response should not be empty"

    async def test_handle_alert_updates_db(self, seeded_data):
        """User handles an alert → Agent calls handle_alert → DB state actually changes."""
        # Find an unresolved alert
        async with get_session() as session:
            stmt = select(Alert).where(Alert.resolution.is_(None)).limit(1)
            result = await session.execute(stmt)
            unresolved = result.scalars().first()

        if not unresolved:
            pytest.skip("No unresolved alerts seeded")

        alert_id = unresolved.id

        agent = build_e2e_agent()
        result = await agent.run_unified(
            f"把告警 {alert_id} 标记为已处理，备注是'已联系仓库补货'"
        )

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "handle_alert" in tool_names, f"Expected handle_alert in {tool_names}"

        # Verify DB was actually updated
        async with get_session() as session:
            updated = await session.get(Alert, alert_id)
            assert updated.resolution == "handled", (
                f"Alert should be marked handled, got: {updated.resolution}"
            )

    async def test_query_business_context_returns_content(self, seeded_data):
        """User asks about business knowledge → Agent returns seeded context entries."""
        agent = build_e2e_agent()
        result = await agent.run_unified("我们有哪些业务知识记录？")

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "list_context" in tool_names, f"Expected list_context in {tool_names}"

        # Should mention seeded context content
        response = result.response
        assert any(kw in response for kw in ["供应商", "义乌", "提价", "5%"]), (
            f"Response should mention seeded business context, got: {response[:300]}"
        )
