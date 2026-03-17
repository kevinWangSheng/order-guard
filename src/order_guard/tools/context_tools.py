"""Business context tools — 3 tools for Agent to manage business knowledge."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, func

from order_guard.mcp.models import ToolInfo
from order_guard.models import BusinessContext, CONTEXT_CATEGORIES
from order_guard.storage.crud import create, get_by_id
from order_guard.storage.database import get_session

MAX_CONTEXT_ITEMS = 20
MAX_CONTEXT_TOKENS = 1000  # rough char count as proxy

# Category display names
_CATEGORY_NAMES = {
    "promotion": "促销活动",
    "strategy": "经营策略",
    "supplier": "供应商",
    "product": "产品信息",
    "logistics": "物流",
    "other": "其他",
}

# ---------------------------------------------------------------------------
# Tool Schema definitions
# ---------------------------------------------------------------------------

TOOL_LIST_CONTEXT = ToolInfo(
    name="list_context",
    description="列出当前生效的业务知识（已过期的自动排除）。可按分类筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": CONTEXT_CATEGORIES,
                "description": "按分类筛选。不传则返回全部。",
            },
        },
        "required": [],
    },
    server_name="context_tools",
)

TOOL_ADD_CONTEXT = ToolInfo(
    name="add_context",
    description="添加一条业务知识，会注入到后续所有分析中作为背景参考。上限 20 条。",
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "业务知识内容，如 '3月全线提价5%' 或 '主要供应商是义乌XX工厂'",
            },
            "category": {
                "type": "string",
                "enum": CONTEXT_CATEGORIES,
                "description": "知识分类。默认 'other'",
            },
            "expires_at": {
                "type": "string",
                "description": "过期时间。ISO 格式如 '2026-04-01' 或相对时间如 '7d'（7天后过期）、'30d'。不传则永不过期",
            },
        },
        "required": ["content"],
    },
    server_name="context_tools",
)

TOOL_DELETE_CONTEXT = ToolInfo(
    name="delete_context",
    description="删除一条业务知识。删除后不可恢复。",
    input_schema={
        "type": "object",
        "properties": {
            "context_id": {
                "type": "string",
                "description": "业务知识 ID，从 list_context 获取",
            },
        },
        "required": ["context_id"],
    },
    server_name="context_tools",
)

TOOL_DEFINITIONS = [TOOL_LIST_CONTEXT, TOOL_ADD_CONTEXT, TOOL_DELETE_CONTEXT]


# ---------------------------------------------------------------------------
# Relative time parser
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(r"^(\d+)([dhm])$")


def _parse_expires_at(value: str) -> datetime | None:
    """Parse expires_at: '7d', '24h', '30m', or ISO format."""
    if not value:
        return None
    value = value.strip()

    # Relative time
    m = _RELATIVE_RE.match(value)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        now = datetime.now(timezone.utc)
        if unit == "d":
            return now + timedelta(days=amount)
        elif unit == "h":
            return now + timedelta(hours=amount)
        elif unit == "m":
            return now + timedelta(minutes=amount)

    # ISO format
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tool executor functions
# ---------------------------------------------------------------------------

async def list_context(**kwargs: Any) -> dict:
    """列出当前生效的业务知识。"""
    category = kwargs.get("category")

    try:
        async with get_session() as session:
            now = datetime.now(timezone.utc)
            stmt = select(BusinessContext).order_by(BusinessContext.created_at)

            # Filter by category
            if category:
                if category not in CONTEXT_CATEGORIES:
                    return {
                        "error": f"无效的分类 '{category}'。",
                        "hint": f"可选分类：{CONTEXT_CATEGORIES}",
                    }
                stmt = stmt.where(BusinessContext.category == category)

            result = await session.execute(stmt)
            entries = result.scalars().all()

            # Filter expired
            items = []
            for e in entries:
                if e.expires_at and e.expires_at.replace(tzinfo=timezone.utc) < now:
                    continue
                items.append({
                    "id": e.id,
                    "content": e.content,
                    "category": e.category,
                    "category_name": _CATEGORY_NAMES.get(e.category, e.category),
                    "expires_at": e.expires_at.isoformat() if e.expires_at else None,
                    "source": e.source,
                    "created_at": e.created_at.isoformat(),
                })

            if items:
                hint = f"共 {len(items)} 条业务知识。可以添加新的或删除过时的。"
            else:
                hint = "暂无业务知识。可以使用 add_context 添加。"

            return {"data": items, "hint": hint}

    except Exception as e:
        logger.error("list_context failed: {}", e)
        return {"error": f"查询业务知识失败: {e}", "hint": "请稍后重试。"}


async def add_context(**kwargs: Any) -> dict:
    """添加一条业务知识。"""
    content = kwargs.get("content", "").strip()
    category = kwargs.get("category", "other").strip()
    expires_at_str = kwargs.get("expires_at", "")
    created_by = kwargs.get("created_by", "")

    # Validation
    if not content:
        return {"error": "content 不能为空。", "hint": "请提供业务知识内容。"}

    if category not in CONTEXT_CATEGORIES:
        return {
            "error": f"无效的分类 '{category}'。",
            "hint": f"可选分类：{CONTEXT_CATEGORIES}",
        }

    # Parse expires_at
    expires_at = None
    if expires_at_str:
        expires_at = _parse_expires_at(expires_at_str)
        if expires_at is None:
            return {
                "error": f"无法解析过期时间 '{expires_at_str}'。",
                "hint": "支持的格式：'7d'（7天后）、'24h'（24小时后）、'2026-04-01'（ISO 日期）。",
            }

    # Check count limit
    try:
        async with get_session() as session:
            now = datetime.now(timezone.utc)
            stmt = select(func.count()).select_from(BusinessContext)
            result = await session.execute(stmt)
            total = result.scalar() or 0

            # Count only non-expired
            active_count = 0
            if total > 0:
                all_stmt = select(BusinessContext)
                all_result = await session.execute(all_stmt)
                all_entries = all_result.scalars().all()
                for e in all_entries:
                    if e.expires_at and e.expires_at.replace(tzinfo=timezone.utc) < now:
                        continue
                    active_count += 1

            if active_count >= MAX_CONTEXT_ITEMS:
                return {
                    "error": f"业务知识已达上限（{MAX_CONTEXT_ITEMS} 条）。",
                    "hint": "请先删除不需要的知识，或设置过期时间。使用 list_context 查看现有知识。",
                }

            # Create entry
            entry = BusinessContext(
                content=content,
                category=category,
                expires_at=expires_at,
                source="chat",
                created_by=created_by,
            )
            entry = await create(session, entry)

            return {
                "data": {
                    "id": entry.id,
                    "content": entry.content,
                    "category": category,
                    "category_name": _CATEGORY_NAMES.get(category, category),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
                "hint": f"业务知识已添加（分类：{_CATEGORY_NAMES.get(category, category)}）。",
            }

    except Exception as e:
        logger.error("add_context failed: {}", e)
        return {"error": f"添加业务知识失败: {e}", "hint": "请稍后重试。"}


async def delete_context(**kwargs: Any) -> dict:
    """删除一条业务知识。"""
    context_id = kwargs.get("context_id", "")
    if not context_id:
        return {"error": "context_id 不能为空。", "hint": "请先用 list_context 获取知识 ID。"}

    try:
        async with get_session() as session:
            entry = await get_by_id(session, BusinessContext, context_id)
            if entry is None:
                return {
                    "error": f"业务知识 ID '{context_id}' 不存在。",
                    "hint": "请使用 list_context 查看所有业务知识及其 ID。",
                }

            content_preview = entry.content[:50]
            await session.delete(entry)
            await session.flush()

            return {
                "data": {"id": context_id, "deleted": True},
                "hint": f"业务知识已删除: '{content_preview}...'",
            }

    except Exception as e:
        logger.error("delete_context failed: {}", e)
        return {"error": f"删除业务知识失败: {e}", "hint": "请稍后重试。"}


# ---------------------------------------------------------------------------
# System prompt injection
# ---------------------------------------------------------------------------

async def build_context_injection(max_items: int = MAX_CONTEXT_ITEMS, max_tokens: int = MAX_CONTEXT_TOKENS) -> str:
    """Build business knowledge text for system prompt injection.

    Groups by category, filters expired, truncates if over limits.
    """
    try:
        async with get_session() as session:
            now = datetime.now(timezone.utc)
            stmt = select(BusinessContext).order_by(BusinessContext.created_at)
            result = await session.execute(stmt)
            entries = result.scalars().all()

            # Filter expired and group by category
            groups: dict[str, list[str]] = {}
            count = 0
            for e in entries:
                if e.expires_at and e.expires_at.replace(tzinfo=timezone.utc) < now:
                    continue
                if count >= max_items:
                    break
                cat = e.category or "other"
                if cat not in groups:
                    groups[cat] = []
                groups[cat].append(e.content)
                count += 1

            if not groups:
                return ""

            # Format output
            lines = ["## 业务背景"]
            total_chars = 0
            for cat in CONTEXT_CATEGORIES:
                if cat not in groups:
                    continue
                cat_name = _CATEGORY_NAMES.get(cat, cat)
                lines.append(f"### {cat_name}")
                for item in groups[cat]:
                    line = f"- {item}"
                    if total_chars + len(line) > max_tokens:
                        lines.append("- ...(已截断)")
                        return "\n".join(lines)
                    lines.append(line)
                    total_chars += len(line)

            return "\n".join(lines)

    except Exception as e:
        logger.debug("Failed to build context injection: {}", e)
        return ""


# ---------------------------------------------------------------------------
# Config initial loading
# ---------------------------------------------------------------------------

async def load_config_context() -> int:
    """Load business_context from config.yaml into DB (source='config').

    Skips if config entries already exist. Returns count of entries loaded.
    """
    try:
        from order_guard.config import get_settings
        settings = get_settings()
        config_text = (settings.business_context or "").strip()
        if not config_text:
            return 0

        async with get_session() as session:
            # Check if config entries already exist
            stmt = select(func.count()).select_from(BusinessContext).where(
                BusinessContext.source == "config"
            )
            result = await session.execute(stmt)
            existing = result.scalar() or 0
            if existing > 0:
                logger.debug("Config business context already loaded ({} entries)", existing)
                return 0

            # Split config text into entries (by newline)
            lines = [line.strip() for line in config_text.split("\n") if line.strip()]
            loaded = 0
            for line in lines:
                # Remove leading bullet points
                clean = line.lstrip("-•· ").strip()
                if not clean:
                    continue
                entry = BusinessContext(
                    content=clean,
                    category="other",
                    source="config",
                )
                await create(session, entry)
                loaded += 1

            logger.info("Loaded {} business context entries from config", loaded)
            return loaded

    except Exception as e:
        logger.error("Failed to load config context: {}", e)
        return 0


# ---------------------------------------------------------------------------
# Tool executors mapping
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: dict[str, Any] = {
    "list_context": list_context,
    "add_context": add_context,
    "delete_context": delete_context,
}
