"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import typer
from rich.console import Console
from rich.table import Table

from order_guard import __version__

app = typer.Typer(name="order-guard", help="OrderGuard — 企业数据智能监控中台")
rules_app = typer.Typer(help="规则管理")
app.add_typer(rules_app, name="rules")
pilot_app = typer.Typer(help="TestPilot — 飞书群自动化测试")
app.add_typer(pilot_app, name="pilot")

console = Console()


def version_callback(value: bool):
    if value:
        typer.echo(f"order-guard {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True,
        help="显示版本号",
    ),
):
    """OrderGuard CLI"""


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="监听地址"),
    port: int = typer.Option(8000, help="监听端口"),
):
    """启动 FastAPI 服务（含 Scheduler 定时任务）"""
    import uvicorn

    uvicorn.run("order_guard.main:app", host=host, port=port, reload=False)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@app.command()
def run(
    rule_id: Optional[str] = typer.Option(None, "--rule-id", "-r", help="指定规则 ID（默认执行所有启用规则）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不推送（调试用）"),
):
    """手动执行一次完整检测流程"""
    asyncio.run(_run_detection(rule_id=rule_id, dry_run=dry_run))


async def _run_detection(rule_id: str | None, dry_run: bool):
    from order_guard.config import get_settings
    from order_guard.engine.analyzer import Analyzer
    from order_guard.engine.rules import RuleManager
    from order_guard.alerts import AlertDispatcher
    from order_guard.scheduler.jobs import run_detection_job
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    settings = get_settings()

    # Setup components
    rm = RuleManager()
    await rm.sync_rules_to_db()

    dispatcher = AlertDispatcher(silence_minutes=settings.alerts.silence_minutes)
    if not dry_run and settings.alerts.channels:
        dispatcher.register_from_config(settings.alerts.channels)

    analyzer = Analyzer()

    # Set up MCP manager if configured
    mcp_manager = None
    data_access_layer = None
    if settings.mcp_servers:
        from order_guard.mcp import MCPManager
        from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
        mcp_configs = [MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers]
        mcp_manager = MCPManager(mcp_configs)
        await mcp_manager.connect_all()

        # Initialize DataAccessLayer (v4)
        from order_guard.data_access import DataAccessLayer
        data_access_layer = DataAccessLayer(mcp_manager, mcp_configs)
        await data_access_layer.initialize()

    # Determine which rules to run
    if rule_id:
        rule_ids = [rule_id]
    else:
        rules = await rm.list_rules(enabled_only=True)
        rule_ids = [r.id for r in rules]

    if not rule_ids:
        console.print("[yellow]No rules to execute[/yellow]")
        return

    console.print(f"Running detection for {len(rule_ids)} rule(s){'  [DRY RUN]' if dry_run else ''}...")

    for rid in rule_ids:
        console.print(f"\n--- Rule: {rid} ---")
        task_run = await run_detection_job(
            rule_id=rid,
            job_name="cli-manual",
            rule_manager=rm,
            analyzer=analyzer,
            dispatcher=dispatcher,
            dry_run=dry_run,
            mcp_manager=mcp_manager,
            data_access_layer=data_access_layer,
        )
        if task_run:
            from order_guard.storage.database import get_session
            from order_guard.storage.crud import get_by_id
            from order_guard.models import TaskRun as TaskRunModel
            async with get_session() as s:
                tr = await get_by_id(s, TaskRunModel, task_run.id)
                if tr:
                    status_color = "green" if tr.status == "success" else "red"
                    console.print(f"Status: [{status_color}]{tr.status}[/{status_color}]")
                    if tr.duration_ms:
                        console.print(f"Duration: {tr.duration_ms}ms")
                    if tr.error:
                        console.print(f"[red]Error: {tr.error}[/red]")
                    if tr.result_summary:
                        summary = tr.result_summary
                        if summary.get("has_alerts"):
                            console.print(f"Alerts: {summary.get('alert_count', 0)}")
                        console.print(f"Summary: {summary.get('summary', 'N/A')[:200]}")

    # Cleanup MCP connections
    if mcp_manager:
        await mcp_manager.disconnect_all()


# ---------------------------------------------------------------------------
# init-rules
# ---------------------------------------------------------------------------

@app.command("init-rules")
def init_rules():
    """扫描数据源，自动推荐并创建监控规则"""
    asyncio.run(_init_rules())


async def _init_rules():
    from order_guard.config import get_settings
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import INIT_RULES_PROMPT, build_unified_prompt
    from order_guard.storage.database import init_db, reset_engine
    from order_guard.tools import rule_tools, context_tools, alert_tools, data_tools, health_tools, report_tools, usage_tools

    reset_engine()
    await init_db()

    settings = get_settings()

    # Initialize MCP + DAL
    mcp_manager = None
    data_access_layer = None
    if settings.mcp_servers:
        from order_guard.mcp import MCPManager
        from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
        mcp_configs = [MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers]
        mcp_manager = MCPManager(mcp_configs)
        console.print("[dim]连接数据源...[/dim]")
        await mcp_manager.connect_all()
        from order_guard.data_access import DataAccessLayer
        data_access_layer = DataAccessLayer(mcp_manager, mcp_configs)
        await data_access_layer.initialize()
        data_tools.configure(data_access_layer=data_access_layer)
        rule_tools.configure(data_access_layer=data_access_layer, mcp_manager=mcp_manager)
    else:
        console.print("[red]未配置任何数据源 (mcp_servers)，请先在 config.yaml 中配置数据源。[/red]")
        return

    # Build tools
    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )
    all_executors = {
        **data_tools.TOOL_EXECUTORS,
        **rule_tools.TOOL_EXECUTORS,
        **context_tools.TOOL_EXECUTORS,
        **alert_tools.TOOL_EXECUTORS,
        **health_tools.TOOL_EXECUTORS,
        **report_tools.TOOL_EXECUTORS,
        **usage_tools.TOOL_EXECUTORS,
    }

    # Build business context
    biz_context = ""
    try:
        from order_guard.tools.context_tools import build_context_injection
        biz_context = await build_context_injection()
    except Exception:
        pass

    system_prompt = build_unified_prompt(biz_context)

    console.print("[dim]正在扫描数据源并生成规则建议...[/dim]\n")

    agent = Agent(
        llm_client=LLMClient(),
        data_access_layer=data_access_layer,
        config=AgentConfig(inject_business_context=False),
        tools=all_tools,
        tool_executors=all_executors,
    )

    result = await agent.run_unified(
        user_message=INIT_RULES_PROMPT,
        system_prompt=system_prompt,
    )

    # Show Agent's response (tools execute directly)
    console.print(result.response)

    # Cleanup
    if mcp_manager:
        await mcp_manager.disconnect_all()


# ---------------------------------------------------------------------------
# rules list / show
# ---------------------------------------------------------------------------

@rules_app.command("list")
def rules_list():
    """列出所有规则"""
    asyncio.run(_rules_list())


async def _rules_list():
    from order_guard.engine.rules import RuleManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    rm = RuleManager()
    await rm.sync_rules_to_db()
    rules = await rm.list_rules()

    if not rules:
        console.print("[yellow]No rules found[/yellow]")
        return

    table = Table(title="Alert Rules")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("MCP Server")
    table.add_column("Enabled", justify="center")

    for r in rules:
        enabled = "[green]✓[/green]" if r.enabled else "[red]✗[/red]"
        table.add_row(r.id, r.name, r.mcp_server, enabled)

    console.print(table)


@rules_app.command("create")
def rules_create(
    description: str = typer.Argument(help="用自然语言描述监控规则，例如：'监控日销量下降超过30%的SKU，每天早上9点检查'"),
):
    """通过自然语言创建监控规则"""
    asyncio.run(_rules_create(description))


async def _rules_create(description: str):
    from order_guard.engine.agent import Agent, AgentConfig, AgentResult
    from order_guard.engine.llm_client import LLMClient
    from order_guard.storage.database import init_db, reset_engine
    from order_guard.tools import rule_tools, context_tools, alert_tools, data_tools, health_tools, report_tools, usage_tools

    reset_engine()
    await init_db()

    # Try to initialize DAL for schema access
    data_access_layer = None
    try:
        from order_guard.config import get_settings
        settings = get_settings()
        if settings.mcp_servers:
            from order_guard.mcp import MCPManager
            from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
            mcp_configs = [MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers]
            mcp_manager = MCPManager(mcp_configs)
            await mcp_manager.connect_all()
            from order_guard.data_access import DataAccessLayer
            data_access_layer = DataAccessLayer(mcp_manager, mcp_configs)
            await data_access_layer.initialize()
            data_tools.configure(data_access_layer=data_access_layer)
            rule_tools.configure(data_access_layer=data_access_layer, mcp_manager=mcp_manager)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not initialize data access layer: {e}[/yellow]")

    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )
    all_executors = {
        **data_tools.TOOL_EXECUTORS,
        **rule_tools.TOOL_EXECUTORS,
        **context_tools.TOOL_EXECUTORS,
        **alert_tools.TOOL_EXECUTORS,
        **health_tools.TOOL_EXECUTORS,
        **report_tools.TOOL_EXECUTORS,
        **usage_tools.TOOL_EXECUTORS,
    }

    llm_client = LLMClient()
    agent = Agent(
        llm_client=llm_client,
        tools=all_tools,
        tool_executors=all_executors,
        config=AgentConfig(inject_business_context=False),
    )

    result = await agent.run_unified(description)
    console.print(result.response)


@rules_app.command("delete")
def rules_delete(rule_id: str = typer.Argument(help="规则 ID")):
    """删除规则"""
    asyncio.run(_rules_delete(rule_id))


async def _rules_delete(rule_id: str):
    from order_guard.engine.rules import RuleManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    rm = RuleManager()
    await rm.sync_rules_to_db()
    rule = await rm.get_rule(rule_id)

    if rule is None:
        console.print(f"[red]Rule not found: {rule_id}[/red]")
        raise typer.Exit(1)

    console.print(f"Rule: {rule.name} ({rule.id})")
    confirm = typer.confirm("确认删除？")
    if confirm:
        deleted = await rm.delete_rule(rule_id)
        if deleted:
            console.print(f"[green]Deleted: {rule.name}[/green]")
        else:
            console.print(f"[red]Failed to delete[/red]")
    else:
        console.print("[yellow]已取消[/yellow]")


@rules_app.command("show")
def rules_show(rule_id: str = typer.Argument(help="规则 ID")):
    """查看规则详情"""
    asyncio.run(_rules_show(rule_id))


async def _rules_show(rule_id: str):
    from order_guard.engine.rules import RuleManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    rm = RuleManager()
    await rm.sync_rules_to_db()
    rule = await rm.get_rule(rule_id)

    if rule is None:
        console.print(f"[red]Rule not found: {rule_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Rule: {rule.id}[/bold]")
    console.print(f"Name: {rule.name}")
    console.print(f"Description: {rule.description}")
    console.print(f"MCP Server: {rule.mcp_server}")
    if rule.data_window:
        console.print(f"Data Window: {rule.data_window}")
    console.print(f"Enabled: {'Yes' if rule.enabled else 'No'}")
    console.print(f"\n[bold]Prompt Template:[/bold]")
    console.print(rule.prompt_template)


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
    severity: Optional[str] = typer.Option(None, "--severity", "-s", help="按级别筛选 (critical/warning/info)"),
):
    """查看最近告警历史"""
    asyncio.run(_history(limit=limit, severity=severity))


async def _history(limit: int, severity: str | None):
    from order_guard.models import Alert
    from order_guard.storage.database import init_db, get_session, reset_engine
    from order_guard.storage.crud import list_all

    reset_engine()
    await init_db()

    async with get_session() as session:
        filters = {"severity": severity} if severity else None
        alerts = await list_all(session, Alert, limit=limit, order_by="created_at", filters=filters)

    if not alerts:
        console.print("[yellow]No alerts found[/yellow]")
        return

    table = Table(title=f"Alert History (last {limit})")
    table.add_column("Time", style="dim")
    table.add_column("Severity")
    table.add_column("Title")
    table.add_column("Rule")
    table.add_column("Status")

    for a in alerts:
        sev_style = {"critical": "red bold", "warning": "yellow", "info": "blue"}.get(a.severity, "")
        status_style = {"sent": "green", "failed": "red", "pending": "yellow"}.get(a.status, "")
        table.add_row(
            a.created_at.strftime("%Y-%m-%d %H:%M"),
            f"[{sev_style}]{a.severity}[/{sev_style}]",
            a.title,
            a.rule_id,
            f"[{status_style}]{a.status}[/{status_style}]",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

@app.command()
def queries(
    last: int = typer.Option(20, "--last", "-n", help="显示条数"),
    rule: Optional[str] = typer.Option(None, "--rule", help="按规则 ID 过滤"),
    status_filter: Optional[str] = typer.Option(None, "--status", help="按状态过滤 (success,error,timeout,rejected)"),
    stats: bool = typer.Option(False, "--stats", help="显示统计摘要"),
):
    """查看 AI Agent 查询历史"""
    asyncio.run(_queries(last=last, rule=rule, status_filter=status_filter, stats=stats))


async def _queries(last: int, rule: str | None, status_filter: str | None, stats: bool):
    from order_guard.models import QueryLog
    from order_guard.storage.database import init_db, get_session, reset_engine
    from sqlmodel import select, func

    reset_engine()
    await init_db()

    async with get_session() as session:
        if stats:
            stmt = select(
                func.count(QueryLog.id),
                func.sum(func.iif(QueryLog.status == "success", 1, 0)),
                func.avg(QueryLog.duration_ms),
                func.avg(QueryLog.rows_returned),
            )
            result = await session.execute(stmt)
            row = result.one_or_none()
            if row:
                total, success, avg_dur, avg_rows = row
                total = total or 0
                success = success or 0
                rate = f"{(success / total * 100):.1f}%" if total > 0 else "N/A"
                console.print("[bold]Query Statistics[/bold]")
                console.print(f"  Total queries: {total}")
                console.print(f"  Success rate: {rate}")
                console.print(f"  Avg duration: {int(avg_dur or 0)}ms")
                console.print(f"  Avg rows returned: {int(avg_rows or 0)}")
            else:
                console.print("[yellow]No query logs found[/yellow]")
            return

        stmt = select(QueryLog).order_by(QueryLog.created_at.desc()).limit(last)
        if rule:
            stmt = stmt.where(QueryLog.rule_id == rule)
        if status_filter:
            statuses = [s.strip() for s in status_filter.split(",")]
            stmt = stmt.where(QueryLog.status.in_(statuses))

        result = await session.execute(stmt)
        logs = result.scalars().all()

    if not logs:
        console.print("[yellow]No query logs found[/yellow]")
        return

    table = Table(title=f"Query Logs (last {last})")
    table.add_column("Time", style="dim")
    table.add_column("Rule")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Rows")
    table.add_column("SQL", max_width=60)

    for log in logs:
        status_style = {
            "success": "green", "error": "red",
            "timeout": "yellow", "rejected": "magenta",
        }.get(log.status, "")
        sql_preview = log.sql[:57] + "..." if len(log.sql) > 60 else log.sql
        table.add_row(
            log.created_at.strftime("%m-%d %H:%M"),
            log.rule_id or "-",
            f"[{status_style}]{log.status}[/{status_style}]",
            f"{log.duration_ms}ms",
            str(log.rows_returned),
            sql_preview,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

reports_app = typer.Typer(help="报告管理")
app.add_typer(reports_app, name="reports")


@reports_app.command("list")
def reports_list():
    """列出所有报告配置"""
    asyncio.run(_reports_list())


async def _reports_list():
    from order_guard.config import get_settings
    from order_guard.engine.reporter import ReportManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    settings = get_settings()
    mgr = ReportManager()
    if settings.reports:
        report_defs = [r.model_dump() for r in settings.reports]
        await mgr.sync_reports_to_db(report_defs)

    reports = await mgr.list_reports()

    if not reports:
        console.print("[yellow]No reports configured[/yellow]")
        return

    table = Table(title="Report Configurations")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("MCP Server")
    table.add_column("Enabled", justify="center")

    for r in reports:
        enabled = "[green]✓[/green]" if r.enabled else "[red]✗[/red]"
        table.add_row(r.id, r.name, r.schedule, r.mcp_server, enabled)

    console.print(table)


@reports_app.command("run")
def reports_run(
    report: str = typer.Option(..., "--report", "-r", help="报告 ID"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只生成不推送"),
):
    """手动触发报告生成"""
    asyncio.run(_reports_run(report_id=report, dry_run=dry_run))


async def _reports_run(report_id: str, dry_run: bool):
    from order_guard.config import get_settings
    from order_guard.engine.reporter import ReportManager, generate_report, push_report
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    settings = get_settings()
    mgr = ReportManager()
    if settings.reports:
        report_defs = [r.model_dump() for r in settings.reports]
        await mgr.sync_reports_to_db(report_defs)

    report = await mgr.get_report(report_id)
    if not report:
        console.print(f"[red]Report not found: {report_id}[/red]")
        raise typer.Exit(1)

    console.print(f"Generating report: {report.name}{'  [DRY RUN]' if dry_run else ''}...")

    # Initialize MCP + DAL if available
    mcp_manager = None
    data_access_layer = None
    if settings.mcp_servers:
        from order_guard.mcp import MCPManager
        from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
        mcp_configs = [MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers]
        mcp_manager = MCPManager(mcp_configs)
        await mcp_manager.connect_all()
        from order_guard.data_access import DataAccessLayer
        data_access_layer = DataAccessLayer(mcp_manager, mcp_configs)
        await data_access_layer.initialize()

    result = await generate_report(
        report,
        data_access_layer=data_access_layer,
        mcp_manager=mcp_manager,
    )

    # Save history
    await mgr.save_history(
        report_id=report_id,
        content=result["content"],
        status=result["status"],
        token_usage=result.get("token_usage", 0),
        duration_ms=result.get("duration_ms", 0),
        error=result.get("error"),
    )

    if result["status"] == "success":
        console.print(f"\n[bold]Report: {report.name}[/bold]")
        console.print(result["content"])
        console.print(f"\n[dim]Duration: {result['duration_ms']}ms[/dim]")

        if not dry_run:
            pushed = await push_report(report, result["content"])
            if pushed:
                console.print("[green]Report pushed successfully[/green]")
            else:
                console.print("[red]Report push failed[/red]")
    else:
        console.print(f"[red]Report generation failed: {result.get('error', 'Unknown error')}[/red]")

    if mcp_manager:
        await mcp_manager.disconnect_all()


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

sessions_app = typer.Typer(help="会话管理")
app.add_typer(sessions_app, name="sessions")


@sessions_app.command("list")
def sessions_list(
    user_id: str = typer.Option("cli", "--user", "-u", help="用户 ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
):
    """列出用户会话"""
    asyncio.run(_sessions_list(user_id=user_id, limit=limit))


async def _sessions_list(user_id: str, limit: int):
    from order_guard.api.session import SessionManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    mgr = SessionManager()
    sessions = await mgr.list_sessions(user_id, limit=limit)

    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(title=f"Sessions for {user_id}")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Active", justify="center")
    table.add_column("Messages", justify="right")
    table.add_column("Updated", style="dim")

    for s in sessions:
        msg_count = await mgr.get_message_count(s.id)
        active = "[green]✓[/green]" if s.is_active else ""
        table.add_row(
            s.id[:8],
            s.title,
            active,
            str(msg_count),
            s.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@sessions_app.command("delete")
def sessions_delete(
    session_id: str = typer.Argument(help="会话 ID（前8位即可）"),
    user_id: str = typer.Option("cli", "--user", "-u", help="用户 ID"),
):
    """删除指定会话"""
    asyncio.run(_sessions_delete(session_id=session_id, user_id=user_id))


async def _sessions_delete(session_id: str, user_id: str):
    from order_guard.api.session import SessionManager
    from order_guard.storage.database import init_db, reset_engine

    reset_engine()
    await init_db()

    mgr = SessionManager()
    sessions = await mgr.list_sessions(user_id, limit=100)

    target = None
    for s in sessions:
        if s.id.startswith(session_id):
            target = s
            break

    if not target:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)

    deleted = await mgr.delete_session(target.id)
    if deleted:
        console.print(f"[green]Deleted session: {target.title} ({target.id[:8]})[/green]")
    else:
        console.print(f"[red]Failed to delete session[/red]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status():
    """查看系统状态（MCP 连接、规则、任务）"""
    asyncio.run(_status())


async def _status():
    from order_guard.config import get_settings
    from order_guard.engine.rules import RuleManager
    from order_guard.models import Alert, TaskRun
    from order_guard.storage.database import init_db, get_session, reset_engine
    from order_guard.storage.crud import list_all

    reset_engine()
    await init_db()

    settings = get_settings()

    console.print(f"[bold]OrderGuard v{__version__}[/bold]")
    console.print(f"Database: {settings.database.url}")
    console.print(f"LLM Model: {settings.llm.model}")
    console.print()

    # MCP Servers
    console.print("[bold]MCP Servers:[/bold]")
    if settings.mcp_servers:
        for srv in settings.mcp_servers:
            status_str = "[green]enabled[/green]" if srv.enabled else "[dim]disabled[/dim]"
            console.print(f"  {srv.name} ({srv.type}): {status_str}")
    else:
        console.print("  [dim]None configured[/dim]")

    # Rules
    rm = RuleManager()
    await rm.sync_rules_to_db()
    rules = await rm.list_rules()
    enabled = sum(1 for r in rules if r.enabled)
    console.print(f"\n[bold]Rules:[/bold] {len(rules)} total, {enabled} enabled")

    # Recent task runs
    async with get_session() as session:
        recent_runs = await list_all(session, TaskRun, limit=5, order_by="started_at")
        alerts = await list_all(session, Alert, limit=5, order_by="created_at")

    console.print(f"\n[bold]Recent Runs:[/bold] ({len(recent_runs)} shown)")
    for r in recent_runs:
        status_style = {"success": "green", "failed": "red", "running": "yellow"}.get(r.status, "")
        console.print(f"  {r.started_at.strftime('%m-%d %H:%M')} [{status_style}]{r.status}[/{status_style}] {r.job_name} ({r.rule_id})")

    console.print(f"\n[bold]Recent Alerts:[/bold] ({len(alerts)} shown)")
    for a in alerts:
        console.print(f"  {a.created_at.strftime('%m-%d %H:%M')} [{a.severity}] {a.title}")


# ---------------------------------------------------------------------------
# pilot — 飞书群自动化测试
# ---------------------------------------------------------------------------

@pilot_app.command("start")
def pilot_start(
    once: bool = typer.Option(False, "--once", help="只跑一轮"),
    role: Optional[str] = typer.Option(None, "--role", help="过滤角色"),
    task: Optional[str] = typer.Option(None, "--task", help="过滤任务"),
    config_file: Optional[str] = typer.Option(None, "--config", help="配置文件路径"),
):
    """启动 TestPilot — 模拟用户与 OrderGuard 对话"""
    asyncio.run(_pilot_start_impl(once, role, task, config_file))


async def _pilot_start_impl(once, role_filter, task_filter, config_file):
    import sys
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from tests.pilot.pilot_bot import load_pilot_config, load_tasks, run_pilot

    config_path = Path(config_file) if config_file else None
    config = load_pilot_config(config_path)

    tasks = load_tasks(config)
    if role_filter:
        tasks = [t for t in tasks if role_filter in (t["role_id"], t["role_name"])]
    if task_filter:
        tasks = [t for t in tasks if task_filter in (t["task_id"], t["task_name"])]

    console.print(f"[bold]TestPilot[/bold]: {len(tasks)} 个场景")
    console.print(f"  目标: {config.orderguard_url}")
    console.print(f"  模式: {config.mode} | 间隔: {config.interval_seconds}s")
    console.print(f"  每场景最多 {config.max_turns_per_scenario} 轮对话")
    if once:
        console.print("  [dim]单次运行模式[/dim]")
    console.print()

    results = await run_pilot(config, once=once, role_filter=role_filter, task_filter=task_filter)

    if results:
        passed = sum(1 for r in results if r.success)
        console.print(f"\n[bold]结果: {passed}/{len(results)} 通过[/bold]")
        if passed < len(results):
            raise typer.Exit(1)


@pilot_app.command("list")
def pilot_list():
    """列出所有可用的测试角色和场景"""
    import sys
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from tests.pilot.pilot_bot import load_pilot_config, load_tasks

    config = load_pilot_config()
    tasks = load_tasks(config)

    table = Table(title="TestPilot 场景列表")
    table.add_column("角色", style="cyan")
    table.add_column("人设")
    table.add_column("场景")
    table.add_column("目标", max_width=40)

    for t in tasks:
        table.add_row(t["role_name"], t["persona_name"], t["task_name"], t["task_goal"])

    console.print(table)
    console.print(f"\n共 {len(tasks)} 个场景")


# ---------------------------------------------------------------------------
# test-scenarios
# ---------------------------------------------------------------------------

@app.command("test-scenarios")
def test_scenarios(
    push_feishu: bool = typer.Option(False, "--push-feishu", help="推送结果到飞书群"),
    role: Optional[str] = typer.Option(None, "--role", help="过滤角色 (ID 或名称)"),
    task: Optional[str] = typer.Option(None, "--task", help="过滤任务 (ID 或名称)"),
    max_turns: int = typer.Option(10, "--max-turns", help="最大对话轮数"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="显示详细输出"),
    webhook_url: Optional[str] = typer.Option(None, "--webhook-url", help="飞书 Webhook URL (默认从配置读取)"),
):
    """运行 AI 场景测试 — 基于角色/人设自动模拟用户对话"""
    asyncio.run(_test_scenarios_impl(push_feishu, role, task, max_turns, verbose, webhook_url))


async def _test_scenarios_impl(
    push_feishu: bool,
    role_filter: str | None,
    task_filter: str | None,
    max_turns: int,
    verbose: bool,
    webhook_url: str | None,
):
    import sys
    # Ensure tests/ is importable
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from tests.scenarios.persona_runner import (
        PERSONAS_YAML,
        format_terminal_table,
        load_personas,
        filter_tasks,
        push_feishu_report,
        run_single_scenario,
        save_json_report,
        setup_test_infrastructure,
        teardown_test_infrastructure,
    )

    # Load and filter tasks
    all_tasks, _ = load_personas(PERSONAS_YAML)
    tasks = filter_tasks(all_tasks, role_filter, task_filter)

    if not tasks:
        console.print("[yellow]没有匹配的场景[/yellow]")
        if role_filter:
            console.print(f"  --role={role_filter}")
        if task_filter:
            console.print(f"  --task={task_filter}")
        console.print("\n可用角色:")
        seen = set()
        for t in all_tasks:
            key = (t.role_id, t.role_name)
            if key not in seen:
                seen.add(key)
                console.print(f"  {t.role_id} ({t.role_name})")
        raise typer.Exit(1)

    # Override max_turns if specified
    if max_turns != 10:
        for t in tasks:
            t.max_turns = max_turns

    console.print(f"[bold]场景测试[/bold]: {len(tasks)} 个场景待运行\n")

    # Setup test infrastructure
    console.print("[dim]初始化测试环境...[/dim]")
    try:
        await setup_test_infrastructure()
    except Exception as e:
        console.print(f"[red]初始化失败: {e}[/red]")
        raise typer.Exit(1)

    # Run scenarios
    results = []
    try:
        for i, task in enumerate(tasks):
            label = f"{task.role_name}/{task.persona_name}/{task.task_name}"
            console.print(f"[{i+1}/{len(tasks)}] {label} ...", end=" ")

            r = await run_single_scenario(task, verbose=verbose)
            results.append(r)

            if r.success:
                console.print(f"[green]✅ PASS[/green] ({r.turns} 轮, {r.total_time:.1f}s)")
            elif r.error:
                console.print(f"[red]❌ ERROR[/red] ({r.error[:60]})")
            else:
                console.print(f"[red]❌ FAIL[/red] ({r.turns} 轮, {r.total_time:.1f}s)")
    finally:
        await teardown_test_infrastructure()

    # Output: terminal table
    console.print()
    console.print(format_terminal_table(results))

    # Output: JSON report
    report_path = save_json_report(results)
    console.print(f"\n[dim]报告已保存: {report_path}[/dim]")

    # Output: Feishu push
    if push_feishu:
        console.print("[dim]推送结果到飞书...[/dim]")
        try:
            await push_feishu_report(results, webhook_url)
            console.print("[green]飞书推送成功[/green]")
        except Exception as e:
            console.print(f"[red]飞书推送失败: {e}[/red]")

    # Exit code
    passed = sum(1 for r in results if r.success)
    if passed < len(results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
