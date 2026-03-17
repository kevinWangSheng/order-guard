"""Report management tools — manage_report + preview_report for Agent."""

from __future__ import annotations

import time
from typing import Any

from croniter import croniter
from loguru import logger

from order_guard.mcp.models import ToolInfo
from order_guard.models import ReportConfig
from order_guard.storage.crud import get_by_id, list_all, update
from order_guard.storage.database import get_session


# ---------------------------------------------------------------------------
# Tool Schema definitions
# ---------------------------------------------------------------------------

TOOL_MANAGE_REPORT = ToolInfo(
    name="manage_report",
    description=(
        "管理报告配置。支持三种操作：\n"
        "- action='list': 列出所有报告配置\n"
        "- action='get': 获取单个报告详情（需要 report_id）\n"
        "- action='update': 更新报告配置（需要 report_id 和 changes）"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "update"],
                "description": "操作类型：list=列出所有报告，get=获取单个报告，update=更新报告配置",
            },
            "report_id": {
                "type": "string",
                "description": "报告 ID（action=get/update 时必填）",
            },
            "changes": {
                "type": "object",
                "description": "要修改的字段（action=update 时使用）",
                "properties": {
                    "name": {"type": "string"},
                    "schedule": {"type": "string", "description": "cron 表达式"},
                    "focus": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "template_style": {
                        "type": "string",
                        "enum": ["standard", "brief", "detailed"],
                    },
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "prompt": {"type": "string"},
                                "datasource": {"type": "string"},
                            },
                        },
                        "description": '章节列表，如 [{"title": "销售概况", "prompt": "统计总销售额", "datasource": "erp_mysql"}]',
                    },
                    "kpis": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "sql": {"type": "string"},
                                "format": {
                                    "type": "string",
                                    "enum": ["number", "currency", "percent"],
                                },
                            },
                        },
                        "description": '关键指标列表，如 [{"name": "总销售额", "sql": "SELECT SUM(amount)...", "format": "currency"}]',
                    },
                },
            },
        },
        "required": ["action"],
    },
    server_name="report_tools",
)

TOOL_PREVIEW_REPORT = ToolInfo(
    name="preview_report",
    description="预览报告内容，按当前配置生成但不推送。用于验证报告配置是否正确。",
    input_schema={
        "type": "object",
        "properties": {
            "report_id": {
                "type": "string",
                "description": "报告 ID",
            },
        },
        "required": ["report_id"],
    },
    server_name="report_tools",
)

TOOL_DEFINITIONS = [TOOL_MANAGE_REPORT, TOOL_PREVIEW_REPORT]


# ---------------------------------------------------------------------------
# External dependencies (set via configure())
# ---------------------------------------------------------------------------

_UNSET = object()
_scheduler = None
_data_access_layer = None
_mcp_manager = None


def configure(
    scheduler: Any = _UNSET,
    data_access_layer: Any = _UNSET,
    mcp_manager: Any = _UNSET,
) -> None:
    """Configure external dependencies for report tools."""
    global _scheduler, _data_access_layer, _mcp_manager
    if scheduler is not _UNSET:
        _scheduler = scheduler
    if data_access_layer is not _UNSET:
        _data_access_layer = data_access_layer
    if mcp_manager is not _UNSET:
        _mcp_manager = mcp_manager


# ---------------------------------------------------------------------------
# manage_report
# ---------------------------------------------------------------------------

async def manage_report(**kwargs: Any) -> dict:
    """Manage report configurations: list / get / update."""
    action = kwargs.get("action", "")
    report_id = kwargs.get("report_id", "")
    changes = kwargs.get("changes", {})

    if action == "list":
        return await _list_reports()
    elif action == "get":
        return await _get_report(report_id)
    elif action == "update":
        return await _update_report(report_id, changes)
    else:
        return {
            "error": f"未知操作: '{action}'",
            "hint": "action 必须是 list / get / update 之一。",
        }


async def _list_reports() -> dict:
    """List all report configurations."""
    try:
        async with get_session() as session:
            reports = await list_all(session, ReportConfig, order_by="created_at", limit=100)

            items = []
            for r in reports:
                items.append({
                    "id": r.id,
                    "name": r.name,
                    "schedule": r.schedule,
                    "template_style": getattr(r, "template_style", "standard"),
                    "sections_count": len(getattr(r, "sections", None) or []),
                    "kpis_count": len(getattr(r, "kpis", None) or []),
                    "enabled": r.enabled,
                })

            if items:
                hint = f"共 {len(items)} 个报告配置。可使用 manage_report(action='get', report_id='...') 查看详情。"
            else:
                hint = "暂无报告配置。"

            return {"data": items, "hint": hint}

    except Exception as e:
        logger.error("list reports failed: {}", e)
        return {"error": f"查询报告列表失败: {e}", "hint": "请稍后重试。"}


async def _get_report(report_id: str) -> dict:
    """Get a single report configuration with full details."""
    if not report_id:
        return {"error": "report_id 不能为空。", "hint": "请先用 manage_report(action='list') 获取报告 ID。"}

    try:
        async with get_session() as session:
            report = await get_by_id(session, ReportConfig, report_id)
            if report is None:
                return {
                    "error": f"报告 ID '{report_id}' 不存在。",
                    "hint": "请使用 manage_report(action='list') 查看所有报告。",
                }

            return {
                "data": {
                    "id": report.id,
                    "name": report.name,
                    "schedule": report.schedule,
                    "mcp_server": report.mcp_server,
                    "focus": report.focus,
                    "channels": report.channels,
                    "template_style": getattr(report, "template_style", "standard"),
                    "sections": getattr(report, "sections", None) or [],
                    "kpis": getattr(report, "kpis", None) or [],
                    "enabled": report.enabled,
                },
                "hint": "可使用 manage_report(action='update') 修改配置，或 preview_report 预览报告。",
            }

    except Exception as e:
        logger.error("get report failed: {}", e)
        return {"error": f"查询报告失败: {e}", "hint": "请稍后重试。"}


async def _update_report(report_id: str, changes: dict[str, Any]) -> dict:
    """Update a report configuration."""
    if not report_id:
        return {"error": "report_id 不能为空。", "hint": "请先用 manage_report(action='list') 获取报告 ID。"}
    if not changes:
        return {"error": "changes 不能为空。", "hint": "请提供要修改的字段。"}

    # Validate schedule if being changed
    new_schedule = changes.get("schedule")
    if new_schedule is not None and not croniter.is_valid(new_schedule):
        return {
            "error": f"schedule '{new_schedule}' 不是合法的 cron 表达式。",
            "hint": "cron 表达式需要 5 个字段：分 时 日 月 周。示例：'0 9 * * *' 表示每天9点。",
        }

    # Validate template_style
    new_style = changes.get("template_style")
    if new_style is not None and new_style not in ("standard", "brief", "detailed"):
        return {
            "error": f"template_style 必须是 standard / brief / detailed 之一，收到: '{new_style}'",
            "hint": "standard=标准报告，brief=简要报告，detailed=详细报告。",
        }

    try:
        async with get_session() as session:
            report = await get_by_id(session, ReportConfig, report_id)
            if report is None:
                return {
                    "error": f"报告 ID '{report_id}' 不存在。",
                    "hint": "请使用 manage_report(action='list') 查看所有报告。",
                }

            old_schedule = report.schedule
            old_enabled = report.enabled

            # Apply changes
            allowed_fields = {"name", "schedule", "focus", "enabled", "template_style", "sections", "kpis"}
            update_kwargs = {k: v for k, v in changes.items() if k in allowed_fields}
            report = await update(session, report, **update_kwargs)

            # Handle scheduler sync
            new_enabled = changes.get("enabled", old_enabled)
            effective_schedule = changes.get("schedule", old_schedule)

            if _scheduler is not None:
                if not new_enabled:
                    _remove_report_schedule(report_id)
                elif new_schedule or (new_enabled and not old_enabled):
                    _remove_report_schedule(report_id)
                    if effective_schedule:
                        _register_report_schedule(report_id, effective_schedule)

            changed_fields = ", ".join(f"{k}={v}" for k, v in update_kwargs.items())
            return {
                "data": {
                    "id": report.id,
                    "name": report.name,
                    "changes": update_kwargs,
                },
                "hint": f"报告已更新: {changed_fields}。",
            }

    except Exception as e:
        logger.error("update report failed: {}", e)
        return {"error": f"更新报告失败: {e}", "hint": "请检查参数后重试。"}


# ---------------------------------------------------------------------------
# preview_report
# ---------------------------------------------------------------------------

async def preview_report(**kwargs: Any) -> dict:
    """Preview a report — generate content but do not push."""
    report_id = kwargs.get("report_id", "")
    if not report_id:
        return {"error": "report_id 不能为空。", "hint": "请先用 manage_report(action='list') 获取报告 ID。"}

    try:
        async with get_session() as session:
            report = await get_by_id(session, ReportConfig, report_id)
            if report is None:
                return {
                    "error": f"报告 ID '{report_id}' 不存在。",
                    "hint": "请使用 manage_report(action='list') 查看所有报告。",
                }

        from order_guard.engine.reporter import generate_report as _generate

        start = time.time()
        result = await _generate(
            report,
            data_access_layer=_data_access_layer,
            mcp_manager=_mcp_manager,
        )
        duration_ms = int((time.time() - start) * 1000)

        if result["status"] == "success":
            return {
                "data": {
                    "report_id": report_id,
                    "report_name": report.name,
                    "content": result["content"],
                    "token_usage": result["token_usage"],
                    "duration_ms": duration_ms,
                },
                "hint": "报告预览已生成（未推送）。如需修改内容，请调整 sections 或 focus 后重新预览。",
            }
        else:
            return {
                "error": f"报告生成失败: {result['error']}",
                "hint": "请检查报告配置和数据源连接。",
            }

    except Exception as e:
        logger.error("preview_report failed: {}", e)
        return {"error": f"预览失败: {e}", "hint": "请检查报告配置和数据源连接。"}


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------

def _register_report_schedule(report_id: str, schedule: str) -> None:
    """Register a report's cron schedule with APScheduler."""
    if _scheduler is None:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(schedule)
        job_id = f"report_{report_id}"
        _scheduler.add_schedule(
            _noop_report_job,
            trigger,
            id=job_id,
            kwargs={"report_id": report_id},
        )
        logger.info("Registered report schedule for {}: {}", report_id, schedule)
    except Exception as e:
        logger.error("Failed to register report schedule for {}: {}", report_id, e)


def _remove_report_schedule(report_id: str) -> None:
    """Remove a report's schedule from APScheduler."""
    if _scheduler is None:
        return
    try:
        job_id = f"report_{report_id}"
        _scheduler.remove_schedule(job_id)
        logger.info("Removed report schedule for {}", report_id)
    except Exception as e:
        logger.debug("Failed to remove report schedule for {} (may not exist): {}", report_id, e)


async def _noop_report_job(**kwargs: Any) -> None:
    """Placeholder report job — actual implementation in scheduler/jobs.py."""
    pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Tool executors mapping
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: dict[str, Any] = {
    "manage_report": manage_report,
    "preview_report": preview_report,
}
