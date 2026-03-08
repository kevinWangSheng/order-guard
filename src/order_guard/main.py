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
