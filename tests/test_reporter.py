"""Tests for Reporter — scheduled report generation (P6)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.engine.reporter import (
    ReportManager,
    generate_report,
    format_kpi,
    REPORT_SYSTEM_PROMPT,
    REPORT_PROMPT_TEMPLATE,
    SECTION_PROMPT_TEMPLATE,
)
from order_guard.models import ReportConfig, ReportHistory


# ---------------------------------------------------------------------------
# ReportManager tests
# ---------------------------------------------------------------------------

class TestReportManager:
    @pytest.mark.asyncio
    async def test_sync_reports_to_db(self):
        with patch("order_guard.engine.reporter.get_session") as mock_gs:
            mock_session = AsyncMock()

            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def fake_session():
                yield mock_session
            mock_gs.return_value = fake_session()

            with patch("order_guard.engine.reporter.get_by_id", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = None
                with patch("order_guard.engine.reporter.create", new_callable=AsyncMock) as mock_create:
                    mock_create.return_value = ReportConfig(id="test-report", name="Test")

                    mgr = ReportManager()
                    count = await mgr.sync_reports_to_db([
                        {"id": "test-report", "name": "Test", "schedule": "0 9 * * *", "mcp_server": "test"},
                    ])
                    assert count == 1

    @pytest.mark.asyncio
    async def test_get_report(self):
        with patch("order_guard.engine.reporter.get_session") as mock_gs:
            mock_session = AsyncMock()
            report = ReportConfig(id="r1", name="Report 1")

            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def fake_session():
                yield mock_session
            mock_gs.return_value = fake_session()

            with patch("order_guard.engine.reporter.get_by_id", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = report
                mgr = ReportManager()
                result = await mgr.get_report("r1")
                assert result is not None
                assert result.name == "Report 1"

    @pytest.mark.asyncio
    async def test_list_reports(self):
        with patch("order_guard.engine.reporter.get_session") as mock_gs:
            mock_session = AsyncMock()
            reports = [
                ReportConfig(id="r1", name="Report 1"),
                ReportConfig(id="r2", name="Report 2"),
            ]

            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def fake_session():
                yield mock_session
            mock_gs.return_value = fake_session()

            with patch("order_guard.engine.reporter.list_all", new_callable=AsyncMock) as mock_list:
                mock_list.return_value = reports
                mgr = ReportManager()
                result = await mgr.list_reports()
                assert len(result) == 2

    @pytest.mark.asyncio
    async def test_save_history(self):
        with patch("order_guard.engine.reporter.get_session") as mock_gs:
            mock_session = AsyncMock()
            history = ReportHistory(report_id="r1", content="test", status="success")

            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def fake_session():
                yield mock_session
            mock_gs.return_value = fake_session()

            with patch("order_guard.engine.reporter.create", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = history
                mgr = ReportManager()
                result = await mgr.save_history(
                    report_id="r1",
                    content="test report",
                    status="success",
                    token_usage=100,
                    duration_ms=5000,
                )
                assert result.status == "success"


# ---------------------------------------------------------------------------
# generate_report tests — backward compatible (no sections)
# ---------------------------------------------------------------------------

class TestGenerateReportLegacy:
    @pytest.mark.asyncio
    async def test_focus_based_generation(self):
        """Report with no sections should use focus-based prompt."""
        report = ReportConfig(
            id="test-report",
            name="Test Report",
            mcp_server="test-server",
            focus="请生成测试报告",
            sections=[],
            kpis=[],
        )

        mock_result = MagicMock()
        mock_result.summary = "# 测试报告\n\n## 销售概况\n总销量: 1,234 件"
        mock_result.token_usage = MagicMock(total_tokens=500)

        with patch("order_guard.engine.reporter.Agent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            MockAgent.return_value = mock_agent

            with patch("order_guard.engine.reporter.LLMClient"):
                result = await generate_report(report)

        assert result["status"] == "success"
        assert "测试报告" in result["content"]
        assert result["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_failure_returns_error(self):
        report = ReportConfig(
            id="test-report",
            name="Test Report",
            mcp_server="test-server",
            focus="请生成报告",
        )

        with patch("order_guard.engine.reporter.Agent") as MockAgent:
            MockAgent.side_effect = Exception("Connection failed")
            with patch("order_guard.engine.reporter.LLMClient"):
                result = await generate_report(report)

        assert result["status"] == "failed"
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_with_dal(self):
        report = ReportConfig(
            id="test-report",
            name="Test Report",
            mcp_server="test-server",
            focus="请生成报告",
        )

        mock_result = MagicMock()
        mock_result.summary = "报告内容"
        mock_result.token_usage = None

        mock_dal = MagicMock()

        with patch("order_guard.engine.reporter.Agent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            MockAgent.return_value = mock_agent

            with patch("order_guard.engine.reporter.LLMClient"):
                result = await generate_report(report, data_access_layer=mock_dal)

        assert result["status"] == "success"
        MockAgent.assert_called_once()
        call_kwargs = MockAgent.call_args[1]
        assert call_kwargs["data_access_layer"] is mock_dal


# ---------------------------------------------------------------------------
# generate_report tests — sections-based generation
# ---------------------------------------------------------------------------

class TestGenerateReportSections:
    @pytest.mark.asyncio
    async def test_sections_generation(self):
        """Report with sections should generate each section separately."""
        report = ReportConfig(
            id="sectioned-report",
            name="分段报告",
            mcp_server="erp-mysql",
            focus="",
            sections=[
                {"title": "销售概况", "prompt": "统计总销售额", "datasource": "erp_mysql"},
                {"title": "库存分析", "prompt": "分析库存状况", "datasource": "erp_mysql"},
            ],
            kpis=[],
        )

        mock_result_1 = MagicMock()
        mock_result_1.summary = "## 销售概况\n\n总销售额: ¥1,000,000"
        mock_result_1.token_usage = MagicMock(total_tokens=300)

        mock_result_2 = MagicMock()
        mock_result_2.summary = "## 库存分析\n\n库存充足"
        mock_result_2.token_usage = MagicMock(total_tokens=200)

        with patch("order_guard.engine.reporter.Agent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.run.side_effect = [mock_result_1, mock_result_2]
            MockAgent.return_value = mock_agent

            with patch("order_guard.engine.reporter.LLMClient"):
                result = await generate_report(report)

        assert result["status"] == "success"
        assert "销售概况" in result["content"]
        assert "库存分析" in result["content"]
        assert result["token_usage"] == 500
        # Agent should be called twice (once per section)
        assert mock_agent.run.call_count == 2

    @pytest.mark.asyncio
    async def test_sections_with_kpis(self):
        """Report with both sections and KPIs should include KPI table."""
        report = ReportConfig(
            id="kpi-report",
            name="KPI 报告",
            mcp_server="erp-mysql",
            focus="",
            sections=[
                {"title": "概述", "prompt": "生成概述", "datasource": "erp_mysql"},
            ],
            kpis=[
                {"name": "总销售额", "sql": "SELECT SUM(amount)...", "format": "currency", "value": 12345.67},
                {"name": "订单数", "sql": "SELECT COUNT(*)...", "format": "number", "value": 500},
                {"name": "利润率", "sql": "SELECT ...", "format": "percent", "value": 15.3},
            ],
        )

        mock_result = MagicMock()
        mock_result.summary = "## 概述\n\n业绩良好"
        mock_result.token_usage = MagicMock(total_tokens=100)

        with patch("order_guard.engine.reporter.Agent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.run.return_value = mock_result
            MockAgent.return_value = mock_agent

            with patch("order_guard.engine.reporter.LLMClient"):
                result = await generate_report(report)

        assert result["status"] == "success"
        content = result["content"]
        assert "关键指标" in content
        assert "¥12,345.67" in content
        assert "500" in content
        assert "15.3%" in content


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------

class TestPrompts:
    def test_system_prompt_content(self):
        assert "经营数据分析" in REPORT_SYSTEM_PROMPT
        assert "Markdown" in REPORT_SYSTEM_PROMPT

    def test_prompt_template_format(self):
        result = REPORT_PROMPT_TEMPLATE.format(
            report_name="测试报告",
            focus="生成销售数据概览",
        )
        assert "测试报告" in result
        assert "销售数据概览" in result

    def test_section_prompt_template_format(self):
        result = SECTION_PROMPT_TEMPLATE.format(
            title="销售概况",
            prompt="统计总销售额",
            datasource_hint="数据源: erp_mysql",
        )
        assert "销售概况" in result
        assert "统计总销售额" in result
        assert "erp_mysql" in result


# ---------------------------------------------------------------------------
# ReportConfig model tests
# ---------------------------------------------------------------------------

class TestReportConfigModel:
    def test_default_fields(self):
        r = ReportConfig(
            id="test",
            name="Test",
            schedule="0 9 * * *",
            mcp_server="test",
            focus="test focus",
        )
        assert r.id == "test"
        assert r.channels == "default"
        assert r.sections == []
        assert r.kpis == []
        assert r.template_style == "standard"

    def test_with_sections_and_kpis(self):
        r = ReportConfig(
            id="test",
            name="Test",
            schedule="0 9 * * *",
            mcp_server="test",
            focus="",
            sections=[{"title": "A", "prompt": "B"}],
            kpis=[{"name": "C", "format": "number"}],
            template_style="detailed",
        )
        assert len(r.sections) == 1
        assert len(r.kpis) == 1
        assert r.template_style == "detailed"

    def test_report_history_model(self):
        h = ReportHistory(
            report_id="test",
            content="report content",
            status="success",
            token_usage=100,
            duration_ms=5000,
        )
        assert h.report_id == "test"
        assert h.status == "success"
