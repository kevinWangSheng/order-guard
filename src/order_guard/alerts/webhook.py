"""Webhook alert channel — generic HTTP POST JSON."""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult


class WebhookChannel(BaseAlertChannel):
    """Send alerts via HTTP POST JSON to a webhook URL."""

    type = "webhook"

    def __init__(self, name: str, url: str, max_retries: int = 3):
        self.name = name
        self._url = url
        self._max_retries = max_retries

    async def send(self, alert: AlertMessage) -> SendResult:
        payload = {
            "severity": alert.severity,
            "title": alert.title,
            "summary": alert.summary,
            "details": alert.details,
            "suggestion": alert.suggestion,
            "timestamp": alert.timestamp.isoformat(),
            "rule": alert.rule_name,
            "source": alert.source,
        }

        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self._url, json=payload)
                    if resp.status_code < 400:
                        logger.info("Webhook sent: {} -> {} ({})", self.name, self._url, resp.status_code)
                        return SendResult(
                            success=True,
                            channel_name=self.name,
                            status_code=resp.status_code,
                            attempts=attempt,
                        )
                    last_error = f"HTTP {resp.status_code}"
                    logger.warning("Webhook failed (attempt {}): {} -> {}", attempt, self.name, last_error)
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
