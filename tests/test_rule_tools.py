"""Tests for rule management tools (N9)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from order_guard.tools.rule_tools import (
    list_rules,
    create_rule,
    update_rule,
    delete_rule,
    test_rule as run_test_rule,
    get_rule_stats,
    configure,
    _describe_cron,
    TOOL_DEFINITIONS,
    TOOL_EXECUTORS,
)
from order_guard.models import AlertRule, Alert, TaskRun, LLMUsageLog


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

    # Patch get_session to use our test DB
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.rule_tools.get_session", _test_session):
        yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_db(db_session):
    """Seed DB with test rules and alerts."""
    engine = db_session
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            # Create rules
            r1 = AlertRule(
                id="rule-stock",
                name="缺货检测",
                mcp_server="erp-mysql",
                prompt_template="检查库存...",
                schedule="0 9 * * *",
                data_window="7d",
                source="yaml",
                enabled=True,
            )
            r2 = AlertRule(
                id="rule-returns",
                name="退货率异常",
                mcp_server="erp-mysql",
                prompt_template="检查退货...",
                schedule="0 18 * * *",
                data_window="30d",
                source="chat",
                enabled=False,
            )
            session.add(r1)
            session.add(r2)

            # Create an alert for rule-stock
            a1 = Alert(
                rule_id="rule-stock",
                severity="critical",
                title="SKU-001 缺货",
                summary="库存为0",
                status="sent",
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            session.add(a1)

            # Create a task run
            tr1 = TaskRun(
                job_name="stock-check",
                rule_id="rule-stock",
                status="success",
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            session.add(tr1)

    # Re-patch get_session after seeding
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.rule_tools.get_session", _test_session):
        yield engine


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_has_6_tools(self):
        assert len(TOOL_DEFINITIONS) == 6

    def test_tool_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert names == {"list_rules", "create_rule", "update_rule", "delete_rule", "test_rule", "get_rule_stats"}

    def test_get_rule_stats_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "get_rule_stats")
        assert set(schema.input_schema["required"]) == {"rule_id"}

    def test_executors_match_definitions(self):
        defined = {t.name for t in TOOL_DEFINITIONS}
        executed = set(TOOL_EXECUTORS.keys())
        assert defined == executed

    def test_create_rule_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "create_rule")
        # No top-level required — single fields or rules array both accepted
        assert schema.input_schema["required"] == []

    def test_update_rule_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "update_rule")
        assert set(schema.input_schema["required"]) == {"rule_id", "changes"}


# ---------------------------------------------------------------------------
# Cron description tests
# ---------------------------------------------------------------------------

class TestDescribeCron:
    def test_common_crons(self):
        assert _describe_cron("0 9 * * *") == "每天 9:00"
        assert _describe_cron("0 * * * *") == "每小时整点"
        assert _describe_cron("*/30 * * * *") == "每30分钟"

    def test_generated_crons(self):
        assert _describe_cron("30 14 * * *") == "每天 14:30"
        assert _describe_cron("0 */3 * * *") == "每3小时"

    def test_complex_cron_returns_original(self):
        assert _describe_cron("0 9 * * 1-5") == "工作日 9:00"


# ---------------------------------------------------------------------------
# Return envelope tests
# ---------------------------------------------------------------------------

class TestReturnEnvelope:
    @pytest.mark.asyncio
    async def test_success_has_data_and_hint(self, seeded_db):
        result = await list_rules()
        assert "data" in result
        assert "hint" in result
        assert isinstance(result["data"], list)

    @pytest.mark.asyncio
    async def test_error_has_error_and_hint(self):
        # Create rule with empty name
        with patch("order_guard.tools.rule_tools.get_session"):
            result = await create_rule(name="", mcp_server="x", prompt_template="y", schedule="0 9 * * *")
        assert "error" in result
        assert "hint" in result


# ---------------------------------------------------------------------------
# list_rules tests
# ---------------------------------------------------------------------------

class TestListRules:
    @pytest.mark.asyncio
    async def test_returns_all_rules(self, seeded_db):
        result = await list_rules()
        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_includes_alerts_24h(self, seeded_db):
        result = await list_rules()
        stock_rule = next(r for r in result["data"] if r["id"] == "rule-stock")
        assert stock_rule["alerts_24h"] == 1

    @pytest.mark.asyncio
    async def test_includes_last_run(self, seeded_db):
        result = await list_rules()
        stock_rule = next(r for r in result["data"] if r["id"] == "rule-stock")
        assert stock_rule["last_run"] is not None
        assert stock_rule["last_run_status"] == "success"

    @pytest.mark.asyncio
    async def test_includes_human_schedule(self, seeded_db):
        result = await list_rules()
        stock_rule = next(r for r in result["data"] if r["id"] == "rule-stock")
        assert stock_rule["schedule_human"] == "每天 9:00"

    @pytest.mark.asyncio
    async def test_hint_mentions_disabled(self, seeded_db):
        result = await list_rules()
        assert "1 条已禁用" in result["hint"]

    @pytest.mark.asyncio
    async def test_empty_rules_hint(self, db_session):
        result = await list_rules()
        assert "暂无" in result["hint"]


# ---------------------------------------------------------------------------
# create_rule tests
# ---------------------------------------------------------------------------

class TestCreateRule:
    @pytest.mark.asyncio
    async def test_success(self, db_session):
        configure(data_access_layer=None)  # Skip DAL check
        result = await create_rule(
            name="测试规则",
            mcp_server="erp",
            prompt_template="检查数据",
            schedule="0 9 * * *",
        )
        assert "data" in result
        assert result["data"]["name"] == "测试规则"
        assert result["data"]["schedule_human"] == "每天 9:00"

    @pytest.mark.asyncio
    async def test_invalid_cron(self, db_session):
        result = await create_rule(
            name="测试",
            mcp_server="erp",
            prompt_template="检查",
            schedule="invalid",
        )
        assert "error" in result
        assert "cron" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_cron_missing_field(self, db_session):
        result = await create_rule(
            name="测试",
            mcp_server="erp",
            prompt_template="检查",
            schedule="0 9 * *",  # Only 4 fields
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_name(self, db_session):
        result = await create_rule(
            name="",
            mcp_server="erp",
            prompt_template="检查",
            schedule="0 9 * * *",
        )
        assert "error" in result
        assert "name" in result["error"]

    @pytest.mark.asyncio
    async def test_mcp_server_not_exist(self, db_session):
        mock_dal = MagicMock()
        mock_dal.list_datasource_ids.return_value = ["erp-mysql", "pg-analytics"]
        configure(data_access_layer=mock_dal)
        try:
            result = await create_rule(
                name="测试",
                mcp_server="nonexistent",
                prompt_template="检查",
                schedule="0 9 * * *",
            )
            assert "error" in result
            assert "不存在" in result["error"]
        finally:
            configure(data_access_layer=None)

    @pytest.mark.asyncio
    async def test_duplicate_name(self, seeded_db):
        configure(data_access_layer=None)
        result = await create_rule(
            name="缺货检测",
            mcp_server="erp",
            prompt_template="检查",
            schedule="0 9 * * *",
        )
        assert "error" in result
        assert "已存在" in result["error"]

    @pytest.mark.asyncio
    async def test_scheduler_registration(self, db_session):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler, data_access_layer=None)
        try:
            result = await create_rule(
                name="带调度的规则",
                mcp_server="erp",
                prompt_template="检查",
                schedule="0 9 * * *",
            )
            assert "data" in result
            mock_scheduler.add_schedule.assert_called_once()
        finally:
            configure(scheduler=None, data_access_layer=None)

    @pytest.mark.asyncio
    async def test_disabled_rule_no_schedule(self, db_session):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler, data_access_layer=None)
        try:
            result = await create_rule(
                name="禁用规则",
                mcp_server="erp",
                prompt_template="检查",
                schedule="0 9 * * *",
                enabled=False,
            )
            assert "data" in result
            mock_scheduler.add_schedule.assert_not_called()
        finally:
            configure(scheduler=None, data_access_layer=None)


# ---------------------------------------------------------------------------
# update_rule tests
# ---------------------------------------------------------------------------

class TestUpdateRule:
    @pytest.mark.asyncio
    async def test_update_partial_fields(self, seeded_db):
        result = await update_rule(rule_id="rule-stock", changes={"name": "新名称"})
        assert "data" in result
        assert result["data"]["changes"]["name"] == "新名称"

    @pytest.mark.asyncio
    async def test_update_schedule_syncs_scheduler(self, seeded_db):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler)

        result = await update_rule(
            rule_id="rule-stock",
            changes={"schedule": "0 */2 * * *"},
        )
        assert "data" in result
        # Should remove old and add new
        mock_scheduler.remove_schedule.assert_called_once()
        mock_scheduler.add_schedule.assert_called_once()

        configure(scheduler=None)

    @pytest.mark.asyncio
    async def test_disable_removes_schedule(self, seeded_db):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler)

        result = await update_rule(
            rule_id="rule-stock",
            changes={"enabled": False},
        )
        assert "data" in result
        mock_scheduler.remove_schedule.assert_called_once()

        configure(scheduler=None)

    @pytest.mark.asyncio
    async def test_enable_registers_schedule(self, seeded_db):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler)

        result = await update_rule(
            rule_id="rule-returns",  # Currently disabled
            changes={"enabled": True},
        )
        assert "data" in result
        mock_scheduler.add_schedule.assert_called_once()

        configure(scheduler=None)

    @pytest.mark.asyncio
    async def test_nonexistent_rule(self, seeded_db):
        result = await update_rule(rule_id="nonexistent", changes={"name": "x"})
        assert "error" in result
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_schedule(self, seeded_db):
        result = await update_rule(
            rule_id="rule-stock",
            changes={"schedule": "bad cron"},
        )
        assert "error" in result
        assert "cron" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_changes(self, seeded_db):
        result = await update_rule(rule_id="rule-stock", changes={})
        assert "error" in result


# ---------------------------------------------------------------------------
# delete_rule tests
# ---------------------------------------------------------------------------

class TestDeleteRule:
    @pytest.mark.asyncio
    async def test_delete_single(self, seeded_db):
        result = await delete_rule(scope="single", rule_id="rule-returns")
        assert "data" in result
        assert result["data"]["deleted_count"] == 1
        assert result["data"]["deleted"][0]["id"] == "rule-returns"

    @pytest.mark.asyncio
    async def test_delete_yaml_rule_hint(self, seeded_db):
        result = await delete_rule(scope="single", rule_id="rule-stock")
        assert "data" in result
        assert "YAML" in result["hint"] or "yaml" in result["hint"].lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, seeded_db):
        result = await delete_rule(scope="single", rule_id="nonexistent")
        assert "data" in result
        assert result["data"]["deleted_count"] == 0
        assert "nonexistent" in result["data"]["not_found"]

    @pytest.mark.asyncio
    async def test_delete_removes_schedule(self, seeded_db):
        mock_scheduler = MagicMock()
        configure(scheduler=mock_scheduler)

        result = await delete_rule(scope="single", rule_id="rule-returns")
        assert "data" in result
        assert result["data"]["deleted_count"] == 1
        mock_scheduler.remove_schedule.assert_called_once()

        configure(scheduler=None)

    @pytest.mark.asyncio
    async def test_delete_all(self, seeded_db):
        result = await delete_rule(scope="all")
        assert "data" in result
        assert result["data"]["deleted_count"] >= 2  # At least rule-stock + rule-returns

    @pytest.mark.asyncio
    async def test_delete_single_missing_rule_id(self, seeded_db):
        result = await delete_rule(scope="single")
        assert "error" in result
        assert "rule_id" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_invalid_scope(self, seeded_db):
        result = await delete_rule(scope="invalid")
        assert "error" in result
        assert "scope" in result["error"]


# ---------------------------------------------------------------------------
# test_rule tests
# ---------------------------------------------------------------------------

class TestTestRule:
    @pytest.mark.asyncio
    async def test_nonexistent_rule(self, seeded_db):
        configure(data_access_layer=None, mcp_manager=None)
        result = await run_test_rule(rule_id="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_backend_error(self, seeded_db):
        configure(data_access_layer=None, mcp_manager=None)
        result = await run_test_rule(rule_id="rule-stock")
        assert "error" in result
        assert "数据访问" in result["error"] or "后端" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_rule_id(self):
        result = await run_test_rule(rule_id="")
        assert "error" in result


# ---------------------------------------------------------------------------
# list_rules effectiveness fields tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def rich_seeded_db(db_session):
    """Seed DB with rules, alerts (including false positives), task runs, and LLM usage."""
    engine = db_session
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            now = datetime.now(timezone.utc)

            # Rule with high false positive rate
            r1 = AlertRule(
                id="rule-fp",
                name="高误报规则",
                mcp_server="erp-mysql",
                prompt_template="检查...",
                schedule="0 9 * * *",
                data_window="7d",
                source="yaml",
                enabled=True,
            )
            # Rule with no triggers
            r2 = AlertRule(
                id="rule-quiet",
                name="安静规则",
                mcp_server="erp-mysql",
                prompt_template="检查...",
                schedule="0 18 * * *",
                data_window="7d",
                source="chat",
                enabled=True,
            )
            # Rule with low success rate
            r3 = AlertRule(
                id="rule-fail",
                name="不稳定规则",
                mcp_server="erp-mysql",
                prompt_template="检查...",
                schedule="0 12 * * *",
                data_window="7d",
                source="yaml",
                enabled=True,
            )
            session.add_all([r1, r2, r3])

            # 10 alerts for rule-fp, 4 are false_positive
            for i in range(10):
                a = Alert(
                    rule_id="rule-fp",
                    severity="warning" if i % 2 == 0 else "critical",
                    title=f"Alert {i}",
                    summary=f"Detail {i}",
                    status="sent",
                    resolution="false_positive" if i < 4 else None,
                    created_at=now - timedelta(hours=i + 1),
                )
                session.add(a)

            # Task runs for rule-fp: 5 success
            for i in range(5):
                tr = TaskRun(
                    job_name="fp-check",
                    rule_id="rule-fp",
                    status="success",
                    started_at=now - timedelta(hours=i + 1),
                    duration_ms=1000 + i * 100,
                )
                session.add(tr)

            # Task runs for rule-fail: 2 success, 8 failed
            for i in range(10):
                tr = TaskRun(
                    job_name="fail-check",
                    rule_id="rule-fail",
                    status="success" if i < 2 else "failed",
                    started_at=now - timedelta(hours=i + 1),
                    duration_ms=500,
                )
                session.add(tr)

            # LLM usage for rule-fp
            usage = LLMUsageLog(
                model="gpt-4",
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
                cost_estimate_usd=0.05,
                trigger_type="rule",
                rule_id="rule-fp",
                created_at=now - timedelta(hours=1),
            )
            session.add(usage)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    with patch("order_guard.tools.rule_tools.get_session", _test_session):
        yield engine


class TestListRulesEffectiveness:
    @pytest.mark.asyncio
    async def test_includes_7d_fields(self, rich_seeded_db):
        result = await list_rules()
        assert "data" in result
        fp_rule = next(r for r in result["data"] if r["id"] == "rule-fp")
        assert fp_rule["trigger_count_7d"] == 10
        assert fp_rule["false_positive_count_7d"] == 4
        assert fp_rule["false_positive_rate"] == 0.4
        assert fp_rule["last_triggered_at"] is not None
        assert fp_rule["run_count_7d"] == 5
        assert fp_rule["run_success_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_quiet_rule_no_triggers(self, rich_seeded_db):
        result = await list_rules()
        quiet_rule = next(r for r in result["data"] if r["id"] == "rule-quiet")
        assert quiet_rule["trigger_count_7d"] == 0
        assert quiet_rule["false_positive_rate"] == 0.0
        assert quiet_rule["last_triggered_at"] is None

    @pytest.mark.asyncio
    async def test_low_success_rate(self, rich_seeded_db):
        result = await list_rules()
        fail_rule = next(r for r in result["data"] if r["id"] == "rule-fail")
        assert fail_rule["run_count_7d"] == 10
        assert fail_rule["run_success_rate"] == 0.2

    @pytest.mark.asyncio
    async def test_smart_hints_false_positive(self, rich_seeded_db):
        result = await list_rules()
        assert "误报率" in result["hint"]

    @pytest.mark.asyncio
    async def test_smart_hints_quiet_rule(self, rich_seeded_db):
        result = await list_rules()
        assert "无触发" in result["hint"]

    @pytest.mark.asyncio
    async def test_smart_hints_low_success(self, rich_seeded_db):
        result = await list_rules()
        assert "成功率" in result["hint"]


# ---------------------------------------------------------------------------
# get_rule_stats tests
# ---------------------------------------------------------------------------

class TestGetRuleStats:
    @pytest.mark.asyncio
    async def test_empty_rule_id(self):
        result = await get_rule_stats(rule_id="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_time_range(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="1y")
        assert "error" in result
        assert "time_range" in result["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_rule(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="nonexistent")
        assert "error" in result
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_basic_section(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        assert "data" in result
        basic = result["data"]["basic"]
        assert basic["name"] == "高误报规则"
        assert basic["enabled"] is True
        assert basic["source"] == "yaml"
        assert basic["schedule"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_execution_section(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        execution = result["data"]["execution"]
        assert execution["total_runs"] == 5
        assert execution["success_runs"] == 5
        assert execution["failed_runs"] == 0
        assert execution["success_rate"] == 1.0
        assert execution["avg_duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_alerts_section(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        alerts = result["data"]["alerts"]
        assert alerts["total_alerts"] == 10
        assert alerts["false_positive_rate"] == 0.4
        assert "warning" in alerts["by_severity"]
        assert "critical" in alerts["by_severity"]
        assert "false_positive" in alerts["by_resolution"]

    @pytest.mark.asyncio
    async def test_trend_section(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        trend = result["data"]["trend"]
        assert len(trend) == 7
        assert all("date" in d and "count" in d for d in trend)
        # At least some days should have counts > 0
        total_in_trend = sum(d["count"] for d in trend)
        assert total_in_trend > 0

    @pytest.mark.asyncio
    async def test_token_usage_section(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        token_usage = result["data"]["token_usage"]
        assert token_usage is not None
        assert token_usage["total_tokens"] == 1500
        assert token_usage["total_cost_usd"] == 0.05

    @pytest.mark.asyncio
    async def test_no_token_usage(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-quiet", time_range="7d")
        assert result["data"]["token_usage"] is None

    @pytest.mark.asyncio
    async def test_failed_rule_stats(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fail", time_range="7d")
        execution = result["data"]["execution"]
        assert execution["total_runs"] == 10
        assert execution["success_runs"] == 2
        assert execution["failed_runs"] == 8
        assert execution["success_rate"] == 0.2

    @pytest.mark.asyncio
    async def test_default_time_range(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp")
        # Default is 30d, should include all data
        assert "data" in result
        assert result["data"]["alerts"]["total_alerts"] == 10

    @pytest.mark.asyncio
    async def test_hint_includes_stats(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        assert "hint" in result
        assert "高误报规则" in result["hint"]
        assert "误报率" in result["hint"]

    @pytest.mark.asyncio
    async def test_response_envelope(self, rich_seeded_db):
        result = await get_rule_stats(rule_id="rule-fp", time_range="7d")
        assert "data" in result
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_error_envelope(self):
        result = await get_rule_stats(rule_id="")
        assert "error" in result
        assert "hint" in result
