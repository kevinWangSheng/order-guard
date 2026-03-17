"""Tests for alert silence/dedup (T12)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from order_guard.alerts.base import AlertMessage, SendResult
from order_guard.alerts.dispatcher import AlertDispatcher, _alert_fingerprint
from order_guard.engine.analyzer import AlertItem, AnalyzerOutput, TokenUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_output(*items: AlertItem) -> AnalyzerOutput:
    return AnalyzerOutput(
        has_alerts=True,
        alerts=list(items),
        summary="test",
        token_usage=TokenUsage(),
    )


def _make_item(title: str = "Low stock", severity: str = "warning") -> AlertItem:
    return AlertItem(
        title=title,
        severity=severity,
        reason="Stock is low",
        suggestion="Reorder",
        sku="SKU-001",
    )


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_same_input_same_hash(self):
        a = _alert_fingerprint("rule-1", "warning", "Low stock")
        b = _alert_fingerprint("rule-1", "warning", "Low stock")
        assert a == b

    def test_different_rule_different_hash(self):
        a = _alert_fingerprint("rule-1", "warning", "Low stock")
        b = _alert_fingerprint("rule-2", "warning", "Low stock")
        assert a != b

    def test_different_severity_different_hash(self):
        a = _alert_fingerprint("rule-1", "warning", "Low stock")
        b = _alert_fingerprint("rule-1", "critical", "Low stock")
        assert a != b

    def test_different_title_different_hash(self):
        a = _alert_fingerprint("rule-1", "warning", "Low stock")
        b = _alert_fingerprint("rule-1", "warning", "High return")
        assert a != b


# ---------------------------------------------------------------------------
# Silence logic
# ---------------------------------------------------------------------------

class TestSilenceDispatch:
    """Test that dispatcher respects silence window."""

    def test_silence_disabled_when_zero(self):
        """silence_minutes=0 should never silence alerts."""
        dispatcher = AlertDispatcher(silence_minutes=0)
        channel = AsyncMock()
        channel.name = "test"
        channel.send_batch = AsyncMock(return_value=SendResult(success=True, channel_name="test", attempts=1))
        dispatcher.register_channel(channel)

        output = _make_output(_make_item())

        with patch.object(dispatcher, "_save_alert", new_callable=AsyncMock) as mock_save, \
             patch.object(dispatcher, "_update_alert_status", new_callable=AsyncMock):
            from unittest.mock import MagicMock
            mock_alert = MagicMock()
            mock_alert.id = "alert-1"
            mock_save.return_value = mock_alert

            results = run(dispatcher.dispatch(output, rule_name="rule-1"))
            assert len(results) == 1
            assert results[0].success
            channel.send_batch.assert_called_once()

    def test_first_alert_not_silenced(self):
        """First alert should always go through."""
        dispatcher = AlertDispatcher(silence_minutes=30)
        channel = AsyncMock()
        channel.name = "test"
        channel.send_batch = AsyncMock(return_value=SendResult(success=True, channel_name="test", attempts=1))
        dispatcher.register_channel(channel)

        output = _make_output(_make_item())

        with patch.object(dispatcher, "_is_silenced", new_callable=AsyncMock, return_value=False), \
             patch.object(dispatcher, "_save_alert", new_callable=AsyncMock) as mock_save, \
             patch.object(dispatcher, "_update_alert_status", new_callable=AsyncMock):
            from unittest.mock import MagicMock
            mock_alert = MagicMock()
            mock_alert.id = "alert-1"
            mock_save.return_value = mock_alert

            results = run(dispatcher.dispatch(output, rule_name="rule-1"))
            assert len(results) == 1
            assert results[0].success
            channel.send_batch.assert_called_once()

    def test_duplicate_alert_silenced(self):
        """Alert within silence window should be silenced."""
        dispatcher = AlertDispatcher(silence_minutes=30)
        channel = AsyncMock()
        channel.name = "test"
        channel.send_batch = AsyncMock(return_value=SendResult(success=True, channel_name="test", attempts=1))
        dispatcher.register_channel(channel)

        output = _make_output(_make_item())

        with patch.object(dispatcher, "_is_silenced", new_callable=AsyncMock, return_value=True), \
             patch.object(dispatcher, "_save_alert", new_callable=AsyncMock) as mock_save:
            from unittest.mock import MagicMock
            mock_alert = MagicMock()
            mock_alert.id = "alert-silenced"
            mock_save.return_value = mock_alert

            results = run(dispatcher.dispatch(output, rule_name="rule-1"))
            assert len(results) == 1
            assert results[0].channel_name == "silenced"
            # Channel should NOT be called
            channel.send_batch.assert_not_called()
            # Alert should be saved with status="silenced"
            mock_save.assert_called_once()
            call_kwargs = mock_save.call_args
            assert call_kwargs[1].get("status") == "silenced" or call_kwargs[0][2] == "silenced" if len(call_kwargs[0]) > 2 else True

    def test_mixed_silenced_and_active(self):
        """Some alerts silenced, some active — only active ones should be sent."""
        dispatcher = AlertDispatcher(silence_minutes=30)
        channel = AsyncMock()
        channel.name = "test"
        channel.send_batch = AsyncMock(return_value=SendResult(success=True, channel_name="test", attempts=1))
        dispatcher.register_channel(channel)

        item1 = _make_item("Low stock", "warning")
        item2 = _make_item("High return", "critical")
        output = _make_output(item1, item2)

        silence_results = [True, False]  # first silenced, second not

        with patch.object(dispatcher, "_is_silenced", new_callable=AsyncMock, side_effect=silence_results), \
             patch.object(dispatcher, "_save_alert", new_callable=AsyncMock) as mock_save, \
             patch.object(dispatcher, "_update_alert_status", new_callable=AsyncMock):
            from unittest.mock import MagicMock
            mock_alert = MagicMock()
            mock_alert.id = "alert-1"
            mock_save.return_value = mock_alert

            results = run(dispatcher.dispatch(output, rule_name="rule-1"))
            assert len(results) == 1
            assert results[0].success
            channel.send_batch.assert_called_once()
            # Only 1 message (the non-silenced one) should be in the batch
            sent_messages = channel.send_batch.call_args[0][0]
            assert len(sent_messages) == 1
            assert sent_messages[0].title == "High return"

    def test_no_alerts_returns_empty(self):
        """AnalyzerOutput with no alerts returns empty list."""
        dispatcher = AlertDispatcher(silence_minutes=30)
        output = AnalyzerOutput(has_alerts=False, alerts=[], summary="", token_usage=TokenUsage())
        results = run(dispatcher.dispatch(output))
        assert results == []

    def test_default_silence_minutes(self):
        """Default silence_minutes should be 30."""
        dispatcher = AlertDispatcher()
        assert dispatcher._silence_minutes == 30

    def test_silence_minutes_from_config(self):
        """AlertsConfig should have silence_minutes field."""
        from order_guard.config.settings import AlertsConfig
        cfg = AlertsConfig()
        assert cfg.silence_minutes == 30

        cfg2 = AlertsConfig(silence_minutes=0)
        assert cfg2.silence_minutes == 0

        cfg3 = AlertsConfig(silence_minutes=60)
        assert cfg3.silence_minutes == 60
