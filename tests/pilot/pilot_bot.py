"""TestPilot — 本地自动化测试机器人.

直接调用 OrderGuard 内部 Agent（同进程），模拟不同角色用户对话，
收集回复、AI 评判、定时推送报告。

不需要额外的飞书 Bot 应用，不需要启动 OrderGuard 服务。
Pilot 自己初始化 MCP 连接和 Agent，走和生产一样的代码路径。

Usage:
    uv run order-guard pilot start          # 启动（每小时一轮）
    uv run order-guard pilot start --once   # 只跑一轮全部场景
    uv run order-guard pilot list           # 列出所有角色/场景
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from loguru import logger


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PilotConfig:
    orderguard_url: str = "http://localhost:8000"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    mode: str = "sequential"          # sequential | random
    interval_seconds: int = 3600      # 每轮间隔（默认 1 小时）
    max_turns_per_scenario: int = 5   # 每个场景最多对话轮数
    reply_timeout_seconds: int = 60   # 等待回复超时
    total_rounds: int = 0             # 总轮数，0=无限
    report_every_n: int = 0           # 每 N 个场景出报告，0=每轮结束出
    personas_file: str = ""
    feishu_webhook_url: str = ""      # 报告推送 webhook


def load_pilot_config(config_path: Path | None = None) -> PilotConfig:
    """Load pilot config, with env var substitution."""
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"

    if not config_path.exists():
        return PilotConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        raw = f.read()

    import re
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), raw)
    data = yaml.safe_load(raw) or {}
    strategy = data.get("strategy", {})

    return PilotConfig(
        orderguard_url=data.get("orderguard_url", "http://localhost:8000"),
        llm_model=data.get("llm", {}).get("model", ""),
        llm_api_key=data.get("llm", {}).get("api_key", ""),
        llm_api_base=data.get("llm", {}).get("api_base", ""),
        mode=strategy.get("mode", "sequential"),
        interval_seconds=strategy.get("interval_seconds", 3600),
        max_turns_per_scenario=strategy.get("max_turns_per_scenario", 5),
        reply_timeout_seconds=strategy.get("reply_timeout_seconds", 60),
        total_rounds=strategy.get("total_rounds", 0),
        report_every_n=strategy.get("report_every_n", 0),
        personas_file=data.get("personas_file", ""),
        feishu_webhook_url=data.get("feishu_webhook_url", ""),
    )


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ConversationRecord:
    role_name: str
    persona_name: str
    task_name: str
    task_id: str
    success: bool = False
    messages: list[dict] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    passed_criteria: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)
    reasoning: str = ""
    response_times: list[float] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Agent infrastructure — initialize once, reuse across scenarios
# ---------------------------------------------------------------------------

_agent_infra: dict[str, Any] = {}


async def _ensure_agent_infra():
    """Initialize MCP connections and DAL (same as OrderGuard server startup)."""
    if _agent_infra.get("ready"):
        return

    from order_guard.config import get_settings
    from order_guard.mcp import MCPManager
    from order_guard.mcp.models import MCPServerConfig as MCPServerConfigModel
    from order_guard.data_access import DataAccessLayer
    from order_guard.storage.database import init_db

    settings = get_settings()

    # Init LangWatch if configured
    lw_key = settings.observability.langwatch_api_key.get_secret_value() if hasattr(settings, 'observability') else ""
    if lw_key:
        try:
            import langwatch
            langwatch.setup(api_key=lw_key)
            from order_guard.engine.agent import langwatch_init
            langwatch_init()
            logger.info("Pilot: LangWatch enabled")
        except Exception as e:
            logger.warning("Pilot: LangWatch init failed: {}", e)

    # Init DB
    await init_db()

    # Sync rules
    from order_guard.engine.rules import RuleManager
    rm = RuleManager()
    await rm.sync_rules_to_db()

    # MCP connections (same pattern as main.py)
    mcp_configs = [
        MCPServerConfigModel(**c.model_dump()) for c in settings.mcp_servers
        if c.enabled
    ]
    mcp_manager = MCPManager(mcp_configs)
    if mcp_configs:
        await mcp_manager.connect_all()
        logger.info("Pilot: {} MCP servers connected", len(mcp_manager._connections))

    # DAL
    dal = DataAccessLayer(mcp_manager, mcp_configs)
    await dal.initialize()

    _agent_infra["mcp_manager"] = mcp_manager
    _agent_infra["dal"] = dal
    _agent_infra["settings"] = settings
    _agent_infra["ready"] = True


async def _cleanup_agent_infra():
    """Disconnect MCP servers."""
    mgr = _agent_infra.get("mcp_manager")
    if mgr:
        await mgr.disconnect_all()
    _agent_infra.clear()


_last_agent_tools: list[dict] = []


async def _call_agent(user_id: str, text: str, context_messages: list[dict]) -> str:
    """Call the unified Agent directly (same code path as Feishu bot)."""
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import build_unified_prompt
    from order_guard.tools import (
        rule_tools, context_tools, alert_tools, data_tools,
        health_tools, report_tools, usage_tools,
    )

    dal = _agent_infra["dal"]
    mcp_manager = _agent_infra["mcp_manager"]

    # Configure tool dependencies (same as feishu.py _run_unified_agent)
    rule_tools.configure(data_access_layer=dal, mcp_manager=mcp_manager)
    data_tools.configure(data_access_layer=dal)
    health_tools.configure(mcp_manager=mcp_manager)
    report_tools.configure(data_access_layer=dal, mcp_manager=mcp_manager)

    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )
    all_executors = {}
    all_executors.update(data_tools.TOOL_EXECUTORS)
    all_executors.update(rule_tools.TOOL_EXECUTORS)
    all_executors.update(context_tools.TOOL_EXECUTORS)
    all_executors.update(alert_tools.TOOL_EXECUTORS)
    all_executors.update(health_tools.TOOL_EXECUTORS)
    all_executors.update(report_tools.TOOL_EXECUTORS)
    all_executors.update(usage_tools.TOOL_EXECUTORS)

    # Business context
    biz_context = ""
    try:
        from order_guard.tools.context_tools import build_context_injection
        biz_context = await build_context_injection()
    except Exception:
        pass

    system_prompt = build_unified_prompt(biz_context)

    agent = Agent(
        llm_client=LLMClient(),
        data_access_layer=dal,
        config=AgentConfig(inject_business_context=False),
        tools=all_tools,
        tool_executors=all_executors,
    )

    result = await agent.run_unified(
        user_message=text,
        system_prompt=system_prompt,
        context_messages=context_messages,
        trigger_type="chat",
        user_id=user_id,
    )

    global _last_agent_tools
    _last_agent_tools = result.tool_calls_log or []

    return result.response or "抱歉，我没有理解你的意思。"


# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------

async def _llm_call(messages: list[dict], model: str, api_key: str, api_base: str = "") -> str:
    import litellm
    resp = await litellm.acompletion(
        model=model, messages=messages,
        temperature=0.7, max_tokens=500,
        api_key=api_key, api_base=api_base or None,
    )
    msg = resp.choices[0].message
    content = msg.content or ""

    # GLM-5 coding plan puts actual reply in reasoning_content, content is empty
    if not content.strip():
        reasoning = getattr(msg, "reasoning_content", "") or ""
        if reasoning:
            content = _extract_reply_from_reasoning(reasoning)

    return content


def _extract_reply_from_reasoning(reasoning: str) -> str:
    """Extract the actual reply from GLM-5's reasoning_content.

    GLM-5 puts analysis (numbered steps) then the actual reply at the end.
    We try to find the final conversational output.
    """
    lines = reasoning.strip().split("\n")

    # Strategy 1: Find content after last "输出" / "回复" / "消息" marker
    for marker in ["输出：", "回复：", "消息：", "最终输出", "最终回复"]:
        for i, line in enumerate(lines):
            if marker in line:
                rest = "\n".join(lines[i:]).split(marker, 1)[-1].strip()
                # Clean up quotes and markdown
                rest = rest.strip('"').strip("'").strip("`").strip()
                if rest and len(rest) > 5:
                    return rest

    # Strategy 2: Take the last non-empty, non-analytical line
    # Skip lines that look like analysis (start with number, *, -, #)
    candidates = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        if line[0] in "0123456789*-#•→>":
            continue
        if line.startswith("**") and line.endswith("**"):
            continue
        candidates.append(line)
        if len(candidates) >= 3:
            break

    if candidates:
        # Return the last meaningful line(s)
        result = candidates[0].strip('"').strip("'").strip()
        if len(result) > 5:
            return result

    # Strategy 3: Last resort — take everything after the last numbered step
    import re
    parts = re.split(r'\n\d+\.\s', reasoning)
    if len(parts) > 1:
        last_part = parts[-1].strip()
        # Find the last quoted text
        quotes = re.findall(r'["""](.+?)["""]', last_part)
        if quotes:
            return quotes[-1]

    return ""


async def generate_user_message(
    persona_prompt: str, history: list[dict],
    model: str, api_key: str, api_base: str = "",
) -> str:
    """AI generates the next user message based on persona."""
    strict_instruction = (
        "\n\n【重要】你必须直接输出对话消息本身，不要输出分析、思考过程、编号列表或任何元信息。"
        "像真人发微信一样，直接说话。"
    )
    if not history:
        messages = [
            {"role": "user", "content": persona_prompt + strict_instruction + "\n\n请开始对话，发出你的第一条消息："},
        ]
    else:
        # Build conversation as a dialogue script
        conv_text = "\n".join(
            f"{'我' if m['role'] == 'user' else '系统'}: {m['content'][:300]}"
            for m in history
        )
        messages = [
            {"role": "user", "content": (
                persona_prompt + strict_instruction +
                f"\n\n之前的对话：\n{conv_text}\n\n请继续对话，发出你的下一条消息："
            )},
        ]
    try:
        result = await _llm_call(messages, model, api_key, api_base)
        return result.strip().strip('"').strip("'")
    except Exception as e:
        logger.error("生成用户消息失败: {}", e)
        return ""


def evaluate_conversation_rules(
    conversation: list[dict],
    criteria: list[str],
    tools_used: list[str],
) -> dict:
    """Rule-based evaluation — no LLM dependency, instant and deterministic.

    Checks criteria by pattern matching against conversation content and tool usage.
    """
    passed = []
    failed = []

    all_content = " ".join(m["content"] for m in conversation if m["role"] == "assistant")
    all_content_lower = all_content.lower()

    for criterion in criteria:
        c = criterion.lower()
        met = False

        # Tool usage checks
        if "调用" in c and ("工具" in c or "tool" in c):
            # Extract tool names from criterion
            tool_keywords = {
                "query": "query", "get_schema": "get_schema",
                "list_datasources": "list_datasources", "list_rules": "list_rules",
                "create_rule": "create_rule", "list_alerts": "list_alerts",
                "handle_alert": "handle_alert", "get_alert_stats": "get_alert_stats",
                "check_health": "check_health",
            }
            for kw, tool_name in tool_keywords.items():
                if kw in c and tool_name in tools_used:
                    met = True
                    break
            # Generic "data query tool" check
            if not met and ("数据查询" in c or "查询" in c):
                if any(t in tools_used for t in ["query", "get_schema", "list_datasources"]):
                    met = True
            # Generic "alert tool" check
            if not met and "告警" in c:
                if any(t in tools_used for t in ["list_alerts", "handle_alert", "get_alert_stats"]):
                    met = True

        # Content checks
        elif "包含" in c or "列出" in c or "说明" in c:
            # Check for data presence (numbers, tables, lists)
            import re
            has_numbers = bool(re.search(r'\d+', all_content))
            has_table = "|" in all_content and "---" in all_content
            has_list = "- " in all_content or "1." in all_content
            has_data = has_numbers or has_table or has_list

            if "数据" in c or "数字" in c or "表格" in c or "列表" in c:
                met = has_data
            elif "规则" in c:
                met = "规则" in all_content and has_data
            elif "状态" in c:
                met = any(kw in all_content for kw in ["正常", "异常", "健康", "连接", "状态"])
            elif "告警" in c:
                met = any(kw in all_content for kw in ["告警", "异常", "条", "严重"])
            elif "数据源" in c or "数据表" in c:
                met = has_data
            else:
                met = len(all_content) > 50  # Generic: has meaningful content

        # Helpfulness / language checks
        elif "帮助" in c or "有帮助" in c:
            met = len(all_content) > 100
        elif "中文" in c:
            met = any('\u4e00' <= ch <= '\u9fff' for ch in all_content[:100])
        elif "友好" in c:
            met = len(all_content) > 50 and "?" not in all_content[:20]

        # Fallback: if criterion doesn't match patterns, pass if we have content
        else:
            met = len(all_content) > 50

        if met:
            passed.append(criterion)
        else:
            failed.append(criterion)

    reasoning = f"工具调用: {tools_used}; 回复长度: {len(all_content)} 字符"
    return {
        "success": len(failed) == 0,
        "passed": passed,
        "failed": failed,
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Load personas
# ---------------------------------------------------------------------------

def load_tasks(config: PilotConfig) -> list[dict]:
    """Load persona tasks from YAML."""
    yaml_path = Path(config.personas_file) if config.personas_file else None
    if not yaml_path or not yaml_path.exists():
        # Pilot 优先用自己的 personas.yaml（criteria 适配真实环境）
        pilot_yaml = Path(__file__).parent / "personas.yaml"
        if pilot_yaml.exists():
            yaml_path = pilot_yaml
        else:
            yaml_path = Path(__file__).parent.parent / "scenarios" / "personas.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"personas.yaml not found: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tasks = []
    for role in data.get("roles", []):
        for persona in role.get("personas", []):
            for task in role.get("tasks", []):
                tasks.append({
                    "role_id": role["id"],
                    "role_name": role["name"],
                    "role_description": role["description"],
                    "persona_id": persona["id"],
                    "persona_name": persona["name"],
                    "persona_traits": persona["traits"],
                    "task_id": task["id"],
                    "task_name": task["name"],
                    "task_goal": task["goal"],
                    "criteria": task["criteria"],
                    "messages": task.get("messages", []),
                })
    return tasks


# ---------------------------------------------------------------------------
# Run one scenario
# ---------------------------------------------------------------------------

async def run_scenario(
    config: PilotConfig, task: dict,
    llm_model: str, llm_key: str, llm_base: str,
) -> ConversationRecord:
    """Run one multi-turn conversation through the real OrderGuard HTTP endpoint."""
    record = ConversationRecord(
        role_name=task["role_name"],
        persona_name=task["persona_name"],
        task_name=task["task_name"],
        task_id=task["task_id"],
        criteria=task["criteria"],
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Unique IDs for this scenario — simulates a unique Feishu user + chat
    user_id = f"pilot_{task['role_id']}_{task['persona_id']}"
    chat_id = f"pilot_chat_{task['role_id']}_{task['persona_id']}_{uuid.uuid4().hex[:8]}"

    persona_prompt = (
        f"你扮演「{task['persona_name']}」，一位{task['role_name']}。\n"
        f"背景：{task['role_description']}\n"
        f"性格：{task['persona_traits']}\n"
        f"任务：{task['task_name']}\n"
        f"目标：{task['task_goal']}\n\n"
        f"用中文自然沟通，像真实用户一样逐步提问，不要一次说完。"
    )

    conversation: list[dict] = []
    preset_messages = task.get("messages", [])

    try:
        await _ensure_agent_infra()

        max_turns = min(config.max_turns_per_scenario, len(preset_messages)) if preset_messages else config.max_turns_per_scenario

        for turn in range(max_turns):
            # 1. Get user message: preset or AI-generated
            if preset_messages and turn < len(preset_messages):
                user_msg = preset_messages[turn]
            else:
                logger.debug("Generating user message (turn {})...", turn + 1)
                user_msg = await generate_user_message(
                    persona_prompt, conversation, llm_model, llm_key, llm_base,
                )
            if not user_msg.strip():
                logger.warning("用户消息为空 (turn {}), 跳过", turn + 1)
                break

            conversation.append({"role": "user", "content": user_msg})
            record.messages.append({
                "role": "user", "content": user_msg,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("[{}] 用户: {}", task["persona_name"], user_msg[:80])

            # 2. Call Agent directly (same code path as Feishu bot)
            t0 = time.time()
            try:
                # Build context from prior conversation
                ctx = [{"role": m["role"], "content": m["content"]} for m in conversation[:-1]]
                reply = await _call_agent(user_id, user_msg, ctx)
            except Exception as e:
                record.error = f"第 {turn + 1} 轮 Agent 错误: {e}"
                logger.error(record.error)
                break

            elapsed = time.time() - t0
            record.response_times.append(elapsed)

            # Extract tools from agent result
            turn_tools = [tc["tool"] for tc in _last_agent_tools] if _last_agent_tools else []

            conversation.append({"role": "assistant", "content": reply})
            record.messages.append({
                "role": "assistant", "content": reply,
                "ts": datetime.now(timezone.utc).isoformat(),
                "response_time": round(elapsed, 1),
                "tools_used": turn_tools,
            })
            logger.info("[{}] Agent ({:.1f}s): {}", task["persona_name"], elapsed, reply[:80])

            # 3. Continue if Agent asked a question and we haven't reached max turns
            if turn >= config.max_turns_per_scenario - 1:
                break
            if "？" not in reply and "?" not in reply and turn >= 1:
                break

        # 5. Evaluate (rule-based, no LLM needed)
        if conversation:
            # Collect tools used from agent results
            tools_used = []
            for m in record.messages:
                for t in m.get("tools_used", []):
                    if t not in tools_used:
                        tools_used.append(t)

            ev = evaluate_conversation_rules(conversation, task["criteria"], tools_used)
            record.success = ev["success"]
            record.passed_criteria = ev["passed"]
            record.failed_criteria = ev["failed"]
            record.reasoning = ev["reasoning"]

    except Exception as e:
        record.error = str(e)
        logger.error("Scenario error: {}", e)

    record.finished_at = datetime.now(timezone.utc).isoformat()
    return record


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report_card(records: list[ConversationRecord]) -> dict:
    """Build Feishu card for test results."""
    passed = sum(1 for r in records if r.success)
    total = len(records)
    all_rts = [rt for r in records for rt in r.response_times]
    avg_rt = sum(all_rts) / len(all_rts) if all_rts else 0

    header_color = "green" if passed == total else ("orange" if passed > total // 2 else "red")

    lines = ["| 角色 | 人设 | 场景 | 结果 | 响应 |",
             "|------|------|------|------|------|"]
    for r in records:
        status = "✅" if r.success else "❌"
        avg = sum(r.response_times) / len(r.response_times) if r.response_times else 0
        lines.append(f"| {r.role_name} | {r.persona_name} | {r.task_name} | {status} | {avg:.1f}s |")

    failed_parts = []
    for r in records:
        if not r.success:
            failed_parts.append(f"**{r.persona_name} — {r.task_name}**")
            for c in r.failed_criteria:
                failed_parts.append(f"  - ❌ {c}")
            if r.error:
                failed_parts.append(f"  - 错误: {r.error[:100]}")

    elements = [
        {"tag": "markdown", "content": f"**{passed}/{total} 通过** | 平均响应 {avg_rt:.1f}s"},
        {"tag": "hr"},
        {"tag": "markdown", "content": "\n".join(lines)},
    ]
    if failed_parts:
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": "\n".join(failed_parts)}]
    elements += [{"tag": "hr"}, {"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"TestPilot | {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
    ]}]

    return {
        "header": {"title": {"tag": "plain_text", "content": f"TestPilot — {passed}/{total} 通过"}, "template": header_color},
        "elements": elements,
    }


async def push_report(records: list[ConversationRecord], webhook_url: str):
    """Push report card to Feishu webhook."""
    if not webhook_url:
        return
    card = build_report_card(records)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json={"msg_type": "interactive", "card": card})
        logger.info("Report pushed to Feishu")
    except Exception as e:
        logger.error("Report push failed: {}", e)


def save_report(records: list[ConversationRecord]) -> Path:
    """Save JSON report to disk."""
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"pilot_{ts}.json"

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
        "passed": sum(1 for r in records if r.success),
        "records": [
            {
                "role": r.role_name, "persona": r.persona_name,
                "task": r.task_name, "success": r.success,
                "passed_criteria": r.passed_criteria,
                "failed_criteria": r.failed_criteria,
                "reasoning": r.reasoning,
                "response_times": r.response_times,
                "messages": r.messages,
                "error": r.error,
                "started_at": r.started_at, "finished_at": r.finished_at,
            }
            for r in records
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# Resolve LLM config
# ---------------------------------------------------------------------------

def resolve_llm(config: PilotConfig) -> tuple[str, str, str]:
    """Get LLM model/key/base from pilot config or OrderGuard settings."""
    model, key, base = config.llm_model, config.llm_api_key, config.llm_api_base
    if not model or not key:
        try:
            from order_guard.config import get_settings
            s = get_settings()
            model = model or s.llm.model
            key = key or (s.llm.api_key.get_secret_value() if s.llm.api_key else "")
            base = base or s.llm.api_base or ""
        except Exception:
            pass
    if not model or not key:
        raise ValueError("LLM 未配置")
    return model, key, base


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_pilot(
    config: PilotConfig,
    once: bool = False,
    role_filter: str | None = None,
    task_filter: str | None = None,
):
    """Main TestPilot loop.

    Runs inside the same process as OrderGuard (called from CLI after server starts),
    or as a standalone script pointing at a running OrderGuard instance.
    """
    llm_model, llm_key, llm_base = resolve_llm(config)
    tasks = load_tasks(config)

    if role_filter:
        tasks = [t for t in tasks if role_filter in (t["role_id"], t["role_name"])]
    if task_filter:
        tasks = [t for t in tasks if task_filter in (t["task_id"], t["task_name"])]
    if not tasks:
        logger.error("No matching scenarios")
        return []

    logger.info("TestPilot: {} scenarios, mode={}, interval={}s",
                len(tasks), config.mode, config.interval_seconds)

    all_records: list[ConversationRecord] = []
    round_count = 0

    try:
        while True:
            round_count += 1
            round_tasks = list(tasks)
            if config.mode == "random":
                random.shuffle(round_tasks)

            logger.info("=== Round {} ({} scenarios) ===", round_count, len(round_tasks))

            round_records = []
            for i, task in enumerate(round_tasks):
                label = f"{task['persona_name']}/{task['task_name']}"
                logger.info("[{}/{}] {}", i + 1, len(round_tasks), label)

                record = await run_scenario(config, task, llm_model, llm_key, llm_base)
                round_records.append(record)
                all_records.append(record)

                status = "✅" if record.success else "❌"
                logger.info("  {} ({:.1f}s avg)", status,
                            sum(record.response_times) / len(record.response_times) if record.response_times else 0)

            # Round report
            passed = sum(1 for r in round_records if r.success)
            logger.info("Round {} done: {}/{} passed", round_count, passed, len(round_records))

            path = save_report(round_records)
            logger.info("Report: {}", path)

            if config.feishu_webhook_url:
                await push_report(round_records, config.feishu_webhook_url)

            if once:
                break
            if config.total_rounds > 0 and round_count >= config.total_rounds:
                break

            logger.info("Next round in {}s...", config.interval_seconds)
            await asyncio.sleep(config.interval_seconds)

    except KeyboardInterrupt:
        logger.info("TestPilot stopped")
    finally:
        # Flush LangWatch traces before cleanup
        try:
            from langwatch.client import get_instance
            get_instance().tracer_provider.force_flush(timeout_millis=10000)
            logger.info("LangWatch traces flushed")
        except Exception as e:
            logger.debug("LangWatch flush skipped: {}", e)
        await _cleanup_agent_infra()

    return all_records
