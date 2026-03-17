"""SQLModel table definitions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Column, Field, SQLModel, JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

class Alert(SQLModel, table=True):
    __tablename__ = "alerts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    rule_id: str = Field(index=True)
    severity: str = Field(default="info")  # critical / warning / info
    title: str = ""
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="pending")  # pending / sent / failed
    created_at: datetime = Field(default_factory=_utcnow)
    sent_at: datetime | None = None
    resolution: str | None = Field(default=None)  # handled / ignored / false_positive
    resolved_by: str = Field(default="")
    resolved_at: datetime | None = Field(default=None)
    note: str = Field(default="")


# ---------------------------------------------------------------------------
# alert_rules
# ---------------------------------------------------------------------------

class AlertRule(SQLModel, table=True):
    __tablename__ = "alert_rules"

    id: str = Field(primary_key=True)
    name: str = ""
    description: str = ""
    prompt_template: str = ""
    mcp_server: str = Field(default="")             # MCP Server name
    data_window: str = Field(default="")            # Time window, e.g. "7d", "30d", "90d"
    schedule: str = Field(default="")               # Cron expression, e.g. "0 9 * * *"
    source: str = Field(default="yaml")             # "yaml" or "chat" (created via conversation)
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# task_runs
# ---------------------------------------------------------------------------

class TaskRun(SQLModel, table=True):
    __tablename__ = "task_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    job_name: str = ""
    rule_id: str = Field(default="", index=True)
    status: str = Field(default="running")  # running / success / failed
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# query_logs
# ---------------------------------------------------------------------------

class QueryLog(SQLModel, table=True):
    __tablename__ = "query_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    rule_id: str = Field(default="", index=True)
    mcp_server: str = ""
    sql: str = ""
    status: str = Field(default="success")  # success / error / timeout / rejected
    rows_returned: int = 0
    duration_ms: int = 0
    error: str | None = None
    agent_iteration: int = 0
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    chat_id: str = Field(default="")
    title: str = Field(default="新会话")
    is_active: bool = Field(default=True)
    pending_action: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    pending_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SessionMessage(SQLModel, table=True):
    __tablename__ = "session_messages"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(index=True)
    role: str = ""  # "user" | "assistant"
    content: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# business_context
# ---------------------------------------------------------------------------

CONTEXT_CATEGORIES = ["promotion", "strategy", "supplier", "product", "logistics", "other"]

class BusinessContext(SQLModel, table=True):
    __tablename__ = "business_context"

    id: str = Field(default_factory=_uuid, primary_key=True)
    content: str = ""         # The business knowledge text
    category: str = Field(default="other")  # promotion/strategy/supplier/product/logistics/other
    expires_at: datetime | None = None      # None = never expires
    source: str = "config"    # "config" (from yaml) or "chat" (from conversation)
    created_by: str = ""      # user_id who added it (empty for config)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# llm_usage_logs
# ---------------------------------------------------------------------------

class LLMUsageLog(SQLModel, table=True):
    __tablename__ = "llm_usage_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate_usd: float = 0.0
    trigger_type: str = ""  # "chat" / "rule" / "report"
    rule_id: str = ""
    user_id: str = ""
    session_id: str = ""
    duration_ms: int = 0
    tool_calls_count: int = 0
    iterations: int = 0
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

class ReportConfig(SQLModel, table=True):
    __tablename__ = "report_configs"

    id: str = Field(primary_key=True)
    name: str = ""
    schedule: str = ""                  # cron expression
    mcp_server: str = ""
    focus: str = ""                     # report content prompt
    channels: str = Field(default="default")  # comma-separated channel names
    sections: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    kpis: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    template_style: str = "standard"    # "standard" / "brief" / "detailed"
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ReportHistory(SQLModel, table=True):
    __tablename__ = "report_history"

    id: str = Field(default_factory=_uuid, primary_key=True)
    report_id: str = Field(index=True)
    content: str = ""
    status: str = Field(default="success")  # success / failed
    token_usage: int = 0
    duration_ms: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# data_sources
# ---------------------------------------------------------------------------

class DataSource(SQLModel, table=True):
    __tablename__ = "data_sources"

    id: str = Field(primary_key=True)
    name: str = ""
    type: str = ""  # mock / netsuite / rest
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# datasource_health_logs
# ---------------------------------------------------------------------------

class DataSourceHealthLog(SQLModel, table=True):
    __tablename__ = "datasource_health_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    datasource_id: str = Field(index=True)
    status: str = ""  # "healthy" / "unhealthy" / "timeout"
    latency_ms: int = 0
    error: str | None = None
    tool_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
