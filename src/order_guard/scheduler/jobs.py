"""Detection job orchestration — MCP Agent pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.engine.analyzer import Analyzer, AnalyzerOutput
from order_guard.engine.rules import RuleManager
from order_guard.models import TaskRun
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
        result = await _run_mcp_pipeline(rule, mcp_manager)

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


async def _run_mcp_pipeline(rule: Any, mcp_manager: Any) -> AnalyzerOutput:
    """Execute the MCP Agent pipeline for a rule."""
    if mcp_manager is None:
        raise ValueError("MCP manager not configured. Cannot run rule.")

    from order_guard.engine.agent import Agent
    from order_guard.engine.llm_client import LLMClient

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
    return await agent.run(rule.prompt_template)


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
