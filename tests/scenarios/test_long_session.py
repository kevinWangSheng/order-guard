"""Long-Running Agent Session Tests.

Simulates extended conversations (15-35 turns) with real databases to collect
performance metrics and detect degradation patterns.

Run:
    # Full 35-turn session (real DB)
    uv run pytest tests/scenarios/test_long_session.py -v -m e2e -s

    # Short 15-turn session
    uv run pytest tests/scenarios/test_long_session.py -v -m e2e -s -k "short"

    # Quick check (no e2e mark filter)
    uv run pytest tests/scenarios/test_long_session.py::test_long_session_short -v -s
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import pytest
import pytest_asyncio

from tests.scenarios.long_session_runner import (
    run_long_session,
    save_long_session_report,
    print_session_summary,
    LONG_SESSION_SCRIPT,
    SHORT_SESSION_SCRIPT,
    SessionReport,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest_asyncio.fixture(scope="module")
async def long_session_infra():
    """Initialize real MCP + DAL for long session tests.

    Same setup as investigation tests — uses real MySQL/PG databases.
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

    data_tools.configure(data_access_layer=dal)
    rule_tools.configure(data_access_layer=dal, mcp_manager=mgr)
    health_tools.configure(mcp_manager=mgr)
    report_tools.configure(data_access_layer=dal, mcp_manager=mgr)

    # Pre-warm schema cache
    schema_context = ""
    try:
        schema_context = await dal.get_or_warm_schema_context()
        print(f"\n  [infra] Schema pre-fetched: {len(schema_context)} chars")
    except Exception as e:
        print(f"\n  [infra] Schema pre-fetch failed: {e}")

    yield {"dal": dal, "mcp_manager": mgr, "schema_context": schema_context}

    await mgr.disconnect_all()


# ─── Test cases ──────────────────────────────────────────────────────────────

MAX_ERROR_RATE = 0.15        # Allow max 15% error turns
MAX_EMPTY_RATE = 0.10        # Allow max 10% empty responses
MAX_AVG_RESPONSE_S = 120     # Alert if avg response > 120s


@pytest.mark.timeout(3600)  # 1 hour hard limit for full session
async def test_long_session_full(long_session_infra):
    """Full 35-turn long-running session across 7 topic phases.

    Collects comprehensive metrics for optimization analysis.
    """
    report: SessionReport = await run_long_session(
        infra=long_session_infra,
        script=LONG_SESSION_SCRIPT,
        persona_name="李姐（运营主管）",
    )

    # Save report
    report_path = save_long_session_report(report)
    print_session_summary(report)
    print(f"\n  📄 Report saved: {report_path}")

    # Basic assertions
    assert report.error_count / max(report.total_turns, 1) <= MAX_ERROR_RATE, (
        f"Too many errors: {report.error_count}/{report.total_turns} "
        f"({report.error_count/report.total_turns:.0%} > {MAX_ERROR_RATE:.0%})"
    )
    assert report.empty_response_count / max(report.total_turns, 1) <= MAX_EMPTY_RATE, (
        f"Too many empty responses: {report.empty_response_count}/{report.total_turns}"
    )
    if report.avg_response_time_ms > MAX_AVG_RESPONSE_S * 1000:
        print(f"\n  ⚠️ Avg response time {report.avg_response_time_ms/1000:.1f}s exceeds {MAX_AVG_RESPONSE_S}s threshold")


@pytest.mark.timeout(1800)  # 30 min hard limit for short session
async def test_long_session_short(long_session_infra):
    """Short 15-turn session — quick check of multi-topic performance.

    Good for CI / quick iteration cycles.
    """
    report: SessionReport = await run_long_session(
        infra=long_session_infra,
        script=SHORT_SESSION_SCRIPT,
        persona_name="李姐（运营主管）",
    )

    report_path = save_long_session_report(report)
    print_session_summary(report)
    print(f"\n  📄 Report saved: {report_path}")

    assert report.error_count / max(report.total_turns, 1) <= MAX_ERROR_RATE
    assert report.empty_response_count / max(report.total_turns, 1) <= MAX_EMPTY_RATE
