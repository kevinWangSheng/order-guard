"""L2 Integration test fixtures — MockLLMClient, real DB, seed helpers.

The MockLLMClient replaces the real LLM with a predetermined sequence of
tool_call steps.  Tools execute *for real* against an in-memory SQLite DB,
so we test the full Agent loop + tool execution + DB read/write without
any LLM API calls.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall
from order_guard.mcp.models import ToolInfo


# ---------------------------------------------------------------------------
# MockLLMClient
# ---------------------------------------------------------------------------

@dataclass
class MockLLMStep:
    """One step in a predetermined Agent conversation.

    Either provide tool_calls (Agent calls tools) or content (Agent terminates).
    """
    tool_calls: list[ToolCall] | None = None
    content: str = ""


class MockLLMClient:
    """Drop-in replacement for LLMClient that replays predetermined steps.

    Usage::

        steps = [
            MockLLMStep(tool_calls=[ToolCall(name="list_rules", arguments={})]),
            MockLLMStep(content="Done."),
        ]
        mock_llm = MockLLMClient(steps)
    """

    def __init__(self, steps: list[MockLLMStep]):
        self._steps = list(steps)
        self._idx = 0
        self._model = "mock-model"
        self.call_count = 0

    async def completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.call_count += 1
        if self._idx >= len(self._steps):
            # Fallback: return empty content to end loop
            return LLMResponse(
                content="",
                token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        step = self._steps[self._idx]
        self._idx += 1

        tcs = []
        if step.tool_calls:
            for tc in step.tool_calls:
                # Assign an id if missing
                if not tc.id:
                    tc = ToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name=tc.name,
                        arguments=tc.arguments,
                    )
                tcs.append(tc)

        return LLMResponse(
            content=step.content,
            tool_calls=tcs,
            token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model=self._model,
        )


# ---------------------------------------------------------------------------
# In-memory DB engine + session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def integration_engine():
    """Shared in-memory SQLite engine for all L2 integration tests."""
    import asyncio

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        # share cache so all connections hit the same in-memory DB
        connect_args={"check_same_thread": False},
    )

    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_create_tables())
    yield engine
    loop.run_until_complete(engine.dispose())
    loop.close()


@pytest.fixture(autouse=True)
def _patch_db_engine(integration_engine):
    """Patch storage.database to use the integration in-memory engine.

    Also clears all rows between tests to keep them isolated.
    """
    import asyncio
    from order_guard.storage import database as _db

    original_engine = _db._engine
    _db._engine = integration_engine

    yield

    # Cleanup: truncate all tables after each test
    async def _cleanup():
        async with AsyncSession(integration_engine, expire_on_commit=False) as session:
            for table in reversed(SQLModel.metadata.sorted_tables):
                await session.execute(table.delete())
            await session.commit()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async test — schedule and let it run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, _cleanup()).result()
        else:
            loop.run_until_complete(_cleanup())
    except RuntimeError:
        asyncio.run(_cleanup())

    _db._engine = original_engine


# ---------------------------------------------------------------------------
# Tool assembly helper (same as production)
# ---------------------------------------------------------------------------

def assemble_all_tools():
    """Assemble all tool definitions + executors exactly as production.

    Same as tests/scenarios/conftest.py — single source of truth.
    """
    from order_guard.tools import (
        data_tools,
        rule_tools,
        context_tools,
        alert_tools,
        health_tools,
        report_tools,
        usage_tools,
    )

    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )

    all_executors = {}
    all_executors.update(data_tools.TOOL_EXECUTORS)
    all_executors.update(rule_tools.TOOL_EXECUTORS)
    all_executors.update(context_tools.TOOL_EXECUTORS)
    all_executors.update(alert_tools.TOOL_EXECUTORS)
    all_executors.update(health_tools.TOOL_EXECUTORS)
    all_executors.update(report_tools.TOOL_EXECUTORS)
    all_executors.update(usage_tools.TOOL_EXECUTORS)

    return all_tools, all_executors


EXPECTED_TOOLS = {
    "list_datasources", "get_schema", "query",
    "list_rules", "create_rule", "update_rule", "delete_rule", "test_rule", "get_rule_stats",
    "list_context", "add_context", "delete_context",
    "list_alerts", "handle_alert", "get_alert_stats",
    "get_usage_stats",
    "check_health",
    "manage_report", "preview_report",
}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

async def seed_rules(count: int = 1, **overrides) -> list:
    """Insert AlertRule rows into the DB."""
    from order_guard.models import AlertRule
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    rules = []
    for i in range(count):
        defaults = {
            "id": f"test-rule-{uuid.uuid4().hex[:6]}",
            "name": f"Test Rule {i+1}",
            "mcp_server": "test-db",
            "prompt_template": "检查测试数据",
            "schedule": "0 9 * * *",
            "data_window": "7d",
            "source": "yaml",
            "enabled": True,
        }
        defaults.update(overrides)
        if "id" in overrides and count > 1:
            defaults["id"] = f"{overrides['id']}-{i}"
        async with get_session() as session:
            rule = AlertRule(**defaults)
            rule = await create(session, rule)
            rules.append(rule)
    return rules


async def seed_alerts(rule_id: str, count: int = 1, **overrides) -> list:
    """Insert Alert rows into the DB."""
    from order_guard.models import Alert
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    alerts = []
    for i in range(count):
        defaults = {
            "rule_id": rule_id,
            "severity": "warning",
            "title": f"Test Alert {i+1}",
            "summary": f"Test summary {i+1}",
            "status": "sent",
        }
        defaults.update(overrides)
        async with get_session() as session:
            alert = Alert(**defaults)
            alert = await create(session, alert)
            alerts.append(alert)
    return alerts


async def seed_task_runs(rule_id: str, count: int = 1, **overrides) -> list:
    """Insert TaskRun rows into the DB."""
    from order_guard.models import TaskRun
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    runs = []
    for i in range(count):
        defaults = {
            "job_name": f"rule-{rule_id}",
            "rule_id": rule_id,
            "status": "success",
        }
        defaults.update(overrides)
        async with get_session() as session:
            run = TaskRun(**defaults)
            run = await create(session, run)
            runs.append(run)
    return runs


async def seed_report_config(report_id: str = "test-report", **overrides):
    """Insert a ReportConfig row into the DB."""
    from order_guard.models import ReportConfig
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    defaults = {
        "id": report_id,
        "name": "Test Report",
        "schedule": "0 9 * * *",
        "mcp_server": "test-db",
        "focus": "测试报告内容",
        "channels": "default",
        "enabled": True,
    }
    defaults.update(overrides)
    async with get_session() as session:
        config = ReportConfig(**defaults)
        return await create(session, config)


async def seed_health_logs(datasource_id: str, count: int = 1, **overrides) -> list:
    """Insert DataSourceHealthLog rows into the DB."""
    from order_guard.models import DataSourceHealthLog
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    logs = []
    for i in range(count):
        defaults = {
            "datasource_id": datasource_id,
            "status": "healthy",
            "latency_ms": 50,
            "tool_count": 3,
        }
        defaults.update(overrides)
        async with get_session() as session:
            log = DataSourceHealthLog(**defaults)
            log = await create(session, log)
            logs.append(log)
    return logs


async def seed_business_context(content: str = "测试业务知识", **overrides):
    """Insert a BusinessContext row into the DB."""
    from order_guard.models import BusinessContext
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    defaults = {
        "content": content,
        "category": "other",
        "source": "chat",
    }
    defaults.update(overrides)
    async with get_session() as session:
        ctx = BusinessContext(**defaults)
        return await create(session, ctx)


async def seed_sessions(user_id: str = "test-user", count: int = 1) -> list:
    """Insert Session rows into the DB."""
    from order_guard.models import Session
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    sessions = []
    for i in range(count):
        async with get_session() as db:
            s = Session(user_id=user_id, chat_id=f"chat-{i}", is_active=(i == 0))
            s = await create(db, s)
            sessions.append(s)
    return sessions


async def seed_usage_logs(count: int = 1, **overrides) -> list:
    """Insert LLMUsageLog rows into the DB."""
    from order_guard.models import LLMUsageLog
    from order_guard.storage.database import get_session
    from order_guard.storage.crud import create

    logs = []
    for i in range(count):
        defaults = {
            "model": "mock-model",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cost_estimate_usd": 0.001,
            "trigger_type": "chat",
            "rule_id": "",
        }
        defaults.update(overrides)
        async with get_session() as session:
            log = LLMUsageLog(**defaults)
            log = await create(session, log)
            logs.append(log)
    return logs


# ---------------------------------------------------------------------------
# Agent builder helper
# ---------------------------------------------------------------------------

def build_mock_agent(
    steps: list[MockLLMStep],
    *,
    tools: list[ToolInfo] | None = None,
    executors: dict | None = None,
    data_access_layer: Any = None,
):
    """Build an Agent with MockLLMClient + given tools."""
    from order_guard.engine.agent import Agent, AgentConfig

    if tools is None or executors is None:
        _tools, _executors = assemble_all_tools()
        tools = tools or _tools
        executors = executors or _executors

    mock_llm = MockLLMClient(steps)
    agent = Agent(
        llm_client=mock_llm,
        config=AgentConfig(
            max_iterations=15,
            inject_schema=False,
            inject_business_context=False,
        ),
        tools=tools,
        tool_executors=executors,
        data_access_layer=data_access_layer,
    )
    return agent, mock_llm
