"""E5+E6 — Investigation scenario tests.

Each test case = 1 scenario × 1 persona → full multi-turn AI conversation
evaluated by SessionScorer (5 dimensions).

Requires: LLM API Key in .env
Run:
    # All investigation tests
    uv run pytest tests/scenarios/test_investigation.py -v -m e2e

    # Single scenario
    uv run pytest tests/scenarios/test_investigation.py -k "S01" -v

    # Single persona
    uv run pytest tests/scenarios/test_investigation.py -k "xiao_wang" -v
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml

from tests.scenarios.session_scorer import SessionScorer, SessionScore, push_score_to_langwatch

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

# ─── Load scenario definitions ───────────────────────────────────────────────

_SCENARIOS_PATH = Path(__file__).parent / "scenarios_v2.yaml"


def _load_scenarios() -> tuple[dict, list[dict]]:
    """Load scenarios_v2.yaml, return (personas_map, scenarios_list)."""
    with open(_SCENARIOS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    personas = {p_id: p_data for p_id, p_data in data.get("personas", {}).items()}
    scenarios = data.get("scenarios", [])
    return personas, scenarios


_PERSONAS, _SCENARIOS = _load_scenarios()

# Flatten: (scenario, persona_id) pairs
_TEST_CASES: list[tuple[dict, str]] = []
for _s in _SCENARIOS:
    for _p_id in _s.get("personas", []):
        _TEST_CASES.append((_s, _p_id))

_TEST_IDS = [f"{s['id']}_{p}" for s, p in _TEST_CASES]


# ─── Infrastructure setup ────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def investigation_infra():
    """Initialize real MCP + DAL for investigation tests.

    Uses real MCP servers from config.yaml (mysql-prod, pg-analytics, etc.)
    so the agent works with actual Kaggle data instead of the toy test-warehouse.
    """
    from order_guard.config import get_settings
    from order_guard.mcp import MCPManager
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.tools import data_tools, rule_tools, health_tools, report_tools
    from order_guard.storage.database import init_db
    from order_guard.engine.agent import langwatch_init

    await init_db()

    # Enable LangWatch tracing so token usage appears on dashboard
    try:
        import langwatch
        langwatch.login()
        langwatch_init()
    except Exception:
        pass  # LangWatch optional

    settings = get_settings()
    mcp_configs = [
        MCPServerConfig(**c.model_dump())
        for c in settings.mcp_servers
        if c.enabled
    ]

    mgr = MCPManager(mcp_configs)
    if mcp_configs:
        await mgr.connect_all()

    dal = DataAccessLayer(mcp_manager=mgr, configs=mcp_configs)
    await dal.initialize()

    # Configure tools (same pattern as production)
    data_tools.configure(data_access_layer=dal)
    rule_tools.configure(data_access_layer=dal, mcp_manager=mgr)
    health_tools.configure(mcp_manager=mgr)
    report_tools.configure(data_access_layer=dal, mcp_manager=mgr)

    # Pre-warm schema cache once — all tests in module share it
    schema_context = ""
    try:
        schema_context = await dal.get_or_warm_schema_context()
        print(f"\n  [infra] Schema pre-fetched: {len(schema_context)} chars")
    except Exception as e:
        print(f"\n  [infra] Schema pre-fetch failed (will fall back to tools): {e}")

    yield {"dal": dal, "mcp_manager": mgr, "schema_context": schema_context}

    await mgr.disconnect_all()


def _build_agent(infra: dict) -> tuple[Any, str]:
    """Build a fully-wired Agent using the investigation infra.

    Returns (agent, system_prompt) — system_prompt has schema pre-injected
    so the agent skips list_datasources/get_schema tool calls entirely.
    """
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import build_unified_prompt
    from order_guard.tools import (
        data_tools, rule_tools, context_tools, alert_tools,
        health_tools, report_tools, usage_tools,
    )

    schema_context = infra.get("schema_context", "")

    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )
    all_executors: dict = {}
    for mod in [data_tools, rule_tools, context_tools, alert_tools,
                health_tools, report_tools, usage_tools]:
        all_executors.update(mod.TOOL_EXECUTORS)

    # When schema is pre-injected, remove discovery tools — saves 2-3 tool calls per turn
    if schema_context:
        _SCHEMA_TOOLS = {"list_datasources", "get_schema"}
        all_tools = [t for t in all_tools if t.name not in _SCHEMA_TOOLS]
        for k in _SCHEMA_TOOLS:
            all_executors.pop(k, None)

    system_prompt = build_unified_prompt(schema_context=schema_context)

    agent = Agent(
        llm_client=LLMClient(),
        data_access_layer=infra["dal"],
        config=AgentConfig(
            inject_business_context=False,
            max_iterations=5,  # Schema injected → 2-3 iterations enough
        ),
        tools=all_tools,
        tool_executors=all_executors,
    )
    return agent, system_prompt


# ─── Scenario runner ─────────────────────────────────────────────────────────

async def _run_investigation(
    scenario: dict,
    persona: dict,
    persona_id: str,
    infra: dict,
) -> tuple[list[dict], list[str]]:
    """Run a goal-driven investigation scenario.

    Uses langwatch-scenario UserSimulatorAgent + JudgeAgent if available,
    otherwise falls back to a simple LLM-driven turn loop.

    Returns (conversation, tools_used).
    """
    from order_guard.config import get_settings

    settings = get_settings()
    llm_model = settings.llm.model
    llm_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None
    llm_base = settings.llm.api_base or None
    max_turns = scenario.get("max_turns", 20)

    agent, agent_system_prompt = _build_agent(infra)

    # Build persona system prompt for user simulator
    guidelines_text = "\n".join(
        f"  - {g}" for g in scenario.get("conversation_guidelines", [])
    )
    # Apply persona-specific extra instructions
    persona_extra = ""
    for guideline in scenario.get("conversation_guidelines", []):
        if f'"{persona_id}"' in guideline or f"'{persona_id}'" in guideline:
            persona_extra += f"\n{guideline}"

    user_sim_prompt = (
        f"你扮演「{persona['name']}」，{persona['role']}。\n"
        f"角色描述：{persona['description']}\n"
        f"你的性格和行为特征：\n{persona['traits']}\n\n"
        f"当前业务背景：\n{scenario['business_context']}\n\n"
        f"对话行为指南（你必须遵守）：\n{guidelines_text}\n"
        f"{persona_extra}\n\n"
        f"用中文自然交流。不要一次性说出所有需求。\n"
        f"像真实用户一样逐步提问，根据 agent 的回答决定下一步。"
    )

    # Use scripted user messages (GLM-5 coding endpoint returns empty for chat)
    scripted_messages = scenario.get("user_script", [
        "帮我看看最近的数据情况",
        "有没有什么异常？",
        "跟之前比怎么样？",
    ])
    return await _scripted_turn_loop(agent, agent_system_prompt, scripted_messages)


def _extract_tools_from_messages(messages: list[dict]) -> list[str]:
    """Extract unique tool names from conversation messages."""
    tools = []
    for msg in messages:
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") if fn else ""
            if name and name not in tools:
                tools.append(name)
    return tools


_TURN_TIMEOUT = 180  # seconds per agent call


async def _scripted_turn_loop(
    agent,
    agent_system_prompt: str,
    user_messages: list[str],
) -> tuple[list[dict], list[str]]:
    """Run agent with scripted user messages. Per-turn timeout + progress logging."""
    conversation: list[dict] = []
    tools_used: list[str] = []

    for turn, user_msg in enumerate(user_messages):
        t_turn = time.time()
        conversation.append({"role": "user", "content": user_msg})

        try:
            ctx = conversation[:-1]
            result = await asyncio.wait_for(
                agent.run_unified(
                    user_message=user_msg,
                    system_prompt=agent_system_prompt,
                    context_messages=ctx,
                    trigger_type="chat",
                ),
                timeout=_TURN_TIMEOUT,
            )
            reply = result.response or ""
            turn_tools = [tc["tool"] for tc in (result.tool_calls_log or [])]
            for t in turn_tools:
                if t not in tools_used:
                    tools_used.append(t)
        except asyncio.TimeoutError:
            print(f"  [turn {turn+1}] TIMEOUT ({_TURN_TIMEOUT}s)", flush=True)
            break
        except Exception as e:
            print(f"  [turn {turn+1}] ERROR: {e}", flush=True)
            break

        conversation.append({"role": "assistant", "content": reply})

        elapsed_turn = time.time() - t_turn
        print(
            f"  [turn {turn+1}/{len(user_messages)}] {elapsed_turn:.1f}s | "
            f"user: {user_msg[:40]} | agent: {reply[:60]}... | tools: {turn_tools}",
            flush=True,
        )

    return conversation, tools_used


# ─── Test cases ──────────────────────────────────────────────────────────────

@pytest.mark.timeout(300)  # 5 min hard limit per test case
@pytest.mark.parametrize("scenario,persona_id", _TEST_CASES, ids=_TEST_IDS)
async def test_investigation_scenario(scenario, persona_id, investigation_infra):
    """Run one investigation scenario with one persona and assert all 5 dimensions pass."""
    persona = _PERSONAS[persona_id]
    scorer = SessionScorer()

    # Rebuild agent fresh per test to avoid event loop issues with module-scoped MCP
    agent, agent_system_prompt = _build_agent(investigation_infra)

    scripted_messages = scenario.get("user_script", [
        "帮我看看最近的数据情况",
        "有没有什么异常？",
        "跟之前比怎么样？",
    ])

    t0 = time.time()
    conversation, tools_used = await _scripted_turn_loop(
        agent, agent_system_prompt, scripted_messages,
    )
    elapsed = time.time() - t0

    assert conversation, "Conversation is empty — agent or user simulator failed to produce messages"

    # Save conversation for debugging
    _save_dir = Path("/tmp/orderguard_investigations")
    _save_dir.mkdir(exist_ok=True)
    _conv_file = _save_dir / f"{scenario['id']}_{persona_id}.json"
    _conv_file.write_text(json.dumps(conversation, ensure_ascii=False, indent=2))
    print(f"\n  [debug] Conversation saved to {_conv_file}")

    score: SessionScore = await scorer.score(
        conversation=conversation,
        scenario=scenario,
        persona_id=persona_id,
        tools_used=tools_used,
    )

    # Push to LangWatch (best-effort, don't fail test if this errors)
    push_score_to_langwatch(score)

    # Report regardless of pass/fail
    print(f"\n{'='*60}")
    print(f"Scenario: {scenario['id']} | Persona: {persona_id}")
    print(f"Turns: {score.turns} | Time: {elapsed:.1f}s | Tools: {tools_used}")
    print(f"Result: {'✅ PASS' if score.passed else '❌ FAIL'}")
    for dim, passed in score.score_summary.items():
        icon = "✅" if passed else "❌"
        reason = getattr(score, dim).reason
        print(f"  {icon} {dim}: {reason[:80]}")
    if score.judge_error:
        print(f"  ⚠ Judge error: {score.judge_error}")
    print(f"{'='*60}")

    if score.judge_error:
        print(f"  ⚠ LLM judge unavailable, used rule-based fallback: {score.judge_error[:100]}")

    assert score.passed, (
        f"Investigation failed — {scenario['id']}/{persona_id}\n"
        f"Failed dimensions: {score.failed_dimensions}\n"
        f"Details:\n"
        + "\n".join(
            f"  ❌ {dim}: {getattr(score, dim).reason}"
            for dim in score.failed_dimensions
        )
    )
