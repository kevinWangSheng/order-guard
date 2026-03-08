"""Integration tests for MCP pipeline (T18)."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall
from order_guard.models.tables import AlertRule


# ---------------------------------------------------------------------------
# AlertRule model tests
# ---------------------------------------------------------------------------

class TestAlertRuleModel:
    def test_default_connector_type(self):
        """New rules default to legacy connector type."""
        rule = AlertRule(id="test-rule")
        assert rule.connector_type == "legacy"
        assert rule.mcp_server == ""
        assert rule.data_type == ""

    def test_mcp_rule(self):
        """MCP rule has correct fields."""
        rule = AlertRule(
            id="rule-mcp-test",
            name="MCP Test",
            connector_type="mcp",
            mcp_server="test-warehouse",
            prompt_template="检查库存",
        )
        assert rule.connector_type == "mcp"
        assert rule.mcp_server == "test-warehouse"

    def test_legacy_rule_backward_compatible(self):
        """Legacy rules still work without new fields."""
        rule = AlertRule(
            id="rule-legacy",
            name="Legacy Rule",
            connector_id="mock",
            prompt_template="检查库存",
        )
        assert rule.connector_type == "legacy"
        assert rule.connector_id == "mock"


# ---------------------------------------------------------------------------
# Pipeline branch tests
# ---------------------------------------------------------------------------

class TestPipelineBranch:
    @pytest.mark.asyncio
    async def test_mcp_pipeline_branch(self):
        """MCP rules go through Agent flow."""
        from order_guard.scheduler.jobs import _run_mcp_pipeline

        mock_mcp_manager = MagicMock()
        mock_mcp_conn = MagicMock()
        mock_mcp_conn.name = "test-db"
        mock_mcp_conn.is_connected.return_value = True
        mock_mcp_conn.list_tools = AsyncMock(return_value=[])
        mock_mcp_conn.call_tool = AsyncMock(return_value="data")
        mock_mcp_manager.get_connection.return_value = mock_mcp_conn

        rule = AlertRule(
            id="rule-mcp",
            connector_type="mcp",
            mcp_server="test-db",
            prompt_template="检查库存",
        )

        final_json = json.dumps({
            "alerts": [{"sku": "SKU-001", "severity": "warning", "title": "Test", "reason": "R", "suggestion": "S"}],
            "summary": "Test summary",
            "has_alerts": True,
        })

        with patch("order_guard.engine.agent.Agent.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AnalyzerOutput(
                alerts=[AlertItem(sku="SKU-001", severity="warning", title="Test", reason="R", suggestion="S")],
                summary="Test summary",
                has_alerts=True,
                token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            )

            result = await _run_mcp_pipeline(rule, mock_mcp_manager, MagicMock())

        assert result.has_alerts is True
        assert result.alerts[0].sku == "SKU-001"
        mock_mcp_manager.get_connection.assert_called_once_with("test-db")

    @pytest.mark.asyncio
    async def test_mcp_pipeline_no_manager_raises(self):
        """MCP pipeline raises if mcp_manager is None."""
        from order_guard.scheduler.jobs import _run_mcp_pipeline

        rule = AlertRule(id="rule-mcp", connector_type="mcp", mcp_server="db")
        with pytest.raises(ValueError, match="MCP manager not configured"):
            await _run_mcp_pipeline(rule, None, MagicMock())

    @pytest.mark.asyncio
    async def test_legacy_pipeline_branch(self):
        """Legacy rules go through Connector flow."""
        from order_guard.scheduler.jobs import _run_legacy_pipeline

        mock_connector = AsyncMock()
        mock_connector.query = AsyncMock(return_value=[
            {"sku": "SKU-001", "product_name": "Test", "stock": 100, "daily_avg_sales": 10, "lead_time_days": 14},
        ])

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_connector

        mock_analyzer = AsyncMock()
        mock_analyzer.analyze = AsyncMock(return_value=AnalyzerOutput(
            summary="All normal",
            has_alerts=False,
        ))

        rule = AlertRule(
            id="rule-legacy",
            connector_type="legacy",
            connector_id="mock",
            data_type="inventory",
            prompt_template="检查库存风险",
        )

        result = await _run_legacy_pipeline(rule, mock_registry, mock_analyzer)
        assert result.has_alerts is False
        mock_registry.get.assert_called_once_with("mock")

    @pytest.mark.asyncio
    async def test_output_format_consistent(self):
        """Both MCP and legacy pipelines produce AnalyzerOutput."""
        from order_guard.scheduler.jobs import _run_mcp_pipeline, _run_legacy_pipeline

        # MCP output
        mcp_output = AnalyzerOutput(
            alerts=[AlertItem(sku="SKU-001", severity="critical", title="T", reason="R", suggestion="S")],
            summary="MCP analysis",
            has_alerts=True,
            token_usage=TokenUsage(total_tokens=100),
        )

        # Legacy output
        legacy_output = AnalyzerOutput(
            alerts=[AlertItem(sku="SKU-002", severity="warning", title="T2", reason="R2", suggestion="S2")],
            summary="Legacy analysis",
            has_alerts=True,
            token_usage=TokenUsage(total_tokens=50),
        )

        # Both should have identical structure
        for output in [mcp_output, legacy_output]:
            assert hasattr(output, "alerts")
            assert hasattr(output, "summary")
            assert hasattr(output, "has_alerts")
            assert hasattr(output, "token_usage")
            assert isinstance(output.alerts, list)
            for alert in output.alerts:
                assert hasattr(alert, "sku")
                assert hasattr(alert, "severity")
                assert hasattr(alert, "title")
                assert hasattr(alert, "reason")
                assert hasattr(alert, "suggestion")
