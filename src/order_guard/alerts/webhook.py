"""Webhook alert channel — generic HTTP POST JSON."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult

# ---------------------------------------------------------------------------
# Feishu interactive card builder
# ---------------------------------------------------------------------------

_SEVERITY_CONFIG = {
    "critical": {"emoji": "🔴", "label": "紧急", "color": "red", "template": "red"},
    "warning": {"emoji": "🟡", "label": "关注", "color": "orange", "template": "orange"},
    "info": {"emoji": "🔵", "label": "信息", "color": "blue", "template": "blue"},
}


def _build_feishu_card_batch(alerts: list[AlertMessage], rule_name: str = "", source: str = "") -> dict[str, Any]:
    """Build a single Feishu interactive card aggregating multiple alerts."""
    # Count by severity
    counts: dict[str, int] = {}
    for a in alerts:
        counts[a.severity] = counts.get(a.severity, 0) + 1

    # Header line: "🔴 2 个紧急 | 🟡 3 个关注"
    header_parts = []
    for sev in ["critical", "warning", "info"]:
        if counts.get(sev, 0) > 0:
            cfg = _SEVERITY_CONFIG[sev]
            header_parts.append(f"{cfg['emoji']} {counts[sev]} 个{cfg['label']}")
    header_text = " | ".join(header_parts)

    # Determine header color from highest severity
    header_color = "blue"
    for sev in ["critical", "warning", "info"]:
        if counts.get(sev, 0) > 0:
            header_color = _SEVERITY_CONFIG[sev]["template"]
            break

    # Build elements
    elements: list[dict[str, Any]] = []

    # Group alerts by severity
    for sev in ["critical", "warning", "info"]:
        sev_alerts = [a for a in alerts if a.severity == sev]
        if not sev_alerts:
            continue

        cfg = _SEVERITY_CONFIG[sev]

        # Section divider with severity label
        elements.append({
            "tag": "markdown",
            "content": f"**{cfg['emoji']} {cfg['label']}**",
        })
        elements.append({"tag": "hr"})

        # Each alert item
        for a in sev_alerts:
            elements.append(_build_alert_element(a))

    # Footer: timestamp + rule info
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    footer_parts = [now]
    if rule_name:
        footer_parts.append(f"规则: {rule_name}")
    if source:
        footer_parts.append(f"数据源: {source}")

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": " | ".join(footer_parts)}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": header_text},
                "template": header_color,
            },
            "elements": elements,
        },
    }


def _build_alert_element(alert: AlertMessage) -> dict[str, Any]:
    """Build a single alert's card element with key metrics."""
    cfg = _SEVERITY_CONFIG.get(alert.severity, _SEVERITY_CONFIG["info"])

    # Extract structured data from details if available
    detail = alert.details[0] if alert.details else {}
    sku = detail.get("sku", "")

    # Build title line
    title_line = f"**{alert.title}**"
    if sku:
        title_line = f"**{sku}** | {alert.title}"

    # Build the content with key action highlighted
    lines = [title_line, ""]

    # Main reason
    if alert.summary:
        lines.append(f"📋 {alert.summary}")

    # Highlighted action
    if alert.suggestion:
        lines.append(f"⚡ **建议: {alert.suggestion}**")

    content = "\n".join(lines)

    return {"tag": "markdown", "content": content}


def _build_feishu_card_single(alert: AlertMessage) -> dict[str, Any]:
    """Build a Feishu card for a single alert."""
    return _build_feishu_card_batch([alert], alert.rule_name, alert.source)


def _format_generic_payload(alert: AlertMessage) -> dict[str, Any]:
    """Format alert as generic JSON payload."""
    return {
        "severity": alert.severity,
        "title": alert.title,
        "summary": alert.summary,
        "details": alert.details,
        "suggestion": alert.suggestion,
        "timestamp": alert.timestamp.isoformat(),
        "rule": alert.rule_name,
        "source": alert.source,
    }


def _format_generic_batch_payload(alerts: list[AlertMessage], rule_name: str, source: str) -> dict[str, Any]:
    """Format a batch of alerts as generic JSON."""
    return {
        "alert_count": len(alerts),
        "rule": rule_name,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alerts": [_format_generic_payload(a) for a in alerts],
    }


# ---------------------------------------------------------------------------
# WebhookChannel
# ---------------------------------------------------------------------------

class WebhookChannel(BaseAlertChannel):
    """Send alerts via HTTP POST JSON to a webhook URL."""

    type = "webhook"

    def __init__(self, name: str, url: str, max_retries: int = 3):
        self.name = name
        self._url = url
        self._max_retries = max_retries
        self._is_feishu = "feishu.cn" in url or "lark.cn" in url

    async def send(self, alert: AlertMessage) -> SendResult:
        """Send a single alert."""
        if self._is_feishu:
            payload = _build_feishu_card_single(alert)
        else:
            payload = _format_generic_payload(alert)
        return await self._post(payload)

    async def send_batch(self, alerts: list[AlertMessage], rule_name: str = "", source: str = "") -> SendResult:
        """Send aggregated alerts as one message."""
        if not alerts:
            return SendResult(success=True, channel_name=self.name, attempts=0)

        if self._is_feishu:
            payload = _build_feishu_card_batch(alerts, rule_name, source)
        else:
            payload = _format_generic_batch_payload(alerts, rule_name, source)
        return await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> SendResult:
        """POST payload with retry logic."""
        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self._url, json=payload)

                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        logger.warning("Webhook failed (attempt {}): {} -> {}", attempt, self.name, last_error)
                    else:
                        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        error_code = body.get("code", body.get("StatusCode", 0))

                        if error_code != 0:
                            last_error = f"API error: code={error_code}, msg={body.get('msg', body.get('StatusMessage', ''))}"
                            logger.warning("Webhook business error (attempt {}): {} -> {}", attempt, self.name, last_error)
                        else:
                            logger.info("Webhook sent: {} -> {} ({})", self.name, self._url, resp.status_code)
                            return SendResult(
                                success=True,
                                channel_name=self.name,
                                status_code=resp.status_code,
                                attempts=attempt,
                            )
            except httpx.HTTPError as e:
                last_error = str(e)
                logger.warning("Webhook error (attempt {}): {} -> {}", attempt, self.name, last_error)

            if attempt < self._max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        logger.error("Webhook exhausted retries: {} -> {}", self.name, last_error)
        return SendResult(
            success=False,
            channel_name=self.name,
            error=last_error,
            attempts=self._max_retries,
        )
