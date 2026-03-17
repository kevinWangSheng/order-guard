"""Tests for WeCom (企业微信) webhook formatting (T13)."""

from __future__ import annotations

from datetime import datetime, timezone

from order_guard.alerts.base import AlertMessage
from order_guard.alerts.webhook import (
    WebhookChannel,
    _build_wecom_markdown_single,
    _build_wecom_markdown_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alert(
    title: str = "库存不足",
    severity: str = "warning",
    summary: str = "SKU-001 库存仅剩 5 件",
    suggestion: str = "建议立即补货",
    sku: str = "SKU-001",
) -> AlertMessage:
    return AlertMessage(
        title=title,
        severity=severity,
        summary=summary,
        suggestion=suggestion,
        details=[{"sku": sku}] if sku else [],
        rule_name="rule-001",
        source="mock",
    )


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

class TestWeComDetection:
    def test_detects_wecom_url(self):
        ch = WebhookChannel(name="wc", url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc")
        assert ch._is_wecom is True
        assert ch._is_feishu is False

    def test_feishu_url_not_wecom(self):
        ch = WebhookChannel(name="fs", url="https://open.feishu.cn/open-apis/bot/v2/hook/abc")
        assert ch._is_wecom is False
        assert ch._is_feishu is True

    def test_generic_url_not_wecom(self):
        ch = WebhookChannel(name="gen", url="https://example.com/webhook")
        assert ch._is_wecom is False
        assert ch._is_feishu is False


# ---------------------------------------------------------------------------
# Single alert format
# ---------------------------------------------------------------------------

class TestWeComSingleAlert:
    def test_message_type_is_markdown(self):
        alert = _make_alert()
        payload = _build_wecom_markdown_single(alert)
        assert payload["msgtype"] == "markdown"
        assert "markdown" in payload
        assert "content" in payload["markdown"]

    def test_contains_title(self):
        alert = _make_alert(title="库存不足")
        payload = _build_wecom_markdown_single(alert)
        content = payload["markdown"]["content"]
        assert "库存不足" in content

    def test_contains_severity_emoji(self):
        alert = _make_alert(severity="critical")
        payload = _build_wecom_markdown_single(alert)
        content = payload["markdown"]["content"]
        assert "🔴" in content

    def test_contains_suggestion(self):
        alert = _make_alert(suggestion="立即补货")
        payload = _build_wecom_markdown_single(alert)
        content = payload["markdown"]["content"]
        assert "立即补货" in content

    def test_contains_summary(self):
        alert = _make_alert(summary="库存仅剩 5 件")
        payload = _build_wecom_markdown_single(alert)
        content = payload["markdown"]["content"]
        assert "库存仅剩 5 件" in content


# ---------------------------------------------------------------------------
# Batch alert format
# ---------------------------------------------------------------------------

class TestWeComBatchAlerts:
    def test_batch_contains_all_alerts(self):
        alerts = [
            _make_alert(title="Low stock A", severity="critical", sku="SKU-A"),
            _make_alert(title="Low stock B", severity="warning", sku="SKU-B"),
            _make_alert(title="Info item", severity="info", sku="SKU-C"),
        ]
        payload = _build_wecom_markdown_batch(alerts, "rule-001", "mock")
        content = payload["markdown"]["content"]
        assert "Low stock A" in content
        assert "Low stock B" in content
        assert "Info item" in content

    def test_batch_has_severity_sections(self):
        alerts = [
            _make_alert(severity="critical"),
            _make_alert(severity="warning"),
        ]
        payload = _build_wecom_markdown_batch(alerts)
        content = payload["markdown"]["content"]
        assert "🔴" in content
        assert "🟡" in content

    def test_batch_has_header_counts(self):
        alerts = [
            _make_alert(severity="critical"),
            _make_alert(severity="critical"),
            _make_alert(severity="warning"),
        ]
        payload = _build_wecom_markdown_batch(alerts)
        content = payload["markdown"]["content"]
        assert "2个紧急" in content
        assert "1个关注" in content

    def test_batch_has_footer(self):
        payload = _build_wecom_markdown_batch(
            [_make_alert()],
            rule_name="rule-001",
            source="mock",
        )
        content = payload["markdown"]["content"]
        assert "规则: rule-001" in content
        assert "数据源: mock" in content

    def test_batch_has_sku_in_title(self):
        alert = _make_alert(title="库存不足", sku="SKU-001")
        payload = _build_wecom_markdown_batch([alert])
        content = payload["markdown"]["content"]
        assert "SKU-001" in content

    def test_empty_batch(self):
        """Empty batch should still produce valid payload."""
        payload = _build_wecom_markdown_batch([])
        assert payload["msgtype"] == "markdown"
