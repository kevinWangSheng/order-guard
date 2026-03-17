"""APScheduler integration with FastAPI."""

from __future__ import annotations

from typing import Any

from apscheduler import AsyncScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.config import get_settings
from order_guard.config.settings import SchedulerJobConfig
from order_guard.engine.analyzer import Analyzer
from order_guard.engine.rules import RuleManager
from order_guard.scheduler.jobs import run_detection_job, run_report_job, _health_check_job


async def create_scheduler(
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
    data_access_layer: Any | None = None,
    mcp_manager: Any | None = None,
) -> AsyncScheduler:
    """Create and configure the APScheduler instance."""
    settings = get_settings()
    scheduler = AsyncScheduler()

    if not settings.scheduler.enabled:
        logger.info("Scheduler is disabled")
        return scheduler

    for job_cfg in settings.scheduler.jobs:
        _register_job(
            scheduler,
            job_cfg,
            rule_manager=rule_manager,
            analyzer=analyzer,
            dispatcher=dispatcher,
            data_access_layer=data_access_layer,
        )

    # Register report jobs
    for report_cfg in settings.reports:
        if not report_cfg.enabled or not report_cfg.schedule:
            continue
        _register_report_job(
            scheduler,
            report_cfg,
            data_access_layer=data_access_layer,
            mcp_manager=mcp_manager,
        )

    # Register health check job
    if settings.health_check.enabled and mcp_manager is not None:
        _register_health_check(scheduler, dispatcher, mcp_manager)

    return scheduler


def _register_health_check(
    scheduler: AsyncScheduler,
    dispatcher: AlertDispatcher,
    mcp_manager: Any,
) -> None:
    """Register the periodic health check job."""
    settings = get_settings()
    interval = settings.health_check.interval_minutes

    trigger = IntervalTrigger(minutes=interval)
    scheduler.add_schedule(
        _health_check_job,
        trigger,
        id="health_check",
        kwargs={
            "mcp_manager": mcp_manager,
            "dispatcher": dispatcher,
        },
    )
    logger.info(
        "Registered health check job (interval={}min, threshold={})",
        interval,
        settings.health_check.alert_threshold,
    )


def _register_job(
    scheduler: AsyncScheduler,
    job_cfg: SchedulerJobConfig,
    *,
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
    data_access_layer: Any | None = None,
) -> None:
    """Register a single job from config."""
    try:
        trigger = CronTrigger.from_crontab(job_cfg.cron)
    except ValueError as e:
        logger.error("Invalid cron expression for job '{}': {}", job_cfg.name, e)
        return

    for rule_id in job_cfg.rule_ids:
        job_id = f"{job_cfg.name}_{rule_id}"
        kwargs = {
            "rule_id": rule_id,
            "job_name": job_cfg.name,
            "rule_manager": rule_manager,
            "analyzer": analyzer,
            "dispatcher": dispatcher,
        }
        if data_access_layer is not None:
            kwargs["data_access_layer"] = data_access_layer
        scheduler.add_schedule(
            run_detection_job,
            trigger,
            id=job_id,
            kwargs=kwargs,
        )
        logger.info("Registered job: {} (cron={}, rule={})", job_cfg.name, job_cfg.cron, rule_id)


def _register_report_job(
    scheduler: AsyncScheduler,
    report_cfg: Any,
    *,
    data_access_layer: Any | None = None,
    mcp_manager: Any | None = None,
) -> None:
    """Register a report job from config."""
    try:
        trigger = CronTrigger.from_crontab(report_cfg.schedule)
    except ValueError as e:
        logger.error("Invalid cron for report '{}': {}", report_cfg.id, e)
        return

    job_id = f"report_{report_cfg.id}"
    kwargs: dict[str, Any] = {
        "report_id": report_cfg.id,
        "job_name": f"report-{report_cfg.name}",
    }
    if data_access_layer is not None:
        kwargs["data_access_layer"] = data_access_layer
    if mcp_manager is not None:
        kwargs["mcp_manager"] = mcp_manager

    scheduler.add_schedule(
        run_report_job,
        trigger,
        id=job_id,
        kwargs=kwargs,
    )
    logger.info("Registered report job: {} (cron={})", report_cfg.name, report_cfg.schedule)
