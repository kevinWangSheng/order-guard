"""L3 E2E — Full pipeline: detection → alert dispatch → webhook push.

This is the TRUE end-to-end test that covers the complete chain:
  Agent (real LLM) queries business data → finds anomaly →
  AnalyzerOutput → AlertDispatcher → Alert saved to DB →
  WebhookChannel.send_batch() → HTTP POST intercepted →
  verify webhook payload content (Feishu card format).

Also tests: report generation with real data → content verification.

Requires LLM API Key in .env.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.alerts.webhook import WebhookChannel
from order_guard.engine.agent import Agent, AgentConfig
from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMClient
from order_guard.engine.rules import RuleManager
from order_guard.models import Alert, TaskRun
from order_guard.storage.database import get_session
from order_guard.storage.crud import list_all
from sqlalchemy import select
from tests.scenarios.conftest import assemble_all_tools, build_e2e_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Helper: mock httpx only for webhook dispatch (not for LLM calls)
# ---------------------------------------------------------------------------

def _make_mock_http():
    """Create a mock httpx context that records webhook calls."""
    recorded = {"calls": []}

    class FakeResponse:
        status_code = 200
        text = '{"code": 0}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"code": 0}

        def raise_for_status(self):
            pass

    async def fake_post(url, **kwargs):
        recorded["calls"].append({"url": url, "kwargs": kwargs})
        return FakeResponse()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return recorded, mock_client


class TestFullDetectionPipeline:
    """Complete detection pipeline: Agent → data query → anomaly → alert → webhook."""

    async def test_detection_to_alert_to_webhook(self, seeded_data):
        """Full chain: Agent detects anomaly → AlertDispatcher → webhook push.

        Phase 1 (real HTTP): Agent.run() with real LLM, queries business data
        Phase 2 (mock HTTP): AlertDispatcher → WebhookChannel → verify payload

        This split is necessary because mock_http patches httpx globally,
        which would break litellm's internal HTTP client.
        """
        # --- Phase 1: Agent detection (real LLM, real HTTP) ---
        all_tools, all_executors = assemble_all_tools()
        agent = Agent(
            llm_client=LLMClient(),
            config=AgentConfig(
                inject_schema=False,
                inject_business_context=True,
            ),
            tools=all_tools,
            tool_executors=all_executors,
        )

        result = await agent.run(
            "检查库存表中库存量低于安全库存的 SKU，列出缺货风险。",
            trigger_type="rule",
        )

        assert result is not None
        assert hasattr(result, "has_alerts")
        assert hasattr(result, "alerts")

        # --- Phase 2: Alert dispatch + webhook (mock HTTP) ---
        recorded, mock_client = _make_mock_http()

        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(
            WebhookChannel(name="test-feishu", url="https://open.feishu.cn/test-hook")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            send_results = await dispatcher.dispatch(
                result,
                rule_name="缺货检测",
                source="test-db",
            )

        if result.has_alerts and len(result.alerts) > 0:
            # --- Verify Alert records in DB ---
            async with get_session() as session:
                stmt = select(Alert).where(Alert.rule_id == "缺货检测")
                db_result = await session.execute(stmt)
                new_alerts = db_result.scalars().all()

            assert len(new_alerts) > 0, (
                "Alerts should be saved to DB after dispatch"
            )

            # --- Verify webhook was called ---
            assert len(recorded["calls"]) > 0, (
                "Webhook should have been called. "
                f"Alerts found: {[a.title for a in new_alerts]}"
            )

            # Verify Feishu interactive card format
            call = recorded["calls"][0]
            assert "feishu.cn" in call["url"]

            payload = call["kwargs"].get("json", {})
            assert payload.get("msg_type") == "interactive", (
                f"Should be Feishu card, got: {json.dumps(payload, ensure_ascii=False)[:200]}"
            )

            # Card content should reference the alert
            card_str = json.dumps(payload, ensure_ascii=False)
            assert any(kw in card_str for kw in [
                "缺货", "SKU", "库存", "紧急", "关注", "critical", "warning",
            ]), (
                f"Card should contain alert content, got: {card_str[:500]}"
            )

            # Send results should indicate success
            assert len(send_results) > 0
            assert send_results[0].success is True
        else:
            # LLM didn't find anomalies — verify no false positives
            assert len(recorded["calls"]) == 0

    async def test_disabled_rule_skips_detection(self, seeded_data, mock_http):
        """Disabled rule should be skipped — no Agent call, no alerts, no webhook."""
        from order_guard.engine.analyzer import Analyzer
        from order_guard.scheduler.jobs import run_detection_job
        from order_guard.tools import data_tools

        dal = data_tools._data_access_layer
        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(
            WebhookChannel(name="test-hook", url="https://open.feishu.cn/test-hook")
        )

        task_run = await run_detection_job(
            rule_id="rule-disabled",
            job_name="e2e-disabled-check",
            rule_manager=RuleManager(),
            analyzer=Analyzer(),
            dispatcher=dispatcher,
            data_access_layer=dal,
        )

        assert task_run is not None
        assert len(mock_http["calls"]) == 0, "Disabled rule should not trigger webhook"

        async with get_session() as session:
            refreshed = await session.get(TaskRun, task_run.id)
            assert refreshed.status == "success"

    async def test_alert_silence_dedup_in_pipeline(self, seeded_data):
        """Dispatch same alert twice → second should be silenced (no double webhook)."""
        output = AnalyzerOutput(
            alerts=[
                AlertItem(
                    sku="SKU-001",
                    severity="critical",
                    title="SKU-001 缺货告警",
                    reason="库存为0",
                    suggestion="立即补货",
                )
            ],
            summary="测试告警",
            has_alerts=True,
        )

        recorded, mock_client = _make_mock_http()

        dispatcher = AlertDispatcher(silence_minutes=30)
        dispatcher.register_channel(
            WebhookChannel(name="test-hook", url="https://open.feishu.cn/test-hook")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            # First dispatch — should send
            results1 = await dispatcher.dispatch(
                output, rule_name="缺货检测", source="test-db"
            )
            assert len(recorded["calls"]) == 1, "First dispatch should send webhook"
            assert results1[0].success is True

            # Verify Alert saved to DB
            async with get_session() as session:
                stmt = select(Alert).where(Alert.title == "SKU-001 缺货告警")
                db_res = await session.execute(stmt)
                alerts_in_db = db_res.scalars().all()
            assert len(alerts_in_db) >= 1, "Alert should be saved to DB"

            # Second dispatch — should be silenced
            recorded["calls"].clear()
            results2 = await dispatcher.dispatch(
                output, rule_name="缺货检测", source="test-db"
            )
            assert len(recorded["calls"]) == 0, (
                "Second dispatch should be silenced (dedup within 30 min window)"
            )

            # Silenced alert should also be in DB with status='silenced'
            async with get_session() as session:
                stmt = select(Alert).where(
                    Alert.title == "SKU-001 缺货告警",
                    Alert.status == "silenced",
                )
                db_res = await session.execute(stmt)
                silenced = db_res.scalars().all()
            assert len(silenced) >= 1, "Silenced alert should be recorded in DB"


class TestFullReportPipeline:
    """Report generation → content with real data → verification."""

    async def test_report_generation_with_real_data(self, seeded_data):
        """generate_report() queries real business data and produces meaningful content."""
        from order_guard.engine.reporter import generate_report
        from order_guard.tools import data_tools

        dal = data_tools._data_access_layer
        report = seeded_data["reports"][0]
        assert report.name == "每日经营报告"

        result = await generate_report(report, data_access_layer=dal)

        assert result["status"] == "success", (
            f"Report should succeed, got: {result['status']}, error: {result.get('error')}"
        )
        assert result["content"], "Report content should not be empty"
        assert result["token_usage"] > 0, "Should have used tokens"

        content = result["content"]
        assert len(content) > 100, f"Report too short: {content[:200]}"

        # Content should reference real data
        has_data = any(kw in content for kw in [
            "SKU", "库存", "订单", "销售", "数据", "商品",
            "inventory", "orders",
        ])
        assert has_data, f"Report should reference business data, got: {content[:500]}"
