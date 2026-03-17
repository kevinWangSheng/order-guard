"""LLM usage tracking — cost estimation and statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from order_guard.mcp.models import ToolInfo
from order_guard.models.tables import LLMUsageLog


# ---------------------------------------------------------------------------
# Tool Schema definition
# ---------------------------------------------------------------------------

TOOL_GET_USAGE_STATS = ToolInfo(
    name="get_usage_stats",
    description=(
        "查询 LLM 用量统计。可指定时间范围和分组方式。\n"
        "- time_range: '7d', '30d', '24h' 等\n"
        "- group_by: 'rule' / 'trigger_type' / 'model' / 'day'\n"
        "- rule_id: 按规则 ID 过滤"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "time_range": {
                "type": "string",
                "description": "时间范围，如 '7d', '30d', '24h'。默认 '7d'。",
            },
            "group_by": {
                "type": "string",
                "enum": ["rule", "trigger_type", "model", "day"],
                "description": "分组方式。不传则只返回总计。",
            },
            "rule_id": {
                "type": "string",
                "description": "按规则 ID 过滤。不传则查所有。",
            },
        },
        "required": [],
    },
    server_name="usage_tools",
)

TOOL_DEFINITIONS = [TOOL_GET_USAGE_STATS]

# ---------------------------------------------------------------------------
# Model pricing (per 1M tokens, USD)
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "qwen/qwen3-coder-plus": {"input": 0.50, "output": 2.00},
}


def _match_pricing(
    model: str,
    custom_pricing: dict[str, dict[str, float]] | None = None,
) -> dict[str, float] | None:
    """Match model to pricing: custom exact → custom prefix → built-in exact → built-in prefix."""
    # Custom pricing: exact match
    if custom_pricing:
        if model in custom_pricing:
            return custom_pricing[model]
        # Custom pricing: prefix match
        for key, price in custom_pricing.items():
            if model.startswith(key):
                return price

    # Built-in: exact match
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]

    # Built-in: prefix match
    for key, price in MODEL_PRICING.items():
        if model.startswith(key):
            return price

    return None


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    custom_pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Estimate cost in USD for a given model and token counts.

    Pricing lookup order: exact match → prefix match → unknown (cost=0).
    """
    pricing = _match_pricing(model, custom_pricing)
    if pricing is None:
        return 0.0

    input_cost = (prompt_tokens / 1_000_000) * pricing.get("input", 0)
    output_cost = (completion_tokens / 1_000_000) * pricing.get("output", 0)
    return round(input_cost + output_cost, 8)


# ---------------------------------------------------------------------------
# Usage statistics
# ---------------------------------------------------------------------------

def _parse_time_range(time_range: str) -> datetime:
    """Parse time range string (e.g. '7d', '30d', '24h') to a cutoff datetime."""
    now = datetime.now(timezone.utc)
    value = int(time_range[:-1])
    unit = time_range[-1]
    if unit == "d":
        return now - timedelta(days=value)
    if unit == "h":
        return now - timedelta(hours=value)
    # Default: treat as days
    return now - timedelta(days=value)


async def get_usage_stats(
    time_range: str = "7d",
    group_by: str | None = None,
    rule_id: str | None = None,
) -> dict[str, Any]:
    """Query LLM usage statistics.

    Args:
        time_range: Time window, e.g. "7d", "30d", "24h".
        group_by: Optional grouping — "rule" / "trigger_type" / "model" / "day".
        rule_id: Optional filter by rule_id.

    Returns:
        {data: {total_tokens, total_cost_usd, count, by_group}, hint: str}
    """
    from order_guard.storage import database as _db

    cutoff = _parse_time_range(time_range)

    try:
        async with _db.get_session() as session:
            return await _query_usage_stats(session, cutoff, group_by, rule_id, time_range)
    except Exception as e:
        logger.error("Failed to query usage stats: {}", e)
        return {
            "data": {"total_tokens": 0, "total_cost_usd": 0.0, "count": 0, "by_group": []},
            "hint": f"查询失败: {e}",
        }


async def _query_usage_stats(
    session: AsyncSession,
    cutoff: datetime,
    group_by: str | None,
    rule_id: str | None,
    time_range: str = "",
) -> dict[str, Any]:
    """Execute the usage stats query within a session."""
    # Base filter
    base_filter = [LLMUsageLog.created_at >= cutoff]
    if rule_id:
        base_filter.append(LLMUsageLog.rule_id == rule_id)

    # Totals
    totals_stmt = select(
        func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LLMUsageLog.cost_estimate_usd), 0.0).label("total_cost_usd"),
        func.count(LLMUsageLog.id).label("count"),
    ).where(*base_filter)

    result = await session.execute(totals_stmt)
    row = result.one()
    total_tokens = int(row.total_tokens)
    total_cost_usd = float(row.total_cost_usd)
    count = int(row.count)

    # Group by
    by_group: list[dict[str, Any]] = []
    if group_by:
        group_col = _resolve_group_column(group_by)
        if group_col is not None:
            group_stmt = select(
                group_col.label("group_key"),
                func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(LLMUsageLog.cost_estimate_usd), 0.0).label("total_cost_usd"),
                func.count(LLMUsageLog.id).label("count"),
            ).where(*base_filter).group_by(group_col)

            group_result = await session.execute(group_stmt)
            for grow in group_result.all():
                key = grow.group_key
                if isinstance(key, datetime):
                    key = key.strftime("%Y-%m-%d")
                by_group.append({
                    "key": str(key) if key else "",
                    "total_tokens": int(grow.total_tokens),
                    "total_cost_usd": float(grow.total_cost_usd),
                    "count": int(grow.count),
                })

    data = {
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost_usd, 6),
        "count": count,
        "by_group": by_group,
    }

    hint = f"最近 {time_range} 共 {count} 次 LLM 调用，消耗 {total_tokens} tokens，估算费用 ${total_cost_usd:.4f}"
    if group_by:
        hint += f"（按 {group_by} 分组）"

    return {"data": data, "hint": hint}


def _resolve_group_column(group_by: str):
    """Map group_by string to a SQLAlchemy column expression."""
    if group_by == "rule":
        return LLMUsageLog.rule_id
    if group_by == "trigger_type":
        return LLMUsageLog.trigger_type
    if group_by == "model":
        return LLMUsageLog.model
    if group_by == "day":
        return func.date(LLMUsageLog.created_at)
    return None


# ---------------------------------------------------------------------------
# Tool executor wrapper
# ---------------------------------------------------------------------------

async def _get_usage_stats_tool(**kwargs: Any) -> dict[str, Any]:
    """Tool executor wrapper for get_usage_stats."""
    return await get_usage_stats(
        time_range=kwargs.get("time_range", "7d"),
        group_by=kwargs.get("group_by"),
        rule_id=kwargs.get("rule_id"),
    )


# ---------------------------------------------------------------------------
# Tool executors mapping
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: dict[str, Any] = {
    "get_usage_stats": _get_usage_stats_tool,
}
