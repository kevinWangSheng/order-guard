"""Tests for alert tools — list_alerts, handle_alert, get_alert_stats."""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from contextlib import asynccontextmanager

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from order_guard.tools.alert_tools import (
    list_alerts,
    handle_alert,
    get_alert_stats,
    TOOL_DEFINITIONS,
)
from order_guard.models import Alert, AlertRule


# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.alert_tools.get_session", _test_session):
        yield engine, _test_session

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_db(db_session):
    engine, test_session = db_session
    now = datetime.now(timezone.utc)

    async with test_session() as session:
        # Rules
        session.add(AlertRule(
            id="rule-stock", name="缺货检测",
            mcp_server="erp", prompt_template="检查",
        ))
        session.add(AlertRule(
            id="rule-returns", name="退货异常",
            mcp_server="erp", prompt_template="检查",
        ))

        # Alerts
        session.add(Alert(
            id="a1", rule_id="rule-stock", severity="critical",
            title="SKU-001 缺货", summary="库存为0", status="sent",
            created_at=now - timedelta(hours=2),
        ))
        session.add(Alert(
            id="a2", rule_id="rule-stock", severity="warning",
            title="SKU-002 低库存", summary="库存低", status="sent",
            created_at=now - timedelta(hours=12),
        ))
        session.add(Alert(
            id="a3", rule_id="rule-returns", severity="info",
            title="退货率正常", summary="正常", status="sent",
            created_at=now - timedelta(days=3),
        ))
        session.add(Alert(
            id="a4", rule_id="rule-stock", severity="critical",
            title="旧告警", summary="很久以前", status="sent",
            created_at=now - timedelta(days=10),
        ))

    yield engine, test_session


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_has_3_tools(self):
        assert len(TOOL_DEFINITIONS) == 3

    def test_tool_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert names == {"list_alerts", "handle_alert", "get_alert_stats"}

    def test_list_alerts_no_required(self):
        tool = next(t for t in TOOL_DEFINITIONS if t.name == "list_alerts")
        assert tool.input_schema["required"] == []

    def test_handle_alert_requires_resolution(self):
        tool = next(t for t in TOOL_DEFINITIONS if t.name == "handle_alert")
        assert "resolution" in tool.input_schema["required"]


# ---------------------------------------------------------------------------
# list_alerts tests
# ---------------------------------------------------------------------------

class TestListAlerts:
    @pytest.mark.asyncio
    async def test_returns_all(self, seeded_db):
        result = await list_alerts()
        assert "data" in result
        assert len(result["data"]) == 4

    @pytest.mark.asyncio
    async def test_time_order_desc(self, seeded_db):
        result = await list_alerts()
        dates = [item["created_at"] for item in result["data"]]
        assert dates == sorted(dates, reverse=True)

    @pytest.mark.asyncio
    async def test_filter_by_rule_id(self, seeded_db):
        result = await list_alerts(rule_id="rule-returns")
        assert len(result["data"]) == 1
        assert result["data"][0]["rule_name"] == "退货异常"

    @pytest.mark.asyncio
    async def test_filter_by_time_24h(self, seeded_db):
        result = await list_alerts(time_range="24h")
        assert len(result["data"]) == 2  # a1 (2h ago) and a2 (12h ago)

    @pytest.mark.asyncio
    async def test_filter_by_time_7d(self, seeded_db):
        result = await list_alerts(time_range="7d")
        assert len(result["data"]) == 3  # a1, a2, a3 (not a4 which is 10d ago)

    @pytest.mark.asyncio
    async def test_limit(self, seeded_db):
        result = await list_alerts(limit=2)
        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_combined_filters(self, seeded_db):
        result = await list_alerts(rule_id="rule-stock", time_range="24h")
        assert len(result["data"]) == 2  # a1 and a2

    @pytest.mark.asyncio
    async def test_nonexistent_rule_returns_empty(self, seeded_db):
        result = await list_alerts(rule_id="nonexistent")
        assert "data" in result
        assert len(result["data"]) == 0

    @pytest.mark.asyncio
    async def test_invalid_time_range(self, seeded_db):
        result = await list_alerts(time_range="1y")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_hint_has_stats(self, seeded_db):
        result = await list_alerts(time_range="24h")
        assert "2 条告警" in result["hint"]

    @pytest.mark.asyncio
    async def test_empty_hint(self, db_session):
        result = await list_alerts()
        assert "暂无" in result["hint"]

    @pytest.mark.asyncio
    async def test_includes_rule_name(self, seeded_db):
        result = await list_alerts()
        stock_alert = next(a for a in result["data"] if a["id"] == "a1")
        assert stock_alert["rule_name"] == "缺货检测"

    @pytest.mark.asyncio
    async def test_return_fields(self, seeded_db):
        result = await list_alerts(limit=1)
        item = result["data"][0]
        assert "id" in item
        assert "rule_name" in item
        assert "severity" in item
        assert "summary" in item
        assert "created_at" in item
        assert "resolution" in item

    @pytest.mark.asyncio
    async def test_resolution_field_in_output(self, seeded_db):
        """list_alerts returns resolution field (None for unresolved)."""
        result = await list_alerts(limit=1)
        item = result["data"][0]
        assert "resolution" in item
        assert item["resolution"] is None


# ---------------------------------------------------------------------------
# handle_alert tests
# ---------------------------------------------------------------------------

class TestHandleAlert:
    @pytest.mark.asyncio
    async def test_single_alert_handled(self, seeded_db):
        """Single alert can be marked as handled."""
        result = await handle_alert(alert_id="a1", resolution="handled")
        assert "data" in result
        assert result["data"]["affected"] == 1
        assert result["data"]["resolution"] == "handled"

    @pytest.mark.asyncio
    async def test_single_alert_with_note(self, seeded_db):
        """Single alert with note."""
        result = await handle_alert(
            alert_id="a1", resolution="ignored", note="已知问题",
        )
        assert result["data"]["affected"] == 1

        # Verify via list
        alerts = await list_alerts()
        a1 = next(a for a in alerts["data"] if a["id"] == "a1")
        assert a1["resolution"] == "ignored"

    @pytest.mark.asyncio
    async def test_batch_by_rule_id(self, seeded_db):
        """Batch handle alerts by rule_id."""
        result = await handle_alert(rule_id="rule-stock", resolution="handled")
        assert "data" in result
        assert result["data"]["affected"] == 3  # a1, a2, a4

    @pytest.mark.asyncio
    async def test_batch_by_rule_id_with_time_range(self, seeded_db):
        """Batch handle by rule_id + time_range."""
        result = await handle_alert(
            rule_id="rule-stock", time_range="24h", resolution="handled",
        )
        assert result["data"]["affected"] == 2  # a1, a2 (within 24h)

    @pytest.mark.asyncio
    async def test_missing_alert_id_and_rule_id(self, seeded_db):
        """Error when neither alert_id nor rule_id is provided."""
        result = await handle_alert(resolution="handled")
        assert "error" in result
        assert "alert_id" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_resolution(self, seeded_db):
        """Error for invalid resolution value."""
        result = await handle_alert(alert_id="a1", resolution="invalid_status")
        assert "error" in result
        assert "resolution" in result["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_alert_id(self, seeded_db):
        """Error for nonexistent alert_id."""
        result = await handle_alert(alert_id="nonexistent", resolution="handled")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_re_mark_alert(self, seeded_db):
        """Already-resolved alert can be re-marked."""
        # First mark
        await handle_alert(alert_id="a1", resolution="handled")
        # Re-mark
        result = await handle_alert(alert_id="a1", resolution="false_positive")
        assert result["data"]["affected"] == 1
        assert result["data"]["resolution"] == "false_positive"

        # Verify
        alerts = await list_alerts()
        a1 = next(a for a in alerts["data"] if a["id"] == "a1")
        assert a1["resolution"] == "false_positive"

    @pytest.mark.asyncio
    async def test_false_positive_resolution(self, seeded_db):
        """false_positive is a valid resolution."""
        result = await handle_alert(alert_id="a1", resolution="false_positive")
        assert result["data"]["affected"] == 1


# ---------------------------------------------------------------------------
# get_alert_stats tests
# ---------------------------------------------------------------------------

class TestGetAlertStats:
    @pytest.mark.asyncio
    async def test_basic_stats(self, seeded_db):
        """Basic stats with no resolved alerts."""
        result = await get_alert_stats(time_range="30d")
        assert "data" in result
        data = result["data"]
        assert data["total"] == 4
        assert data["unresolved_count"] == 4
        assert data["resolution_rate"] == 0.0
        assert "by_severity" in data
        assert "by_resolution" in data
        assert "top_rules" in data

    @pytest.mark.asyncio
    async def test_stats_by_severity(self, seeded_db):
        """by_severity breakdown is correct."""
        result = await get_alert_stats(time_range="30d")
        by_sev = result["data"]["by_severity"]
        assert by_sev.get("critical", 0) == 2
        assert by_sev.get("warning", 0) == 1
        assert by_sev.get("info", 0) == 1

    @pytest.mark.asyncio
    async def test_stats_after_handling(self, seeded_db):
        """Stats update after handling alerts."""
        await handle_alert(alert_id="a1", resolution="handled")
        await handle_alert(alert_id="a2", resolution="ignored")

        result = await get_alert_stats(time_range="30d")
        data = result["data"]
        assert data["total"] == 4
        assert data["unresolved_count"] == 2
        assert data["resolution_rate"] == 50.0
        assert data["avg_resolution_time_hours"] is not None

    @pytest.mark.asyncio
    async def test_stats_by_rule_id(self, seeded_db):
        """Stats filtered by rule_id."""
        result = await get_alert_stats(time_range="30d", rule_id="rule-returns")
        data = result["data"]
        assert data["total"] == 1
        assert data["by_severity"].get("info", 0) == 1

    @pytest.mark.asyncio
    async def test_stats_empty_data(self, db_session):
        """Stats with no alerts should not error."""
        result = await get_alert_stats()
        assert "data" in result
        data = result["data"]
        assert data["total"] == 0
        assert data["unresolved_count"] == 0
        assert data["resolution_rate"] == 0.0
        assert data["top_rules"] == []
        assert data["avg_resolution_time_hours"] is None

    @pytest.mark.asyncio
    async def test_top_rules(self, seeded_db):
        """top_rules returns rules sorted by count desc."""
        result = await get_alert_stats(time_range="30d")
        top = result["data"]["top_rules"]
        assert len(top) == 2
        assert top[0]["rule_id"] == "rule-stock"
        assert top[0]["count"] == 3
        assert top[1]["rule_id"] == "rule-returns"
        assert top[1]["count"] == 1

    @pytest.mark.asyncio
    async def test_hint_contains_summary(self, seeded_db):
        """Hint contains meaningful summary."""
        result = await get_alert_stats(time_range="30d")
        assert "4 条告警" in result["hint"]
        assert "处理率" in result["hint"]

    @pytest.mark.asyncio
    async def test_default_time_range_7d(self, seeded_db):
        """Default time_range is 7d."""
        result = await get_alert_stats()
        # 7d should include a1, a2, a3 but not a4 (10d ago)
        assert result["data"]["total"] == 3
