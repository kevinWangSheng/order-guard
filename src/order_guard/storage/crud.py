"""Generic CRUD operations for SQLModel tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlmodel import SQLModel

T = TypeVar("T", bound=SQLModel)


async def create(session: AsyncSession, obj: T) -> T:
    """Insert a new record."""
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def get_by_id(session: AsyncSession, model: Type[T], id_: str) -> T | None:
    """Get a record by primary key."""
    return await session.get(model, id_)


async def list_all(
    session: AsyncSession,
    model: Type[T],
    *,
    limit: int = 100,
    offset: int = 0,
    order_by: str | None = None,
    descending: bool = True,
    filters: dict[str, Any] | None = None,
) -> Sequence[T]:
    """List records with pagination and optional filters."""
    stmt = select(model)

    if filters:
        for field, value in filters.items():
            if hasattr(model, field):
                stmt = stmt.where(getattr(model, field) == value)

    if order_by and hasattr(model, order_by):
        col = getattr(model, order_by)
        stmt = stmt.order_by(col.desc() if descending else col.asc())

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def update(session: AsyncSession, obj: T, **kwargs: Any) -> T:
    """Update fields on an existing record."""
    for key, value in kwargs.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    # Update timestamp if model has updated_at
    if hasattr(obj, "updated_at"):
        obj.updated_at = datetime.now(timezone.utc)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj
