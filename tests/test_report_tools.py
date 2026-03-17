"""Tests for report management tools (P6)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from order_guard.tools.report_tools import (
    manage_report,
    preview_report,
    configure,
    TOOL_DEFINITIONS,
)
from order_guard.models import ReportConfig, ReportHistory
from order_guard.engine.reporter import format_kpi


# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory SQLite DB for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.report_tools.get_session", _test_session):
        yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_db(db_session):
    """Seed DB with test report configs."""
    engine = db_session
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            r1 = ReportConfig(
                id="daily-sales",
                name="每日销售日报",
                schedule="0 9 * * *",
                mcp_server="erp-mysql",
                focus="生成销售数据概览",
                enabled=True,
                template_style="standard",
                sections=[],
                kpis=[],
            )
            r2 = ReportConfig(
                id="weekly-report",
                name="每周经营周报",
                schedule="0 9 * * 1",
                mcp_server="erp-mysql",
                focus="生成周度经营分析",
                enabled=False,
                template_style="detailed",
                sections=[
                    {"title": "销售概况", "prompt": "统计总销售额", "datasource": "erp_mysql"},
                    {"title": "库存分析", "prompt": "分析库存状况", "datasource": "erp_mysql"},
                ],
                kpis=[
                    {"name": "总销售额", "sql": "SELECT SUM(amount) FROM orders", "format": "currency"},
                    {"name": "订单数", "sql": "SELECT COUNT(*) FROM orders", "format": "number"},
                ],
            )
            session.add(r1)
            session.add(r2)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.report_tools.get_session", _test_session):
        yield engine


# ---------------------------------------------------------------------------
# Tool definition tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_has_2_tools(self):
        assert len(TOOL_DEFINITIONS) == 2

    def test_tool_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert names == {"manage_report", "preview_report"}

    def test_manage_report_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "manage_report")
        assert "action" in schema.input_schema["required"]

    def test_preview_report_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "preview_report")
        assert "report_id" in schema.input_schema["required"]


# ---------------------------------------------------------------------------
# manage_report list tests
# ---------------------------------------------------------------------------

class TestManageReportList:
    @pytest.mark.asyncio
    async def test_list_returns_all_reports(self, seeded_db):
        result = await manage_report(action="list")
        assert "data" in result
        assert "hint" in result
        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_list_includes_sections_count(self, seeded_db):
        result = await manage_report(action="list")
        weekly = next(r for r in result["data"] if r["id"] == "weekly-report")
        assert weekly["sections_count"] == 2
        assert weekly["kpis_count"] == 2

    @pytest.mark.asyncio
    async def test_list_empty(self, db_session):
        result = await manage_report(action="list")
        assert "data" in result
        assert len(result["data"]) == 0
        assert "暂无" in result["hint"]


# ---------------------------------------------------------------------------
# manage_report get tests
# ---------------------------------------------------------------------------

class TestManageReportGet:
    @pytest.mark.asyncio
    async def test_get_report_details(self, seeded_db):
        result = await manage_report(action="get", report_id="weekly-report")
        assert "data" in result
        data = result["data"]
        assert data["id"] == "weekly-report"
        assert data["template_style"] == "detailed"
        assert len(data["sections"]) == 2
        assert len(data["kpis"]) == 2

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, seeded_db):
        result = await manage_report(action="get", report_id="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self, seeded_db):
        result = await manage_report(action="get", report_id="")
        assert "error" in result


# ---------------------------------------------------------------------------
# manage_report update tests
# ---------------------------------------------------------------------------

class TestManageReportUpdate:
    @pytest.mark.asyncio
    async def test_update_name(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"name": "新日报名称"},
        )
        assert "data" in result
        assert result["data"]["changes"]["name"] == "新日报名称"

    @pytest.mark.asyncio
    async def test_update_schedule(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"schedule": "0 10 * * *"},
        )
        assert "data" in result

    @pytest.mark.asyncio
    async def test_update_invalid_cron(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"schedule": "invalid"},
        )
        assert "error" in result
        assert "cron" in result["error"]

    @pytest.mark.asyncio
    async def test_update_template_style(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"template_style": "brief"},
        )
        assert "data" in result

    @pytest.mark.asyncio
    async def test_update_invalid_template_style(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"template_style": "fancy"},
        )
        assert "error" in result
        assert "template_style" in result["error"]

    @pytest.mark.asyncio
    async def test_update_sections_and_kpis(self, seeded_db):
        new_sections = [{"title": "新章节", "prompt": "分析新数据", "datasource": "pg"}]
        new_kpis = [{"name": "利润率", "sql": "SELECT ...", "format": "percent"}]
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={"sections": new_sections, "kpis": new_kpis},
        )
        assert "data" in result
        assert result["data"]["changes"]["sections"] == new_sections
        assert result["data"]["changes"]["kpis"] == new_kpis

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="nonexistent",
            changes={"name": "x"},
        )
        assert "error" in result
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_update_empty_changes(self, seeded_db):
        result = await manage_report(
            action="update",
            report_id="daily-sales",
            changes={},
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_syncs_scheduler(self, seeded_db):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler)
        try:
            result = await manage_report(
                action="update",
                report_id="daily-sales",
                changes={"schedule": "0 */2 * * *"},
            )
            assert "data" in result
            mock_scheduler.remove_schedule.assert_called_once()
            mock_scheduler.add_schedule.assert_called_once()
        finally:
            configure(scheduler=None)

    @pytest.mark.asyncio
    async def test_unknown_action(self, seeded_db):
        result = await manage_report(action="delete")
        assert "error" in result
        assert "未知操作" in result["error"]


# ---------------------------------------------------------------------------
# preview_report tests
# ---------------------------------------------------------------------------

class TestPreviewReport:
    @pytest.mark.asyncio
    async def test_preview_generates_no_push(self, seeded_db):
        """Preview should generate content but not push."""
        configure(data_access_layer=None, mcp_manager=None)

        mock_result = {
            "content": "# 每日销售日报\n\n销售总额: ¥100,000",
            "token_usage": 500,
            "duration_ms": 1000,
            "status": "success",
            "error": None,
        }

        with patch(
            "order_guard.engine.reporter.generate_report",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await preview_report(report_id="daily-sales")

        assert "data" in result
        assert result["data"]["report_name"] == "每日销售日报"
        assert "¥100,000" in result["data"]["content"]

    @pytest.mark.asyncio
    async def test_preview_nonexistent(self, seeded_db):
        result = await preview_report(report_id="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_preview_empty_id(self):
        result = await preview_report(report_id="")
        assert "error" in result


# ---------------------------------------------------------------------------
# KPI formatting tests
# ---------------------------------------------------------------------------

class TestKpiFormatting:
    def test_format_currency(self):
        assert format_kpi(12345.67, "currency") == "¥12,345.67"
        assert format_kpi(99.5, "currency") == "¥99.50"

    def test_format_percent(self):
        assert format_kpi(85.3, "percent") == "85.3%"
        assert format_kpi(0.5, "percent") == "0.5%"

    def test_format_number(self):
        assert format_kpi(1234, "number") == "1,234"
        assert format_kpi(1234.56, "number") == "1,234.56"

    def test_format_none(self):
        assert format_kpi(None, "currency") == "N/A"
        assert format_kpi(None, "number") == "N/A"

    def test_format_invalid_value(self):
        assert format_kpi("not_a_number", "currency") == "not_a_number"

    def test_format_unknown_type(self):
        assert format_kpi(42, "unknown") == "42"


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_report_without_sections_uses_focus(self, seeded_db):
        """A report with no sections should fall back to focus-based generation."""
        result = await manage_report(action="get", report_id="daily-sales")
        assert "data" in result
        data = result["data"]
        assert data["sections"] == []
        assert data["focus"] == "生成销售数据概览"

    @pytest.mark.asyncio
    async def test_report_with_sections(self, seeded_db):
        """A report with sections should include them."""
        result = await manage_report(action="get", report_id="weekly-report")
        assert "data" in result
        data = result["data"]
        assert len(data["sections"]) == 2
        assert data["sections"][0]["title"] == "销售概况"
