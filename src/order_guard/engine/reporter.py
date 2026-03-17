"""Reporter — generate and push scheduled business reports."""

from __future__ import annotations

import time
from typing import Any, Sequence

from loguru import logger

from order_guard.models import ReportConfig, ReportHistory
from order_guard.storage.database import get_session
from order_guard.storage.crud import create, get_by_id, list_all, update
from order_guard.engine.agent import Agent, AgentConfig
from order_guard.engine.llm_client import LLMClient


REPORT_SYSTEM_PROMPT = """你是企业经营数据分析助手。你正在生成一份定时经营报告。

要求：
1. 使用工具查询数据库获取真实数据
2. 用具体数字说话，不要泛泛而谈
3. 输出格式：Markdown，结构清晰
4. 包含：数据概览、异常/风险点、建议
5. 语言：中文，简洁专业"""

REPORT_PROMPT_TEMPLATE = """请根据以下要求生成经营报告：

## 报告名称
{report_name}

## 内容要求
{focus}

请查询数据库获取真实数据，生成报告。直接输出报告内容（Markdown格式），不要输出 JSON。"""

SECTION_PROMPT_TEMPLATE = """请根据以下要求生成报告的一个章节：

## 章节标题
{title}

## 内容要求
{prompt}

{datasource_hint}

请查询数据库获取真实数据，生成该章节内容。直接输出章节内容（Markdown格式），不要输出 JSON。"""


# ---------------------------------------------------------------------------
# KPI formatting
# ---------------------------------------------------------------------------

def format_kpi(value: Any, fmt: str) -> str:
    """Format a KPI value according to format type.

    Supported formats: number, currency, percent
    """
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    if fmt == "currency":
        if num >= 10000:
            return f"¥{num:,.2f}"
        return f"¥{num:,.2f}"
    elif fmt == "percent":
        return f"{num:.1f}%"
    elif fmt == "number":
        if num == int(num):
            return f"{int(num):,}"
        return f"{num:,.2f}"
    return str(value)


class ReportManager:
    """Manage report configurations — YAML loading and DB operations."""

    async def sync_reports_to_db(self, report_configs: list[dict[str, Any]]) -> int:
        """Sync YAML report configs to database."""
        synced = 0
        for cfg in report_configs:
            report_id = cfg.get("id", "")
            if not report_id:
                continue

            async with get_session() as session:
                existing = await get_by_id(session, ReportConfig, report_id)
                if existing:
                    await update(
                        session, existing,
                        name=cfg.get("name", existing.name),
                        schedule=cfg.get("schedule", existing.schedule),
                        mcp_server=cfg.get("mcp_server", existing.mcp_server),
                        focus=cfg.get("focus", existing.focus),
                        channels=cfg.get("channels", existing.channels),
                        enabled=cfg.get("enabled", existing.enabled),
                    )
                else:
                    new_report = ReportConfig(
                        id=report_id,
                        name=cfg.get("name", ""),
                        schedule=cfg.get("schedule", ""),
                        mcp_server=cfg.get("mcp_server", ""),
                        focus=cfg.get("focus", ""),
                        channels=cfg.get("channels", "default"),
                        enabled=cfg.get("enabled", True),
                    )
                    await create(session, new_report)
                synced += 1
        return synced

    async def get_report(self, report_id: str) -> ReportConfig | None:
        async with get_session() as session:
            return await get_by_id(session, ReportConfig, report_id)

    async def list_reports(self, *, enabled_only: bool = False) -> Sequence[ReportConfig]:
        async with get_session() as session:
            filters = {"enabled": True} if enabled_only else None
            return await list_all(session, ReportConfig, order_by="created_at", filters=filters)

    async def save_history(
        self,
        report_id: str,
        content: str,
        status: str = "success",
        token_usage: int = 0,
        duration_ms: int = 0,
        error: str | None = None,
    ) -> ReportHistory:
        async with get_session() as session:
            history = ReportHistory(
                report_id=report_id,
                content=content,
                status=status,
                token_usage=token_usage,
                duration_ms=duration_ms,
                error=error,
            )
            return await create(session, history)


async def generate_report(
    report: ReportConfig,
    data_access_layer: Any | None = None,
    mcp_manager: Any | None = None,
) -> dict[str, Any]:
    """Generate a report using Agent to query data and LLM to summarize.

    If report.sections is non-empty, generates each section separately and
    merges them into a full report. Otherwise falls back to focus-based
    generation for backward compatibility.

    Returns dict with: content, token_usage, duration_ms, status, error
    """
    start = time.time()

    try:
        llm_client = LLMClient()

        # Build Agent with DAL or MCP connection
        dal = data_access_layer
        mcp_conn = None
        if mcp_manager and report.mcp_server:
            try:
                mcp_conn = mcp_manager.get_connection(report.mcp_server)
            except Exception:
                pass

        sections = getattr(report, "sections", None) or []
        kpis = getattr(report, "kpis", None) or []

        if sections:
            # Section-based generation
            content, total_tokens = await _generate_sections(
                report, sections, kpis, llm_client, dal, mcp_conn, mcp_manager,
            )
        else:
            # Legacy focus-based generation (backward compatible)
            agent = Agent(
                llm_client=llm_client,
                mcp_connection=mcp_conn,
                data_access_layer=dal,
                config=AgentConfig(inject_schema=True, validate_sql=True, inject_business_context=True),
            )

            prompt = REPORT_PROMPT_TEMPLATE.format(
                report_name=report.name,
                focus=report.focus,
            )

            result = await agent.run(
                rule_prompt=prompt,
                system_prompt=REPORT_SYSTEM_PROMPT,
            )

            content = result.summary or "报告生成完成，但未获取到有效数据。"
            total_tokens = result.token_usage.total_tokens if hasattr(result, "token_usage") and result.token_usage else 0

        duration_ms = int((time.time() - start) * 1000)

        return {
            "content": content,
            "token_usage": total_tokens,
            "duration_ms": duration_ms,
            "status": "success",
            "error": None,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.error("Report generation failed: {}", e)
        return {
            "content": "",
            "token_usage": 0,
            "duration_ms": duration_ms,
            "status": "failed",
            "error": str(e)[:200],
        }


async def _generate_sections(
    report: ReportConfig,
    sections: list[dict[str, Any]],
    kpis: list[dict[str, Any]],
    llm_client: LLMClient,
    dal: Any | None,
    mcp_conn: Any | None,
    mcp_manager: Any | None,
) -> tuple[str, int]:
    """Generate each section with its own Agent call, then merge.

    Returns (full_content, total_tokens).
    """
    parts: list[str] = [f"# {report.name}\n"]
    total_tokens = 0

    for section in sections:
        title = section.get("title", "未命名章节")
        prompt = section.get("prompt", "")
        datasource = section.get("datasource", "") or report.mcp_server

        # Resolve MCP connection per-section datasource
        section_mcp_conn = mcp_conn
        if mcp_manager and datasource and datasource != report.mcp_server:
            try:
                section_mcp_conn = mcp_manager.get_connection(datasource)
            except Exception:
                section_mcp_conn = mcp_conn

        agent = Agent(
            llm_client=llm_client,
            mcp_connection=section_mcp_conn,
            data_access_layer=dal,
            config=AgentConfig(inject_schema=True, validate_sql=True, inject_business_context=True),
        )

        datasource_hint = f"数据源: {datasource}" if datasource else ""
        section_prompt = SECTION_PROMPT_TEMPLATE.format(
            title=title,
            prompt=prompt,
            datasource_hint=datasource_hint,
        )

        result = await agent.run(
            rule_prompt=section_prompt,
            system_prompt=REPORT_SYSTEM_PROMPT,
        )

        section_content = result.summary or f"## {title}\n\n暂无数据。"
        parts.append(section_content)

        if hasattr(result, "token_usage") and result.token_usage:
            total_tokens += result.token_usage.total_tokens

    # Append KPIs summary if defined
    if kpis:
        kpi_lines = ["## 关键指标\n"]
        kpi_lines.append("| 指标 | 值 |")
        kpi_lines.append("| --- | --- |")
        for kpi in kpis:
            name = kpi.get("name", "")
            fmt = kpi.get("format", "number")
            value = kpi.get("value")  # May be pre-computed or None
            formatted = format_kpi(value, fmt)
            kpi_lines.append(f"| {name} | {formatted} |")
        parts.append("\n".join(kpi_lines))

    return "\n\n".join(parts), total_tokens
