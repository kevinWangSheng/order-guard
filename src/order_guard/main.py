"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from order_guard import __version__
from order_guard.config import get_settings
from order_guard.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(log_dir=settings.app.log_dir, level=settings.app.log_level)
    logger.info("OrderGuard v{} starting", __version__)

    # Initialize storage
    from order_guard.storage.database import init_db
    await init_db()

    # Sync rules from YAML
    from order_guard.engine.rules import RuleManager
    rule_manager = RuleManager()
    await rule_manager.sync_rules_to_db()

    # Set up connectors
    from order_guard.connectors.registry import ConnectorRegistry
    connector_registry = ConnectorRegistry()
    if settings.connectors:
        connector_registry.register_from_config(
            [c.model_dump() for c in settings.connectors]
        )

    # Set up alert dispatcher
    from order_guard.alerts.dispatcher import AlertDispatcher
    dispatcher = AlertDispatcher()
    if settings.alerts.channels:
        dispatcher.register_from_config(settings.alerts.channels)

    # Set up scheduler
    from order_guard.engine.analyzer import Analyzer
    from order_guard.scheduler.setup import create_scheduler
    analyzer = Analyzer()
    scheduler = await create_scheduler(
        connector_registry=connector_registry,
        rule_manager=rule_manager,
        analyzer=analyzer,
        dispatcher=dispatcher,
    )

    # Store components on app state for access in routes/CLI
    app.state.rule_manager = rule_manager
    app.state.connector_registry = connector_registry
    app.state.dispatcher = dispatcher
    app.state.analyzer = analyzer
    app.state.scheduler = scheduler

    # Start scheduler
    if settings.scheduler.enabled:
        async with scheduler:
            logger.info("Scheduler started")
            yield
            logger.info("Scheduler stopping")
    else:
        yield

    logger.info("OrderGuard shutting down")


app = FastAPI(
    title="OrderGuard",
    description="企业数据智能监控中台",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("order_guard.main:app", host="0.0.0.0", port=8000)
