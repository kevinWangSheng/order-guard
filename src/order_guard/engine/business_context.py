"""Business context — load, merge, and manage business knowledge for Agent prompts."""

from __future__ import annotations

from loguru import logger

from order_guard.config import get_settings

MAX_CONTEXT_LENGTH = 2000


async def get_business_context() -> str:
    """Get merged business context: config base + DB updates.

    Returns combined text, truncated to MAX_CONTEXT_LENGTH.
    """
    settings = get_settings()
    base = (settings.business_context or "").strip()

    # Load chat-added updates from DB
    updates = await _load_db_updates()
    if updates:
        parts = [base] if base else []
        parts.extend(updates)
        merged = "\n".join(parts)
    else:
        merged = base

    if not merged:
        return ""

    # Truncate if too long
    if len(merged) > MAX_CONTEXT_LENGTH:
        merged = merged[:MAX_CONTEXT_LENGTH] + "\n...(已截断)"
        logger.info("Business context truncated to {} chars", MAX_CONTEXT_LENGTH)

    return merged


async def add_business_context(content: str, user_id: str = "") -> bool:
    """Add a new business context entry from chat conversation.

    Returns True if saved successfully.
    """
    content = content.strip()
    if not content:
        return False

    try:
        from order_guard.models import BusinessContext
        from order_guard.storage.database import get_session
        from order_guard.storage.crud import create

        async with get_session() as session:
            entry = BusinessContext(
                content=content,
                source="chat",
                created_by=user_id,
            )
            await create(session, entry)
        logger.info("Business context added: '{}' (by {})", content[:50], user_id or "system")
        return True
    except Exception as e:
        logger.error("Failed to add business context: {}", e)
        return False


async def _load_db_updates() -> list[str]:
    """Load chat-added business context entries from DB."""
    try:
        from order_guard.models import BusinessContext
        from order_guard.storage.database import get_session
        from sqlalchemy import select

        async with get_session() as session:
            stmt = select(BusinessContext).where(
                BusinessContext.source == "chat"
            ).order_by(BusinessContext.created_at)
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return [e.content for e in entries if e.content.strip()]
    except Exception as e:
        logger.debug("Failed to load business context from DB: {}", e)
        return []


def build_business_context_prompt(context: str) -> str:
    """Build the business context section for Agent system prompt."""
    if not context:
        return ""
    return f"""## 公司业务背景
{context}

请在分析时结合以上业务背景，给出更贴合实际的判断和建议。"""
