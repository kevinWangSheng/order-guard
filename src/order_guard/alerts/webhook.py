"""Webhook alert channel — generic HTTP POST JSON."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult


def _format_feishu_payload(alert: AlertMessage) -> dict[str, Any]:
    """Format alert as Feishu webhook message (msg_type: text)."""
    severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(alert.severity, "⚪")

    lines = [
        f"{severity_emoji} [{alert.severity.upper()}] {alert.title}",
        f"规则: {alert.rule_name}" if alert.rule_name else "",
        f"数据源: {alert.source}" if alert.source else "",
        "",
        f"摘要: {alert.summary}",
    ]
    if alert.suggestion:
        lines.append(f"建议: {alert.suggestion}")

    # Add detail items
    for item in alert.details:
        if item.get("sku"):
            lines.append(f"  - SKU: {item['sku']} | {item.get('reason', '')}")

    lines.append(f"\n时间: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    text = "\n".join(line for line in lines if line is not None)
    return {
        "msg_type": "text",
        "content": {"text": text},
    }


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


class WebhookChannel(BaseAlertChannel):
    """Send alerts via HTTP POST JSON to a webhook URL."""

    type = "webhook"

    def __init__(self, name: str, url: str, max_retries: int = 3):
        self.name = name
        self._url = url
        self._max_retries = max_retries
        # Auto-detect Feishu webhook
        self._is_feishu = "feishu.cn" in url or "lark.cn" in url

    async def send(self, alert: AlertMessage) -> SendResult:
        if self._is_feishu:
            payload = _format_feishu_payload(alert)
        else:
            payload = _format_generic_payload(alert)

        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self._url, json=payload)

                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        logger.warning("Webhook failed (attempt {}): {} -> {}", attempt, self.name, last_error)
                    else:
                        # Check response body for business error codes (Feishu returns 200 with error in body)
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

            # Exponential backoff: 1s, 2s, 4s
            if attempt < self._max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        logger.error("Webhook exhausted retries: {} -> {}", self.name, last_error)
        return SendResult(
            success=False,
            channel_name=self.name,
            error=last_error,
            attempts=self._max_retries,
        )
