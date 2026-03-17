"""Persona-based scenario runner.

Loads role/persona/task definitions from YAML, runs each scenario using
langwatch-scenario framework (AI simulates users, JudgeAgent evaluates),
and collects structured results.

Can be invoked via:
  - pytest: tests/scenarios/test_persona_scenarios.py
  - CLI:    order-guard test-scenarios [--push-feishu] [--role NAME]
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PersonaTask:
    """A single (role, persona, task) test case."""
    role_id: str
    role_name: str
    role_description: str
    persona_id: str
    persona_name: str
    persona_traits: str
    task_id: str
    task_name: str
    task_goal: str
    criteria: list[str]
    max_turns: int = 10


@dataclass
class ScenarioRunResult:
    """Result of running one scenario."""
    role_name: str
    persona_name: str
    task_name: str
    task_id: str
    success: bool = False
    passed_criteria: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)
    reasoning: str | None = None
    turns: int = 0
    tools_used: list[str] = field(default_factory=list)
    total_time: float = 0.0
    agent_time: float = 0.0
    token_usage: int = 0
    error: str | None = None
    messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

PERSONAS_YAML = Path(__file__).parent / "personas.yaml"


def load_personas(yaml_path: Path = PERSONAS_YAML) -> tuple[list[PersonaTask], dict]:
    """Load personas.yaml and flatten into a list of PersonaTask.

    Returns (tasks, raw_config).
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    default_max_turns = config.get("defaults", {}).get("max_turns", 10)
    tasks: list[PersonaTask] = []

    for role in config.get("roles", []):
        for persona in role.get("personas", []):
            for task in role.get("tasks", []):
                tasks.append(PersonaTask(
                    role_id=role["id"],
                    role_name=role["name"],
                    role_description=role["description"],
                    persona_id=persona["id"],
                    persona_name=persona["name"],
                    persona_traits=persona["traits"],
                    task_id=task["id"],
                    task_name=task["name"],
                    task_goal=task["goal"],
                    criteria=task["criteria"],
                    max_turns=default_max_turns,
                ))

    return tasks, config


def filter_tasks(
    tasks: list[PersonaTask],
    role_filter: str | None = None,
    task_filter: str | None = None,
) -> list[PersonaTask]:
    """Filter tasks by role name/id and task name/id."""
    result = tasks
    if role_filter:
        result = [t for t in result if role_filter in (t.role_id, t.role_name)]
    if task_filter:
        result = [t for t in result if task_filter in (t.task_id, t.task_name)]
    return result


# ---------------------------------------------------------------------------
# Test infrastructure setup (standalone, no pytest fixtures)
# ---------------------------------------------------------------------------

_cleanup_refs: list[Any] = []


async def setup_test_infrastructure():
    """Set up in-memory DB + DAL for scenario testing.

    Mirrors what conftest.py fixtures do, but callable from CLI.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlmodel import SQLModel

    from order_guard.storage import database as _db
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.data_access.sql_adapter import SQLAdapter
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.mcp.manager import MCPManager
    from order_guard.tools import data_tools
    from tests.scenarios.conftest import FakeMCPConnection, _create_business_db
    from tests.scenarios.seed_data import seed_all

    # 1. Metadata DB (in-memory)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    original_engine = _db._engine
    _db._engine = engine

    # 2. Business data DB + DAL wiring
    db = _create_business_db()
    fake_conn = FakeMCPConnection(db)

    config = MCPServerConfig(
        name="test-db",
        type="dbhub",
        transport="stdio",
        command="fake",
        enabled=True,
    )
    mgr = MCPManager()
    dal = DataAccessLayer(mcp_manager=mgr, configs=[config])
    adapter = SQLAdapter(fake_conn, config)
    adapter._is_sqlite = True
    dal._adapters["test-db"] = adapter

    original_dal = data_tools._data_access_layer
    data_tools.configure(dal)

    # 3. Seed metadata
    await seed_all()

    # Store for cleanup
    _cleanup_refs.clear()
    _cleanup_refs.extend([engine, db, original_engine, original_dal])

    return dal


async def teardown_test_infrastructure():
    """Clean up after scenario testing."""
    from order_guard.storage import database as _db
    from order_guard.tools import data_tools

    if len(_cleanup_refs) >= 4:
        engine, db, original_engine, original_dal = _cleanup_refs[:4]
        data_tools.configure(original_dal)
        _db._engine = original_engine
        db.close()
        await engine.dispose()
    _cleanup_refs.clear()


# ---------------------------------------------------------------------------
# Scenario execution
# ---------------------------------------------------------------------------

def _build_persona_prompt(task: PersonaTask) -> str:
    """Build the user simulator system prompt from persona definition."""
    return (
        f"你扮演「{task.persona_name}」，一位{task.role_name}。\n"
        f"角色背景：{task.role_description}\n"
        f"你的性格：{task.persona_traits}\n"
        f"当前任务：{task.task_name}\n"
        f"你的目标：{task.task_goal}\n\n"
        f"用中文交流。根据你的性格特点自然地提问和回应。"
        f"不要在第一条消息就说出所有需求，像真实用户一样逐步沟通。"
    )


def _extract_tools(messages: list) -> list[str]:
    """Extract tool names from scenario conversation messages.

    Handles multiple formats:
    - dict with nested dicts (raw JSON)
    - scenario SDK objects (PostApiScenarioEvents... types)
    - OpenAI-style tool_calls
    """
    tools = []
    for msg in messages:
        # Get tool_calls from message (dict or object)
        if isinstance(msg, dict):
            tcs = msg.get("tool_calls", [])
        else:
            tcs = getattr(msg, "tool_calls", []) or []

        for tc in tcs:
            name = ""
            # Try dict access
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
            else:
                # Object access (scenario SDK types)
                fn = getattr(tc, "function", None) or getattr(tc, "function_", None)
                if fn:
                    name = getattr(fn, "name", "")

            if name and name not in tools:
                tools.append(name)
    return tools


def _count_turns(messages: list) -> int:
    """Count user turns in conversation."""
    count = 0
    for msg in messages:
        msg_dict = msg if isinstance(msg, dict) else msg.model_dump()
        if msg_dict.get("role") == "user":
            count += 1
    return count


async def run_single_scenario(task: PersonaTask, verbose: bool = False) -> ScenarioRunResult:
    """Run one persona scenario using langwatch-scenario."""
    import scenario
    from order_guard.config import get_settings
    from tests.scenarios.conftest import OrderGuardAgent

    settings = get_settings()
    llm_model = settings.llm.model
    llm_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None
    llm_base = settings.llm.api_base or None

    result = ScenarioRunResult(
        role_name=task.role_name,
        persona_name=task.persona_name,
        task_name=task.task_name,
        task_id=task.task_id,
    )

    try:
        t0 = time.time()
        agent = OrderGuardAgent()
        user_sim = scenario.UserSimulatorAgent(
            model=llm_model,
            api_key=llm_key,
            api_base=llm_base,
            system_prompt=_build_persona_prompt(task),
        )
        judge = scenario.JudgeAgent(
            criteria=task.criteria,
            model=llm_model,
            api_key=llm_key,
            api_base=llm_base,
        )

        scenario_result = await scenario.run(
            name=f"{task.role_name}/{task.persona_name}/{task.task_name}",
            description=f"{task.persona_name}（{task.role_name}）— {task.task_goal}",
            agents=[agent, user_sim, judge],
            max_turns=task.max_turns,
            verbose=verbose,
        )

        elapsed = time.time() - t0
        result.success = scenario_result.success
        result.passed_criteria = scenario_result.passed_criteria or []
        result.failed_criteria = scenario_result.failed_criteria or []
        result.reasoning = scenario_result.reasoning
        result.total_time = scenario_result.total_time or elapsed
        result.agent_time = scenario_result.agent_time or 0.0
        result.tools_used = _extract_tools(scenario_result.messages)
        result.turns = _count_turns(scenario_result.messages)
        result.messages = [
            m if isinstance(m, dict) else m.model_dump()
            for m in scenario_result.messages
        ]

    except Exception as e:
        logger.error("Scenario failed with error: {}", e)
        result.error = str(e)
        result.success = False

    return result


async def run_all_scenarios(
    yaml_path: Path = PERSONAS_YAML,
    role_filter: str | None = None,
    task_filter: str | None = None,
    verbose: bool = False,
) -> list[ScenarioRunResult]:
    """Run all matching scenarios and return results."""
    all_tasks, _ = load_personas(yaml_path)
    tasks = filter_tasks(all_tasks, role_filter, task_filter)

    if not tasks:
        logger.warning("No scenarios matched filters (role={}, task={})", role_filter, task_filter)
        return []

    results: list[ScenarioRunResult] = []
    for i, task in enumerate(tasks):
        label = f"{task.role_name}/{task.persona_name}/{task.task_name}"
        logger.info("[{}/{}] Running: {}", i + 1, len(tasks), label)

        r = await run_single_scenario(task, verbose=verbose)
        results.append(r)

        status = "✅ PASS" if r.success else "❌ FAIL"
        logger.info("  {} — {} turns, tools: {}, {:.1f}s", status, r.turns, r.tools_used, r.total_time)

    return results


# ---------------------------------------------------------------------------
# Output: terminal table
# ---------------------------------------------------------------------------

def format_terminal_table(results: list[ScenarioRunResult]) -> str:
    """Format results as a Rich-compatible table string."""
    from rich.table import Table
    from rich.console import Console
    import io

    table = Table(title="场景测试报告", show_lines=True)
    table.add_column("角色", style="cyan", width=10)
    table.add_column("人设", width=8)
    table.add_column("场景", width=14)
    table.add_column("结果", width=6, justify="center")
    table.add_column("轮数", width=4, justify="center")
    table.add_column("工具", width=30)
    table.add_column("耗时", width=6, justify="right")

    for r in results:
        status = "✅" if r.success else "❌"
        tools_str = ", ".join(r.tools_used) if r.tools_used else "-"
        time_str = f"{r.total_time:.1f}s"
        error_note = f"\n[red]{r.error[:50]}[/red]" if r.error else ""
        table.add_row(
            r.role_name, r.persona_name, r.task_name,
            status, str(r.turns), tools_str, time_str + error_note,
        )

    # Summary row
    passed = sum(1 for r in results if r.success)
    total = len(results)
    total_time = sum(r.total_time for r in results)
    table.add_section()
    table.add_row(
        f"[bold]合计 {passed}/{total}[/bold]", "", "",
        "", "", "", f"[bold]{total_time:.1f}s[/bold]",
    )

    buf = io.StringIO()
    console = Console(file=buf, width=120)
    console.print(table)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Output: JSON report
# ---------------------------------------------------------------------------

def save_json_report(
    results: list[ScenarioRunResult],
    output_dir: Path = Path(__file__).parent / "reports",
) -> Path:
    """Save results as a timestamped JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = output_dir / f"{ts}_results.json"

    passed = sum(1 for r in results if r.success)
    total_time = sum(r.total_time for r in results)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "duration_seconds": round(total_time, 1),
        "results": [
            {
                "role": r.role_name,
                "persona": r.persona_name,
                "task": r.task_name,
                "task_id": r.task_id,
                "success": r.success,
                "passed_criteria": r.passed_criteria,
                "failed_criteria": r.failed_criteria,
                "reasoning": r.reasoning,
                "turns": r.turns,
                "tools_used": r.tools_used,
                "total_time": round(r.total_time, 2),
                "error": r.error,
            }
            for r in results
        ],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return filepath


# ---------------------------------------------------------------------------
# Output: Feishu webhook push
# ---------------------------------------------------------------------------

def build_feishu_report_card(results: list[ScenarioRunResult]) -> dict:
    """Build a Feishu interactive card for test results."""
    passed = sum(1 for r in results if r.success)
    total = len(results)
    total_time = sum(r.total_time for r in results)
    all_pass = passed == total

    # Header
    header_color = "green" if all_pass else "red"
    header_text = f"场景测试报告 — {passed}/{total} 通过"

    # Table rows
    lines = ["| 角色 | 人设 | 场景 | 结果 | 轮数 | 耗时 |",
             "|------|------|------|------|------|------|"]
    for r in results:
        status = "✅" if r.success else "❌"
        lines.append(
            f"| {r.role_name} | {r.persona_name} | {r.task_name} "
            f"| {status} | {r.turns} | {r.total_time:.1f}s |"
        )

    table_md = "\n".join(lines)

    # Failed details
    failed_section = ""
    failed_results = [r for r in results if not r.success]
    if failed_results:
        parts = ["\n---\n**失败详情：**\n"]
        for r in failed_results:
            parts.append(f"**{r.role_name}/{r.persona_name} — {r.task_name}**")
            if r.failed_criteria:
                for c in r.failed_criteria:
                    parts.append(f"  - ❌ {c}")
            if r.error:
                parts.append(f"  - 错误: {r.error[:100]}")
            parts.append("")
        failed_section = "\n".join(parts)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    footer = f"⏱️ 总耗时 {total_time:.1f}s | {ts}"

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": header_text},
                "template": header_color,
            },
            "elements": [
                {"tag": "markdown", "content": table_md},
                {"tag": "markdown", "content": failed_section} if failed_section else None,
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": footer},
                ]},
            ],
        },
    }
    # Remove None elements
    card["card"]["elements"] = [e for e in card["card"]["elements"] if e is not None]
    return card


async def push_feishu_report(results: list[ScenarioRunResult], webhook_url: str | None = None):
    """Push test report to Feishu webhook."""
    import httpx
    from order_guard.config import get_settings

    if not webhook_url:
        settings = get_settings()
        # Try to find a Feishu webhook from alert channels
        for ch in settings.alerts.channels:
            if ch.enabled and "feishu" in (ch.url or ""):
                webhook_url = ch.url
                break

    if not webhook_url:
        logger.warning("No Feishu webhook URL configured, skip push")
        return

    card = build_feishu_report_card(results)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=card)
        if resp.status_code == 200:
            logger.info("Report pushed to Feishu successfully")
        else:
            logger.error("Failed to push report: {} {}", resp.status_code, resp.text[:200])
