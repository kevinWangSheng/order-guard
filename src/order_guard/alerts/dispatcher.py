"""Alert dispatcher — convert AI output to alerts and deliver to channels."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, and_

from order_guard.alerts.base import AlertMessage, BaseAlertChannel, SendResult
from order_guard.alerts.webhook import WebhookChannel
from order_guard.config.settings import AlertChannelConfig
from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.models import Alert
from order_guard.storage.database import get_session
from order_guard.storage.crud import create, update, get_by_id


def _alert_fingerprint(rule_id: str, severity: str, title: str) -> str:
    """Generate a dedup fingerprint for an alert."""
    raw = f"{rule_id}|{severity}|{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AlertDispatcher:
    """Dispatch alerts to multiple channels and record results in DB."""

    def __init__(self, silence_minutes: int = 30):
        self._channels: list[BaseAlertChannel] = []
        self._silence_minutes = silence_minutes

    def register_from_config(self, channels_config: list[AlertChannelConfig]) -> None:
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
        """Convert AI output to alerts, save to DB, and push to channels (batched)."""
        if not analyzer_output.has_alerts:
            logger.info("No alerts to dispatch")
            return []

        # 1. Save all alerts to DB + build messages (apply silence/dedup)
        messages: list[AlertMessage] = []
        db_alert_ids: list[str] = []

        for alert_item in analyzer_output.alerts:
            # Check silence window
            silenced = False
            if self._silence_minutes > 0:
                silenced = await self._is_silenced(rule_name, alert_item)

            msg = AlertMessage(
                severity=alert_item.severity,
                title=alert_item.title,
                summary=alert_item.reason,
                details=[alert_item.model_dump()],
                suggestion=alert_item.suggestion,
                rule_name=rule_name,
                source=source,
            )

            if silenced:
                # Save to DB as silenced, but don't push
                db_alert = await self._save_alert(alert_item, rule_name, status="silenced")
                logger.info("Alert silenced (dedup): {} [{}]", alert_item.title, rule_name)
            else:
                messages.append(msg)
                db_alert = await self._save_alert(alert_item, rule_name)
                db_alert_ids.append(db_alert.id if db_alert else "")

        if not messages:
            logger.info("All alerts silenced for rule: {}", rule_name)
            return [SendResult(success=True, channel_name="silenced", attempts=0)]

        # 2. Dry run — log and return
        if dry_run:
            for msg in messages:
                logger.info("[DRY RUN] Would send alert: {}", msg.title)
            return [SendResult(success=True, channel_name="dry-run", attempts=0)]

        # 3. Send batched to each channel (one message per channel)
        results: list[SendResult] = []
        for channel in self._channels:
            result = await channel.send_batch(messages, rule_name=rule_name, source=source)
            results.append(result)

            # Update all DB alerts with send status
            for alert_id in db_alert_ids:
                if alert_id:
                    await self._update_alert_status(alert_id, result)

        return results

    async def _is_silenced(self, rule_id: str, item: AlertItem) -> bool:
        """Check if a similar alert was sent recently within the silence window."""
        fingerprint = _alert_fingerprint(rule_id, item.severity, item.title)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self._silence_minutes)
        try:
            async with get_session() as session:
                stmt = (
                    select(Alert)
                    .where(
                        and_(
                            Alert.rule_id == rule_id,
                            Alert.severity == item.severity,
                            Alert.title == item.title,
                            Alert.status.in_(["sent", "pending"]),
                            Alert.created_at >= cutoff,
                        )
                    )
                    .limit(1)
                )
                result = await session.execute(stmt)
                return result.scalars().first() is not None
        except Exception as e:
            logger.error("Failed to check silence window: {}", e)
            return False  # On error, don't silence

    async def _save_alert(self, item: AlertItem, rule_id: str, status: str = "pending") -> Alert | None:
        try:
            async with get_session() as session:
                alert = Alert(
                    rule_id=rule_id,
                    severity=item.severity,
                    title=item.title,
                    summary=item.reason,
                    details=item.model_dump(),
                    status=status,
                )
                return await create(session, alert)
        except Exception as e:
            logger.error("Failed to save alert to DB: {}", e)
            return None

    async def _update_alert_status(self, alert_id: str, result: SendResult) -> None:
        try:
            async with get_session() as session:
                alert = await get_by_id(session, Alert, alert_id)
                if alert:
                    new_status = "sent" if result.success else "failed"
                    sent_at = datetime.now(timezone.utc) if result.success else None
                    await update(session, alert, status=new_status, sent_at=sent_at)
        except Exception as e:
            logger.error("Failed to update alert status: {}", e)
