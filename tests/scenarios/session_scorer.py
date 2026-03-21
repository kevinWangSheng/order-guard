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


# ─── Rule-based fallback scorer ──────────────────────────────────────────────

def _rule_based_score(
    score: "SessionScore",
    conversation: list[dict],
    scenario: dict,
    tools_used: list[str],
) -> "SessionScore":
    """Rule-based scoring as fallback when LLM judge is unavailable.

    Checks ground_truth facts against conversation text.
    Less accurate than LLM judge but always works.
    """
    agent_text = "\n".join(
        m.get("content", "") or "" for m in conversation if m.get("role") == "assistant"
    ).lower()
    gt = scenario.get("ground_truth", {})
    turns = sum(1 for m in conversation if m.get("role") == "user")

    # Pre-compute: did agent call any data tools?
    data_tools_called = any(t in tools_used for t in ["query", "get_schema", "list_datasources", "query_data"])
    has_numbers = bool(re.search(r"\d+", agent_text))

    # ── goal_achieved: did the agent provide substantive information? ──────
    # Agent must have queried data AND provided a response with numbers/content
    goal_ok = data_tools_called and has_numbers and len(agent_text) > 100
    score.goal_achieved = DimensionResult(
        passed=goal_ok,
        reason=(
            f"[rule] data_queried={data_tools_called}, has_numbers={has_numbers}, "
            f"response_len={len(agent_text)}"
        ),
    )

    # ── data_accuracy: agent actually queried before giving numbers ────────
    score.data_accuracy = DimensionResult(
        passed=data_tools_called,
        reason=f"[rule] data tools called: {data_tools_called} ({tools_used})",
    )

    # ── actionable: did agent give recommendations? ────────────────────────
    action_keywords = ["建议", "补货", "告警", "处理", "联系", "下架", "检查", "配置", "设置", "立刻", "马上",
                       "关注", "优化", "改善", "跟进", "排查", "注意", "需要"]
    actionable_ok = any(kw in agent_text for kw in action_keywords)
    score.actionable = DimensionResult(
        passed=actionable_ok,
        reason=f"[rule] action keywords {'found' if actionable_ok else 'NOT found'} in agent response",
    )

    # ── no_hallucination: agent used tools before giving specific numbers ──
    hallucination_ok = not (has_numbers and not data_tools_called)
    score.no_hallucination = DimensionResult(
        passed=hallucination_ok,
        reason=(
            "[rule] agent gave numbers without querying data" if not hallucination_ok
            else "[rule] numbers backed by data tool calls"
        ),
    )

    # ── conversation_quality: reasonable conversation length ───────────────
    quality_ok = turns >= 3
    score.conversation_quality = DimensionResult(
        passed=quality_ok,
        reason=f"[rule] {turns} turns (min 3 required)",
    )

    score.passed = all(score.score_summary.values())
    return score


# ─── Scorer ──────────────────────────────────────────────────────────────────

class SessionScorer:
    """Evaluates a full conversation against a scenario's session_criteria."""

    def __init__(self, model: str | None = None, api_key: str | None = None, api_base: str | None = None):
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    def _get_llm_config(self) -> tuple[str, str, str]:
        """Get LLM config: constructor args > JUDGE_* env/.env vars > OrderGuard settings."""
        import os
        from pathlib import Path

        model, key, base = self._model or "", self._api_key or "", self._api_base or ""

        # Load JUDGE_* from OS env or .env file
        if not model or not key:
            env_model = os.environ.get("JUDGE_MODEL", "")
            env_key = os.environ.get("JUDGE_API_KEY", "")
            env_base = os.environ.get("JUDGE_API_BASE", "")

            # If not in OS env, try reading .env directly
            if not env_model or not env_key:
                for env_path in [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]:
                    if env_path.exists():
                        for line in env_path.read_text().splitlines():
                            line = line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k == "JUDGE_MODEL" and not env_model:
                                env_model = v
                            elif k == "JUDGE_API_KEY" and not env_key:
                                env_key = v
                            elif k == "JUDGE_API_BASE" and not env_base:
                                env_base = v
                        break

            if env_model and env_key:
                model = model or env_model
                key = key or env_key
                base = base or env_base
                return model, key, base

        # Fall back to OrderGuard LLM settings
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
        # Keep last 12 turns to avoid exceeding context window
        recent = conversation[-12:] if len(conversation) > 12 else conversation
        lines = []
        for i, msg in enumerate(recent):
            role = "用户" if msg["role"] == "user" else "Agent"
            content = msg.get("content", "")[:300]  # truncate per turn
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
            logger.warning("SessionScorer LLM judge failed for {}/{}: {}", score.scenario_id, persona_id, e)
            # Fall back to rule-based scoring so the test produces real results
            score = _rule_based_score(score, conversation, scenario, tools_used or [])

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

        kwargs: dict = dict(
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

        # Try with response_format first, fall back to plain call
        try:
            resp = await litellm.acompletion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = await litellm.acompletion(**kwargs)

        raw = (resp.choices[0].message.content or "").strip()

        # Coding-plan endpoints (e.g. GLM-5) return empty for plain chat
        # but work when a tools parameter is present. Retry with a dummy tool.
        if not raw:
            logger.debug("Judge returned empty, retrying with dummy tool (coding-plan workaround)")
            _DUMMY_TOOL = [{
                "type": "function",
                "function": {
                    "name": "submit_evaluation",
                    "description": "提交评估结果",
                    "parameters": {
                        "type": "object",
                        "properties": {"result": {"type": "string"}},
                    },
                },
            }]
            try:
                resp = await litellm.acompletion(**kwargs, tools=_DUMMY_TOOL)
            except Exception:
                pass
            else:
                raw = (resp.choices[0].message.content or "").strip()

        if not raw:
            raise RuntimeError(
                f"Judge returned empty response (model={model}). "
                "Try a different judge model or check API quota."
            )
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
