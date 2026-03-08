"""Base alert channel and message types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AlertMessage(BaseModel):
    """Structured alert message for channel delivery."""
    severity: str = "info"  # critical / warning / info
    title: str = ""
    summary: str = ""
    details: list[dict[str, Any]] = Field(default_factory=list)
    suggestion: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rule_name: str = ""
    source: str = ""


class SendResult(BaseModel):
    """Result of sending an alert through a channel."""
    success: bool = False
    channel_name: str = ""
    status_code: int | None = None
    error: str | None = None
    attempts: int = 0


class BaseAlertChannel(ABC):
    """Abstract base for alert delivery channels."""

    name: str
    type: str

    @abstractmethod
    async def send(self, alert: AlertMessage) -> SendResult:
        """Send an alert message. Returns result with success/failure info."""
        ...
