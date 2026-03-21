"""Long-Running Agent Session Test — stress test + metrics collection.

Simulates a 30-50 turn continuous conversation with multiple topic switches,
collecting per-turn metrics to identify performance degradation and optimization
opportunities.

Metrics collected per turn:
- response_time_ms: wall clock time for agent.run_unified()
- prompt_tokens / completion_tokens / total_tokens
- tool_calls_count: number of tool calls in the agent loop
- context_messages_count: total messages fed to LLM (grows over time)
- context_chars: approximate character count of context
- tools_used: which tools were called

Degradation signals:
- Response time increasing over turns
- Token usage exploding (context window pressure)
- Tool call failures or empty responses
- Hallucination creep (late-turn answers less grounded)

Usage:
    # Run with real databases (MySQL/PG)
    uv run pytest tests/scenarios/test_long_session.py -v -m e2e -s

    # Quick test (fewer turns)
    uv run pytest tests/scenarios/test_long_session.py -v -m e2e -s -k "short"
"""
from __future__ import annotations

import json
import time
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ─── Per-turn metrics ────────────────────────────────────────────────────────

@dataclass
class TurnMetrics:
    """Metrics for a single conversation turn."""
    turn_number: int
    user_message: str
    agent_response: str  # truncated
    topic: str  # which conversation phase

    # Timing
    response_time_ms: int = 0

    # Token usage (from LLM)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Tool calls
    tool_calls_count: int = 0
    tools_used: list[str] = field(default_factory=list)

    # Context size
    context_messages_count: int = 0
    context_chars: int = 0

    # Errors
    error: str = ""
    is_empty_response: bool = False


@dataclass
class SessionReport:
    """Aggregate report for a long-running session."""
    session_id: str
    total_turns: int
    total_time_s: float
    persona: str
    model: str

    # Per-turn data
    turns: list[TurnMetrics] = field(default_factory=list)

    # Aggregates (computed)
    avg_response_time_ms: float = 0
    p50_response_time_ms: float = 0
    p95_response_time_ms: float = 0
    max_response_time_ms: int = 0

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_all_tokens: int = 0

    total_tool_calls: int = 0
    unique_tools_used: list[str] = field(default_factory=list)

    error_count: int = 0
    empty_response_count: int = 0

    # Degradation signals
    degradation: dict[str, Any] = field(default_factory=dict)

    def compute_aggregates(self):
        """Compute aggregate metrics from per-turn data."""
        if not self.turns:
            return

        times = [t.response_time_ms for t in self.turns if not t.error]
        if times:
            self.avg_response_time_ms = statistics.mean(times)
            self.p50_response_time_ms = statistics.median(times)
            sorted_times = sorted(times)
            p95_idx = int(len(sorted_times) * 0.95)
            self.p95_response_time_ms = sorted_times[min(p95_idx, len(sorted_times) - 1)]
            self.max_response_time_ms = max(times)

        self.total_prompt_tokens = sum(t.prompt_tokens for t in self.turns)
        self.total_completion_tokens = sum(t.completion_tokens for t in self.turns)
        self.total_all_tokens = sum(t.total_tokens for t in self.turns)
        self.total_tool_calls = sum(t.tool_calls_count for t in self.turns)

        all_tools = set()
        for t in self.turns:
            all_tools.update(t.tools_used)
        self.unique_tools_used = sorted(all_tools)

        self.error_count = sum(1 for t in self.turns if t.error)
        self.empty_response_count = sum(1 for t in self.turns if t.is_empty_response)

        # Detect degradation
        self.degradation = detect_degradation(self.turns)


def detect_degradation(turns: list[TurnMetrics]) -> dict[str, Any]:
    """Detect performance degradation patterns across turns."""
    result: dict[str, Any] = {"signals": [], "severity": "none"}

    valid_turns = [t for t in turns if not t.error]
    if len(valid_turns) < 6:
        return result

    # Split into first half / second half
    mid = len(valid_turns) // 2
    first_half = valid_turns[:mid]
    second_half = valid_turns[mid:]

    # 1. Response time degradation
    avg_first = statistics.mean([t.response_time_ms for t in first_half])
    avg_second = statistics.mean([t.response_time_ms for t in second_half])
    if avg_first > 0:
        time_increase_pct = (avg_second - avg_first) / avg_first * 100
        if time_increase_pct > 50:
            result["signals"].append({
                "type": "response_time_degradation",
                "first_half_avg_ms": round(avg_first),
                "second_half_avg_ms": round(avg_second),
                "increase_pct": round(time_increase_pct, 1),
            })

    # 2. Token usage explosion (prompt tokens growing)
    first_prompt = statistics.mean([t.prompt_tokens for t in first_half]) if first_half else 0
    second_prompt = statistics.mean([t.prompt_tokens for t in second_half]) if second_half else 0
    if first_prompt > 0:
        token_increase_pct = (second_prompt - first_prompt) / first_prompt * 100
        if token_increase_pct > 100:
            result["signals"].append({
                "type": "token_explosion",
                "first_half_avg_prompt_tokens": round(first_prompt),
                "second_half_avg_prompt_tokens": round(second_prompt),
                "increase_pct": round(token_increase_pct, 1),
            })

    # 3. Context size growth
    context_sizes = [t.context_chars for t in valid_turns]
    if context_sizes[-1] > context_sizes[0] * 3 and context_sizes[-1] > 10000:
        result["signals"].append({
            "type": "context_bloat",
            "first_turn_chars": context_sizes[0],
            "last_turn_chars": context_sizes[-1],
            "growth_factor": round(context_sizes[-1] / max(context_sizes[0], 1), 1),
        })

    # 4. Empty responses in second half
    second_half_empties = sum(1 for t in second_half if t.is_empty_response)
    if second_half_empties > 0:
        result["signals"].append({
            "type": "late_empty_responses",
            "count": second_half_empties,
            "turns": [t.turn_number for t in second_half if t.is_empty_response],
        })

    # 5. Tool call pattern changes
    first_tool_avg = statistics.mean([t.tool_calls_count for t in first_half]) if first_half else 0
    second_tool_avg = statistics.mean([t.tool_calls_count for t in second_half]) if second_half else 0
    if first_tool_avg > 0 and second_tool_avg / first_tool_avg > 2:
        result["signals"].append({
            "type": "tool_call_inflation",
            "first_half_avg": round(first_tool_avg, 1),
            "second_half_avg": round(second_tool_avg, 1),
        })

    # Severity
    if len(result["signals"]) >= 3:
        result["severity"] = "high"
    elif len(result["signals"]) >= 1:
        result["severity"] = "medium"

    return result


# ─── Conversation script ────────────────────────────────────────────────────

# Topic phases for a long session — simulates realistic usage patterns
LONG_SESSION_SCRIPT: list[dict[str, str]] = [
    # Phase 1: 订单摸底 (turns 1-5)
    {"topic": "orders_overview", "message": "帮我看看最近的订单整体情况怎么样？"},
    {"topic": "orders_overview", "message": "最近一周和上个月比，订单量变化大吗？"},
    {"topic": "orders_overview", "message": "客单价大概是多少？有没有下降趋势？"},
    {"topic": "orders_overview", "message": "哪些品类卖得最好？"},
    {"topic": "orders_overview", "message": "有没有哪些订单状态异常的？比如长时间未发货的？"},

    # Phase 2: 评分/退款分析 (turns 6-10)
    {"topic": "reviews_returns", "message": "切换个话题，最近客户评分情况怎么样？"},
    {"topic": "reviews_returns", "message": "低评分的订单主要集中在哪些商品？"},
    {"topic": "reviews_returns", "message": "退款率是多少？跟上个月比怎么样？"},
    {"topic": "reviews_returns", "message": "退款原因主要是什么？能分类看看吗？"},
    {"topic": "reviews_returns", "message": "评分最差的前5个商品，能列出来吗？"},

    # Phase 3: 物流/交付 (turns 11-15)
    {"topic": "logistics", "message": "看看物流情况，平均配送时间是多少天？"},
    {"topic": "logistics", "message": "有没有配送超时严重的订单？"},
    {"topic": "logistics", "message": "不同物流渠道的表现对比怎样？"},
    {"topic": "logistics", "message": "哪些地区配送最慢？"},
    {"topic": "logistics", "message": "物流延迟和差评之间有没有关联？"},

    # Phase 4: 规则配置 (turns 16-20)
    {"topic": "rules", "message": "现在有哪些监控规则在运行？"},
    {"topic": "rules", "message": "这些规则最近的触发情况怎么样？有没有误报多的？"},
    {"topic": "rules", "message": "帮我创建一个规则：检测评分低于3星的订单突增，每天早上8点运行"},
    {"topic": "rules", "message": "确认创建"},
    {"topic": "rules", "message": "能测试一下刚创建的规则吗？"},

    # Phase 5: 告警检查 (turns 21-25)
    {"topic": "alerts", "message": "看看最近有什么告警"},
    {"topic": "alerts", "message": "有多少未处理的告警？"},
    {"topic": "alerts", "message": "按严重程度分组看看"},
    {"topic": "alerts", "message": "把不重要的 info 级别告警全部标记为已处理"},
    {"topic": "alerts", "message": "确认处理"},

    # Phase 6: 回顾验证 (turns 26-30) — 回到之前话题，测试一致性
    {"topic": "consistency_check", "message": "回到之前的话题，刚才说订单量是多少来着？"},
    {"topic": "consistency_check", "message": "那个配送最慢的地区是哪个？再确认一下"},
    {"topic": "consistency_check", "message": "评分最差的商品，刚才说的和现在查出来的一样吗？"},
    {"topic": "consistency_check", "message": "给我出一个今天检查的总结报告"},
    {"topic": "consistency_check", "message": "整体来看，最需要优先处理的3件事是什么？"},

    # Phase 7: 压力追加 (turns 31-35) — 连续快速提问
    {"topic": "rapid_fire", "message": "今天总共卖了多少钱？"},
    {"topic": "rapid_fire", "message": "库存不足的商品有几个？"},
    {"topic": "rapid_fire", "message": "系统的数据源都正常吗？检查一下"},
    {"topic": "rapid_fire", "message": "LLM 用量统计看看，今天花了多少 token"},
    {"topic": "rapid_fire", "message": "好的，今天就到这里，谢谢"},
]

# Shorter version for quick testing
SHORT_SESSION_SCRIPT = LONG_SESSION_SCRIPT[:15]


# ─── Session runner ──────────────────────────────────────────────────────────

async def run_long_session(
    infra: dict,
    script: list[dict[str, str]] | None = None,
    persona_name: str = "李姐（运营主管）",
    session_id: str | None = None,
) -> SessionReport:
    """Run a long conversation session and collect per-turn metrics.

    Args:
        infra: investigation infra dict (from investigation_infra fixture)
        script: list of {"topic", "message"} dicts. Defaults to LONG_SESSION_SCRIPT.
        persona_name: persona label for report.
        session_id: unique session ID for report.
    """
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import build_unified_prompt
    from order_guard.tools import (
        data_tools, rule_tools, context_tools, alert_tools,
        health_tools, report_tools, usage_tools,
    )
    from order_guard.config import get_settings

    if script is None:
        script = LONG_SESSION_SCRIPT

    if session_id is None:
        session_id = f"long_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    settings = get_settings()
    model_name = settings.llm.model

    # Build agent (same as investigation tests)
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

    # Remove schema discovery tools if pre-injected
    if schema_context:
        _SKIP = {"list_datasources", "get_schema"}
        all_tools = [t for t in all_tools if t.name not in _SKIP]
        for k in _SKIP:
            all_executors.pop(k, None)

    system_prompt = build_unified_prompt(schema_context=schema_context)

    agent = Agent(
        llm_client=LLMClient(),
        data_access_layer=infra["dal"],
        config=AgentConfig(
            inject_business_context=False,
            max_iterations=8,
        ),
        tools=all_tools,
        tool_executors=all_executors,
    )

    report = SessionReport(
        session_id=session_id,
        total_turns=len(script),
        total_time_s=0,
        persona=persona_name,
        model=model_name,
    )

    conversation: list[dict[str, Any]] = []
    session_start = time.time()

    for i, step in enumerate(script):
        turn_num = i + 1
        user_msg = step["message"]
        topic = step["topic"]

        logger.info(
            "[Long T{}/{}] topic={} | {}",
            turn_num, len(script), topic, user_msg[:50],
        )

        # Measure context size before call
        ctx_msgs = list(conversation)  # copy
        ctx_chars = sum(len(m.get("content", "")) for m in ctx_msgs)

        conversation.append({"role": "user", "content": user_msg})

        metrics = TurnMetrics(
            turn_number=turn_num,
            user_message=user_msg,
            agent_response="",
            topic=topic,
            context_messages_count=len(ctx_msgs),
            context_chars=ctx_chars,
        )

        t0 = time.time()
        try:
            result = await agent.run_unified(
                user_message=user_msg,
                system_prompt=system_prompt,
                context_messages=ctx_msgs,
                trigger_type="chat",
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            reply = result.response or ""
            tool_log = result.tool_calls_log or []

            metrics.response_time_ms = elapsed_ms
            metrics.agent_response = reply[:500]
            metrics.tool_calls_count = len(tool_log)
            metrics.tools_used = list({tc["tool"] for tc in tool_log})
            metrics.is_empty_response = len(reply.strip()) == 0

            # Token usage from AgentResult (added in this session)
            if result.token_usage:
                metrics.prompt_tokens = result.token_usage.prompt_tokens
                metrics.completion_tokens = result.token_usage.completion_tokens
                metrics.total_tokens = result.token_usage.total_tokens

            conversation.append({"role": "assistant", "content": reply})

        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            metrics.response_time_ms = elapsed_ms
            metrics.error = str(e)[:200]
            logger.error("[Long T{}] ERROR: {}", turn_num, e)

        report.turns.append(metrics)

        # Progress log
        status = "❌" if metrics.error else ("⚠️" if metrics.is_empty_response else "✅")
        logger.info(
            "  {} {:.1f}s | tools={} | ctx_msgs={} ctx_chars={} | {}",
            status, elapsed_ms / 1000,
            metrics.tools_used,
            metrics.context_messages_count,
            metrics.context_chars,
            metrics.agent_response[:60] + "..." if len(metrics.agent_response) > 60 else metrics.agent_response,
        )

    report.total_time_s = time.time() - session_start
    report.compute_aggregates()

    return report


# ─── Report output ──────────────────────────────────────────────────────────

def save_long_session_report(report: SessionReport) -> Path:
    """Save session report as JSON."""
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"long_session_{ts}.json"

    data = {
        "session_id": report.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "persona": report.persona,
        "model": report.model,
        "total_turns": report.total_turns,
        "total_time_s": round(report.total_time_s, 1),
        "summary": {
            "avg_response_time_ms": round(report.avg_response_time_ms),
            "p50_response_time_ms": round(report.p50_response_time_ms),
            "p95_response_time_ms": round(report.p95_response_time_ms),
            "max_response_time_ms": report.max_response_time_ms,
            "total_prompt_tokens": report.total_prompt_tokens,
            "total_completion_tokens": report.total_completion_tokens,
            "total_all_tokens": report.total_all_tokens,
            "total_tool_calls": report.total_tool_calls,
            "unique_tools_used": report.unique_tools_used,
            "error_count": report.error_count,
            "empty_response_count": report.empty_response_count,
        },
        "degradation": report.degradation,
        "turns": [
            {
                "turn": t.turn_number,
                "topic": t.topic,
                "user_message": t.user_message,
                "agent_response": t.agent_response,
                "response_time_ms": t.response_time_ms,
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "total_tokens": t.total_tokens,
                "tool_calls_count": t.tool_calls_count,
                "tools_used": t.tools_used,
                "context_messages_count": t.context_messages_count,
                "context_chars": t.context_chars,
                "error": t.error,
                "is_empty_response": t.is_empty_response,
            }
            for t in report.turns
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def print_session_summary(report: SessionReport):
    """Print a terminal-friendly summary of the session."""
    print(f"\n{'='*70}")
    print(f"  Long Session Report: {report.session_id}")
    print(f"  Model: {report.model} | Persona: {report.persona}")
    print(f"  Turns: {report.total_turns} | Time: {report.total_time_s:.1f}s")
    print(f"{'='*70}")

    print(f"\n📊 Response Time:")
    print(f"  avg={report.avg_response_time_ms:.0f}ms  p50={report.p50_response_time_ms:.0f}ms"
          f"  p95={report.p95_response_time_ms:.0f}ms  max={report.max_response_time_ms}ms")

    print(f"\n🔢 Token Usage:")
    print(f"  prompt={report.total_prompt_tokens:,}  completion={report.total_completion_tokens:,}"
          f"  total={report.total_all_tokens:,}")
    if report.turns:
        avg_prompt = report.total_prompt_tokens / len(report.turns)
        print(f"  avg prompt/turn={avg_prompt:,.0f}")

    print(f"\n🔧 Tool Calls:")
    print(f"  total={report.total_tool_calls}  tools={report.unique_tools_used}")

    print(f"\n❗ Issues:")
    print(f"  errors={report.error_count}  empty_responses={report.empty_response_count}")

    # Per-turn timeline
    print(f"\n📈 Turn-by-Turn Timeline:")
    print(f"  {'Turn':<5} {'Topic':<20} {'Time':>8} {'Tools':>6} {'CtxMsg':>7} {'CtxChar':>8} {'Status'}")
    print(f"  {'─'*5} {'─'*20} {'─'*8} {'─'*6} {'─'*7} {'─'*8} {'─'*6}")
    for t in report.turns:
        status = "ERR" if t.error else ("EMPTY" if t.is_empty_response else "OK")
        print(
            f"  {t.turn_number:<5} {t.topic:<20} {t.response_time_ms/1000:>7.1f}s"
            f" {t.tool_calls_count:>6} {t.context_messages_count:>7} {t.context_chars:>8} {status}"
        )

    # Degradation
    deg = report.degradation
    if deg.get("signals"):
        print(f"\n⚠️  Degradation Signals (severity: {deg['severity']}):")
        for sig in deg["signals"]:
            sig_type = sig["type"]
            if sig_type == "response_time_degradation":
                print(f"  ⏱️  Response time increased {sig['increase_pct']:.0f}% "
                      f"(first half avg={sig['first_half_avg_ms']}ms → second half avg={sig['second_half_avg_ms']}ms)")
            elif sig_type == "token_explosion":
                print(f"  💥 Prompt tokens increased {sig['increase_pct']:.0f}% "
                      f"(first half avg={sig['first_half_avg_prompt_tokens']} → {sig['second_half_avg_prompt_tokens']})")
            elif sig_type == "context_bloat":
                print(f"  📦 Context grew {sig['growth_factor']}x "
                      f"({sig['first_turn_chars']} → {sig['last_turn_chars']} chars)")
            elif sig_type == "late_empty_responses":
                print(f"  🕳️  {sig['count']} empty responses in second half (turns: {sig['turns']})")
            elif sig_type == "tool_call_inflation":
                print(f"  🔧 Tool calls increased "
                      f"(first half avg={sig['first_half_avg']:.1f} → {sig['second_half_avg']:.1f})")
    else:
        print(f"\n✅ No degradation signals detected")

    print(f"\n{'='*70}")
