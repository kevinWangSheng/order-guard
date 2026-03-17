"""Tests for data source health check tools (P4)."""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from order_guard.tools.health_tools import (
    check_datasource_health,
    check_health,
    cleanup_old_logs,
    configure,
    get_consecutive_failures,
    get_uptime_24h,
    TOOL_DEFINITIONS,
    TOOL_EXECUTORS,
)
from order_guard.models import DataSourceHealthLog, AlertRule


# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.health_tools.get_session", _test_session):
        yield engine, _test_session

    await engine.dispose()


def _make_mock_mcp_manager(datasource_ids: list[str], healthy: dict[str, bool] | None = None):
    """Create a mock MCP manager with mock connections."""
    if healthy is None:
        healthy = {ds_id: True for ds_id in datasource_ids}

    connections = {}
    for ds_id in datasource_ids:
        conn = MagicMock()
        conn.name = ds_id
        is_healthy = healthy.get(ds_id, True)
        conn.is_connected.return_value = is_healthy

        if is_healthy:
            tool1 = MagicMock()
            tool1.name = "execute_sql"
            tool2 = MagicMock()
            tool2.name = "list_tables"
            conn.list_tools = AsyncMock(return_value=[tool1, tool2])
            conn.connect = AsyncMock()
        else:
            conn.connect = AsyncMock(side_effect=Exception("Connection refused"))
            conn.list_tools = AsyncMock(side_effect=Exception("Connection refused"))

        connections[ds_id] = conn

    manager = MagicMock()
    manager._connections = connections

    def get_conn(name):
        if name not in connections:
            raise KeyError(f"MCP server '{name}' not found")
        return connections[name]

    manager.get_connection = MagicMock(side_effect=get_conn)
    return manager


# ---------------------------------------------------------------------------
# Test: Tool definitions
# ---------------------------------------------------------------------------

def test_tool_definitions_exist():
    assert len(TOOL_DEFINITIONS) == 1
    assert TOOL_DEFINITIONS[0].name == "check_health"
    assert "check_health" in TOOL_EXECUTORS


# ---------------------------------------------------------------------------
# Test: Healthy data source returns healthy + latency_ms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_healthy_datasource(db_session):
    engine, test_session = db_session
    manager = _make_mock_mcp_manager(["erp-db"], {"erp-db": True})

    result = await check_datasource_health("erp-db", manager, timeout_seconds=5)

    assert result["datasource_id"] == "erp-db"
    assert result["status"] == "healthy"
    assert result["latency_ms"] >= 0
    assert result["error"] is None
    assert result["tool_count"] == 2

    # Verify log was saved
    async with test_session() as session:
        from sqlalchemy import select
        stmt = select(DataSourceHealthLog)
        logs = (await session.execute(stmt)).scalars().all()
        assert len(logs) == 1
        assert logs[0].status == "healthy"


# ---------------------------------------------------------------------------
# Test: Unhealthy data source returns unhealthy + error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_unhealthy_datasource(db_session):
    engine, test_session = db_session
    manager = _make_mock_mcp_manager(["bad-db"], {"bad-db": False})

    result = await check_datasource_health("bad-db", manager, timeout_seconds=5)

    assert result["datasource_id"] == "bad-db"
    assert result["status"] == "unhealthy"
    assert result["error"] is not None
    assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# Test: Timeout handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_timeout_datasource(db_session):
    engine, test_session = db_session

    manager = _make_mock_mcp_manager(["slow-db"], {"slow-db": True})
    # Override list_tools to simulate timeout
    conn = manager._connections["slow-db"]

    async def slow_list_tools():
        await asyncio.sleep(10)
        return []

    conn.list_tools = slow_list_tools

    result = await check_datasource_health("slow-db", manager, timeout_seconds=1)

    assert result["datasource_id"] == "slow-db"
    assert result["status"] == "timeout"
    assert "timeout" in result["error"].lower()


# ---------------------------------------------------------------------------
# Test: Consecutive failure count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consecutive_failures(db_session):
    engine, test_session = db_session

    # Insert logs: healthy, unhealthy, unhealthy, unhealthy (most recent first)
    now = datetime.now(timezone.utc)
    async with test_session() as session:
        for i, status in enumerate(["healthy", "unhealthy", "unhealthy", "unhealthy"]):
            log = DataSourceHealthLog(
                datasource_id="test-db",
                status=status,
                latency_ms=10,
                created_at=now + timedelta(minutes=i),
            )
            session.add(log)

    count = await get_consecutive_failures("test-db")
    assert count == 3


@pytest.mark.asyncio
async def test_consecutive_failures_all_healthy(db_session):
    engine, test_session = db_session

    now = datetime.now(timezone.utc)
    async with test_session() as session:
        for i in range(3):
            log = DataSourceHealthLog(
                datasource_id="ok-db",
                status="healthy",
                latency_ms=5,
                created_at=now + timedelta(minutes=i),
            )
            session.add(log)

    count = await get_consecutive_failures("ok-db")
    assert count == 0


# ---------------------------------------------------------------------------
# Test: check_health single datasource
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_health_single(db_session):
    engine, test_session = db_session
    manager = _make_mock_mcp_manager(["erp-db", "analytics-db"])

    mock_settings = MagicMock()
    mock_settings.health_check.timeout_seconds = 5

    with patch("order_guard.tools.health_tools.get_settings", return_value=mock_settings):
        configure(mcp_manager=manager)
        result = await check_health(datasource_id="erp-db")

    assert "data" in result
    assert len(result["data"]) == 1
    assert result["data"][0]["datasource_id"] == "erp-db"
    assert result["data"][0]["status"] == "healthy"


# ---------------------------------------------------------------------------
# Test: check_health all datasources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_health_all(db_session):
    engine, test_session = db_session
    manager = _make_mock_mcp_manager(["erp-db", "analytics-db"])

    mock_settings = MagicMock()
    mock_settings.health_check.timeout_seconds = 5

    with patch("order_guard.tools.health_tools.get_settings", return_value=mock_settings):
        configure(mcp_manager=manager)
        result = await check_health()

    assert "data" in result
    assert len(result["data"]) == 2
    assert result["hint"] == "所有 2 个数据源状态正常。"


# ---------------------------------------------------------------------------
# Test: 24h uptime calculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uptime_24h(db_session):
    engine, test_session = db_session

    now = datetime.now(timezone.utc)
    async with test_session() as session:
        # 8 healthy, 2 unhealthy = 80% uptime
        for i in range(8):
            session.add(DataSourceHealthLog(
                datasource_id="ds1",
                status="healthy",
                latency_ms=5,
                created_at=now - timedelta(hours=i),
            ))
        for i in range(2):
            session.add(DataSourceHealthLog(
                datasource_id="ds1",
                status="unhealthy",
                latency_ms=0,
                error="fail",
                created_at=now - timedelta(hours=8 + i),
            ))

    uptime = await get_uptime_24h("ds1")
    assert uptime == 80.0


@pytest.mark.asyncio
async def test_uptime_24h_no_logs(db_session):
    uptime = await get_uptime_24h("nonexistent-ds")
    assert uptime == 100.0


# ---------------------------------------------------------------------------
# Test: Log cleanup (older than retention_hours)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_old_logs(db_session):
    engine, test_session = db_session

    now = datetime.now(timezone.utc)
    async with test_session() as session:
        # Old log (should be deleted)
        session.add(DataSourceHealthLog(
            datasource_id="ds1",
            status="healthy",
            latency_ms=5,
            created_at=now - timedelta(hours=100),
        ))
        # Recent log (should be kept)
        session.add(DataSourceHealthLog(
            datasource_id="ds1",
            status="healthy",
            latency_ms=5,
            created_at=now - timedelta(hours=1),
        ))

    deleted = await cleanup_old_logs(retention_hours=72)
    assert deleted == 1

    # Verify only 1 log remains
    async with test_session() as session:
        from sqlalchemy import select, func
        stmt = select(func.count()).select_from(DataSourceHealthLog)
        count = (await session.execute(stmt)).scalar()
        assert count == 1


# ---------------------------------------------------------------------------
# Test: Alert trigger condition (consecutive failures >= threshold)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_job_triggers_alert(db_session):
    """Health check job should trigger alert when consecutive failures >= threshold."""
    engine, test_session = db_session

    # Pre-seed 2 failures (threshold is 3, this check will make it 3)
    now = datetime.now(timezone.utc)
    async with test_session() as session:
        for i in range(2):
            session.add(DataSourceHealthLog(
                datasource_id="bad-db",
                status="unhealthy",
                latency_ms=0,
                error="Connection refused",
                created_at=now - timedelta(minutes=10 - i),
            ))

    manager = _make_mock_mcp_manager(["bad-db"], {"bad-db": False})
    dispatcher = MagicMock()
    dispatcher._channels = [MagicMock()]
    dispatcher._channels[0].send = AsyncMock()

    with patch("order_guard.scheduler.jobs.get_session", test_session), \
         patch("order_guard.tools.health_tools.get_session", test_session):

        from order_guard.scheduler.jobs import _health_check_job

        mock_settings = MagicMock()
        mock_settings.health_check.enabled = True
        mock_settings.health_check.timeout_seconds = 5
        mock_settings.health_check.alert_threshold = 3
        mock_settings.health_check.retention_hours = 72

        with patch("order_guard.config.settings.get_settings", return_value=mock_settings), \
             patch("order_guard.tools.health_tools.get_settings", return_value=mock_settings):
            await _health_check_job(manager, dispatcher)

    # Alert should have been sent
    assert dispatcher._channels[0].send.called


# ---------------------------------------------------------------------------
# Test: Recovery notification trigger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_job_triggers_recovery(db_session):
    """Health check job should send recovery notice when previously failing DS comes back."""
    engine, test_session = db_session

    # Pre-seed 3 failures (>= threshold)
    now = datetime.now(timezone.utc)
    async with test_session() as session:
        for i in range(3):
            session.add(DataSourceHealthLog(
                datasource_id="recovered-db",
                status="unhealthy",
                latency_ms=0,
                error="Connection refused",
                created_at=now - timedelta(minutes=15 - i),
            ))

    # Now the DB is healthy again
    manager = _make_mock_mcp_manager(["recovered-db"], {"recovered-db": True})
    dispatcher = MagicMock()
    dispatcher._channels = [MagicMock()]
    dispatcher._channels[0].send = AsyncMock()

    with patch("order_guard.scheduler.jobs.get_session", test_session), \
         patch("order_guard.tools.health_tools.get_session", test_session):

        from order_guard.scheduler.jobs import _health_check_job

        mock_settings = MagicMock()
        mock_settings.health_check.enabled = True
        mock_settings.health_check.timeout_seconds = 5
        mock_settings.health_check.alert_threshold = 3
        mock_settings.health_check.retention_hours = 72

        with patch("order_guard.config.settings.get_settings", return_value=mock_settings), \
             patch("order_guard.tools.health_tools.get_settings", return_value=mock_settings):
            await _health_check_job(manager, dispatcher)

    # Recovery notification should have been sent
    assert dispatcher._channels[0].send.called
    # Check that the message contains recovery text
    call_args = dispatcher._channels[0].send.call_args
    alert_msg = call_args[0][0]
    assert "恢复" in alert_msg.summary


# ---------------------------------------------------------------------------
# Test: check_health with nonexistent datasource
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_health_nonexistent(db_session):
    manager = _make_mock_mcp_manager(["erp-db"])

    mock_settings = MagicMock()
    mock_settings.health_check.timeout_seconds = 5

    with patch("order_guard.tools.health_tools.get_settings", return_value=mock_settings):
        configure(mcp_manager=manager)
        result = await check_health(datasource_id="nonexistent")

    assert "error" in result
    assert "不存在" in result["error"]


# ---------------------------------------------------------------------------
# Test: check_health with no MCP manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_health_no_manager(db_session):
    configure(mcp_manager=None)
    # Reset the global to None
    import order_guard.tools.health_tools as ht
    ht._mcp_manager = None

    result = await check_health()
    assert "error" in result
