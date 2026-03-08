"""Alert dispatcher — convert AI output to alerts and deliver to channels."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult
from order_guard.alerts.webhook import WebhookChannel
from order_guard.config.settings import AlertChannelConfig
from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.models import Alert
from order_guard.storage.database import get_session
from order_guard.storage.crud import create, update, get_by_id


class AlertDispatcher:
    """Dispatch alerts to multiple channels and record results in DB."""

    def __init__(self):
        self._channels: list[BaseAlertChannel] = []

    def register_from_config(self, channels_config: list[AlertChannelConfig]) -> None:
        """Register channels from configuration."""
        for cfg in channels_config:
            if not cfg.enabled:
                continue
            if cfg.type == "webhook":
                self._channels.append(WebhookChannel(name=cfg.name, url=cfg.url))
            else:
                logger.warning("Unknown alert channel type: {}", cfg.type)

    def register_channel(self, channel: BaseAlertChannel) -> None:
        self._channels.append(channel)

    async def dispatch(
        self,
        analyzer_output: AnalyzerOutput,
        rule_name: str = "",
        source: str = "",
        *,
        dry_run: bool = False,
    ) -> list[SendResult]:
        """Convert AI output to alerts, save to DB, and push to channels."""
        if not analyzer_output.has_alerts:
            logger.info("No alerts to dispatch")
            return []

        results: list[SendResult] = []

        for alert_item in analyzer_output.alerts:
            # Build message
            msg = AlertMessage(
                severity=alert_item.severity,
                title=alert_item.title,
                summary=alert_item.reason,
                details=[alert_item.model_dump()],
                suggestion=alert_item.suggestion,
                rule_name=rule_name,
                source=source,
            )

            # Save to DB
            db_alert = await self._save_alert(alert_item, rule_name)

            if dry_run:
                logger.info("[DRY RUN] Would send alert: {}", msg.title)
                results.append(SendResult(success=True, channel_name="dry-run", attempts=0))
                continue

            # Send to all channels
            for channel in self._channels:
                result = await channel.send(msg)
                results.append(result)

                # Update DB status
                if db_alert:
                    await self._update_alert_status(db_alert.id, result)

        return results

    async def _save_alert(self, item: AlertItem, rule_id: str) -> Alert | None:
        """Save alert record to database."""
        try:
            async with get_session() as session:
                alert = Alert(
                    rule_id=rule_id,
                    severity=item.severity,
                    title=item.title,
                    summary=item.reason,
                    details=item.model_dump(),
                    status="pending",
                )
                return await create(session, alert)
        except Exception as e:
            logger.error("Failed to save alert to DB: {}", e)
            return None

    async def _update_alert_status(self, alert_id: str, result: SendResult) -> None:
        """Update alert status after send attempt."""
        try:
            async with get_session() as session:
                alert = await get_by_id(session, Alert, alert_id)
                if alert:
                    new_status = "sent" if result.success else "failed"
                    sent_at = datetime.now(timezone.utc) if result.success else None
                    await update(session, alert, status=new_status, sent_at=sent_at)
        except Exception as e:
            logger.error("Failed to update alert status: {}", e)
