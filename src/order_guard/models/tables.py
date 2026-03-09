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
