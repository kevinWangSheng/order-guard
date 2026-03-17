"""Tests for LLM usage tracking — cost estimation, stats, and _log_usage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from order_guard.tools.usage_tools import (
    MODEL_PRICING,
    estimate_cost,
    get_usage_stats,
)
from order_guard.models.tables import LLMUsageLog
from order_guard.engine.llm_client import LLMResponse, TokenUsage, ToolCall


# ---------------------------------------------------------------------------
# estimate_cost tests
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_exact_match_gpt4o(self):
        """Exact match for gpt-4o."""
        cost = estimate_cost("gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # input: 2.50, output: 10.00
        assert cost == pytest.approx(12.50)

    def test_exact_match_gpt4o_mini(self):
        """Exact match for gpt-4o-mini."""
        cost = estimate_cost("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # input: 0.15, output: 0.60
        assert cost == pytest.approx(0.75)

    def test_exact_match_deepseek(self):
        """Exact match for deepseek-chat."""
        cost = estimate_cost("deepseek-chat", prompt_tokens=500_000, completion_tokens=200_000)
        # input: 0.14 * 0.5 = 0.07, output: 0.28 * 0.2 = 0.056
        assert cost == pytest.approx(0.126)

    def test_prefix_match(self):
        """Prefix match: gpt-4o-2024-01-01 should match gpt-4o."""
        cost = estimate_cost("gpt-4o-2024-01-01", prompt_tokens=1_000_000, completion_tokens=0)
        # Should match gpt-4o: input 2.50
        assert cost == pytest.approx(2.50)

    def test_prefix_match_claude(self):
        """Prefix match for claude variant."""
        cost = estimate_cost(
            "claude-sonnet-4-20250514-v2",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        # Should match claude-sonnet-4-20250514: input 3.00, output 15.00
        assert cost == pytest.approx(18.00)

    def test_unknown_model_returns_zero(self):
        """Unknown model should return 0 cost."""
        cost = estimate_cost("unknown-model-xyz", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert cost == 0.0

    def test_custom_pricing_exact_override(self):
        """Custom pricing should override built-in pricing."""
        custom = {"gpt-4o": {"input": 5.00, "output": 20.00}}
        cost = estimate_cost("gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000, custom_pricing=custom)
        # Custom: input 5.00, output 20.00
        assert cost == pytest.approx(25.00)

    def test_custom_pricing_new_model(self):
        """Custom pricing for a model not in built-in list."""
        custom = {"my-custom-llm": {"input": 1.00, "output": 2.00}}
        cost = estimate_cost("my-custom-llm", prompt_tokens=1_000_000, completion_tokens=1_000_000, custom_pricing=custom)
        assert cost == pytest.approx(3.00)

    def test_custom_pricing_prefix_match(self):
        """Custom pricing should also support prefix matching."""
        custom = {"my-model": {"input": 1.00, "output": 2.00}}
        cost = estimate_cost("my-model-v2", prompt_tokens=1_000_000, completion_tokens=1_000_000, custom_pricing=custom)
        assert cost == pytest.approx(3.00)

    def test_zero_tokens(self):
        """Zero tokens should return zero cost."""
        cost = estimate_cost("gpt-4o", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0

    def test_small_token_count(self):
        """Small token counts should compute correctly."""
        cost = estimate_cost("gpt-4o", prompt_tokens=100, completion_tokens=50)
        # input: 2.50 * 100/1M = 0.00025, output: 10.00 * 50/1M = 0.0005
        assert cost == pytest.approx(0.00075)


# ---------------------------------------------------------------------------
# LLMUsageLog model tests
# ---------------------------------------------------------------------------

class TestLLMUsageLogModel:
    def test_fields(self):
        log = LLMUsageLog(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_estimate_usd=0.00075,
            trigger_type="chat",
            rule_id="rule-1",
            user_id="user-1",
            session_id="sess-1",
            duration_ms=1234,
            tool_calls_count=3,
            iterations=5,
        )
        assert log.model == "gpt-4o"
        assert log.prompt_tokens == 100
        assert log.completion_tokens == 50
        assert log.total_tokens == 150
        assert log.cost_estimate_usd == pytest.approx(0.00075)
        assert log.trigger_type == "chat"
        assert log.rule_id == "rule-1"
        assert log.user_id == "user-1"
        assert log.session_id == "sess-1"
        assert log.duration_ms == 1234
        assert log.tool_calls_count == 3
        assert log.iterations == 5

    def test_defaults(self):
        log = LLMUsageLog()
        assert log.model == ""
        assert log.prompt_tokens == 0
        assert log.completion_tokens == 0
        assert log.total_tokens == 0
        assert log.cost_estimate_usd == 0.0
        assert log.trigger_type == ""
        assert log.rule_id == ""
        assert log.user_id == ""
        assert log.session_id == ""
        assert log.duration_ms == 0
        assert log.tool_calls_count == 0
        assert log.iterations == 0
        assert log.id  # UUID should be generated
        assert log.created_at  # Timestamp should be generated


# ---------------------------------------------------------------------------
# get_usage_stats tests (with in-memory SQLite)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def usage_db(tmp_path):
    """Set up an in-memory DB with LLMUsageLog records."""
    import os
    os.environ["OG_CONFIG_FILE"] = "/dev/null"

    from order_guard.storage.database import reset_engine, get_engine, get_session
    from sqlmodel import SQLModel
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

    # Reset any existing engine
    reset_engine()

    db_path = tmp_path / "test_usage.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Patch get_session to use our test engine
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _test_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Seed some data
    now = datetime.now(timezone.utc)
    records = [
        LLMUsageLog(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_estimate_usd=0.0075,
            trigger_type="rule",
            rule_id="rule-1",
            created_at=now - timedelta(hours=1),
        ),
        LLMUsageLog(
            model="gpt-4o",
            prompt_tokens=2000,
            completion_tokens=1000,
            total_tokens=3000,
            cost_estimate_usd=0.015,
            trigger_type="chat",
            rule_id="",
            user_id="user-1",
            created_at=now - timedelta(hours=2),
        ),
        LLMUsageLog(
            model="deepseek-chat",
            prompt_tokens=5000,
            completion_tokens=2000,
            total_tokens=7000,
            cost_estimate_usd=0.00126,
            trigger_type="rule",
            rule_id="rule-2",
            created_at=now - timedelta(hours=3),
        ),
        # Old record (beyond 7d)
        LLMUsageLog(
            model="gpt-4o",
            prompt_tokens=10000,
            completion_tokens=5000,
            total_tokens=15000,
            cost_estimate_usd=0.075,
            trigger_type="rule",
            rule_id="rule-1",
            created_at=now - timedelta(days=10),
        ),
    ]

    async with _test_session() as session:
        for r in records:
            session.add(r)

    yield _test_session

    # Cleanup
    await engine.dispose()
    reset_engine()


@pytest.mark.asyncio
class TestGetUsageStats:
    async def test_basic_stats(self, usage_db):
        """Basic stats within 7d should sum 3 recent records."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d")

        data = result["data"]
        assert data["count"] == 3
        assert data["total_tokens"] == 1500 + 3000 + 7000
        assert data["total_cost_usd"] == pytest.approx(0.0075 + 0.015 + 0.00126)

    async def test_group_by_trigger_type(self, usage_db):
        """Group by trigger_type should return 2 groups (rule, chat)."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", group_by="trigger_type")

        groups = result["data"]["by_group"]
        assert len(groups) == 2
        keys = {g["key"] for g in groups}
        assert keys == {"rule", "chat"}

    async def test_group_by_model(self, usage_db):
        """Group by model should return 2 groups (gpt-4o, deepseek-chat)."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", group_by="model")

        groups = result["data"]["by_group"]
        assert len(groups) == 2
        keys = {g["key"] for g in groups}
        assert keys == {"gpt-4o", "deepseek-chat"}

    async def test_group_by_rule(self, usage_db):
        """Group by rule should return 3 groups (rule-1, rule-2, empty)."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", group_by="rule")

        groups = result["data"]["by_group"]
        assert len(groups) == 3

    async def test_group_by_day(self, usage_db):
        """Group by day should return day-level grouping."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", group_by="day")

        groups = result["data"]["by_group"]
        assert len(groups) >= 1
        # Each group key should look like a date
        for g in groups:
            assert len(g["key"]) == 10  # "YYYY-MM-DD"

    async def test_filter_by_rule_id(self, usage_db):
        """Filtering by rule_id should only return matching records."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", rule_id="rule-1")

        data = result["data"]
        assert data["count"] == 1
        assert data["total_tokens"] == 1500

    async def test_empty_data(self, usage_db):
        """Filtering by nonexistent rule_id should return zero stats."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d", rule_id="nonexistent")

        data = result["data"]
        assert data["count"] == 0
        assert data["total_tokens"] == 0
        assert data["total_cost_usd"] == 0.0
        assert data["by_group"] == []

    async def test_hint_present(self, usage_db):
        """Result should have a hint string."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="7d")

        assert "hint" in result
        assert isinstance(result["hint"], str)
        assert len(result["hint"]) > 0

    async def test_30d_includes_old_record(self, usage_db):
        """30d range should include the old record."""
        with patch("order_guard.storage.database.get_session", usage_db):
            result = await get_usage_stats(time_range="30d")

        data = result["data"]
        assert data["count"] == 4


# ---------------------------------------------------------------------------
# Agent._log_usage integration test
# ---------------------------------------------------------------------------

FINAL_JSON = json.dumps({
    "alerts": [],
    "summary": "No issues",
    "has_alerts": False,
})


@pytest.mark.asyncio
class TestAgentLogUsage:
    async def test_log_usage_called_on_run(self):
        """Agent.run() should call _log_usage with correct parameters."""
        from order_guard.engine.agent import Agent, AgentConfig

        mcp = AsyncMock()
        mcp.name = "test-db"
        tool = MagicMock()
        tool.name = "execute_sql"
        tool.description = "Execute SQL"
        tool.input_schema = {}
        tool.server_name = "test-db"
        mcp.list_tools.return_value = [tool]

        llm = AsyncMock()
        llm._model = "gpt-4o"

        async def _mock_completion(messages, **kwargs):
            return LLMResponse(
                content=FINAL_JSON,
                tool_calls=[],
                token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                model="gpt-4o",
            )

        llm.completion.side_effect = _mock_completion

        logged_usages = []

        async def _mock_log_usage(**kwargs):
            logged_usages.append(kwargs)

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=False, validate_sql=False),
            rule_id="test-rule",
        )
        agent._log_usage = _mock_log_usage

        await agent.run(
            "检查数据",
            trigger_type="rule",
            user_id="u1",
            session_id="s1",
        )

        assert len(logged_usages) == 1
        log = logged_usages[0]
        assert log["token_usage"].prompt_tokens == 100
        assert log["token_usage"].completion_tokens == 50
        assert log["token_usage"].total_tokens == 150
        assert log["model"] == "gpt-4o"
        assert log["trigger_type"] == "rule"
        assert log["rule_id"] == "test-rule"
        assert log["user_id"] == "u1"
        assert log["session_id"] == "s1"
        assert log["duration_ms"] >= 0
        assert log["iterations"] == 1

    async def test_log_usage_writes_to_db(self, tmp_path):
        """_log_usage should write a record to the database."""
        import os
        os.environ["OG_CONFIG_FILE"] = "/dev/null"

        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.storage.database import reset_engine
        from sqlmodel import SQLModel
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy import select
        from contextlib import asynccontextmanager

        reset_engine()

        db_path = tmp_path / "test_log_usage.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(db_url, echo=False)

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        @asynccontextmanager
        async def _test_session():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        mcp = AsyncMock()
        mcp.name = "test-db"
        llm = AsyncMock()
        llm._model = "gpt-4o"

        agent = Agent(
            llm_client=llm,
            mcp_connection=mcp,
            config=AgentConfig(inject_schema=False),
            rule_id="test-rule",
        )

        token_usage = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300)

        with patch("order_guard.storage.database.get_session", _test_session), \
             patch("order_guard.config.get_settings") as mock_settings:
            mock_llm_config = MagicMock()
            mock_llm_config.custom_pricing = {}
            mock_settings.return_value.llm = mock_llm_config

            await agent._log_usage(
                token_usage=token_usage,
                model="gpt-4o",
                trigger_type="chat",
                rule_id="test-rule",
                user_id="user-1",
                session_id="sess-1",
                duration_ms=500,
                tool_calls_count=2,
                iterations=3,
            )

        # Verify record was written
        async with _test_session() as session:
            result = await session.execute(select(LLMUsageLog))
            logs = result.scalars().all()

        assert len(logs) == 1
        log = logs[0]
        assert log.model == "gpt-4o"
        assert log.prompt_tokens == 200
        assert log.completion_tokens == 100
        assert log.total_tokens == 300
        assert log.trigger_type == "chat"
        assert log.rule_id == "test-rule"
        assert log.user_id == "user-1"
        assert log.session_id == "sess-1"
        assert log.duration_ms == 500
        assert log.tool_calls_count == 2
        assert log.iterations == 3
        assert log.cost_estimate_usd > 0

        await engine.dispose()
        reset_engine()
