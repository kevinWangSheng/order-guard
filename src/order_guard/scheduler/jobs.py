"""Detection job orchestration — MCP Agent pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.engine.analyzer import Analyzer, AnalyzerOutput
from order_guard.engine.rules import RuleManager
from order_guard.models import AlertRule, DataSourceHealthLog, TaskRun
from order_guard.storage.crud import create, update
from order_guard.storage.database import get_session


async def run_detection_job(
    rule_id: str,
    job_name: str,
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
    *,
    dry_run: bool = False,
    mcp_manager: Any | None = None,
    data_access_layer: Any | None = None,
) -> TaskRun | None:
    """Execute the MCP Agent pipeline for a single rule.

    Pipeline: load rule → Agent (explore + query + analyze) → dispatch alerts
    """
    start_time = time.monotonic()
    task_run: TaskRun | None = None

    # 1. Create task_run record
    try:
        async with get_session() as session:
            task_run = await create(session, TaskRun(
                job_name=job_name,
                rule_id=rule_id,
                status="running",
            ))
    except Exception as e:
        logger.error("Failed to create task_run: {}", e)

    try:
        # 2. Load rule
        rule = await rule_manager.get_rule(rule_id)
        if rule is None:
            raise ValueError(f"Rule not found: {rule_id}")
        if not rule.enabled:
            logger.info("Rule {} is disabled, skipping", rule_id)
            await _complete_task_run(task_run, "success", time.monotonic() - start_time, result={"skipped": True})
            return task_run

        # 3. Run MCP Agent pipeline
        result = await _run_mcp_pipeline(
            rule, mcp_manager,
            data_access_layer=data_access_layer,
        )

        # 4. Dispatch alerts
        send_results = await dispatcher.dispatch(
            result,
            rule_name=rule.name,
            source=rule.mcp_server,
            dry_run=dry_run,
        )

        # 5. Record success
        duration = time.monotonic() - start_time
        result_summary = {
            "has_alerts": result.has_alerts,
            "alert_count": len(result.alerts),
            "send_results": len(send_results),
            "token_usage": result.token_usage.model_dump(),
            "summary": result.summary[:200],
        }
        await _complete_task_run(task_run, "success", duration, result=result_summary)

        logger.info(
            "Job {} (rule={}) completed: {} alerts, {}ms",
            job_name, rule_id, len(result.alerts), int(duration * 1000),
        )
        return task_run

    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error("Job {} (rule={}) failed: {}", job_name, rule_id, e)
        await _complete_task_run(task_run, "failed", duration, error=str(e))
        return task_run


async def _run_mcp_pipeline(
    rule: Any,
    mcp_manager: Any,
    *,
    data_access_layer: Any | None = None,
) -> AnalyzerOutput:
    """Execute the MCP Agent pipeline for a rule."""
    from order_guard.engine.agent import Agent
    from order_guard.engine.llm_client import LLMClient

    # Prefer DataAccessLayer (v4) over direct MCP connection.
    # DAL path: Agent discovers schema on-demand via get_schema tool.
    # No mcp_connection needed — avoids redundant schema pre-injection.
    if data_access_layer is not None:
        data_window = getattr(rule, "data_window", "") or ""

        agent = Agent(
            llm_client=LLMClient(),
            data_access_layer=data_access_layer,
            data_window=data_window,
            rule_id=rule.id,
        )
        return await agent.run(rule.prompt_template, trigger_type="rule")

    # Fallback: direct MCP connection (legacy path)
    if mcp_manager is None:
        raise ValueError("MCP manager not configured. Cannot run rule.")

    mcp_conn = mcp_manager.get_connection(rule.mcp_server)
    if not mcp_conn.is_connected():
        await mcp_conn.connect()

    # Get schema_filter config from MCP server settings
    schema_filter = None
    schema_sample_rows = 3
    try:
        from order_guard.config.settings import get_settings
        settings = get_settings()
        for srv in settings.mcp_servers:
            if srv.name == rule.mcp_server:
                schema_filter_cfg = srv.schema_filter
                schema_sample_rows = srv.schema_sample_rows
                from order_guard.mcp.models import SchemaFilterConfig
                schema_filter = SchemaFilterConfig(
                    blocked_tables=schema_filter_cfg.blocked_tables,
                    blocked_columns=schema_filter_cfg.blocked_columns,
                    cold_tables=schema_filter_cfg.cold_tables,
                )
                break
    except Exception as e:
        logger.debug("Could not load schema_filter config: {}", e)

    data_window = getattr(rule, "data_window", "") or ""

    agent = Agent(
        llm_client=LLMClient(),
        mcp_connection=mcp_conn,
        schema_filter=schema_filter,
        schema_sample_rows=schema_sample_rows,
        data_window=data_window,
        rule_id=rule.id,
    )
    return await agent.run(rule.prompt_template, trigger_type="rule")


async def _complete_task_run(
    task_run: TaskRun | None,
    status: str,
    duration: float,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update task_run record with completion info."""
    if task_run is None:
        return
    try:
        async with get_session() as session:
            from order_guard.storage.crud import get_by_id, update
            tr = await get_by_id(session, TaskRun, task_run.id)
            if tr:
                await update(
                    session,
                    tr,
                    status=status,
                    completed_at=datetime.now(timezone.utc),
                    duration_ms=int(duration * 1000),
                    error=error,
                    result_summary=result or {},
                )
    except Exception as e:
        logger.error("Failed to update task_run: {}", e)


async def run_report_job(
    report_id: str,
    job_name: str = "scheduled-report",
    data_access_layer: Any | None = None,
    mcp_manager: Any | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Execute a scheduled report generation and push."""
    from order_guard.engine.reporter import ReportManager, generate_report, push_report

    logger.info("Starting report job: {} ({})", report_id, job_name)

    mgr = ReportManager()
    report = await mgr.get_report(report_id)
    if not report:
        logger.error("Report config not found: {}", report_id)
        return None

    if not report.enabled:
        logger.info("Report is disabled: {}", report_id)
        return None

    # Generate report
    result = await generate_report(
        report,
        data_access_layer=data_access_layer,
        mcp_manager=mcp_manager,
    )

    # Save to history
    await mgr.save_history(
        report_id=report_id,
        content=result["content"],
        status=result["status"],
        token_usage=result.get("token_usage", 0),
        duration_ms=result.get("duration_ms", 0),
        error=result.get("error"),
    )

    # Push if successful
    if result["status"] == "success" and result["content"]:
        await push_report(report, result["content"], dry_run=dry_run)

    logger.info("Report job completed: {} (status={})", report_id, result["status"])
    return result


# ---------------------------------------------------------------------------
# Health check job
# ---------------------------------------------------------------------------

async def _health_check_job(
    mcp_manager: Any,
    dispatcher: AlertDispatcher,
) -> None:
    """Periodic health check job: probe all data sources, alert on failures, notify on recovery."""
    from order_guard.config.settings import get_settings
    from order_guard.tools.health_tools import (
        check_datasource_health,
        cleanup_old_logs,
        get_consecutive_failures,
    )

    settings = get_settings()
    hc_cfg = settings.health_check
    if not hc_cfg.enabled:
        return

    # Cleanup old logs
    await cleanup_old_logs(hc_cfg.retention_hours)

    # Get all datasource IDs
    try:
        all_ids = list(mcp_manager._connections.keys())
    except Exception:
        all_ids = []

    if not all_ids:
        return

    for ds_id in all_ids:
        # Get consecutive failures BEFORE this check (to detect recovery)
        prev_failures = await get_consecutive_failures(ds_id)

        # Run health check
        result = await check_datasource_health(
            ds_id, mcp_manager, hc_cfg.timeout_seconds
        )

        if result["status"] != "healthy":
            current_failures = await get_consecutive_failures(ds_id)
            if current_failures >= hc_cfg.alert_threshold:
                affected_rules = await _get_affected_rules(ds_id)
                affected_str = ", ".join(affected_rules) if affected_rules else "无"

                alert_text = (
                    f"数据源连接异常\n"
                    f"数据源：{ds_id}\n"
                    f"状态：连续 {current_failures} 次探活失败\n"
                    f"最近错误：{result.get('error', '未知')}\n"
                    f"影响范围：{affected_str}"
                )
                await _send_health_alert(dispatcher, "critical", alert_text)
                logger.warning(
                    "Health alert: {} consecutive failures for '{}'",
                    current_failures, ds_id,
                )
        else:
            if prev_failures >= hc_cfg.alert_threshold:
                downtime_minutes = await _estimate_downtime(ds_id)
                recovery_text = (
                    f"数据源已恢复\n"
                    f"数据源：{ds_id}\n"
                    f"状态：连接正常（延迟 {result['latency_ms']}ms）\n"
                    f"故障持续：{downtime_minutes} 分钟"
                )
                await _send_health_alert(dispatcher, "info", recovery_text)
                logger.info("Health recovery: '{}' is back online", ds_id)


async def _get_affected_rules(datasource_id: str) -> list[str]:
    """Find rules that depend on a given datasource (MCP server)."""
    try:
        from sqlalchemy import select
        async with get_session() as session:
            stmt = select(AlertRule).where(
                AlertRule.mcp_server == datasource_id,
                AlertRule.enabled == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            rules = result.scalars().all()
            return [f"{r.id}({r.name})" for r in rules]
    except Exception as e:
        logger.debug("_get_affected_rules error: {}", e)
        return []


async def _estimate_downtime(datasource_id: str) -> int:
    """Estimate downtime in minutes by looking at the first failure in the current streak."""
    try:
        from sqlalchemy import select
        async with get_session() as session:
            stmt = (
                select(DataSourceHealthLog)
                .where(DataSourceHealthLog.datasource_id == datasource_id)
                .order_by(DataSourceHealthLog.created_at.desc())
                .limit(200)
            )
            result = await session.execute(stmt)
            logs = result.scalars().all()

            first_failure_time = None
            for log in logs:
                if log.status == "healthy":
                    if first_failure_time is None:
                        continue
                    break
                first_failure_time = log.created_at

            if first_failure_time:
                now = datetime.now(timezone.utc)
                if first_failure_time.tzinfo is None:
                    first_failure_time = first_failure_time.replace(tzinfo=timezone.utc)
                delta = now - first_failure_time
                return max(1, int(delta.total_seconds() / 60))
            return 0
    except Exception as e:
        logger.debug("_estimate_downtime error: {}", e)
        return 0


async def _send_health_alert(
    dispatcher: AlertDispatcher,
    severity: str,
    text: str,
) -> None:
    """Send a health alert via the alert dispatcher channels."""
    from order_guard.alerts.base import AlertMessage

    msg = AlertMessage(
        severity=severity,
        title=text.split("\n")[0],
        summary=text,
        rule_name="health-check",
        source="system",
    )
    for channel in dispatcher._channels:
        try:
            await channel.send(msg)
        except Exception as e:
            logger.error("Failed to send health alert via {}: {}", channel.name, e)
