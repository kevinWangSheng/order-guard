"""Alert history tools — tools for Agent to query and manage alert lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, func

from order_guard.mcp.models import ToolInfo
from order_guard.models import Alert, AlertRule
from order_guard.storage.database import get_session

# ---------------------------------------------------------------------------
# Valid resolutions
# ---------------------------------------------------------------------------

VALID_RESOLUTIONS = frozenset({"handled", "ignored", "false_positive"})

# ---------------------------------------------------------------------------
# Tool Schema definitions
# ---------------------------------------------------------------------------

TOOL_LIST_ALERTS = ToolInfo(
    name="list_alerts",
    description="查询历史告警记录。可按规则和时间范围筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "按规则 ID 筛选，从 list_rules 获取。不传则查所有规则",
            },
            "time_range": {
                "type": "string",
                "enum": ["24h", "7d", "30d"],
                "description": "时间范围。不传则不限",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数上限，默认 20",
            },
        },
        "required": [],
    },
    server_name="alert_tools",
)

TOOL_HANDLE_ALERT = ToolInfo(
    name="handle_alert",
    description="标记告警处理状态。可按 alert_id 单条处理，或按 rule_id + time_range 批量处理。",
    input_schema={
        "type": "object",
        "properties": {
            "alert_id": {
                "type": "string",
                "description": "告警 ID，单条处理时传入",
            },
            "rule_id": {
                "type": "string",
                "description": "规则 ID，批量处理时传入",
            },
            "time_range": {
                "type": "string",
                "enum": ["24h", "7d", "30d"],
                "description": "时间范围，批量处理时可选",
            },
            "resolution": {
                "type": "string",
                "enum": ["handled", "ignored", "false_positive"],
                "description": "处理状态：handled=已处理, ignored=忽略, false_positive=误报",
            },
            "note": {
                "type": "string",
                "description": "处理备注（可选）",
            },
        },
        "required": ["resolution"],
    },
    server_name="alert_tools",
)

TOOL_GET_ALERT_STATS = ToolInfo(
    name="get_alert_stats",
    description="获取告警处理率统计。返回总数、按严重程度/处理状态分布、处理率等。",
    input_schema={
        "type": "object",
        "properties": {
            "time_range": {
                "type": "string",
                "enum": ["24h", "7d", "30d"],
                "description": "时间范围，默认 7d",
            },
            "rule_id": {
                "type": "string",
                "description": "按规则 ID 筛选",
            },
        },
        "required": [],
    },
    server_name="alert_tools",
)

TOOL_DEFINITIONS = [TOOL_LIST_ALERTS, TOOL_HANDLE_ALERT, TOOL_GET_ALERT_STATS]

_TIME_RANGE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


# ---------------------------------------------------------------------------
# Tool executor functions
# ---------------------------------------------------------------------------

async def list_alerts(**kwargs: Any) -> dict:
    """查询历史告警记录。"""
    rule_id = kwargs.get("rule_id")
    time_range = kwargs.get("time_range")
    limit = kwargs.get("limit", 20)

    # Validate time_range
    if time_range and time_range not in _TIME_RANGE_MAP:
        return {
            "error": f"无效的 time_range '{time_range}'。",
            "hint": f"可选值：{list(_TIME_RANGE_MAP.keys())}",
        }

    try:
        async with get_session() as session:
            stmt = select(Alert).order_by(Alert.created_at.desc())

            if rule_id:
                stmt = stmt.where(Alert.rule_id == rule_id)

            if time_range:
                cutoff = datetime.now(timezone.utc) - _TIME_RANGE_MAP[time_range]
                stmt = stmt.where(Alert.created_at >= cutoff)

            stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            alerts = result.scalars().all()

            # Collect rule names
            rule_ids = {a.rule_id for a in alerts}
            rule_names: dict[str, str] = {}
            for rid in rule_ids:
                rule = await session.get(AlertRule, rid)
                if rule:
                    rule_names[rid] = rule.name

            items = []
            severity_counts: dict[str, int] = {}
            for a in alerts:
                items.append({
                    "id": a.id,
                    "rule_id": a.rule_id,
                    "rule_name": rule_names.get(a.rule_id, a.rule_id),
                    "severity": a.severity,
                    "title": a.title,
                    "summary": a.summary,
                    "status": a.status,
                    "resolution": a.resolution,
                    "created_at": a.created_at.isoformat(),
                })
                severity_counts[a.severity] = severity_counts.get(a.severity, 0) + 1

            # Build hint
            if items:
                time_desc = f"最近{time_range}" if time_range else ""
                parts = [f"{time_desc}共 {len(items)} 条告警"]
                for sev in ["critical", "warning", "info"]:
                    if sev in severity_counts:
                        parts.append(f"{severity_counts[sev]} 条{sev}")
                hint = "，".join(parts) + "。"
            else:
                hint = "暂无告警记录。"

            return {"data": items, "hint": hint}

    except Exception as e:
        logger.error("list_alerts failed: {}", e)
        return {"error": f"查询告警失败: {e}", "hint": "请稍后重试。"}


async def handle_alert(**kwargs: Any) -> dict:
    """标记告警处理状态。支持单条（alert_id）或批量（rule_id）。"""
    alert_id = kwargs.get("alert_id")
    rule_id = kwargs.get("rule_id")
    time_range = kwargs.get("time_range")
    resolution = kwargs.get("resolution")
    note = kwargs.get("note", "")

    # Validate: alert_id 和 rule_id 必须至少传一个
    if not alert_id and not rule_id:
        return {
            "error": "必须提供 alert_id 或 rule_id 之一。",
            "hint": "alert_id 用于单条处理，rule_id 用于批量处理。",
        }

    # Validate resolution
    if resolution not in VALID_RESOLUTIONS:
        return {
            "error": f"无效的 resolution '{resolution}'。",
            "hint": f"可选值：{sorted(VALID_RESOLUTIONS)}",
        }

    # Validate time_range
    if time_range and time_range not in _TIME_RANGE_MAP:
        return {
            "error": f"无效的 time_range '{time_range}'。",
            "hint": f"可选值：{list(_TIME_RANGE_MAP.keys())}",
        }

    now = datetime.now(timezone.utc)

    try:
        async with get_session() as session:
            if alert_id:
                # Single alert
                alert = await session.get(Alert, alert_id)
                if not alert:
                    return {
                        "error": f"告警 {alert_id} 不存在。",
                        "hint": "请检查 alert_id 是否正确。",
                    }
                alert.resolution = resolution
                alert.resolved_by = kwargs.get("resolved_by", "agent")
                alert.resolved_at = now
                alert.note = note
                session.add(alert)
                await session.flush()

                return {
                    "data": {"affected": 1, "alert_id": alert_id, "resolution": resolution},
                    "hint": f"告警 {alert_id} 已标记为 {resolution}。",
                }
            else:
                # Batch by rule_id
                stmt = select(Alert).where(Alert.rule_id == rule_id)

                if time_range:
                    cutoff = now - _TIME_RANGE_MAP[time_range]
                    stmt = stmt.where(Alert.created_at >= cutoff)

                result = await session.execute(stmt)
                alerts = result.scalars().all()

                count = 0
                for a in alerts:
                    a.resolution = resolution
                    a.resolved_by = kwargs.get("resolved_by", "agent")
                    a.resolved_at = now
                    a.note = note
                    session.add(a)
                    count += 1

                await session.flush()

                time_desc = f"最近 {time_range} " if time_range else ""
                return {
                    "data": {"affected": count, "rule_id": rule_id, "resolution": resolution},
                    "hint": f"规则 {rule_id} {time_desc}的 {count} 条告警已标记为 {resolution}。",
                }

    except Exception as e:
        logger.error("handle_alert failed: {}", e)
        return {"error": f"处理告警失败: {e}", "hint": "请稍后重试。"}


async def get_alert_stats(**kwargs: Any) -> dict:
    """获取告警处理率统计。"""
    time_range = kwargs.get("time_range", "7d")
    rule_id = kwargs.get("rule_id")

    # Validate time_range
    if time_range not in _TIME_RANGE_MAP:
        return {
            "error": f"无效的 time_range '{time_range}'。",
            "hint": f"可选值：{list(_TIME_RANGE_MAP.keys())}",
        }

    cutoff = datetime.now(timezone.utc) - _TIME_RANGE_MAP[time_range]

    try:
        async with get_session() as session:
            # Base filter
            base_filter = [Alert.created_at >= cutoff]
            if rule_id:
                base_filter.append(Alert.rule_id == rule_id)

            # Total count
            total_stmt = select(func.count(Alert.id)).where(*base_filter)
            total_result = await session.execute(total_stmt)
            total = total_result.scalar() or 0

            # By severity
            sev_stmt = (
                select(Alert.severity, func.count(Alert.id))
                .where(*base_filter)
                .group_by(Alert.severity)
            )
            sev_result = await session.execute(sev_stmt)
            by_severity = {row[0]: row[1] for row in sev_result.all()}

            # By resolution
            res_stmt = (
                select(Alert.resolution, func.count(Alert.id))
                .where(*base_filter)
                .group_by(Alert.resolution)
            )
            res_result = await session.execute(res_stmt)
            by_resolution_raw = {
                (row[0] or "unresolved"): row[1] for row in res_result.all()
            }

            # Unresolved count
            unresolved_count = by_resolution_raw.get("unresolved", 0)

            # Resolution rate
            resolved_count = total - unresolved_count
            resolution_rate = round(resolved_count / total * 100, 1) if total > 0 else 0.0

            # Average resolution time (only for resolved alerts)
            avg_hours: float | None = None
            if resolved_count > 0:
                avg_stmt = (
                    select(Alert.created_at, Alert.resolved_at)
                    .where(*base_filter, Alert.resolution.isnot(None))
                )
                avg_result = await session.execute(avg_stmt)
                durations = []
                for row in avg_result.all():
                    created = row[0]
                    resolved = row[1]
                    if resolved and created:
                        diff = (resolved - created).total_seconds() / 3600
                        durations.append(diff)
                if durations:
                    avg_hours = round(sum(durations) / len(durations), 1)

            # Top rules (top 5 by alert count)
            top_stmt = (
                select(Alert.rule_id, func.count(Alert.id).label("cnt"))
                .where(*base_filter)
                .group_by(Alert.rule_id)
                .order_by(func.count(Alert.id).desc())
                .limit(5)
            )
            top_result = await session.execute(top_stmt)
            top_rules_raw = top_result.all()

            # Resolve rule names
            top_rules = []
            for row in top_rules_raw:
                rid = row[0]
                cnt = row[1]
                rule = await session.get(AlertRule, rid)
                top_rules.append({
                    "rule_id": rid,
                    "rule_name": rule.name if rule else rid,
                    "count": cnt,
                })

            stats = {
                "total": total,
                "by_severity": by_severity,
                "by_resolution": by_resolution_raw,
                "unresolved_count": unresolved_count,
                "resolution_rate": resolution_rate,
                "avg_resolution_time_hours": avg_hours,
                "top_rules": top_rules,
            }

            # Build hint
            hint_parts = [f"最近 {time_range} 共 {total} 条告警"]
            if total > 0:
                hint_parts.append(f"处理率 {resolution_rate}%")
                hint_parts.append(f"未处理 {unresolved_count} 条")
            hint = "，".join(hint_parts) + "。"

            return {"data": stats, "hint": hint}

    except Exception as e:
        logger.error("get_alert_stats failed: {}", e)
        return {"error": f"统计查询失败: {e}", "hint": "请稍后重试。"}


TOOL_EXECUTORS: dict[str, Any] = {
    "list_alerts": list_alerts,
    "handle_alert": handle_alert,
    "get_alert_stats": get_alert_stats,
}
