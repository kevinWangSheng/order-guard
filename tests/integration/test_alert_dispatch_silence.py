"""L2 — Alert dispatch + silence/dedup integration tests.

Tests the AlertDispatcher with real DB operations: saving alerts, checking
silence windows, dedup, and channel send verification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult
from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.models import Alert
from order_guard.storage.database import get_session
from tests.integration.conftest import seed_alerts, seed_rules

pytestmark = pytest.mark.asyncio


class FakeChannel(BaseAlertChannel):
    """In-memory channel that records calls."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.type = "fake"
        self.sent: list[AlertMessage] = []
        self.batch_calls: list[list[AlertMessage]] = []

    async def send(self, alert: AlertMessage) -> SendResult:
        self.sent.append(alert)
        return SendResult(success=True, channel_name=self.name, attempts=1)

    async def send_batch(self, alerts: list[AlertMessage], **kwargs) -> SendResult:
        self.batch_calls.append(alerts)
        self.sent.extend(alerts)
        return SendResult(success=True, channel_name=self.name, attempts=1)


class TestAlertDispatch:
    """Test alert dispatch: save to DB + channel send."""

    async def test_dispatch_saves_alerts_to_db(self):
        """Dispatched alerts should be saved to the database."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(channel)

        output = AnalyzerOutput(
            alerts=[
                AlertItem(sku="SKU-1", severity="critical", title="缺货", reason="库存0", suggestion="补货"),
            ],
            has_alerts=True,
        )
        await dispatcher.dispatch(output, rule_name="test-rule")

        # Verify DB
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(Alert))
            alerts = result.scalars().all()
            assert len(alerts) == 1
            assert alerts[0].severity == "critical"
            assert alerts[0].title == "缺货"

    async def test_dispatch_sends_to_channel(self):
        """Dispatched alerts should be sent to registered channels."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(channel)

        output = AnalyzerOutput(
            alerts=[
                AlertItem(sku="SKU-1", severity="warning", title="告警1", reason="r", suggestion="s"),
                AlertItem(sku="SKU-2", severity="critical", title="告警2", reason="r", suggestion="s"),
            ],
            has_alerts=True,
        )
        results = await dispatcher.dispatch(output, rule_name="test-rule")

        assert len(results) == 1
        assert results[0].success is True
        assert len(channel.batch_calls) == 1
        assert len(channel.batch_calls[0]) == 2

    async def test_dry_run_does_not_send(self):
        """dry_run should log but not call channel.send."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(channel)

        output = AnalyzerOutput(
            alerts=[AlertItem(sku="SKU-1", severity="info", title="测试", reason="r", suggestion="s")],
            has_alerts=True,
        )
        results = await dispatcher.dispatch(output, rule_name="test-rule", dry_run=True)

        assert results[0].channel_name == "dry-run"
        assert len(channel.sent) == 0


class TestAlertSilence:
    """Test silence window / dedup logic."""

    async def test_same_alert_silenced_within_window(self):
        """Same rule+severity+title within silence window should be silenced."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=30)
        dispatcher.register_channel(channel)

        alert_item = AlertItem(
            sku="SKU-1", severity="critical", title="缺货", reason="库存0", suggestion="补货"
        )

        # First dispatch — should send
        output1 = AnalyzerOutput(alerts=[alert_item], has_alerts=True)
        results1 = await dispatcher.dispatch(output1, rule_name="rule-1")
        assert len(channel.sent) == 1

        # Second dispatch — same alert should be silenced
        output2 = AnalyzerOutput(alerts=[alert_item], has_alerts=True)
        results2 = await dispatcher.dispatch(output2, rule_name="rule-1")

        # Channel should still have only 1 sent (second was silenced)
        assert len(channel.batch_calls) == 1  # Only first batch was sent

    async def test_different_severity_not_silenced(self):
        """Different severity should NOT be silenced."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=30)
        dispatcher.register_channel(channel)

        alert1 = AlertItem(sku="SKU-1", severity="critical", title="缺货", reason="r", suggestion="s")
        alert2 = AlertItem(sku="SKU-1", severity="warning", title="缺货", reason="r", suggestion="s")

        await dispatcher.dispatch(AnalyzerOutput(alerts=[alert1], has_alerts=True), rule_name="rule-1")
        await dispatcher.dispatch(AnalyzerOutput(alerts=[alert2], has_alerts=True), rule_name="rule-1")

        # Both should be sent (different severity)
        assert len(channel.batch_calls) == 2

    async def test_silence_disabled_when_zero(self):
        """silence_minutes=0 should disable silencing entirely."""
        channel = FakeChannel()
        dispatcher = AlertDispatcher(silence_minutes=0)
        dispatcher.register_channel(channel)

        alert = AlertItem(sku="SKU-1", severity="critical", title="缺货", reason="r", suggestion="s")

        await dispatcher.dispatch(AnalyzerOutput(alerts=[alert], has_alerts=True), rule_name="rule-1")
        await dispatcher.dispatch(AnalyzerOutput(alerts=[alert], has_alerts=True), rule_name="rule-1")

        # Both should be sent
        assert len(channel.batch_calls) == 2
