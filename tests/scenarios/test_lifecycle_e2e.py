"""L3 E2E — Full lifecycle with real LLM.

Tests multi-step business workflows with data verification:
1. Query data → find anomaly → check alerts → handle → verify DB
2. Schema exploration → query with real data → accurate response

Requires LLM API Key in .env.
"""
from __future__ import annotations

import pytest

from order_guard.models import Alert
from order_guard.storage.database import get_session
from order_guard.storage.crud import list_all
from sqlalchemy import select
from tests.scenarios.conftest import build_e2e_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class TestLifecycleE2E:
    """E2E lifecycle tests — multi-step workflows with DB state verification."""

    async def test_data_exploration_to_query(self, seeded_data):
        """Agent explores schema then queries → response has accurate data.

        Flow: list_datasources → get_schema → query → real inventory numbers.
        """
        agent = build_e2e_agent()

        # Step 1: Explore what data sources are available
        result1 = await agent.run_unified("有哪些数据源可以查询？")
        tool_names1 = [tc["tool"] for tc in result1.tool_calls_log]
        assert "list_datasources" in tool_names1, (
            f"Expected list_datasources, called: {tool_names1}"
        )
        assert "test-db" in result1.response, (
            f"Response should mention test-db datasource, got: {result1.response[:200]}"
        )

        # Step 2: Query specific data
        result2 = await agent.run_unified(
            "查一下 test-db 数据源中 inventory 表里库存最少的3个商品"
        )
        tool_names2 = [tc["tool"] for tc in result2.tool_calls_log]
        assert "query" in tool_names2 or "get_schema" in tool_names2, (
            f"Expected query or get_schema, called: {tool_names2}"
        )

        # Should mention the lowest-stock items (SKU-001: 0, SKU-005: 3, SKU-003: 5)
        response = result2.response
        low_items = ["SKU-001", "SKU-003", "SKU-005"]
        found = [s for s in low_items if s in response]
        assert len(found) >= 1, (
            f"Response should mention low-stock items, got: {response[:300]}"
        )

    async def test_alert_lifecycle_with_db_verification(self, seeded_data):
        """Full alert lifecycle: list → handle → verify DB state change.

        Flow: list_alerts → see unresolved → handle_alert → DB updated.
        """
        agent = build_e2e_agent()

        # Step 1: List alerts
        result1 = await agent.run_unified("查看未处理的告警")
        tool_names1 = [tc["tool"] for tc in result1.tool_calls_log]
        assert "list_alerts" in tool_names1

        # Step 2: Find an unresolved alert and handle it
        async with get_session() as session:
            stmt = select(Alert).where(Alert.resolution.is_(None)).limit(1)
            res = await session.execute(stmt)
            unresolved = res.scalars().first()

        if not unresolved:
            pytest.skip("No unresolved alerts")

        alert_id = unresolved.id
        result2 = await agent.run_unified(
            f"处理告警 {alert_id}，标记为已处理"
        )

        tool_names2 = [tc["tool"] for tc in result2.tool_calls_log]
        assert "handle_alert" in tool_names2, f"Expected handle_alert, called: {tool_names2}"

        # Step 3: Verify DB state
        async with get_session() as session:
            updated = await session.get(Alert, alert_id)
            assert updated.resolution == "handled", (
                f"Alert should be handled, got: {updated.resolution}"
            )

        # Step 4: Get stats — handled count should reflect the change
        result3 = await agent.run_unified("查看告警统计")
        tool_names3 = [tc["tool"] for tc in result3.tool_calls_log]
        assert "get_alert_stats" in tool_names3 or "list_alerts" in tool_names3
        assert result3.response
