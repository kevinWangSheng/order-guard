"""L2 — Session management integration tests.

Tests the SessionManager lifecycle with real DB operations:
create/get/switch/delete sessions, message context, pending actions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from order_guard.api.session import SessionManager
from order_guard.models import Session, SessionMessage
from order_guard.storage.database import get_session
from tests.integration.conftest import seed_sessions

pytestmark = pytest.mark.asyncio


class TestSessionLifecycle:
    """Test session CRUD operations against real DB."""

    async def test_create_session(self):
        sm = SessionManager()
        session = await sm.create_session("user-1", "chat-1")
        assert session.user_id == "user-1"
        assert session.is_active is True

    async def test_get_or_create_active(self):
        sm = SessionManager()

        # First call creates
        s1 = await sm.get_or_create_active("user-2")
        assert s1.is_active is True

        # Second call returns same
        s2 = await sm.get_or_create_active("user-2")
        assert s2.id == s1.id

    async def test_create_new_deactivates_old(self):
        sm = SessionManager()

        s1 = await sm.create_session("user-3")
        s2 = await sm.create_session("user-3")

        # s1 should be deactivated
        async with get_session() as db:
            from sqlalchemy import select
            result = await db.execute(select(Session).where(Session.id == s1.id))
            old = result.scalars().first()
            assert old.is_active is False

        assert s2.is_active is True

    async def test_add_message_and_get_context(self):
        sm = SessionManager()
        session = await sm.create_session("user-4")

        await sm.add_message(session.id, "user", "你好")
        await sm.add_message(session.id, "assistant", "你好！有什么可以帮助您？")
        await sm.add_message(session.id, "user", "查看库存")

        context = await sm.get_context(session.id)
        assert len(context) == 3
        assert context[0]["role"] == "user"
        assert context[0]["content"] == "你好"

    async def test_max_turns_truncation(self):
        """get_context should respect max_turns."""
        sm = SessionManager(max_turns=2)
        session = await sm.create_session("user-5")

        # Add 6 messages (3 turns)
        for i in range(3):
            await sm.add_message(session.id, "user", f"问题{i}")
            await sm.add_message(session.id, "assistant", f"回答{i}")

        context = await sm.get_context(session.id)
        # max_turns=2 → 4 messages
        assert len(context) == 4

    async def test_session_timeout_detection(self):
        """is_session_timed_out should detect stale sessions."""
        sm = SessionManager()
        session = await sm.create_session("user-6")

        # No messages yet → not timed out
        timed_out = await sm.is_session_timed_out(session.id, timeout_minutes=30)
        assert timed_out is False

        # Add a message
        await sm.add_message(session.id, "user", "test")

        # Just added → not timed out
        timed_out = await sm.is_session_timed_out(session.id, timeout_minutes=30)
        assert timed_out is False

        # Timeout disabled → never times out
        timed_out = await sm.is_session_timed_out(session.id, timeout_minutes=0)
        assert timed_out is False

    async def test_pending_action_lifecycle(self):
        """Test set/get/clear pending actions."""
        sm = SessionManager()
        session = await sm.create_session("user-7")

        # No pending initially
        pending = await sm.get_pending_actions(session.id)
        assert pending is None

        # Set pending
        action = {"tool_name": "create_rule", "args": {"name": "test"}, "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        await sm.set_pending_action(session.id, action)

        # Get pending
        pending = await sm.get_pending_actions(session.id)
        assert pending is not None
        assert len(pending) == 1
        assert pending[0]["tool_name"] == "create_rule"

        # Clear pending
        await sm.set_pending_action(session.id, None)
        pending = await sm.get_pending_actions(session.id)
        assert pending is None

    async def test_delete_session(self):
        sm = SessionManager()
        session = await sm.create_session("user-8")
        await sm.add_message(session.id, "user", "test")

        deleted = await sm.delete_session(session.id)
        assert deleted is True

        # Session should be gone
        active = await sm.get_active_session("user-8")
        assert active is None
