"""Rule management tools — 6 tools for Agent to manage alert rules."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter
from loguru import logger
from sqlalchemy import select, func, case

from order_guard.mcp.models import ToolInfo
from order_guard.models import Alert, AlertRule, TaskRun
from order_guard.storage.crud import create, get_by_id, list_all, update
from order_guard.storage.database import get_session


# ---------------------------------------------------------------------------
# Human-readable cron description
# ---------------------------------------------------------------------------

_CRON_DESCRIPTIONS: dict[str, str] = {
    "0 * * * *": "每小时整点",
    "*/30 * * * *": "每30分钟",
    "0 9 * * *": "每天 9:00",
    "0 9 * * 1-5": "工作日 9:00",
    "0 9,18 * * *": "每天 9:00 和 18:00",
    "0 0 * * *": "每天 0:00",
    "0 0 * * 1": "每周一 0:00",
    "0 0 1 * *": "每月1号 0:00",
}


def _describe_cron(expr: str) -> str:
    """Convert cron expression to human-readable Chinese."""
    if expr in _CRON_DESCRIPTIONS:
        return _CRON_DESCRIPTIONS[expr]

    parts = expr.strip().split()
    if len(parts) != 5:
        return expr

    minute, hour, dom, month, dow = parts

    # Simple cases
    if dom == "*" and month == "*" and dow == "*":
        if hour == "*":
            if minute == "0":
                return "每小时整点"
            if minute.startswith("*/"):
                return f"每{minute[2:]}分钟"
            return f"每小时第{minute}分"
        if minute == "0":
            if hour.startswith("*/"):
                return f"每{hour[2:]}小时"
            return f"每天 {hour}:00"
        return f"每天 {hour}:{minute.zfill(2)}"

    return expr


# ---------------------------------------------------------------------------
# Tool Schema definitions
# ---------------------------------------------------------------------------

TOOL_LIST_RULES = ToolInfo(
    name="list_rules",
    description=(
        "列出所有已配置的监控规则。返回规则名称、数据源、执行频率、启用状态、"
        "上次运行时间、最近24小时告警数。"
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    server_name="rule_tools",
)

_RULE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "规则名称，如 '库存低于安全线检查'",
        },
        "mcp_server": {
            "type": "string",
            "description": "数据源 ID，从 list_datasources 获取",
        },
        "prompt_template": {
            "type": "string",
            "description": "分析 prompt 模板，包含具体的 SQL 查询逻辑和分析要求",
        },
        "schedule": {
            "type": "string",
            "description": "cron 表达式，如 '0 9 * * *'（每天9点）、'0 */2 * * *'（每2小时）",
        },
        "data_window": {
            "type": "string",
            "description": "数据时间窗口，如 '7d'、'24h'、'30d'。默认 '7d'",
        },
        "enabled": {
            "type": "boolean",
            "description": "是否立即启用。默认 true",
        },
    },
    "required": ["name", "mcp_server", "prompt_template", "schedule"],
}

TOOL_CREATE_RULE = ToolInfo(
    name="create_rule",
    description=(
        "创建监控规则。支持单条或批量创建。"
        "单条创建：直接传 name/mcp_server/prompt_template/schedule 等字段。"
        "批量创建：传 rules 数组，每个元素包含上述字段，一次调用创建全部。"
        "创建前请先用 list_datasources 和 get_schema 了解可用的数据源和表结构。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_RULE_ITEM_SCHEMA["properties"],
            "rules": {
                "type": "array",
                "description": "批量创建时传入规则列表。与单条参数互斥，优先使用 rules。",
                "items": _RULE_ITEM_SCHEMA,
            },
        },
        "required": [],
    },
    server_name="rule_tools",
)

TOOL_UPDATE_RULE = ToolInfo(
    name="update_rule",
    description=(
        "修改已有监控规则。只需传入要修改的字段。修改前请先用 list_rules 确认规则 ID。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "规则 ID，从 list_rules 获取",
            },
            "changes": {
                "type": "object",
                "description": '要修改的字段，如 {"schedule": "0 */2 * * *", "enabled": false}',
                "properties": {
                    "name": {"type": "string"},
                    "mcp_server": {"type": "string"},
                    "prompt_template": {"type": "string"},
                    "schedule": {"type": "string"},
                    "data_window": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
            },
        },
        "required": ["rule_id", "changes"],
    },
    server_name="rule_tools",
)

TOOL_DELETE_RULE = ToolInfo(
    name="delete_rule",
    description="删除监控规则并移除其定时任务。删除后不可恢复。",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["single", "all"],
                "description": "删除范围：single=删除指定规则，all=删除全部规则",
            },
            "rule_id": {
                "type": "string",
                "description": "规则 ID（scope=single 时必填），从 list_rules 获取",
            },
        },
        "required": ["scope"],
    },
    server_name="rule_tools",
)

TOOL_TEST_RULE = ToolInfo(
    name="test_rule",
    description="试运行一条规则，执行数据分析但不推送告警。用于验证规则是否正确配置。",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "规则 ID，从 list_rules 获取",
            },
        },
        "required": ["rule_id"],
    },
    server_name="rule_tools",
)

TOOL_GET_RULE_STATS = ToolInfo(
    name="get_rule_stats",
    description=(
        "获取单条规则的详细运行统计。包括执行成功率、告警分布、误报率、每日趋势、"
        "LLM Token 消耗等。用于评估规则效果和优化规则配置。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "规则 ID，从 list_rules 获取",
            },
            "time_range": {
                "type": "string",
                "enum": ["7d", "30d", "90d"],
                "description": "统计时间范围，默认 30d",
            },
        },
        "required": ["rule_id"],
    },
    server_name="rule_tools",
)

TOOL_DEFINITIONS = [
    TOOL_LIST_RULES,
    TOOL_CREATE_RULE,
    TOOL_UPDATE_RULE,
    TOOL_DELETE_RULE,
    TOOL_TEST_RULE,
    TOOL_GET_RULE_STATS,
]


# ---------------------------------------------------------------------------
# Tool executor functions
# ---------------------------------------------------------------------------

# These will be set externally by the Agent/main setup
_UNSET = object()
_scheduler = None
_data_access_layer = None
_mcp_manager = None


def configure(
    scheduler: Any = _UNSET,
    data_access_layer: Any = _UNSET,
    mcp_manager: Any = _UNSET,
) -> None:
    """Configure external dependencies for rule tools."""
    global _scheduler, _data_access_layer, _mcp_manager
    if scheduler is not _UNSET:
        _scheduler = scheduler
    if data_access_layer is not _UNSET:
        _data_access_layer = data_access_layer
    if mcp_manager is not _UNSET:
        _mcp_manager = mcp_manager


async def list_rules(**kwargs: Any) -> dict:
    """列出所有已配置的监控规则，附带效果评估指标。"""
    try:
        async with get_session() as session:
            rules = await list_all(session, AlertRule, order_by="created_at", limit=100)

            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(hours=24)
            week_ago = now - timedelta(days=7)

            items = []
            hint_warnings: list[str] = []

            for rule in rules:
                # Count alerts in last 24h
                stmt_24h = (
                    select(func.count())
                    .select_from(Alert)
                    .where(Alert.rule_id == rule.id)
                    .where(Alert.created_at >= day_ago)
                )
                result = await session.execute(stmt_24h)
                alerts_24h = result.scalar() or 0

                # Count alerts in last 7d
                stmt_7d = (
                    select(func.count())
                    .select_from(Alert)
                    .where(Alert.rule_id == rule.id)
                    .where(Alert.created_at >= week_ago)
                )
                result = await session.execute(stmt_7d)
                trigger_count_7d = result.scalar() or 0

                # Count false positives in last 7d
                stmt_fp = (
                    select(func.count())
                    .select_from(Alert)
                    .where(Alert.rule_id == rule.id)
                    .where(Alert.created_at >= week_ago)
                    .where(Alert.resolution == "false_positive")
                )
                result = await session.execute(stmt_fp)
                false_positive_count_7d = result.scalar() or 0

                false_positive_rate = round(
                    false_positive_count_7d / trigger_count_7d, 2
                ) if trigger_count_7d > 0 else 0.0

                # Last triggered at
                stmt_last = (
                    select(Alert.created_at)
                    .where(Alert.rule_id == rule.id)
                    .order_by(Alert.created_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt_last)
                last_triggered_row = result.scalar()
                last_triggered_at = (
                    last_triggered_row.isoformat() if last_triggered_row else None
                )

                # TaskRun stats in last 7d
                stmt_runs = (
                    select(func.count())
                    .select_from(TaskRun)
                    .where(TaskRun.rule_id == rule.id)
                    .where(TaskRun.started_at >= week_ago)
                )
                result = await session.execute(stmt_runs)
                run_count_7d = result.scalar() or 0

                stmt_success_runs = (
                    select(func.count())
                    .select_from(TaskRun)
                    .where(TaskRun.rule_id == rule.id)
                    .where(TaskRun.started_at >= week_ago)
                    .where(TaskRun.status == "success")
                )
                result = await session.execute(stmt_success_runs)
                success_runs_7d = result.scalar() or 0

                run_success_rate = round(
                    success_runs_7d / run_count_7d, 2
                ) if run_count_7d > 0 else 1.0

                # Last task run
                stmt_last_run = (
                    select(TaskRun)
                    .where(TaskRun.rule_id == rule.id)
                    .order_by(TaskRun.started_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt_last_run)
                last_run = result.scalars().first()

                items.append({
                    "id": rule.id,
                    "name": rule.name,
                    "datasource": rule.mcp_server,
                    "schedule": rule.schedule,
                    "schedule_human": _describe_cron(rule.schedule) if rule.schedule else "未设置",
                    "enabled": rule.enabled,
                    "last_run": last_run.started_at.isoformat() if last_run else None,
                    "last_run_status": last_run.status if last_run else None,
                    "alerts_24h": alerts_24h,
                    "trigger_count_7d": trigger_count_7d,
                    "false_positive_count_7d": false_positive_count_7d,
                    "false_positive_rate": false_positive_rate,
                    "last_triggered_at": last_triggered_at,
                    "run_count_7d": run_count_7d,
                    "run_success_rate": run_success_rate,
                })

                # Smart hints per rule
                if false_positive_rate > 0.3:
                    hint_warnings.append(
                        f"⚠ 规则「{rule.name}」误报率 {false_positive_rate:.0%}，建议调整阈值或 prompt"
                    )
                if trigger_count_7d == 0 and rule.enabled and last_triggered_at is None:
                    hint_warnings.append(
                        f"💤 规则「{rule.name}」7 天内无触发，请确认是否仍需保留"
                    )
                elif trigger_count_7d == 0 and rule.enabled:
                    # Has triggered before but not in 7d
                    hint_warnings.append(
                        f"💤 规则「{rule.name}」最近 7 天无触发，请确认是否仍需保留"
                    )
                if run_success_rate < 0.9 and run_count_7d > 0:
                    hint_warnings.append(
                        f"⚠ 规则「{rule.name}」执行成功率 {run_success_rate:.0%}，建议检查数据源连接"
                    )

            disabled_count = sum(1 for i in items if not i["enabled"])
            if items:
                hint = f"共 {len(items)} 条规则"
                if disabled_count:
                    hint += f"，其中 {disabled_count} 条已禁用"
                if hint_warnings:
                    hint += "。\n" + "\n".join(hint_warnings)
                else:
                    hint += "，运行状态正常。可以创建新规则或修改已有规则。"
            else:
                hint = "暂无监控规则。可以使用 create_rule 创建新规则。"

            return {"data": items, "hint": hint}

    except Exception as e:
        logger.error("list_rules failed: {}", e)
        return {"error": f"查询规则列表失败: {e}", "hint": "请稍后重试。"}


async def create_rule(**kwargs: Any) -> dict:
    """创建监控规则，支持单条或批量（传 rules 数组）。"""
    # Determine single vs batch mode
    rules_input = kwargs.get("rules")
    if rules_input is not None:
        # Batch mode
        return await _create_rules_batch(rules_input)
    else:
        # Single mode — wrap as single-item batch
        return await _create_rules_batch([kwargs])


async def _create_rules_batch(rules_input: list[dict[str, Any]]) -> dict:
    """Internal: create one or more rules."""
    if not rules_input:
        return {"error": "请提供至少一条规则。", "hint": "需要 name/mcp_server/prompt_template/schedule。"}

    # Pre-load existing names for uniqueness check
    existing_names: set[str] = set()
    try:
        async with get_session() as session:
            existing = await list_all(session, AlertRule, limit=500)
            existing_names = {r.name for r in existing}
    except Exception as e:
        logger.warning("Name uniqueness pre-check failed: {}", e)

    # Pre-load available datasources
    available_ds: list[str] | None = None
    if _data_access_layer is not None:
        available_ds = _data_access_layer.list_datasource_ids()

    created: list[dict] = []
    failed: list[dict] = []

    for idx, rule_input in enumerate(rules_input):
        name = (rule_input.get("name") or "").strip()
        mcp_server = (rule_input.get("mcp_server") or "").strip()
        prompt_template = (rule_input.get("prompt_template") or "").strip()
        schedule = (rule_input.get("schedule") or "").strip()
        data_window = (rule_input.get("data_window") or "7d").strip()
        enabled = rule_input.get("enabled", True)

        # Validate
        error = None
        if not name:
            error = "name 不能为空"
        elif not mcp_server:
            error = "mcp_server 不能为空"
        elif not prompt_template:
            error = "prompt_template 不能为空"
        elif not schedule:
            error = "schedule 不能为空"
        elif not croniter.is_valid(schedule):
            error = f"schedule '{schedule}' 不是合法的 cron 表达式"
        elif available_ds is not None and mcp_server not in available_ds:
            error = f"数据源 '{mcp_server}' 不存在，可用: {available_ds}"
        elif name in existing_names:
            error = f"规则名称 '{name}' 已存在"

        if error:
            failed.append({"index": idx, "name": name or f"rule_{idx}", "error": error})
            continue

        # Create
        try:
            rule_id = f"chat-{uuid.uuid4().hex[:8]}"
            async with get_session() as session:
                rule = AlertRule(
                    id=rule_id,
                    name=name,
                    mcp_server=mcp_server,
                    prompt_template=prompt_template,
                    schedule=schedule,
                    data_window=data_window,
                    source="chat",
                    enabled=enabled,
                )
                rule = await create(session, rule)

            if enabled and _scheduler is not None:
                _register_rule_schedule(rule_id, schedule)

            existing_names.add(name)  # Prevent duplicates within batch
            created.append({
                "id": rule.id,
                "name": rule.name,
                "datasource": rule.mcp_server,
                "schedule": schedule,
                "schedule_human": _describe_cron(schedule),
                "data_window": data_window,
                "enabled": enabled,
            })
        except Exception as e:
            logger.error("create_rule: failed to create '{}': {}", name, e)
            failed.append({"index": idx, "name": name, "error": str(e)})

    # Single rule response (backward compatible)
    if len(rules_input) == 1 and len(created) == 1:
        return {
            "data": created[0],
            "hint": f"规则已创建（{created[0]['schedule_human']}执行）。建议使用 test_rule 试运行验证。",
        }
    if len(rules_input) == 1 and failed:
        return {"error": failed[0]["error"], "hint": "请检查参数后重试。"}

    # Batch response
    hint_parts = [f"批量创建完成：成功 {len(created)} 条"]
    if failed:
        hint_parts.append(f"失败 {len(failed)} 条")
    hint = "，".join(hint_parts) + "。"

    return {
        "data": {
            "created": created,
            "created_count": len(created),
            "failed": failed,
            "failed_count": len(failed),
        },
        "hint": hint,
    }


async def update_rule(**kwargs: Any) -> dict:
    """修改已有规则，只传需要改的字段。"""
    rule_id = kwargs.get("rule_id", "")
    changes = kwargs.get("changes", {})

    if not rule_id:
        return {"error": "rule_id 不能为空。", "hint": "请先用 list_rules 获取规则 ID。"}
    if not changes:
        return {"error": "changes 不能为空。", "hint": "请提供要修改的字段。"}

    # Validate schedule if being changed
    new_schedule = changes.get("schedule")
    if new_schedule is not None and not croniter.is_valid(new_schedule):
        return {
            "error": f"schedule '{new_schedule}' 不是合法的 cron 表达式。",
            "hint": "cron 表达式需要 5 个字段：分 时 日 月 周。示例：'0 9 * * *' 表示每天9点。",
        }

    # Validate mcp_server if being changed
    new_server = changes.get("mcp_server")
    if new_server is not None and _data_access_layer is not None:
        available = _data_access_layer.list_datasource_ids()
        if new_server not in available:
            return {
                "error": f"数据源 '{new_server}' 不存在。",
                "hint": f"可用的数据源：{available}。",
            }

    try:
        async with get_session() as session:
            rule = await get_by_id(session, AlertRule, rule_id)
            if rule is None:
                return {
                    "error": f"规则 ID '{rule_id}' 不存在。",
                    "hint": "请使用 list_rules 查看所有规则及其 ID。",
                }

            old_schedule = rule.schedule
            old_enabled = rule.enabled

            # Apply changes
            allowed_fields = {"name", "mcp_server", "prompt_template", "schedule", "data_window", "enabled"}
            update_kwargs = {k: v for k, v in changes.items() if k in allowed_fields}
            rule = await update(session, rule, **update_kwargs)

            # Handle scheduler sync
            new_enabled = changes.get("enabled", old_enabled)
            effective_schedule = changes.get("schedule", old_schedule)

            if _scheduler is not None:
                if not new_enabled:
                    # Disabled → remove schedule
                    _remove_rule_schedule(rule_id)
                elif new_schedule or (new_enabled and not old_enabled):
                    # Schedule changed or re-enabled → re-register
                    _remove_rule_schedule(rule_id)
                    if effective_schedule:
                        _register_rule_schedule(rule_id, effective_schedule)

            changed_fields = ", ".join(f"{k}={v}" for k, v in update_kwargs.items())
            return {
                "data": {
                    "id": rule.id,
                    "name": rule.name,
                    "changes": update_kwargs,
                },
                "hint": f"规则已更新: {changed_fields}。",
            }

    except Exception as e:
        logger.error("update_rule failed: {}", e)
        return {"error": f"更新规则失败: {e}", "hint": "请检查参数后重试。"}


async def delete_rule(**kwargs: Any) -> dict:
    """删除监控规则。通过 scope 控制删除范围。"""
    scope = kwargs.get("scope", "single").strip().lower()
    rule_id = kwargs.get("rule_id", "").strip()

    if scope not in ("single", "all"):
        return {"error": f"scope 必须是 'single' 或 'all'，收到: '{scope}'", "hint": "single=删除指定规则，all=删除全部规则。"}

    if scope == "single" and not rule_id:
        return {"error": "scope=single 时 rule_id 不能为空。", "hint": "请先用 list_rules 获取规则 ID。"}

    try:
        # Determine target rule IDs
        if scope == "all":
            async with get_session() as session:
                all_rules = await list_all(session, AlertRule, limit=500)
                if not all_rules:
                    return {"data": {"deleted_count": 0}, "hint": "当前没有任何规则。"}
                target_ids = [r.id for r in all_rules]
        else:
            target_ids = [rule_id]

        # Delete each rule
        deleted = []
        not_found = []
        has_yaml = False

        for rid in target_ids:
            async with get_session() as session:
                rule = await get_by_id(session, AlertRule, rid)
                if rule is None:
                    not_found.append(rid)
                    continue

                deleted.append({"id": rid, "name": rule.name})
                if rule.source == "yaml":
                    has_yaml = True
                await session.delete(rule)
                await session.flush()

            # Remove from scheduler
            if _scheduler is not None:
                _remove_rule_schedule(rid)

        # Build response
        hint_parts = [f"已删除 {len(deleted)} 条规则。"]
        if not_found:
            hint_parts.append(f"未找到 {len(not_found)} 个 ID: {', '.join(not_found)}。")
        if has_yaml:
            hint_parts.append("注意：部分规则来源于 YAML 配置，重启后会重新同步。如需永久删除，请同时修改 rules.yaml。")

        return {
            "data": {
                "deleted": deleted,
                "deleted_count": len(deleted),
                "not_found": not_found,
            },
            "hint": " ".join(hint_parts),
        }

    except Exception as e:
        logger.error("delete_rule failed: {}", e)
        return {"error": f"删除规则失败: {e}", "hint": "请稍后重试。"}


async def test_rule(**kwargs: Any) -> dict:
    """试运行规则，不推送告警。"""
    rule_id = kwargs.get("rule_id", "")
    if not rule_id:
        return {"error": "rule_id 不能为空。", "hint": "请先用 list_rules 获取规则 ID。"}

    try:
        async with get_session() as session:
            rule = await get_by_id(session, AlertRule, rule_id)
            if rule is None:
                return {
                    "error": f"规则 ID '{rule_id}' 不存在。",
                    "hint": "请使用 list_rules 查看所有规则及其 ID。",
                }

        # Run the pipeline in dry-run mode
        from order_guard.engine.llm_client import LLMClient
        from order_guard.engine.agent import Agent

        start_time = time.monotonic()

        if _data_access_layer is not None:
            agent = Agent(
                llm_client=LLMClient(),
                data_access_layer=_data_access_layer,
                data_window=rule.data_window or "",
                rule_id=rule.id,
            )
        elif _mcp_manager is not None:
            mcp_conn = _mcp_manager.get_connection(rule.mcp_server)
            if not mcp_conn.is_connected():
                await mcp_conn.connect()
            agent = Agent(
                llm_client=LLMClient(),
                mcp_connection=mcp_conn,
                data_window=rule.data_window or "",
                rule_id=rule.id,
            )
        else:
            return {
                "error": "没有可用的数据访问后端。",
                "hint": "请检查数据源配置。",
            }

        result = await agent.run(rule.prompt_template)
        duration_ms = int((time.monotonic() - start_time) * 1000)

        alerts_data = []
        for a in result.alerts:
            alerts_data.append({
                "sku": a.sku,
                "severity": a.severity,
                "title": a.title,
                "reason": a.reason,
                "suggestion": a.suggestion,
            })

        return {
            "data": {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "alerts_found": len(alerts_data),
                "alerts": alerts_data,
                "summary": result.summary,
                "duration_ms": duration_ms,
            },
            "hint": (
                f"试运行完成，发现 {len(alerts_data)} 条告警（未推送）。"
                if alerts_data
                else "试运行完成，未发现异常。"
            ),
        }

    except Exception as e:
        logger.error("test_rule failed: {}", e)
        return {"error": f"试运行失败: {e}", "hint": "请检查规则配置和数据源连接。"}


async def get_rule_stats(**kwargs: Any) -> dict:
    """获取单条规则的详细运行统计。"""
    rule_id = kwargs.get("rule_id", "")
    time_range = kwargs.get("time_range", "30d")

    if not rule_id:
        return {"error": "rule_id 不能为空。", "hint": "请先用 list_rules 获取规则 ID。"}

    _TIME_RANGE_MAP = {"7d": 7, "30d": 30, "90d": 90}
    if time_range not in _TIME_RANGE_MAP:
        return {
            "error": f"无效的 time_range '{time_range}'。",
            "hint": "可选值：7d, 30d, 90d",
        }

    days = _TIME_RANGE_MAP[time_range]

    try:
        async with get_session() as session:
            rule = await get_by_id(session, AlertRule, rule_id)
            if rule is None:
                return {
                    "error": f"规则 ID '{rule_id}' 不存在。",
                    "hint": "请使用 list_rules 查看所有规则及其 ID。",
                }

            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(days=days)

            # --- basic ---
            basic = {
                "name": rule.name,
                "schedule": rule.schedule,
                "schedule_human": _describe_cron(rule.schedule) if rule.schedule else "未设置",
                "enabled": rule.enabled,
                "created_at": rule.created_at.isoformat(),
                "source": rule.source,
            }

            # --- execution ---
            stmt_total = (
                select(func.count())
                .select_from(TaskRun)
                .where(TaskRun.rule_id == rule_id)
                .where(TaskRun.started_at >= cutoff)
            )
            result = await session.execute(stmt_total)
            total_runs = result.scalar() or 0

            stmt_success = (
                select(func.count())
                .select_from(TaskRun)
                .where(TaskRun.rule_id == rule_id)
                .where(TaskRun.started_at >= cutoff)
                .where(TaskRun.status == "success")
            )
            result = await session.execute(stmt_success)
            success_runs = result.scalar() or 0

            failed_runs = total_runs - success_runs
            success_rate = round(success_runs / total_runs, 2) if total_runs > 0 else 1.0

            stmt_avg_dur = (
                select(func.avg(TaskRun.duration_ms))
                .where(TaskRun.rule_id == rule_id)
                .where(TaskRun.started_at >= cutoff)
                .where(TaskRun.duration_ms.isnot(None))
            )
            result = await session.execute(stmt_avg_dur)
            avg_duration_ms = result.scalar()
            avg_duration_ms = round(avg_duration_ms) if avg_duration_ms is not None else None

            execution = {
                "total_runs": total_runs,
                "success_runs": success_runs,
                "failed_runs": failed_runs,
                "success_rate": success_rate,
                "avg_duration_ms": avg_duration_ms,
            }

            # --- alerts ---
            stmt_alerts_total = (
                select(func.count())
                .select_from(Alert)
                .where(Alert.rule_id == rule_id)
                .where(Alert.created_at >= cutoff)
            )
            result = await session.execute(stmt_alerts_total)
            total_alerts = result.scalar() or 0

            # By severity
            stmt_sev = (
                select(Alert.severity, func.count(Alert.id))
                .where(Alert.rule_id == rule_id)
                .where(Alert.created_at >= cutoff)
                .group_by(Alert.severity)
            )
            result = await session.execute(stmt_sev)
            by_severity = {row[0]: row[1] for row in result.all()}

            # By resolution
            stmt_res = (
                select(Alert.resolution, func.count(Alert.id))
                .where(Alert.rule_id == rule_id)
                .where(Alert.created_at >= cutoff)
                .group_by(Alert.resolution)
            )
            result = await session.execute(stmt_res)
            by_resolution = {
                (row[0] or "unresolved"): row[1] for row in result.all()
            }

            fp_count = by_resolution.get("false_positive", 0)
            false_positive_rate = round(fp_count / total_alerts, 2) if total_alerts > 0 else 0.0

            alerts_stats = {
                "total_alerts": total_alerts,
                "by_severity": by_severity,
                "by_resolution": by_resolution,
                "false_positive_rate": false_positive_rate,
            }

            # --- trend: daily alert counts ---
            trend = []
            for d in range(days):
                day_start = (now - timedelta(days=days - 1 - d)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                day_end = day_start + timedelta(days=1)
                stmt_day = (
                    select(func.count())
                    .select_from(Alert)
                    .where(Alert.rule_id == rule_id)
                    .where(Alert.created_at >= day_start)
                    .where(Alert.created_at < day_end)
                )
                result = await session.execute(stmt_day)
                count = result.scalar() or 0
                trend.append({
                    "date": day_start.strftime("%Y-%m-%d"),
                    "count": count,
                })

            # --- token_usage (from LLMUsageLog) ---
            token_usage_data: dict[str, Any] | None = None
            try:
                from order_guard.models import LLMUsageLog

                stmt_tokens = (
                    select(
                        func.sum(LLMUsageLog.total_tokens),
                        func.sum(LLMUsageLog.cost_estimate_usd),
                    )
                    .where(LLMUsageLog.rule_id == rule_id)
                    .where(LLMUsageLog.created_at >= cutoff)
                )
                result = await session.execute(stmt_tokens)
                row = result.one()
                if row[0] is not None:
                    token_usage_data = {
                        "total_tokens": int(row[0]),
                        "total_cost_usd": round(float(row[1] or 0), 4),
                    }
            except Exception:
                pass  # LLMUsageLog may not exist in some environments

            data = {
                "basic": basic,
                "execution": execution,
                "alerts": alerts_stats,
                "trend": trend,
                "token_usage": token_usage_data,
            }

            # Build hint
            hint_parts = [f"规则「{rule.name}」最近 {time_range} 统计"]
            if total_runs > 0:
                hint_parts.append(f"执行 {total_runs} 次（成功率 {success_rate:.0%}）")
            if total_alerts > 0:
                hint_parts.append(f"产生 {total_alerts} 条告警")
                if false_positive_rate > 0.3:
                    hint_parts.append(f"误报率 {false_positive_rate:.0%}，建议调整规则")
            else:
                hint_parts.append("无告警产生")
            hint = "，".join(hint_parts) + "。"

            return {"data": data, "hint": hint}

    except Exception as e:
        logger.error("get_rule_stats failed: {}", e)
        return {"error": f"查询规则统计失败: {e}", "hint": "请稍后重试。"}


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------

def _register_rule_schedule(rule_id: str, schedule: str) -> None:
    """Register a rule's cron schedule with APScheduler."""
    if _scheduler is None:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger
        from order_guard.scheduler.jobs import run_detection_job

        trigger = CronTrigger.from_crontab(schedule)
        job_id = f"rule_{rule_id}"

        kwargs: dict[str, Any] = {
            "rule_id": rule_id,
            "job_name": f"rule-{rule_id}",
            "rule_manager": _get_rule_manager(),
            "analyzer": _get_analyzer(),
            "dispatcher": _get_dispatcher(),
        }
        if _data_access_layer is not None:
            kwargs["data_access_layer"] = _data_access_layer

        _scheduler.add_schedule(
            run_detection_job,
            trigger,
            id=job_id,
            kwargs=kwargs,
        )
        logger.info("Registered schedule for rule {}: {}", rule_id, schedule)
    except Exception as e:
        logger.error("Failed to register schedule for rule {}: {}", rule_id, e)


def _remove_rule_schedule(rule_id: str) -> None:
    """Remove a rule's schedule from APScheduler."""
    if _scheduler is None:
        return
    try:
        job_id = f"rule_{rule_id}"
        _scheduler.remove_schedule(job_id)
        logger.info("Removed schedule for rule {}", rule_id)
    except Exception as e:
        logger.debug("Failed to remove schedule for rule {} (may not exist): {}", rule_id, e)


def _get_rule_manager() -> Any:
    from order_guard.engine.rules import RuleManager
    return RuleManager()


def _get_analyzer() -> Any:
    from order_guard.engine.analyzer import Analyzer
    return Analyzer()


def _get_dispatcher() -> Any:
    from order_guard.alerts.dispatcher import AlertDispatcher
    return AlertDispatcher()


# ---------------------------------------------------------------------------
# Tool executors mapping
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: dict[str, Any] = {
    "list_rules": list_rules,
    "create_rule": create_rule,
    "update_rule": update_rule,
    "delete_rule": delete_rule,
    "test_rule": test_rule,
    "get_rule_stats": get_rule_stats,
}
