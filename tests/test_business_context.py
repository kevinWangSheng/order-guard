"""Tests for business context injection (N8)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from order_guard.engine.business_context import (
    MAX_CONTEXT_LENGTH,
    add_business_context,
    build_business_context_prompt,
    get_business_context,
)


# ---------------------------------------------------------------------------
# build_business_context_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_empty_context(self):
        assert build_business_context_prompt("") == ""

    def test_with_context(self):
        result = build_business_context_prompt("公司主营家居品类")
        assert "公司主营家居品类" in result
        assert "业务背景" in result

    def test_prompt_structure(self):
        result = build_business_context_prompt("test content")
        assert result.startswith("## 公司业务背景")
        assert "test content" in result
        assert "贴合实际" in result


# ---------------------------------------------------------------------------
# get_business_context
# ---------------------------------------------------------------------------

class TestGetContext:
    @pytest.mark.asyncio
    async def test_config_context(self):
        with patch("order_guard.engine.business_context.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(business_context="公司主营家居品类")
            with patch("order_guard.engine.business_context._load_db_updates", new_callable=AsyncMock, return_value=[]):
                result = await get_business_context()
                assert result == "公司主营家居品类"

    @pytest.mark.asyncio
    async def test_empty_config(self):
        with patch("order_guard.engine.business_context.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(business_context="")
            with patch("order_guard.engine.business_context._load_db_updates", new_callable=AsyncMock, return_value=[]):
                result = await get_business_context()
                assert result == ""

    @pytest.mark.asyncio
    async def test_config_plus_db_updates(self):
        with patch("order_guard.engine.business_context.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(business_context="公司主营家居")
            with patch("order_guard.engine.business_context._load_db_updates", new_callable=AsyncMock, return_value=["下周TEMU促销"]):
                result = await get_business_context()
                assert "公司主营家居" in result
                assert "下周TEMU促销" in result

    @pytest.mark.asyncio
    async def test_db_updates_only(self):
        with patch("order_guard.engine.business_context.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(business_context="")
            with patch("order_guard.engine.business_context._load_db_updates", new_callable=AsyncMock, return_value=["促销信息"]):
                result = await get_business_context()
                assert result == "促销信息"

    @pytest.mark.asyncio
    async def test_truncation(self):
        long_text = "x" * (MAX_CONTEXT_LENGTH + 100)
        with patch("order_guard.engine.business_context.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(business_context=long_text)
            with patch("order_guard.engine.business_context._load_db_updates", new_callable=AsyncMock, return_value=[]):
                result = await get_business_context()
                assert len(result) <= MAX_CONTEXT_LENGTH + 20
                assert "已截断" in result


# ---------------------------------------------------------------------------
# add_business_context
# ---------------------------------------------------------------------------

class TestAddContext:
    @pytest.mark.asyncio
    async def test_add_empty(self):
        result = await add_business_context("")
        assert result is False

    @pytest.mark.asyncio
    async def test_add_whitespace(self):
        result = await add_business_context("   ")
        assert result is False

    @pytest.mark.asyncio
    async def test_add_success(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("order_guard.storage.database.get_session", return_value=mock_session):
            with patch("order_guard.storage.crud.create", new_callable=AsyncMock) as mock_create:
                result = await add_business_context("下周促销", "user123")
                assert result is True
                mock_create.assert_called_once()
                entry = mock_create.call_args[0][1]
                assert entry.content == "下周促销"
                assert entry.source == "chat"
                assert entry.created_by == "user123"


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------

class TestAgentIntegration:
    @pytest.mark.asyncio
    async def test_agent_injects_business_context(self):
        """Agent should inject business context into system prompt."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        llm = MagicMock(spec=LLMClient)
        response = MagicMock()
        response.content = json.dumps({
            "alerts": [], "summary": "All good", "has_alerts": False,
        })
        response.tool_calls = []
        response.token_usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        llm.completion = AsyncMock(return_value=response)

        dal = MagicMock()
        dal.get_tools.return_value = []

        # Patch the functions where they're imported from
        with patch("order_guard.engine.business_context.get_business_context", new_callable=AsyncMock, return_value="主营家居品类") as mock_get:
            with patch("order_guard.engine.business_context.build_business_context_prompt", return_value="## 业务背景\n主营家居品类"):
                agent = Agent(
                    llm_client=llm,
                    data_access_layer=dal,
                    config=AgentConfig(
                        inject_schema=False,
                        inject_business_context=True,
                    ),
                )
                await agent.run("Check inventory")

                # Verify system prompt contains business context
                call_args = llm.completion.call_args
                messages = call_args[0][0]
                system_msg = messages[0]["content"]
                assert "业务背景" in system_msg
                assert "主营家居品类" in system_msg

    @pytest.mark.asyncio
    async def test_agent_works_without_business_context(self):
        """Agent should work fine when business_context is empty."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        llm = MagicMock(spec=LLMClient)
        response = MagicMock()
        response.content = json.dumps({
            "alerts": [], "summary": "OK", "has_alerts": False,
        })
        response.tool_calls = []
        response.token_usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        llm.completion = AsyncMock(return_value=response)

        dal = MagicMock()
        dal.get_tools.return_value = []

        with patch("order_guard.engine.business_context.get_business_context", new_callable=AsyncMock, return_value=""):
            agent = Agent(
                llm_client=llm,
                data_access_layer=dal,
                config=AgentConfig(
                    inject_schema=False,
                    inject_business_context=True,
                ),
            )
            result = await agent.run("Check inventory")
            assert result.summary == "OK"

    @pytest.mark.asyncio
    async def test_agent_disabled_business_context(self):
        """Agent should not call get_business_context when disabled."""
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        llm = MagicMock(spec=LLMClient)
        response = MagicMock()
        response.content = json.dumps({
            "alerts": [], "summary": "OK", "has_alerts": False,
        })
        response.tool_calls = []
        response.token_usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        llm.completion = AsyncMock(return_value=response)

        dal = MagicMock()
        dal.get_tools.return_value = []

        with patch("order_guard.engine.business_context.get_business_context", new_callable=AsyncMock) as mock_get:
            agent = Agent(
                llm_client=llm,
                data_access_layer=dal,
                config=AgentConfig(
                    inject_schema=False,
                    inject_business_context=False,
                ),
            )
            await agent.run("Check inventory")
            mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_business_context_field_exists(self):
        from order_guard.config.settings import Settings
        assert "business_context" in Settings.model_fields

    def test_default_empty(self):
        with patch("order_guard.config.settings.Path.exists", return_value=False):
            from order_guard.config.settings import Settings
            s = Settings()
            assert s.business_context == ""


# ---------------------------------------------------------------------------
# DB Model
# ---------------------------------------------------------------------------

class TestBusinessContextModel:
    def test_model_fields(self):
        from order_guard.models import BusinessContext
        entry = BusinessContext(content="test", source="chat", created_by="user1")
        assert entry.content == "test"
        assert entry.source == "chat"
        assert entry.created_by == "user1"
        assert entry.id

    def test_default_source(self):
        from order_guard.models import BusinessContext
        entry = BusinessContext(content="test")
        assert entry.source == "config"
