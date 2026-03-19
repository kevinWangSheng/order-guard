"""Session Scorer — 5-dimension LLM-as-Judge for investigation scenarios.

Evaluates the FULL conversation trajectory (not individual turns) against
5 dimensions defined in scenarios_v2.yaml.

Usage:
    scorer = SessionScorer()
    score = await scorer.score(
        conversation=[{"role": "user", "content": "..."}, ...],
        scenario=loaded_scenario_dict,
    )
    print(score.passed, score.dimensions)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ─── Data models ─────────────────────────────────────────────────────────────

@dataclass
class DimensionResult:
    passed: bool
    reason: str


@dataclass
class SessionScore:
    scenario_id: str
    persona_id: str

    # 5 dimensions
    goal_achieved: DimensionResult = field(default_factory=lambda: DimensionResult(False, ""))
    data_accuracy: DimensionResult = field(default_factory=lambda: DimensionResult(False, ""))
    actionable: DimensionResult = field(default_factory=lambda: DimensionResult(False, ""))
    no_hallucination: DimensionResult = field(default_factory=lambda: DimensionResult(False, ""))
    conversation_quality: DimensionResult = field(default_factory=lambda: DimensionResult(False, ""))

    # Meta
    passed: bool = False          # True iff all 5 dimensions pass
    turns: int = 0
    tools_used: list[str] = field(default_factory=list)
    judge_error: str = ""         # non-empty if judge call failed

    @property
    def score_summary(self) -> dict[str, bool]:
        return {
            "goal_achieved": self.goal_achieved.passed,
            "data_accuracy": self.data_accuracy.passed,
            "actionable": self.actionable.passed,
            "no_hallucination": self.no_hallucination.passed,
            "conversation_quality": self.conversation_quality.passed,
        }

    @property
    def failed_dimensions(self) -> list[str]:
        return [k for k, v in self.score_summary.items() if not v]

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "persona_id": self.persona_id,
            "passed": self.passed,
            "turns": self.turns,
            "tools_used": self.tools_used,
            "dimensions": {
                k: {"passed": v, "reason": getattr(self, k).reason}
                for k, v in self.score_summary.items()
            },
            "judge_error": self.judge_error,
        }


# ─── Judge prompt ─────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
你是一位专业的 AI Agent 对话质量评估员。

你的任务：给定一段对话记录和评估标准，对每个维度独立打分（PASS / FAIL）并给出简洁的中文理由。

评分原则：
- 严格基于对话实际内容，不要假设或推测未说出来的内容
- data_accuracy 必须与提供的 ground_truth 数据对比，不能仅凭感觉
- no_hallucination 重点检查 Agent 是否说了对话中根本查不到的数字
- conversation_quality 检查 Agent 对追问的处理质量，而非对话有多长
- 每个维度独立判断，不要因为某个维度好就放松其他维度的标准

输出格式（严格 JSON，不要有任何额外文字）：
{
  "goal_achieved":        {"passed": true/false, "reason": "一句话理由"},
  "data_accuracy":        {"passed": true/false, "reason": "一句话理由"},
  "actionable":           {"passed": true/false, "reason": "一句话理由"},
  "no_hallucination":     {"passed": true/false, "reason": "一句话理由"},
  "conversation_quality": {"passed": true/false, "reason": "一句话理由"}
}
"""

_JUDGE_USER_TEMPLATE = """\
## 对话记录

{conversation_text}

## 评估标准

goal_achieved 通过条件:
{goal_criterion}

data_accuracy 通过条件:
{data_criterion}
参考 ground_truth 数据:
{ground_truth_json}

actionable 通过条件:
{actionable_criterion}

no_hallucination 通过条件:
{hallucination_criterion}

conversation_quality 通过条件:
{quality_criterion}

## 工具调用记录
{tools_text}

请按照要求评分。
"""


# ─── Scorer ──────────────────────────────────────────────────────────────────

class SessionScorer:
    """Evaluates a full conversation against a scenario's session_criteria."""

    def __init__(self, model: str | None = None, api_key: str | None = None, api_base: str | None = None):
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    def _get_llm_config(self) -> tuple[str, str, str]:
        """Get LLM config from args or fall back to OrderGuard settings."""
        model, key, base = self._model or "", self._api_key or "", self._api_base or ""
        if not model or not key:
            try:
                from order_guard.config import get_settings
                s = get_settings()
                model = model or s.llm.model
                key = key or (s.llm.api_key.get_secret_value() if s.llm.api_key else "")
                base = base or s.llm.api_base or ""
            except Exception:
                pass
        return model, key, base

    def _format_conversation(self, conversation: list[dict]) -> str:
        lines = []
        for i, msg in enumerate(conversation):
            role = "用户" if msg["role"] == "user" else "Agent"
            content = msg.get("content", "")[:600]  # truncate very long turns
            lines.append(f"[{i+1}] {role}: {content}")
        return "\n\n".join(lines)

    def _format_tools(self, tools_used: list[str]) -> str:
        if not tools_used:
            return "（无工具调用记录）"
        return "Agent 调用的工具（按顺序）: " + " → ".join(tools_used)

    async def score(
        self,
        conversation: list[dict],
        scenario: dict,
        persona_id: str = "",
        tools_used: list[str] | None = None,
    ) -> SessionScore:
        """Score a full conversation against the scenario's session_criteria.

        Args:
            conversation: list of {"role": "user"|"assistant", "content": str}
            scenario: loaded scenario dict from scenarios_v2.yaml
            persona_id: which persona was used (for logging)
            tools_used: list of tool names called during the conversation
        """
        score = SessionScore(
            scenario_id=scenario.get("id", "unknown"),
            persona_id=persona_id,
            turns=sum(1 for m in conversation if m["role"] == "user"),
            tools_used=tools_used or [],
        )

        criteria = scenario.get("session_criteria", {})
        ground_truth = scenario.get("ground_truth", {})

        try:
            result = await self._call_judge(
                conversation=conversation,
                criteria=criteria,
                ground_truth=ground_truth,
                tools_used=tools_used or [],
            )
            score.goal_achieved        = DimensionResult(**result["goal_achieved"])
            score.data_accuracy        = DimensionResult(**result["data_accuracy"])
            score.actionable           = DimensionResult(**result["actionable"])
            score.no_hallucination     = DimensionResult(**result["no_hallucination"])
            score.conversation_quality = DimensionResult(**result["conversation_quality"])
            score.passed = all(score.score_summary.values())

        except Exception as e:
            score.judge_error = str(e)
            logger.warning("SessionScorer judge failed for {}/{}: {}", score.scenario_id, persona_id, e)

        return score

    async def _call_judge(
        self,
        conversation: list[dict],
        criteria: dict,
        ground_truth: dict,
        tools_used: list[str],
    ) -> dict:
        import litellm

        model, key, base = self._get_llm_config()
        if not model or not key:
            raise RuntimeError("LLM not configured (model or api_key missing)")

        user_msg = _JUDGE_USER_TEMPLATE.format(
            conversation_text=self._format_conversation(conversation),
            goal_criterion=criteria.get("goal_achieved", "(not specified)"),
            data_criterion=criteria.get("data_accuracy", "(not specified)"),
            ground_truth_json=json.dumps(ground_truth, ensure_ascii=False, indent=2),
            actionable_criterion=criteria.get("actionable", "(not specified)"),
            hallucination_criterion=criteria.get("no_hallucination", "(not specified)"),
            quality_criterion=criteria.get("conversation_quality", "(not specified)"),
            tools_text=self._format_tools(tools_used),
        )

        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
            max_tokens=800,
            api_key=key,
            api_base=base or None,
        )

        raw = resp.choices[0].message.content or ""
        return self._parse_judge_response(raw)

    def _parse_judge_response(self, raw: str) -> dict:
        """Extract JSON from judge response, tolerating markdown fences."""
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()

        parsed = json.loads(raw)

        # Normalize: accept {"passed": ..., "reason": ...} or {"pass": ..., "reason": ...}
        result = {}
        for dim in ["goal_achieved", "data_accuracy", "actionable", "no_hallucination", "conversation_quality"]:
            dim_data = parsed.get(dim, {})
            passed_val = dim_data.get("passed", dim_data.get("pass", False))
            result[dim] = {
                "passed": bool(passed_val),
                "reason": str(dim_data.get("reason", "")),
            }
        return result


# ─── LangWatch integration ────────────────────────────────────────────────────

def push_score_to_langwatch(
    score: SessionScore,
    trace_name: str | None = None,
) -> None:
    """Push SessionScore to LangWatch as a trace with scores.

    No-op if LangWatch is not configured.
    """
    try:
        from langfuse import Langfuse
        lf = Langfuse()

        name = trace_name or f"investigation/{score.scenario_id}/{score.persona_id}"
        trace = lf.trace(
            name=name,
            metadata={
                "scenario_id": score.scenario_id,
                "persona_id": score.persona_id,
                "turns": score.turns,
                "tools_used": score.tools_used,
            },
            tags=["investigation", score.scenario_id, score.persona_id],
        )

        # Overall pass/fail
        trace.score(
            name="session_pass",
            value=1.0 if score.passed else 0.0,
            comment=f"Failed: {score.failed_dimensions}" if not score.passed else "All passed",
        )

        # Per-dimension scores
        for dim, passed in score.score_summary.items():
            reason = getattr(score, dim).reason
            trace.score(
                name=f"dim_{dim}",
                value=1.0 if passed else 0.0,
                comment=reason,
            )

        lf.flush()
        logger.debug("LangWatch scores pushed for {}/{}", score.scenario_id, score.persona_id)

    except Exception as e:
        logger.debug("LangWatch push skipped: {}", e)
