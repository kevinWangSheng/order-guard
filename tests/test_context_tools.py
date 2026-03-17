"""Tests for business context tools (N10)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager

from order_guard.tools.context_tools import (
    list_context,
    add_context,
    delete_context,
    build_context_injection,
    load_config_context,
    _parse_expires_at,
    TOOL_DEFINITIONS,
    MAX_CONTEXT_ITEMS,
)
from order_guard.models import BusinessContext, CONTEXT_CATEGORIES


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

    with patch("order_guard.tools.context_tools.get_session", _test_session):
        yield engine, _test_session

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_db(db_session):
    engine, test_session = db_session

    async with test_session() as session:
        ctx1 = BusinessContext(
            id="ctx-1",
            content="3月全线提价5%",
            category="strategy",
            source="config",
        )
        ctx2 = BusinessContext(
            id="ctx-2",
            content="主要供应商是义乌XX工厂",
            category="supplier",
            source="chat",
            created_by="user1",
        )
        ctx3 = BusinessContext(
            id="ctx-expired",
            content="过期的促销",
            category="promotion",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            source="chat",
        )
        session.add(ctx1)
        session.add(ctx2)
        session.add(ctx3)

    yield engine, test_session


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_has_3_tools(self):
        assert len(TOOL_DEFINITIONS) == 3

    def test_tool_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert names == {"list_context", "add_context", "delete_context"}

    def test_add_context_required_fields(self):
        schema = next(t for t in TOOL_DEFINITIONS if t.name == "add_context")
        assert schema.input_schema["required"] == ["content"]


# ---------------------------------------------------------------------------
# Relative time parsing
# ---------------------------------------------------------------------------

class TestParseExpiresAt:
    def test_days(self):
        result = _parse_expires_at("7d")
        assert result is not None
        diff = result - datetime.now(timezone.utc)
        assert 6 < diff.total_seconds() / 86400 < 8

    def test_hours(self):
        result = _parse_expires_at("24h")
        assert result is not None
        diff = result - datetime.now(timezone.utc)
        assert 23 < diff.total_seconds() / 3600 < 25

    def test_iso_date(self):
        result = _parse_expires_at("2026-12-31")
        assert result is not None
        assert result.year == 2026
        assert result.month == 12

    def test_empty(self):
        assert _parse_expires_at("") is None

    def test_invalid(self):
        assert _parse_expires_at("bad") is None


# ---------------------------------------------------------------------------
# list_context tests
# ---------------------------------------------------------------------------

class TestListContext:
    @pytest.mark.asyncio
    async def test_returns_non_expired(self, seeded_db):
        result = await list_context()
        assert "data" in result
        assert len(result["data"]) == 2  # expired one excluded
        ids = {item["id"] for item in result["data"]}
        assert "ctx-expired" not in ids

    @pytest.mark.asyncio
    async def test_filter_by_category(self, seeded_db):
        result = await list_context(category="supplier")
        assert len(result["data"]) == 1
        assert result["data"][0]["content"] == "主要供应商是义乌XX工厂"

    @pytest.mark.asyncio
    async def test_invalid_category(self, seeded_db):
        result = await list_context(category="invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_hint(self, db_session):
        result = await list_context()
        assert "暂无" in result["hint"]

    @pytest.mark.asyncio
    async def test_includes_category_name(self, seeded_db):
        result = await list_context()
        item = next(i for i in result["data"] if i["category"] == "strategy")
        assert item["category_name"] == "经营策略"


# ---------------------------------------------------------------------------
# add_context tests
# ---------------------------------------------------------------------------

class TestAddContext:
    @pytest.mark.asyncio
    async def test_success(self, db_session):
        result = await add_context(content="TEMU 平台做满减活动", category="promotion")
        assert "data" in result
        assert result["data"]["category"] == "promotion"

    @pytest.mark.asyncio
    async def test_with_expires_at(self, db_session):
        result = await add_context(content="临时折扣", expires_at="30d")
        assert "data" in result
        assert result["data"]["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_default_category(self, db_session):
        result = await add_context(content="一条知识")
        assert result["data"]["category"] == "other"

    @pytest.mark.asyncio
    async def test_empty_content(self, db_session):
        result = await add_context(content="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_category(self, db_session):
        result = await add_context(content="知识", category="invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_expires_at(self, db_session):
        result = await add_context(content="知识", expires_at="bad")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_max_items_limit(self, db_session):
        engine, test_session = db_session
        # Seed 20 items
        async with test_session() as session:
            for i in range(20):
                session.add(BusinessContext(content=f"知识{i}", category="other", source="chat"))

        result = await add_context(content="第21条")
        assert "error" in result
        assert "上限" in result["error"]


# ---------------------------------------------------------------------------
# delete_context tests
# ---------------------------------------------------------------------------

class TestDeleteContext:
    @pytest.mark.asyncio
    async def test_success(self, seeded_db):
        result = await delete_context(context_id="ctx-2")
        assert "data" in result
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio
    async def test_nonexistent(self, seeded_db):
        result = await delete_context(context_id="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_id(self):
        result = await delete_context(context_id="")
        assert "error" in result


# ---------------------------------------------------------------------------
# build_context_injection tests
# ---------------------------------------------------------------------------

class TestBuildContextInjection:
    @pytest.mark.asyncio
    async def test_groups_by_category(self, seeded_db):
        text = await build_context_injection()
        assert "## 业务背景" in text
        assert "### 经营策略" in text
        assert "### 供应商" in text

    @pytest.mark.asyncio
    async def test_excludes_expired(self, seeded_db):
        text = await build_context_injection()
        assert "过期的促销" not in text

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self, db_session):
        text = await build_context_injection()
        assert text == ""

    @pytest.mark.asyncio
    async def test_truncates_at_max_tokens(self, db_session):
        engine, test_session = db_session
        # Add items that exceed token limit
        async with test_session() as session:
            for i in range(15):
                session.add(BusinessContext(
                    content="x" * 200,
                    category="other",
                    source="chat",
                ))
        text = await build_context_injection(max_tokens=500)
        assert "已截断" in text

    @pytest.mark.asyncio
    async def test_respects_max_items(self, db_session):
        engine, test_session = db_session
        async with test_session() as session:
            for i in range(25):
                session.add(BusinessContext(
                    content=f"item{i}",
                    category="other",
                    source="chat",
                ))
        text = await build_context_injection(max_items=5)
        assert text.count("- item") <= 5


# ---------------------------------------------------------------------------
# Config initial loading tests
# ---------------------------------------------------------------------------

class TestLoadConfigContext:
    @pytest.mark.asyncio
    async def test_loads_from_config(self, db_session):
        mock_settings = type("S", (), {"business_context": "知识1\n知识2\n- 知识3"})()
        with patch("order_guard.config.get_settings", return_value=mock_settings):
            count = await load_config_context()
        assert count == 3

    @pytest.mark.asyncio
    async def test_no_duplicate_on_reload(self, db_session):
        mock_settings = type("S", (), {"business_context": "知识1"})()
        with patch("order_guard.config.get_settings", return_value=mock_settings):
            count1 = await load_config_context()
            count2 = await load_config_context()
        assert count1 == 1
        assert count2 == 0  # Already loaded

    @pytest.mark.asyncio
    async def test_empty_config(self, db_session):
        mock_settings = type("S", (), {"business_context": ""})()
        with patch("order_guard.config.get_settings", return_value=mock_settings):
            count = await load_config_context()
        assert count == 0
