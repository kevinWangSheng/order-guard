"""L2 — Health check pipeline integration tests.

Tests health check recording, consecutive failure counting,
uptime calculation, and log cleanup with real DB operations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from order_guard.models import DataSourceHealthLog
from order_guard.storage.database import get_session
from order_guard.storage.crud import create
from order_guard.tools.health_tools import (
    get_consecutive_failures,
    get_uptime_24h,
    cleanup_old_logs,
)
from tests.integration.conftest import seed_health_logs

pytestmark = pytest.mark.asyncio


class TestHealthCheckPipeline:
    """Test health check recording and analysis."""

    async def test_healthy_check_recorded(self):
        """Healthy checks should be recorded in DB."""
        await seed_health_logs("ds-1", 1, status="healthy", latency_ms=42)

        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DataSourceHealthLog).where(DataSourceHealthLog.datasource_id == "ds-1")
            )
            logs = result.scalars().all()
            assert len(logs) == 1
            assert logs[0].status == "healthy"
            assert logs[0].latency_ms == 42

    async def test_unhealthy_check_recorded(self):
        """Unhealthy checks should record error."""
        await seed_health_logs("ds-2", 1, status="unhealthy", error="Connection refused")

        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DataSourceHealthLog).where(DataSourceHealthLog.datasource_id == "ds-2")
            )
            logs = result.scalars().all()
            assert len(logs) == 1
            assert logs[0].status == "unhealthy"
            assert logs[0].error == "Connection refused"

    async def test_consecutive_failures_counting(self):
        """get_consecutive_failures should count failures until a healthy record."""
        # Seed: healthy, unhealthy, unhealthy, unhealthy (latest first)
        ds_id = "ds-consec"
        async with get_session() as session:
            # Oldest: healthy
            log1 = DataSourceHealthLog(
                datasource_id=ds_id, status="healthy", latency_ms=50,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            )
            await create(session, log1)
        async with get_session() as session:
            # Then 3 failures
            for i in range(3):
                log = DataSourceHealthLog(
                    datasource_id=ds_id, status="unhealthy", latency_ms=0,
                    error=f"error-{i}",
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=5-i),
                )
                await create(session, log)

        failures = await get_consecutive_failures(ds_id)
        assert failures == 3

    async def test_uptime_24h_calculation(self):
        """get_uptime_24h should calculate percentage correctly."""
        ds_id = "ds-uptime"
        # Seed 8 healthy, 2 unhealthy = 80% uptime
        for i in range(8):
            await seed_health_logs(ds_id, 1, status="healthy")
        for i in range(2):
            await seed_health_logs(ds_id, 1, status="unhealthy")

        uptime = await get_uptime_24h(ds_id)
        assert uptime == 80.0

    async def test_cleanup_old_logs(self):
        """cleanup_old_logs should delete logs older than retention."""
        ds_id = "ds-cleanup"
        # Seed an old log
        async with get_session() as session:
            old_log = DataSourceHealthLog(
                datasource_id=ds_id, status="healthy", latency_ms=50,
                created_at=datetime.now(timezone.utc) - timedelta(hours=100),
            )
            await create(session, old_log)

        # Seed a recent log
        await seed_health_logs(ds_id, 1, status="healthy")

        deleted = await cleanup_old_logs(retention_hours=72)
        assert deleted == 1

        # Recent log should remain
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DataSourceHealthLog).where(DataSourceHealthLog.datasource_id == ds_id)
            )
            remaining = result.scalars().all()
            assert len(remaining) == 1
