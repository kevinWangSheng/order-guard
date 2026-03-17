"""L3 E2E — Detection pipeline with real LLM.

Tests the complete detection chain:
seed data → Agent queries business data → finds anomalies → response contains real data.

The DAL is wired to an in-memory SQLite with business tables (inventory, orders).
Agent uses real LLM to decide which tools to call and interpret results.

Requires LLM API Key in .env.
"""
from __future__ import annotations

import pytest

from tests.scenarios.conftest import build_e2e_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class TestDetectionE2E:
    """E2E detection tests — Agent queries real business data via DAL."""

    async def test_stockout_detection_via_query(self, seeded_data):
        """Agent should query inventory, find SKU-001 with quantity=0, and report it.

        Full chain: LLM → list_datasources/get_schema/query → real SQLite data → response.
        """
        agent = build_e2e_agent()
        result = await agent.run_unified(
            "查询库存数据，找出库存量为0的商品，告诉我哪些SKU缺货了"
        )

        # Agent should have used data query tools
        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        data_tools_used = {"list_datasources", "get_schema", "query"} & set(tool_names)
        assert len(data_tools_used) > 0, (
            f"Agent should use data tools, but only called: {tool_names}"
        )

        # Response should mention SKU-001 (the stockout item)
        assert "SKU-001" in result.response, (
            f"Response should mention stockout SKU-001, got: {result.response[:200]}"
        )

    async def test_low_stock_detection(self, seeded_data):
        """Agent should find items below safety stock level."""
        agent = build_e2e_agent()
        result = await agent.run_unified(
            "检查哪些商品的库存低于安全库存水平，列出SKU和当前库存"
        )

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "query" in tool_names, f"Agent should call query tool, called: {tool_names}"

        # Should mention at least one of the low-stock items
        response = result.response
        low_stock_skus = ["SKU-001", "SKU-003", "SKU-005"]
        found = [sku for sku in low_stock_skus if sku in response]
        assert len(found) >= 1, (
            f"Response should mention low-stock SKUs {low_stock_skus}, got: {response[:300]}"
        )

    async def test_list_alerts_returns_seeded_data(self, seeded_data):
        """Agent should find and return seeded alerts with real content."""
        agent = build_e2e_agent()
        result = await agent.run_unified("查看最近的告警记录，有几条？严重程度如何？")

        tool_names = [tc["tool"] for tc in result.tool_calls_log]
        assert "list_alerts" in tool_names, f"Expected list_alerts in {tool_names}"

        # Response should reference the seeded alert data
        response = result.response
        # We seeded 3 alerts — at least mention some count or severity
        assert any(kw in response for kw in ["critical", "warning", "严重", "告警", "3", "缺货"]), (
            f"Response should reference seeded alerts, got: {response[:300]}"
        )
