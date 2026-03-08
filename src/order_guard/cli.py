"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
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
    from order_guard.connectors import ConnectorRegistry
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

    reg = ConnectorRegistry()
    if settings.connectors:
        reg.register_from_config([c.model_dump() for c in settings.connectors])

    dispatcher = AlertDispatcher(silence_minutes=settings.alerts.silence_minutes)
    if not dry_run and settings.alerts.channels:
        dispatcher.register_from_config(settings.alerts.channels)

    analyzer = Analyzer()

    # Set up MCP manager if configured
    mcp_manager = None
    if settings.mcp_servers:
        from order_guard.mcp import MCPManager
        from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
        mcp_configs = [MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers]
        mcp_manager = MCPManager(mcp_configs)
        await mcp_manager.connect_all()

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
            connector_registry=reg,
            rule_manager=rm,
            analyzer=analyzer,
            dispatcher=dispatcher,
            dry_run=dry_run,
            mcp_manager=mcp_manager,
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
    table.add_column("Type")
    table.add_column("Source")
    table.add_column("Enabled", justify="center")

    for r in rules:
        enabled = "[green]✓[/green]" if r.enabled else "[red]✗[/red]"
        source = r.mcp_server if r.connector_type == "mcp" else r.connector_id
        table.add_row(r.id, r.name, r.connector_type, source, enabled)

    console.print(table)


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
    console.print(f"Connector: {rule.connector_id}")
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
# status
# ---------------------------------------------------------------------------

@app.command()
def status():
    """查看系统状态（数据源连接、规则、任务）"""
    asyncio.run(_status())


async def _status():
    from order_guard.config import get_settings
    from order_guard.connectors import ConnectorRegistry
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

    # Connectors health
    console.print("[bold]Data Sources:[/bold]")
    reg = ConnectorRegistry()
    if settings.connectors:
        reg.register_from_config([c.model_dump() for c in settings.connectors])
    for name in reg.list_names():
        connector = reg.get(name)
        try:
            healthy = await connector.health_check()
            status = "[green]OK[/green]" if healthy else "[red]FAIL[/red]"
        except Exception:
            status = "[red]ERROR[/red]"
        console.print(f"  {name} ({connector.type}): {status}")

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


if __name__ == "__main__":
    app()
