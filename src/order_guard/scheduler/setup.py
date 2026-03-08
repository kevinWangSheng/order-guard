"""APScheduler integration with FastAPI."""

from __future__ import annotations

from apscheduler import AsyncScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from order_guard.alerts.dispatcher import AlertDispatcher
from order_guard.config import get_settings
from order_guard.config.settings import SchedulerJobConfig
from order_guard.connectors.registry import ConnectorRegistry
from order_guard.engine.analyzer import Analyzer
from order_guard.engine.rules import RuleManager
from order_guard.scheduler.jobs import run_detection_job


async def create_scheduler(
    connector_registry: ConnectorRegistry,
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
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
            connector_registry=connector_registry,
            rule_manager=rule_manager,
            analyzer=analyzer,
            dispatcher=dispatcher,
        )

    return scheduler


def _register_job(
    scheduler: AsyncScheduler,
    job_cfg: SchedulerJobConfig,
    *,
    connector_registry: ConnectorRegistry,
    rule_manager: RuleManager,
    analyzer: Analyzer,
    dispatcher: AlertDispatcher,
) -> None:
    """Register a single job from config."""
    try:
        trigger = CronTrigger.from_crontab(job_cfg.cron)
    except ValueError as e:
        logger.error("Invalid cron expression for job '{}': {}", job_cfg.name, e)
        return

    for rule_id in job_cfg.rule_ids:
        job_id = f"{job_cfg.name}_{rule_id}"
        scheduler.add_schedule(
            run_detection_job,
            trigger,
            id=job_id,
            kwargs={
                "rule_id": rule_id,
                "job_name": job_cfg.name,
                "connector_registry": connector_registry,
                "rule_manager": rule_manager,
                "analyzer": analyzer,
                "dispatcher": dispatcher,
            },
        )
        logger.info("Registered job: {} (cron={}, rule={})", job_cfg.name, job_cfg.cron, rule_id)
