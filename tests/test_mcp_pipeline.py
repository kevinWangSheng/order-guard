"""Integration tests for MCP pipeline (T18, updated T25)."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall
from order_guard.models.tables import AlertRule


# ---------------------------------------------------------------------------
# AlertRule model tests (post-T25: legacy fields removed)
# ---------------------------------------------------------------------------

class TestAlertRuleModel:
    def test_default_fields(self):
        """New rules have correct defaults."""
        rule = AlertRule(id="test-rule")
        assert rule.mcp_server == ""
        assert rule.data_window == ""
        assert rule.enabled is True

    def test_mcp_rule(self):
        """MCP rule has correct fields."""
        rule = AlertRule(
            id="rule-mcp-test",
            name="MCP Test",
            mcp_server="test-warehouse",
            prompt_template="检查库存",
        )
        assert rule.mcp_server == "test-warehouse"
        assert rule.name == "MCP Test"

    def test_rule_with_data_window(self):
        """Rule with data_window field."""
        rule = AlertRule(
            id="rule-window",
            name="Window Rule",
            mcp_server="db",
            data_window="30d",
        )
        assert rule.data_window == "30d"


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
            mcp_server="test-db",
            prompt_template="检查库存",
        )

        with patch("order_guard.engine.agent.Agent.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AnalyzerOutput(
                alerts=[AlertItem(sku="SKU-001", severity="warning", title="Test", reason="R", suggestion="S")],
                summary="Test summary",
                has_alerts=True,
                token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            )

            result = await _run_mcp_pipeline(rule, mock_mcp_manager)

        assert result.has_alerts is True
        assert result.alerts[0].sku == "SKU-001"
        mock_mcp_manager.get_connection.assert_called_once_with("test-db")

    @pytest.mark.asyncio
    async def test_mcp_pipeline_no_manager_raises(self):
        """MCP pipeline raises if mcp_manager is None."""
        from order_guard.scheduler.jobs import _run_mcp_pipeline

        rule = AlertRule(id="rule-mcp", mcp_server="db")
        with pytest.raises(ValueError, match="MCP manager not configured"):
            await _run_mcp_pipeline(rule, None)

    @pytest.mark.asyncio
    async def test_output_format(self):
        """MCP pipeline produces AnalyzerOutput with expected structure."""
        output = AnalyzerOutput(
            alerts=[AlertItem(sku="SKU-001", severity="critical", title="T", reason="R", suggestion="S")],
            summary="MCP analysis",
            has_alerts=True,
            token_usage=TokenUsage(total_tokens=100),
        )

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
