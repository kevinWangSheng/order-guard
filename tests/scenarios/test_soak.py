"""E7 — Soak Test Entry.

Runs the same scenario/persona N times to check pass rate stability.
Alert threshold: pass_rate < 85%.

Run:
    uv run pytest tests/scenarios/test_soak.py -v -m soak
    uv run pytest tests/scenarios/test_soak.py -k "S01_xiao_wang" -v -m soak
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tests.scenarios.soak_runner import (
    run_soak,
    save_soak_report,
    SoakResult,
    PASS_RATE_THRESHOLD,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.soak]

SOAK_ROUNDS = 10   # configurable: increase for thorough testing


@pytest_asyncio.fixture(scope="module")
async def soak_infra():
    """Shared investigation infra for soak tests (module-scoped)."""
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


# ─── Soak test cases ─────────────────────────────────────────────────────────
# Start with highest-priority scenario/persona combos.
# Add more as needed.

@pytest.mark.parametrize("scenario_id,persona_id", [
    ("S01", "xiao_wang"),    # 菜鸡运营 × 库存调查 — most important stability check
    ("S01", "li_jie"),       # 老运营 × 库存调查
    ("S02", "li_jie"),       # 老运营 × 退货诊断
])
async def test_soak(scenario_id, persona_id, soak_infra):
    """Run one scenario/persona for SOAK_ROUNDS iterations and assert pass_rate >= 85%."""
    result: SoakResult = await run_soak(
        scenario_id=scenario_id,
        persona_id=persona_id,
        rounds=SOAK_ROUNDS,
        infra=soak_infra,
    )

    print(f"\n{result.summary_line}")
    print(f"  Dimension failures: {result.dimension_fail_counts}")

    # Save report
    path = save_soak_report([result])
    print(f"  Report: {path}")

    assert result.pass_rate >= PASS_RATE_THRESHOLD, (
        f"Soak FAILED: {scenario_id}/{persona_id} pass rate {result.pass_rate:.0%} "
        f"< threshold {PASS_RATE_THRESHOLD:.0%}\n"
        f"Top failing dimensions: {result.dimension_fail_counts}\n"
        f"Action: review LangWatch traces for this scenario to find root cause"
    )
