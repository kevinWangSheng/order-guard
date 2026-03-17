"""L2 — Report pipeline integration tests.

Tests report config CRUD, report history saving, and report management tools
with real DB operations.
"""
from __future__ import annotations

import pytest

from order_guard.models import ReportConfig, ReportHistory
from order_guard.engine.reporter import ReportManager
from order_guard.storage.database import get_session
from tests.integration.conftest import seed_report_config

pytestmark = pytest.mark.asyncio


class TestReportPipeline:
    """Test report management with real DB."""

    async def test_sync_reports_to_db(self):
        """sync_reports_to_db should insert new configs."""
        mgr = ReportManager()
        count = await mgr.sync_reports_to_db([
            {"id": "daily-rpt", "name": "日报", "schedule": "0 9 * * *", "mcp_server": "db", "focus": "销售"},
        ])
        assert count == 1

        report = await mgr.get_report("daily-rpt")
        assert report is not None
        assert report.name == "日报"

    async def test_save_history(self):
        """save_history should insert a ReportHistory record."""
        await seed_report_config("hist-rpt")
        mgr = ReportManager()

        history = await mgr.save_history(
            report_id="hist-rpt",
            content="# 报告内容\n数据正常",
            status="success",
            token_usage=1000,
            duration_ms=5000,
        )
        assert history.report_id == "hist-rpt"
        assert history.status == "success"

        # Verify in DB
        async with get_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(ReportHistory).where(ReportHistory.report_id == "hist-rpt")
            )
            rows = result.scalars().all()
            assert len(rows) == 1
            assert rows[0].token_usage == 1000

    async def test_disabled_report_not_listed_when_filtered(self):
        """list_reports with enabled_only should exclude disabled reports."""
        await seed_report_config("enabled-rpt", enabled=True)
        await seed_report_config("disabled-rpt", enabled=False)

        mgr = ReportManager()
        reports = await mgr.list_reports(enabled_only=True)
        ids = [r.id for r in reports]
        assert "enabled-rpt" in ids
        assert "disabled-rpt" not in ids

    async def test_manage_report_get(self):
        """manage_report(action='get') should return report details."""
        await seed_report_config("get-rpt", name="获取测试报告")

        from order_guard.tools.report_tools import manage_report
        result = await manage_report(action="get", report_id="get-rpt")
        assert "data" in result
        assert result["data"]["name"] == "获取测试报告"

    async def test_manage_report_update(self):
        """manage_report(action='update') should update fields in DB."""
        await seed_report_config("upd-rpt", name="旧名称")

        from order_guard.tools.report_tools import manage_report
        result = await manage_report(
            action="update",
            report_id="upd-rpt",
            changes={"name": "新名称", "enabled": False},
        )
        assert "data" in result

        # Verify in DB
        mgr = ReportManager()
        report = await mgr.get_report("upd-rpt")
        assert report.name == "新名称"
        assert report.enabled is False
