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

@pytest_asyncio.fixture(scope="module")
async def investigation_infra():
    """Initialize real MCP + DAL for investigation tests (module-scoped, shared).

    Uses real MCP servers from config.yaml (mysql-prod, pg-analytics, etc.)
    so the agent works with actual Kaggle data instead of the toy test-warehouse.
    """
    from order_guard.config import get_settings
    from order_guard.mcp import MCPManager
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.tools import data_tools, rule_tools, health_tools, report_tools
    from order_guard.storage.database import init_db

    await init_db()

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

    yield {"dal": dal, "mcp_manager": mgr}

    await mgr.disconnect_all()


def _build_agent(infra: dict):
    """Build a fully-wired Agent using the investigation infra."""
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import build_unified_prompt
    from order_guard.tools import (
        data_tools, rule_tools, context_tools, alert_tools,
        health_tools, report_tools, usage_tools,
    )

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

    return Agent(
        llm_client=LLMClient(),
        data_access_layer=infra["dal"],
        config=AgentConfig(inject_business_context=False),
        tools=all_tools,
        tool_executors=all_executors,
    )


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

    agent = _build_agent(infra)

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

    # Use langwatch-scenario if available (proper UserSimulatorAgent)
    try:
        import scenario as sc

        class InvestigationAgent(sc.AgentAdapter):
            async def call(self, input: sc.AgentInput) -> sc.AgentReturnTypes:
                user_msg = ""
                history = []
                for msg in input.messages:
                    if msg["role"] == "user":
                        user_msg = msg.get("content", "")
                    history.append(msg)
                if history:
                    history = history[:-1]

                result = await agent.run_unified(
                    user_message=user_msg,
                    context_messages=history,
                    trigger_type="chat",
                )
                tool_calls = [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["tool"],
                            "arguments": json.dumps(tc.get("args", {})),
                        },
                    }
                    for i, tc in enumerate(result.tool_calls_log or [])
                ]
                msg: dict = {"role": "assistant", "content": result.response or ""}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                return msg

        scenario_result = await sc.run(
            name=f"{scenario['id']}/{persona_id}",
            description=f"{persona['name']} — {scenario['title']}",
            agents=[
                InvestigationAgent(),
                sc.UserSimulatorAgent(
                    model=llm_model,
                    api_key=llm_key,
                    api_base=llm_base,
                    system_prompt=user_sim_prompt,
                ),
            ],
            set_id="orderguard-investigation",
            max_turns=max_turns,
            verbose=False,
        )

        conversation = [
            m if isinstance(m, dict) else m.model_dump()
            for m in (scenario_result.messages or [])
        ]
        tools_used = _extract_tools_from_messages(conversation)
        return conversation, tools_used

    except ImportError:
        # Fall back to simple turn loop
        return await _simple_turn_loop(
            agent, user_sim_prompt, llm_model, llm_key, llm_base, max_turns
        )


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


async def _simple_turn_loop(
    agent,
    user_sim_prompt: str,
    model: str,
    api_key: str,
    api_base: str | None,
    max_turns: int,
) -> tuple[list[dict], list[str]]:
    """LLM-driven user simulator: generates user messages, agent responds."""
    import litellm
    from loguru import logger

    conversation: list[dict] = []
    tools_used: list[str] = []

    for turn in range(max_turns):
        # Build user simulator messages (system + turn instruction)
        history_text = "\n".join(
            f"{'用户' if m['role']=='user' else 'Agent'}: {m['content'][:300]}"
            for m in conversation[-6:]  # last 3 exchange pairs
        )
        if conversation:
            turn_instruction = (
                f"对话历史（最近几轮）：\n{history_text}\n\n"
                f"现在是第{turn+1}轮，请根据你的角色和目标，发出下一条消息（一句话，不超过50字）："
            )
        else:
            turn_instruction = "请发出第一条消息，开始这次对话（一句话，不超过50字）："

        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": user_sim_prompt},
                    {"role": "user", "content": turn_instruction},
                ],
                temperature=0.7,
                max_tokens=150,
                api_key=api_key,
                api_base=api_base or None,
            )
            user_msg = (resp.choices[0].message.content or "").strip().strip('"\'')
        except Exception as e:
            logger.warning("User sim failed at turn {}: {}", turn + 1, e)
            break

        if not user_msg:
            logger.warning("User sim returned empty message at turn {}", turn + 1)
            break

        conversation.append({"role": "user", "content": user_msg})

        # Call agent
        ctx = [m for m in conversation[:-1]]
        result = await agent.run_unified(
            user_message=user_msg,
            context_messages=ctx,
            trigger_type="chat",
        )
        reply = result.response or ""
        for tc in (result.tool_calls_log or []):
            if tc["tool"] not in tools_used:
                tools_used.append(tc["tool"])

        conversation.append({"role": "assistant", "content": reply})

        # Stop if user simulator signals conversation end or goal is reached
        closing_signals = ["再见", "谢谢", "好的，明白了", "知道了", "了解了", "好的，谢谢", "没问题了"]
        user_msg_lower = user_msg.lower()
        if turn >= 4 and any(s in user_msg for s in closing_signals):
            break
        # Stop if agent didn't ask a follow-up (natural conversation end)
        if turn >= 5 and "？" not in reply and "?" not in reply:
            break

    return conversation, tools_used


# ─── Test cases ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scenario,persona_id", _TEST_CASES, ids=_TEST_IDS)
async def test_investigation_scenario(scenario, persona_id, investigation_infra):
    """Run one investigation scenario with one persona and assert all 5 dimensions pass."""
    persona = _PERSONAS[persona_id]
    scorer = SessionScorer()

    t0 = time.time()
    conversation, tools_used = await _run_investigation(
        scenario=scenario,
        persona=persona,
        persona_id=persona_id,
        infra=investigation_infra,
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
