"""E8 — Temporal Coherence Test Entry.

Runs a 30-turn conversation with probe questions injected at specific turns.
Checks that the Agent's answers are consistent (no self-contradiction over long conversations).

Run:
    uv run pytest tests/scenarios/test_temporal.py -v -m e2e
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.temporal_coherence_test import (
    run_temporal_coherence_test,
    DEFAULT_PROBES,
    CoherenceScore,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

COHERENCE_THRESHOLD = 0.90  # 90% of probes must be consistent


@pytest_asyncio.fixture(scope="module")
async def temporal_infra():
    """Investigation infra for temporal tests."""
    from tests.scenarios.ground_truth_db import build_ground_truth_db
    from tests.scenarios.conftest import FakeMCPConnection
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.data_access.sql_adapter import SQLAdapter
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.mcp.manager import MCPManager
    from order_guard.tools import data_tools, rule_tools, health_tools, report_tools
    from order_guard.storage.database import init_db

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
    yield infra
    gt_db.close()


async def test_temporal_coherence_30_turns(temporal_infra):
    """30-turn conversation with 2 probe injections.

    Verifies that answers given at turn 3 and 9 are still consistent
    when the same questions are asked at turns 26 and 27 (after topic switches).
    """
    score: CoherenceScore = await run_temporal_coherence_test(
        infra=temporal_infra,
        probes=DEFAULT_PROBES,
        max_turns=30,
    )

    print(f"\n{score.summary}")
    for pr in score.probe_results:
        icon = "✅" if pr.consistent else "❌"
        print(f"  {icon} [{pr.probe_id}]")
        print(f"    Turn ~3  answer: {pr.first_answer[:100]}")
        print(f"    Turn ~26 answer: {pr.second_answer[:100]}")
        if pr.inconsistency_note:
            print(f"    Note: {pr.inconsistency_note}")

    assert score.overall_score >= COHERENCE_THRESHOLD, (
        f"Temporal coherence too low: {score.overall_score:.0%} < {COHERENCE_THRESHOLD:.0%}\n"
        f"Inconsistent probes:\n"
        + "\n".join(
            f"  [{r.probe_id}]: {r.inconsistency_note}"
            for r in score.probe_results
            if not r.consistent
        )
        + "\nAction: Check LangWatch traces for context truncation or hallucination patterns."
    )
