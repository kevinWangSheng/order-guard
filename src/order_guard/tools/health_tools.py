"""Data source health check tools — probe connectivity, track uptime, alert on failures."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, delete

from order_guard.config.settings import get_settings
from order_guard.mcp.models import ToolInfo
from order_guard.models import DataSourceHealthLog
from order_guard.storage.database import get_session


# ---------------------------------------------------------------------------
# Tool Schema definition
# ---------------------------------------------------------------------------

TOOL_CHECK_HEALTH = ToolInfo(
    name="check_health",
    description="检查数据源健康状态。不传参则检查所有数据源，传 datasource_id 则只检查指定数据源。返回状态、延迟、24h 可用率。",
    input_schema={
        "type": "object",
        "properties": {
            "datasource_id": {
                "type": "string",
                "description": "数据源 ID。不传则检查所有数据源。",
            },
        },
        "required": [],
    },
    server_name="health_tools",
)

TOOL_DEFINITIONS = [TOOL_CHECK_HEALTH]


# ---------------------------------------------------------------------------
# Module-level dependency references (set via configure())
# ---------------------------------------------------------------------------

_mcp_manager: Any | None = None


def configure(*, mcp_manager: Any = None) -> None:
    """Configure dependencies for health tools."""
    global _mcp_manager
    if mcp_manager is not None:
        _mcp_manager = mcp_manager


# ---------------------------------------------------------------------------
# Core health check logic
# ---------------------------------------------------------------------------

async def check_datasource_health(
    datasource_id: str,
    mcp_manager: Any,
    timeout_seconds: int = 10,
) -> dict:
    """Probe a single MCP data source and record result.

    Returns {datasource_id, status, latency_ms, error, tool_count}.
    """
    start = time.monotonic()
    status = "healthy"
    error_msg: str | None = None
    tool_count = 0

    try:
        conn = mcp_manager.get_connection(datasource_id)

        # Check if connected
        if not conn.is_connected():
            try:
                await asyncio.wait_for(conn.connect(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                latency_ms = int((time.monotonic() - start) * 1000)
                log = DataSourceHealthLog(
                    datasource_id=datasource_id,
                    status="timeout",
                    latency_ms=latency_ms,
                    error="Connection timeout",
                    tool_count=0,
                )
                await _save_health_log(log)
                return {
                    "datasource_id": datasource_id,
                    "status": "timeout",
                    "latency_ms": latency_ms,
                    "error": "Connection timeout",
                    "tool_count": 0,
                }

        # Probe: list tools as a health indicator
        try:
            tools = await asyncio.wait_for(
                conn.list_tools(),
                timeout=timeout_seconds,
            )
            tool_count = len(tools)
        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            log = DataSourceHealthLog(
                datasource_id=datasource_id,
                status="timeout",
                latency_ms=latency_ms,
                error="list_tools timeout",
                tool_count=0,
            )
            await _save_health_log(log)
            return {
                "datasource_id": datasource_id,
                "status": "timeout",
                "latency_ms": latency_ms,
                "error": "list_tools timeout",
                "tool_count": 0,
            }

    except Exception as e:
        status = "unhealthy"
        error_msg = str(e)

    latency_ms = int((time.monotonic() - start) * 1000)

    log = DataSourceHealthLog(
        datasource_id=datasource_id,
        status=status,
        latency_ms=latency_ms,
        error=error_msg,
        tool_count=tool_count,
    )
    await _save_health_log(log)

    return {
        "datasource_id": datasource_id,
        "status": status,
        "latency_ms": latency_ms,
        "error": error_msg,
        "tool_count": tool_count,
    }


async def get_consecutive_failures(datasource_id: str) -> int:
    """Count consecutive failures from the most recent log backward.

    Stops counting when a 'healthy' record is encountered.
    """
    try:
        async with get_session() as session:
            stmt = (
                select(DataSourceHealthLog)
                .where(DataSourceHealthLog.datasource_id == datasource_id)
                .order_by(DataSourceHealthLog.created_at.desc())
                .limit(100)
            )
            result = await session.execute(stmt)
            logs = result.scalars().all()

            count = 0
            for log in logs:
                if log.status == "healthy":
                    break
                count += 1
            return count
    except Exception as e:
        logger.error("get_consecutive_failures error: {}", e)
        return 0


async def get_uptime_24h(datasource_id: str) -> float:
    """Calculate 24h uptime percentage for a datasource."""
    try:
        async with get_session() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            stmt = (
                select(DataSourceHealthLog)
                .where(
                    DataSourceHealthLog.datasource_id == datasource_id,
                    DataSourceHealthLog.created_at >= cutoff,
                )
            )
            result = await session.execute(stmt)
            logs = result.scalars().all()

            if not logs:
                return 100.0

            healthy = sum(1 for l in logs if l.status == "healthy")
            return round(healthy / len(logs) * 100, 1)
    except Exception as e:
        logger.error("get_uptime_24h error: {}", e)
        return 0.0


async def get_last_check(datasource_id: str) -> str | None:
    """Get the timestamp of the last health check."""
    try:
        async with get_session() as session:
            stmt = (
                select(DataSourceHealthLog)
                .where(DataSourceHealthLog.datasource_id == datasource_id)
                .order_by(DataSourceHealthLog.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            log = result.scalars().first()
            if log:
                return log.created_at.isoformat()
            return None
    except Exception as e:
        logger.error("get_last_check error: {}", e)
        return None


# ---------------------------------------------------------------------------
# Tool executor: check_health
# ---------------------------------------------------------------------------

async def check_health(**kwargs: Any) -> dict:
    """Check health of one or all data sources.

    Returns {data, hint} envelope format.
    """
    datasource_id = kwargs.get("datasource_id")

    if _mcp_manager is None:
        return {
            "error": "MCP Manager 未初始化。",
            "hint": "请确认系统已正确配置数据源。",
        }

    settings = get_settings()
    timeout = settings.health_check.timeout_seconds

    # Determine which datasources to check
    try:
        all_ids = list(_mcp_manager._connections.keys())
    except Exception:
        all_ids = []

    if not all_ids:
        return {
            "data": [],
            "hint": "暂无已配置的数据源。",
        }

    if datasource_id:
        if datasource_id not in all_ids:
            return {
                "error": f"数据源 '{datasource_id}' 不存在。",
                "hint": f"可用数据源：{', '.join(all_ids)}",
            }
        target_ids = [datasource_id]
    else:
        target_ids = all_ids

    # Check each datasource
    results = []
    for ds_id in target_ids:
        result = await check_datasource_health(ds_id, _mcp_manager, timeout)
        uptime = await get_uptime_24h(ds_id)
        last_check = await get_last_check(ds_id)
        results.append({
            "datasource_id": ds_id,
            "status": result["status"],
            "latency_ms": result["latency_ms"],
            "last_check": last_check,
            "uptime_24h": uptime,
            "error": result.get("error"),
        })

    # Build hint
    healthy_count = sum(1 for r in results if r["status"] == "healthy")
    total = len(results)
    if healthy_count == total:
        hint = f"所有 {total} 个数据源状态正常。"
    else:
        unhealthy = total - healthy_count
        hint = f"{total} 个数据源中 {unhealthy} 个异常，{healthy_count} 个正常。"

    return {"data": results, "hint": hint}


# ---------------------------------------------------------------------------
# Log cleanup
# ---------------------------------------------------------------------------

async def cleanup_old_logs(retention_hours: int = 72) -> int:
    """Delete health logs older than retention_hours. Returns count deleted."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        async with get_session() as session:
            stmt = delete(DataSourceHealthLog).where(
                DataSourceHealthLog.created_at < cutoff
            )
            result = await session.execute(stmt)
            deleted = result.rowcount
            if deleted:
                logger.info("Cleaned up {} old health logs", deleted)
            return deleted
    except Exception as e:
        logger.error("cleanup_old_logs error: {}", e)
        return 0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _save_health_log(log: DataSourceHealthLog) -> None:
    """Save a health log entry to the database."""
    try:
        from order_guard.storage.crud import create
        async with get_session() as session:
            await create(session, log)
    except Exception as e:
        logger.error("Failed to save health log: {}", e)


# ---------------------------------------------------------------------------
# Tool executors mapping
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: dict[str, Any] = {
    "check_health": check_health,
}
