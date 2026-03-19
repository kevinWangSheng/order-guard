"""Temporal Coherence Test — E8.

Tests that the Agent's answers remain consistent throughout a long conversation.
Injects "probe questions" at specific turns and checks that the same question
asked later gives the same answer (when no new data was queried in between).

Detects: context window overflow, memory degradation, hallucination creep.

Usage:
    uv run pytest tests/scenarios/test_temporal.py -v -m e2e
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


# ─── Probe definition ────────────────────────────────────────────────────────

@dataclass
class TemporalProbe:
    """A probe question injected at a specific turn."""
    probe_id: str
    question: str         # question to inject
    inject_at_turn: int   # inject at this user turn (1-indexed)
    repeat_at_turn: int   # repeat at this user turn
    extractor: str        # "sku_list" | "number" | "yesno" | "verbatim"


@dataclass
class ProbeResult:
    probe_id: str
    first_answer: str
    second_answer: str
    consistent: bool
    inconsistency_note: str = ""


@dataclass
class CoherenceScore:
    scenario_label: str
    total_turns: int
    probe_results: list[ProbeResult] = field(default_factory=list)
    consistent: bool = True   # True iff all probes are consistent
    overall_score: float = 1.0  # fraction of consistent probes

    @property
    def summary(self) -> str:
        passed = sum(1 for p in self.probe_results if p.consistent)
        total = len(self.probe_results)
        return (
            f"Temporal coherence: {passed}/{total} probes consistent "
            f"({self.overall_score:.0%})"
        )


# ─── Default 30-turn scenario ─────────────────────────────────────────────────

# A 30-turn conversation scaffold that covers multiple topics.
# The UserSimulator follows this guide; probes are injected by ProbeInjector.
LONG_CONVERSATION_GUIDE = """
你是一位有经验的电商运营主管（李姐）。
你在做一个全面的经营检查，按这个顺序推进：

第1-6轮：检查库存
  - 先问整体库存情况
  - 追问最危险的 SKU
  - 要求估算库存天数

第7-12轮：切换到退货分析
  - 问退货率整体情况
  - 追问退货最高的商品
  - 要求分析原因

第13-18轮：切换到销售数据
  - 问本月销售总额
  - 追问最畅销品类
  - 问销售趋势

第19-24轮：切换到规则配置
  - 问有哪些监控规则
  - 讨论是否需要新规则

第25-30轮：回顾总结
  - 回到库存话题（触发探针验证）
  - 要求出一个今日操作总结

用中文自然交流，像真实用户一样。每轮只说1-2句话。
"""

DEFAULT_PROBES = [
    TemporalProbe(
        probe_id="stockout_check",
        question="SKU-001 现在库存是多少？有货吗？",
        inject_at_turn=3,
        repeat_at_turn=26,
        extractor="yesno",        # Check: still says "no stock / 缺货"
    ),
    TemporalProbe(
        probe_id="return_rate",
        question="那个退货率最高的商品是哪个？大概多少？",
        inject_at_turn=9,
        repeat_at_turn=27,
        extractor="sku_list",     # Check: still mentions SKU-004
    ),
]


# ─── Probe injector ──────────────────────────────────────────────────────────

class ProbeInjector:
    """Injects probe questions at specific turns during a conversation.

    Works with the simple turn loop (not scenario framework).
    """

    def __init__(self, probes: list[TemporalProbe]):
        self._probes = {p.inject_at_turn: p for p in probes}
        self._probes.update({p.repeat_at_turn: p for p in probes})
        self._first_answers: dict[str, str] = {}

    def get_override(self, turn: int) -> str | None:
        """Return probe question if this turn should be overridden, else None."""
        probe = self._probes.get(turn)
        return probe.question if probe else None

    def record_answer(self, turn: int, answer: str) -> None:
        """Record the agent's answer at a probe turn."""
        probe = self._probes.get(turn)
        if not probe:
            return
        if turn == probe.inject_at_turn:
            self._first_answers[probe.probe_id] = answer
        # For repeat turn: handled in evaluate()

    def get_first_answer(self, probe_id: str) -> str:
        return self._first_answers.get(probe_id, "")


# ─── Consistency evaluation ──────────────────────────────────────────────────

def evaluate_consistency(
    probe: TemporalProbe,
    first_answer: str,
    second_answer: str,
) -> ProbeResult:
    """Check if two answers are consistent for a given probe."""
    first = first_answer.lower()
    second = second_answer.lower()

    consistent = False
    note = ""

    if probe.extractor == "yesno":
        # Both should agree: both say "no stock/缺货" or both say "has stock"
        first_no = any(kw in first for kw in ["缺货", "没有", "0", "库存为0", "out of stock"])
        second_no = any(kw in second for kw in ["缺货", "没有", "0", "库存为0", "out of stock"])
        first_yes = any(kw in first for kw in ["有货", "库存", "件", "in stock"])
        second_yes = any(kw in second for kw in ["有货", "库存", "件", "in stock"])
        consistent = (first_no == second_no)
        if not consistent:
            note = f"First says {'缺货' if first_no else '有货'}, second says {'缺货' if second_no else '有货'}"

    elif probe.extractor == "sku_list":
        # Both should mention the same SKU(s)
        first_skus = set(re.findall(r"SKU-\d+", first_answer, re.IGNORECASE))
        second_skus = set(re.findall(r"SKU-\d+", second_answer, re.IGNORECASE))
        if not first_skus and not second_skus:
            consistent = True  # Neither mentioned a SKU — both vague
        elif not first_skus or not second_skus:
            consistent = False
            note = f"First SKUs: {first_skus}, Second SKUs: {second_skus}"
        else:
            # At least some overlap
            consistent = bool(first_skus & second_skus)
            if not consistent:
                note = f"No overlap — First: {first_skus}, Second: {second_skus}"

    elif probe.extractor == "number":
        # Extract first number from each answer and compare (allow ±10%)
        def extract_number(text: str) -> float | None:
            matches = re.findall(r"\d+\.?\d*", text)
            return float(matches[0]) if matches else None

        n1, n2 = extract_number(first_answer), extract_number(second_answer)
        if n1 is None or n2 is None:
            consistent = (n1 is None and n2 is None)
            note = f"Could not extract numbers: n1={n1}, n2={n2}"
        else:
            diff = abs(n1 - n2) / max(n1, n2, 1)
            consistent = diff <= 0.10  # Allow 10% variance
            if not consistent:
                note = f"Numbers differ > 10%: {n1} vs {n2}"

    elif probe.extractor == "verbatim":
        # Rough semantic similarity: >50% word overlap
        words1 = set(re.findall(r"\w+", first))
        words2 = set(re.findall(r"\w+", second))
        if not words1 and not words2:
            consistent = True
        elif not words1 or not words2:
            consistent = False
        else:
            overlap = len(words1 & words2) / len(words1 | words2)
            consistent = overlap >= 0.4
            if not consistent:
                note = f"Word overlap only {overlap:.0%}"

    return ProbeResult(
        probe_id=probe.probe_id,
        first_answer=first_answer[:200],
        second_answer=second_answer[:200],
        consistent=consistent,
        inconsistency_note=note,
    )


# ─── Main runner ─────────────────────────────────────────────────────────────

async def run_temporal_coherence_test(
    infra: dict,
    probes: list[TemporalProbe] | None = None,
    max_turns: int = 30,
    conversation_guide: str = LONG_CONVERSATION_GUIDE,
) -> CoherenceScore:
    """Run a long conversation with probe injections and evaluate consistency.

    Args:
        infra: investigation infra dict (from investigation_infra fixture)
        probes: list of TemporalProbe objects (defaults to DEFAULT_PROBES)
        max_turns: total conversation turns
        conversation_guide: system prompt for UserSimulatorAgent
    """
    from order_guard.config import get_settings
    from tests.scenarios.test_investigation import _build_agent

    if probes is None:
        probes = DEFAULT_PROBES

    settings = get_settings()
    llm_model = settings.llm.model
    llm_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None
    llm_base = settings.llm.api_base or None

    agent = _build_agent(infra)
    injector = ProbeInjector(probes)

    probe_answers: dict[str, dict[str, str]] = {}  # probe_id → {first, second}
    conversation: list[dict] = []

    import litellm

    for turn_num in range(1, max_turns + 1):
        # Check if this turn should be overridden by a probe
        probe_override = injector.get_override(turn_num)

        if probe_override:
            user_msg = probe_override
            logger.info("[Temporal T{}] 🔍 Probe: {}", turn_num, user_msg[:60])
        else:
            # Generate normal user message
            history_text = "\n".join(
                f"{'用户' if m['role']=='user' else 'Agent'}: {m['content'][:200]}"
                for m in conversation[-6:]  # last 3 turns for context
            )
            gen_prompt = (
                conversation_guide
                + f"\n\n最近的对话：\n{history_text}\n\n"
                f"现在是第 {turn_num} 轮，请根据指南发出下一条消息："
            )
            try:
                resp = await litellm.acompletion(
                    model=llm_model,
                    messages=[{"role": "user", "content": gen_prompt}],
                    temperature=0.7,
                    max_tokens=200,
                    api_key=llm_key,
                    api_base=llm_base or None,
                )
                user_msg = (resp.choices[0].message.content or "").strip().strip('"\'')
            except Exception as e:
                logger.warning("User sim failed at turn {}: {}", turn_num, e)
                break

        if not user_msg:
            continue

        conversation.append({"role": "user", "content": user_msg})

        # Call agent
        ctx = conversation[:-1]
        result = await agent.run_unified(
            user_message=user_msg,
            context_messages=ctx,
            trigger_type="chat",
        )
        reply = result.response or ""
        conversation.append({"role": "assistant", "content": reply})

        logger.debug("[Temporal T{}] Agent: {}", turn_num, reply[:80])

        # Record probe answer
        if probe_override:
            for probe in probes:
                if turn_num == probe.inject_at_turn:
                    if probe.probe_id not in probe_answers:
                        probe_answers[probe.probe_id] = {}
                    probe_answers[probe.probe_id]["first"] = reply
                elif turn_num == probe.repeat_at_turn:
                    if probe.probe_id not in probe_answers:
                        probe_answers[probe.probe_id] = {}
                    probe_answers[probe.probe_id]["second"] = reply

    # Evaluate consistency
    probe_results = []
    for probe in probes:
        answers = probe_answers.get(probe.probe_id, {})
        first = answers.get("first", "")
        second = answers.get("second", "")

        if not first or not second:
            result_obj = ProbeResult(
                probe_id=probe.probe_id,
                first_answer=first,
                second_answer=second,
                consistent=False,
                inconsistency_note="One or both probe answers are missing",
            )
        else:
            result_obj = evaluate_consistency(probe, first, second)

        probe_results.append(result_obj)
        icon = "✅" if result_obj.consistent else "❌"
        logger.info(
            "[Temporal] {} Probe '{}': consistent={} {}",
            icon, probe.probe_id, result_obj.consistent,
            f"({result_obj.inconsistency_note})" if result_obj.inconsistency_note else "",
        )

    consistent_count = sum(1 for r in probe_results if r.consistent)
    overall_score = consistent_count / len(probe_results) if probe_results else 1.0

    score = CoherenceScore(
        scenario_label=f"temporal_30turns_{len(probes)}probes",
        total_turns=len([m for m in conversation if m["role"] == "user"]),
        probe_results=probe_results,
        consistent=all(r.consistent for r in probe_results),
        overall_score=overall_score,
    )
    logger.info("{}", score.summary)
    return score
