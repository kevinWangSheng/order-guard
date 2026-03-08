"""Detection job orchestration — the core pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.connectors.registry import ConnectorRegistry
from order_guard.engine.analyzer import Analyzer, AnalyzerOutput
from order_guard.engine.metrics import MetricsEngine
from order_guard.engine.rules import RuleManager
from order_guard.engine.summary import SummaryBuilder
from order_guard.models import TaskRun
from order_guard.storage.crud import create, update
from order_guard.storage.database import get_session


async def run_detection_job(
    rule_id: str,
    job_name: str,
    connector_registry: ConnectorRegistry,
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
    *,
    dry_run: bool = False,
    mcp_manager: Any | None = None,
) -> TaskRun | None:
    """Execute the full detection pipeline for a single rule.

    Pipeline: load rule → fetch data → compute metrics → LLM analyze → dispatch alerts
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

        if rule.connector_type == "mcp":
            # ---- MCP Agent flow ----
            result = await _run_mcp_pipeline(rule, mcp_manager, analyzer)
        else:
            # ---- Legacy Connector flow ----
            result = await _run_legacy_pipeline(rule, connector_registry, analyzer)

        # Dispatch alerts (unified for both flows)
        source = rule.mcp_server if rule.connector_type == "mcp" else rule.connector_id
        send_results = await dispatcher.dispatch(
            result,
            rule_name=rule.name,
            source=source,
            dry_run=dry_run,
        )

        # 7. Record success
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


async def _run_mcp_pipeline(rule: Any, mcp_manager: Any, analyzer: Analyzer) -> AnalyzerOutput:
    """Execute the MCP Agent pipeline for a rule."""
    if mcp_manager is None:
        raise ValueError("MCP manager not configured. Cannot run MCP rule.")

    from order_guard.engine.agent import Agent
    from order_guard.engine.llm_client import LLMClient

    mcp_conn = mcp_manager.get_connection(rule.mcp_server)
    if not mcp_conn.is_connected():
        await mcp_conn.connect()

    agent = Agent(llm_client=LLMClient(), mcp_connection=mcp_conn)
    result = await agent.run(rule.prompt_template)
    return result


async def _run_legacy_pipeline(rule: Any, connector_registry: ConnectorRegistry, analyzer: Analyzer) -> AnalyzerOutput:
    """Execute the legacy Connector pipeline for a rule."""
    connector = connector_registry.get(rule.connector_id)
    data_type = rule.data_type or _infer_data_type(rule.prompt_template)
    raw_data = await connector.query(data_type)

    engine = MetricsEngine()
    if data_type == "inventory":
        metrics = engine.compute_inventory_metrics(raw_data)
        summary = SummaryBuilder().build_inventory_summary(metrics)
    elif data_type == "orders":
        metrics = engine.compute_order_metrics(raw_data)
        summary = SummaryBuilder().build_order_summary(metrics)
    else:
        metrics = raw_data
        summary = str(raw_data)

    return await analyzer.analyze(summary, rule.prompt_template)


def _infer_data_type(prompt_template: str) -> str:
    """Infer data type from rule prompt content."""
    prompt_lower = prompt_template.lower()
    if "退货" in prompt_lower or "退货率" in prompt_lower or "订单" in prompt_lower or "return" in prompt_lower:
        return "orders"
    if "库存" in prompt_lower or "stock" in prompt_lower or "inventory" in prompt_lower:
        return "inventory"
    return "inventory"  # default


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
