"""Database engine and session management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from order_guard.config import get_settings

_engine = None


def _to_async_url(url: str) -> str:
    """Convert sync DB URL to async variant."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        async_url = _to_async_url(settings.database.url)

        # Ensure parent directory exists for SQLite
        if "sqlite" in async_url:
            db_path = async_url.split("sqlite+aiosqlite:///")[-1]
            if db_path:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(async_url, echo=settings.app.debug)
    return _engine


async def init_db():
    """Create all tables (for development/testing; use Alembic in production)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session with automatic commit/rollback."""
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_engine():
    """Reset engine (for testing)."""
    global _engine
    _engine = None
