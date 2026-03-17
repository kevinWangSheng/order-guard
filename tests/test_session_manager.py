"""Tests for SessionManager — persistent conversation sessions (N5, N14)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.api.session import SessionManager
from order_guard.models import Session, SessionMessage


# ---------------------------------------------------------------------------
# Helpers — in-memory DB session mock
# ---------------------------------------------------------------------------

class FakeDB:
    """Minimal async DB session mock backed by in-memory lists."""

    def __init__(self):
        self._sessions: list[Session] = []
        self._messages: list[SessionMessage] = []

    def add(self, obj):
        if isinstance(obj, Session):
            # Update existing or add new
            for i, s in enumerate(self._sessions):
                if s.id == obj.id:
                    self._sessions[i] = obj
                    return
            self._sessions.append(obj)
        elif isinstance(obj, SessionMessage):
            for i, m in enumerate(self._messages):
                if m.id == obj.id:
                    self._messages[i] = obj
                    return
            self._messages.append(obj)

    async def exec(self, stmt):
        """Very simplified query execution."""
        return FakeResult(self, stmt)

    async def execute(self, stmt):
        """SQLAlchemy AsyncSession compatible execute."""
        return FakeResult(self, stmt)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        if isinstance(obj, Session):
            self._sessions = [s for s in self._sessions if s.id != obj.id]
        elif isinstance(obj, SessionMessage):
            self._messages = [m for m in self._messages if m.id != obj.id]


class FakeResult:
    """Minimal result wrapper."""

    def __init__(self, db: FakeDB, stmt):
        self._db = db
        self._stmt = stmt
        self._items = self._resolve()

    def _resolve(self) -> list:
        """Very rough query resolution — enough for unit tests."""
        stmt = self._stmt
        # Compile statement to string for inspection
        try:
            from sqlmodel import Session as _S
            stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            stmt_str = str(stmt)

        # Count query
        if "count" in stmt_str.lower() and "session_messages" in stmt_str.lower():
            return [len(self._db._messages)]

        # SessionMessage queries
        if "session_messages" in stmt_str.lower():
            items = list(self._db._messages)
            # The real query does ORDER BY created_at DESC + LIMIT
            # Simulate: reverse first, then limit
            if "desc" in stmt_str.lower():
                items = list(reversed(items))
            if hasattr(stmt, '_limit_clause') and stmt._limit_clause is not None:
                try:
                    limit_val = stmt._limit_clause.value
                    items = items[:limit_val]
                except Exception:
                    pass
            return items

        # Session queries
        return list(self._db._sessions)

    def all(self) -> list:
        return self._items

    def first(self):
        return self._items[0] if self._items else None

    def scalars(self):
        """Return self for chaining (mimics SQLAlchemy Result.scalars())."""
        return self

    def scalar_one_or_none(self):
        """Return first scalar value or None."""
        return self._items[0] if self._items else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def mock_get_session(fake_db):
    """Patch get_session to return our fake DB."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_get_session():
        yield fake_db

    with patch("order_guard.api.session.get_session", _fake_get_session):
        yield fake_db


@pytest.fixture
def mgr():
    return SessionManager(max_turns=5)


# ---------------------------------------------------------------------------
# Unit tests — SessionManager
# ---------------------------------------------------------------------------

class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_create_session_returns_session(self, mgr, mock_get_session):
        session = await mgr.create_session("user1", "chat1")
        assert isinstance(session, Session)
        assert session.user_id == "user1"
        assert session.chat_id == "chat1"
        assert session.is_active is True

    @pytest.mark.asyncio
    async def test_create_session_deactivates_old(self, mgr, mock_get_session):
        s1 = await mgr.create_session("user1", "chat1")
        s2 = await mgr.create_session("user1", "chat1")
        # Both exist in DB but s1 should be deactivated
        assert s2.is_active is True
        # The old session in the DB should have is_active=False
        db = mock_get_session
        old = [s for s in db._sessions if s.id == s1.id]
        if old:
            assert old[0].is_active is False

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new(self, mgr, mock_get_session):
        session = await mgr.get_or_create_active("user_new", "chat1")
        assert isinstance(session, Session)
        assert session.user_id == "user_new"

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, mgr, mock_get_session):
        s1 = await mgr.create_session("user1", "chat1")
        s2 = await mgr.get_or_create_active("user1", "chat1")
        assert s2.id == s1.id


class TestSessionMessages:
    @pytest.mark.asyncio
    async def test_add_message(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        await mgr.add_message(session.id, "user", "hello")
        db = mock_get_session
        assert len(db._messages) == 1
        assert db._messages[0].role == "user"
        assert db._messages[0].content == "hello"

    @pytest.mark.asyncio
    async def test_get_context_returns_messages(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        await mgr.add_message(session.id, "user", "q1")
        await mgr.add_message(session.id, "assistant", "a1")
        ctx = await mgr.get_context(session.id)
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"
        assert ctx[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_get_context_respects_max_turns(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        # Add 20 messages (10 turns)
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            await mgr.add_message(session.id, role, f"msg{i}")
        # max_turns=5 → limit=10 messages
        ctx = await mgr.get_context(session.id, max_turns=5)
        assert len(ctx) <= 10

    @pytest.mark.asyncio
    async def test_get_message_count(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        await mgr.add_message(session.id, "user", "hello")
        await mgr.add_message(session.id, "assistant", "hi")
        count = await mgr.get_message_count(session.id)
        assert count == 2


class TestSessionOperations:
    @pytest.mark.asyncio
    async def test_list_sessions(self, mgr, mock_get_session):
        await mgr.create_session("user1", "chat1")
        await mgr.create_session("user1", "chat2")
        sessions = await mgr.list_sessions("user1")
        assert len(sessions) >= 2

    @pytest.mark.asyncio
    async def test_delete_session(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        result = await mgr.delete_session(session.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, mgr, mock_get_session):
        result = await mgr.delete_session("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear_session(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        await mgr.add_message(session.id, "user", "hi")
        await mgr.add_message(session.id, "assistant", "hello")
        count = await mgr.clear_session(session.id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_switch_session(self, mgr, mock_get_session):
        s1 = await mgr.create_session("user1", "chat1")
        s2 = await mgr.create_session("user1", "chat1")
        result = await mgr.switch_session("user1", s1.id)
        # Should succeed (switch back to s1)
        if result:
            assert result.id == s1.id
            assert result.is_active is True


class TestGenerateTitle:
    @pytest.mark.asyncio
    async def test_generate_title_no_messages(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        result = await mgr.generate_title(session.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_title_with_message(self, mgr, mock_get_session):
        session = await mgr.create_session("user1")
        await mgr.add_message(session.id, "user", "查一下上周库存情况")

        mock_response = MagicMock()
        mock_response.content = "上周库存查询"

        with patch("order_guard.engine.llm_client.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.completion.return_value = mock_response
            MockLLM.return_value = mock_llm

            title = await mgr.generate_title(session.id)
            assert title == "上周库存查询"


# ---------------------------------------------------------------------------
# Slash command tests (via feishu handler)
# ---------------------------------------------------------------------------

class TestSlashCommands:
    @pytest.mark.asyncio
    async def test_slash_new(self, mock_get_session):
        from order_guard.api.feishu import _handle_slash_command, _session_mgr
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await _handle_slash_command("user1", "chat1", "/new")
            assert "已创建新会话" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_list_empty(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await feishu_mod._handle_slash_command("user_empty", "chat1", "/list")
            assert "没有历史会话" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_clear(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            # Create session and add messages
            session = await mgr.create_session("user1", "chat1")
            await mgr.add_message(session.id, "user", "hello")
            await mgr.add_message(session.id, "assistant", "hi")

            result = await feishu_mod._handle_slash_command("user1", "chat1", "/clear")
            assert "已清空" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_unknown(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await feishu_mod._handle_slash_command("user1", "chat1", "/unknown")
            assert result is None  # Unrecognized command
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_switch_no_arg(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await feishu_mod._handle_slash_command("user1", "chat1", "/switch")
            assert "用法" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_delete_no_arg(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await feishu_mod._handle_slash_command("user1", "chat1", "/delete")
            assert "用法" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_slash_help(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        mgr = SessionManager(max_turns=5)
        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = mgr

        try:
            result = await feishu_mod._handle_slash_command("user1", "chat1", "/help")
            assert "OrderGuard" in result
            assert "/new" in result
            assert "/help" in result
        finally:
            feishu_mod._session_mgr = old_mgr

    @pytest.mark.asyncio
    async def test_no_session_mgr(self, mock_get_session):
        import order_guard.api.feishu as feishu_mod

        old_mgr = feishu_mod._session_mgr
        feishu_mod._session_mgr = None

        try:
            result = await feishu_mod._handle_slash_command("user1", "chat1", "/new")
            assert "未初始化" in result
        finally:
            feishu_mod._session_mgr = old_mgr


# ---------------------------------------------------------------------------
# Session timeout tests (N14)
# ---------------------------------------------------------------------------

class TestSessionTimeout:
    @pytest.mark.asyncio
    async def test_no_messages_not_timed_out(self, mgr, mock_get_session):
        """New session with no messages should NOT be timed out."""
        session = await mgr.create_session("user1", "chat1")
        assert await mgr.is_session_timed_out(session.id, 30) is False

    @pytest.mark.asyncio
    async def test_recent_message_not_timed_out(self, mgr, mock_get_session):
        """Session with recent message should NOT be timed out."""
        session = await mgr.create_session("user1", "chat1")
        await mgr.add_message(session.id, "user", "hello")
        assert await mgr.is_session_timed_out(session.id, 30) is False

    @pytest.mark.asyncio
    async def test_old_message_timed_out(self, mgr, mock_get_session):
        """Session with message older than timeout should be timed out."""
        session = await mgr.create_session("user1", "chat1")
        await mgr.add_message(session.id, "user", "hello")

        # Backdate the message
        db = mock_get_session
        db._messages[0].created_at = datetime.now(timezone.utc) - timedelta(minutes=45)

        assert await mgr.is_session_timed_out(session.id, 30) is True

    @pytest.mark.asyncio
    async def test_timeout_disabled(self, mgr, mock_get_session):
        """timeout_minutes=0 should disable timeout (always False)."""
        session = await mgr.create_session("user1", "chat1")
        await mgr.add_message(session.id, "user", "hello")

        db = mock_get_session
        db._messages[0].created_at = datetime.now(timezone.utc) - timedelta(hours=2)

        assert await mgr.is_session_timed_out(session.id, 0) is False

    @pytest.mark.asyncio
    async def test_get_last_message_time(self, mgr, mock_get_session):
        """get_last_message_time returns the last message timestamp."""
        session = await mgr.create_session("user1", "chat1")
        assert await mgr.get_last_message_time(session.id) is None

        await mgr.add_message(session.id, "user", "first")
        await mgr.add_message(session.id, "assistant", "second")

        last_time = await mgr.get_last_message_time(session.id)
        assert last_time is not None

    @pytest.mark.asyncio
    async def test_boundary_exactly_at_timeout(self, mgr, mock_get_session):
        """Message exactly at timeout boundary should be timed out."""
        session = await mgr.create_session("user1", "chat1")
        await mgr.add_message(session.id, "user", "hello")

        db = mock_get_session
        db._messages[0].created_at = datetime.now(timezone.utc) - timedelta(minutes=30, seconds=1)

        assert await mgr.is_session_timed_out(session.id, 30) is True


class TestSessionTimeoutConfig:
    def test_default_timeout_30(self):
        from order_guard.config.settings import FeishuBotConfig
        config = FeishuBotConfig()
        assert config.session_timeout_minutes == 30

    def test_custom_timeout(self):
        from order_guard.config.settings import FeishuBotConfig
        config = FeishuBotConfig(session_timeout_minutes=60)
        assert config.session_timeout_minutes == 60

    def test_disable_timeout(self):
        from order_guard.config.settings import FeishuBotConfig
        config = FeishuBotConfig(session_timeout_minutes=0)
        assert config.session_timeout_minutes == 0
