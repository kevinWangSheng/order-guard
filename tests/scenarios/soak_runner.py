"""Soak Test Runner — E7.

Runs the same investigation scenario N times and tracks pass rate / variance.
Goal: pass_rate >= 85% per scenario. Below 85% means the agent is unstable
even if individual runs look acceptable.

Usage:
    uv run pytest tests/scenarios/test_soak.py -v -m soak
    uv run pytest tests/scenarios/test_soak.py -k "S01" -v -m soak
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from tests.scenarios.session_scorer import SessionScore, SessionScorer, push_score_to_langwatch

PASS_RATE_THRESHOLD = 0.85  # Alert if below this
SOAK_DEFAULT_ROUNDS = 10


@dataclass
class SoakResult:
    scenario_id: str
    persona_id: str
    rounds: int
    passed: int
    failed: int
    pass_rate: float
    avg_turns: float
    avg_time: float
    dimension_fail_counts: dict[str, int] = field(default_factory=dict)
    scores: list[SessionScore] = field(default_factory=list)
    alert: bool = False       # True if pass_rate < threshold

    @property
    def summary_line(self) -> str:
        color = "✅" if not self.alert else "⚠️"
        return (
            f"{color} {self.scenario_id}/{self.persona_id}: "
            f"{self.passed}/{self.rounds} ({self.pass_rate:.0%}) | "
            f"avg {self.avg_turns:.1f} turns, {self.avg_time:.1f}s | "
            f"fail dims: {self.dimension_fail_counts}"
        )


async def run_soak(
    scenario_id: str,
    persona_id: str,
    rounds: int = SOAK_DEFAULT_ROUNDS,
    infra: dict | None = None,
) -> SoakResult:
    """Run one scenario/persona combination for `rounds` iterations.

    Args:
        scenario_id: e.g. "S01"
        persona_id: e.g. "xiao_wang"
        rounds: number of times to run
        infra: pre-built investigation infra dict (from investigation_infra fixture)
    """
    from tests.scenarios.test_investigation import (
        _load_scenarios, _run_investigation, _PERSONAS,
    )
    from tests.scenarios.ground_truth_db import build_ground_truth_db
    from tests.scenarios.conftest import FakeMCPConnection
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.data_access.sql_adapter import SQLAdapter
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.mcp.manager import MCPManager
    from order_guard.tools import data_tools, rule_tools, health_tools, report_tools
    from order_guard.storage.database import init_db

    personas_map, scenarios_list = _load_scenarios()
    scenario = next((s for s in scenarios_list if s["id"] == scenario_id), None)
    if not scenario:
        raise ValueError(f"Scenario {scenario_id!r} not found in scenarios_v2.yaml")
    persona = personas_map.get(persona_id)
    if not persona:
        raise ValueError(f"Persona {persona_id!r} not found in scenarios_v2.yaml")

    # Build infra if not provided
    if infra is None:
        await init_db()
        gt_db = build_ground_truth_db()
        fake_conn = FakeMCPConnection(gt_db)
        config = MCPServerConfig(
            name="test-warehouse", type="dbhub", transport="stdio",
            command="fake", enabled=True,
        )
        mgr = MCPManager()
        dal = DataAccessLayer(mcp_manager=mgr, configs=[config])
        adapter = SQLAdapter(fake_conn, config)
        adapter._is_sqlite = True
        dal._adapters["test-warehouse"] = adapter
        data_tools.configure(data_access_layer=dal)
        rule_tools.configure(data_access_layer=dal, mcp_manager=mgr)
        health_tools.configure(mcp_manager=mgr)
        report_tools.configure(data_access_layer=dal, mcp_manager=mgr)
        infra = {"dal": dal, "mcp_manager": mgr, "gt_db": gt_db}

    scorer = SessionScorer()
    scores: list[SessionScore] = []
    times: list[float] = []

    for i in range(rounds):
        logger.info("[Soak {}/{}] {}/{}", i + 1, rounds, scenario_id, persona_id)
        t0 = time.time()
        try:
            conversation, tools_used = await _run_investigation(
                scenario=scenario,
                persona=persona,
                persona_id=persona_id,
                infra=infra,
            )
            score = await scorer.score(
                conversation=conversation,
                scenario=scenario,
                persona_id=persona_id,
                tools_used=tools_used,
            )
        except Exception as e:
            logger.error("Soak run {}/{} failed: {}", i + 1, rounds, e)
            # Create a failed score
            score = SessionScore(scenario_id=scenario_id, persona_id=persona_id)
            score.judge_error = str(e)

        elapsed = time.time() - t0
        times.append(elapsed)
        scores.append(score)
        push_score_to_langwatch(score, trace_name=f"soak/{scenario_id}/{persona_id}/run{i+1}")

        status = "✅" if score.passed else "❌"
        logger.info("  {} ({:.1f}s) fails={}", status, elapsed, score.failed_dimensions)

    # Aggregate
    passed = sum(1 for s in scores if s.passed)
    pass_rate = passed / rounds if rounds > 0 else 0.0
    avg_turns = sum(s.turns for s in scores) / rounds if rounds > 0 else 0.0
    avg_time = sum(times) / len(times) if times else 0.0

    dim_fail_counts: dict[str, int] = {}
    for s in scores:
        for dim in s.failed_dimensions:
            dim_fail_counts[dim] = dim_fail_counts.get(dim, 0) + 1

    result = SoakResult(
        scenario_id=scenario_id,
        persona_id=persona_id,
        rounds=rounds,
        passed=passed,
        failed=rounds - passed,
        pass_rate=pass_rate,
        avg_turns=avg_turns,
        avg_time=avg_time,
        dimension_fail_counts=dim_fail_counts,
        scores=scores,
        alert=pass_rate < PASS_RATE_THRESHOLD,
    )
    logger.info("{}", result.summary_line)
    return result


def save_soak_report(results: list[SoakResult]) -> Path:
    """Save soak test results as JSON report."""
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"soak_{ts}.json"

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "threshold": PASS_RATE_THRESHOLD,
        "results": [
            {
                "scenario_id": r.scenario_id,
                "persona_id": r.persona_id,
                "rounds": r.rounds,
                "passed": r.passed,
                "pass_rate": round(r.pass_rate, 4),
                "avg_turns": round(r.avg_turns, 1),
                "avg_time": round(r.avg_time, 1),
                "dimension_fail_counts": r.dimension_fail_counts,
                "alert": r.alert,
            }
            for r in results
        ],
        "alerts": [r.summary_line for r in results if r.alert],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
