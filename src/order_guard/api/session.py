"""Session manager — persistent multi-turn conversation management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlmodel import col

from order_guard.models import Session, SessionMessage
from order_guard.storage.database import get_session
from order_guard.storage.crud import create


class SessionManager:
    """Manage persistent conversation sessions.

    Replaces the in-memory ConversationManager with DB-backed sessions.
    """

    def __init__(self, max_turns: int = 10):
        self._max_turns = max_turns

    async def get_or_create_active(self, user_id: str, chat_id: str = "") -> Session:
        """Get active session or create a new one."""
        session = await self.get_active_session(user_id, chat_id)
        if session is None:
            session = await self.create_session(user_id, chat_id)
        return session

    async def create_session(self, user_id: str, chat_id: str = "") -> Session:
        """Create a new session. Deactivates previous active session."""
        async with get_session() as db:
            # Deactivate current active session
            stmt = select(Session).where(
                Session.user_id == user_id,
                Session.is_active == True,
            )
            if chat_id:
                stmt = stmt.where(Session.chat_id == chat_id)
            result = await db.execute(stmt)
            for old in result.scalars().all():
                old.is_active = False
                db.add(old)

            # Create new session
            new_session = Session(user_id=user_id, chat_id=chat_id)
            db.add(new_session)
            await db.commit()
            await db.refresh(new_session)
            logger.info("Created session {} for user {}", new_session.id[:8], user_id)
            return new_session

    async def get_active_session(self, user_id: str, chat_id: str = "") -> Session | None:
        """Get the user's currently active session."""
        async with get_session() as db:
            stmt = select(Session).where(
                Session.user_id == user_id,
                Session.is_active == True,
            )
            if chat_id:
                stmt = stmt.where(Session.chat_id == chat_id)
            result = await db.execute(stmt)
            return result.scalars().first()

    async def get_last_message_time(self, session_id: str) -> datetime | None:
        """Get the timestamp of the last message in a session."""
        async with get_session() as db:
            stmt = (
                select(SessionMessage)
                .where(SessionMessage.session_id == session_id)
                .order_by(col(SessionMessage.created_at).desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            msg = result.scalars().first()
            return msg.created_at if msg else None

    async def is_session_timed_out(self, session_id: str, timeout_minutes: int = 30) -> bool:
        """Check if a session has timed out due to inactivity.

        Returns False if timeout_minutes <= 0 (disabled) or no messages yet.
        """
        if timeout_minutes <= 0:
            return False
        last_time = await self.get_last_message_time(session_id)
        if last_time is None:
            return False
        # Ensure timezone-aware comparison
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_time > timedelta(minutes=timeout_minutes)

    async def switch_session(self, user_id: str, session_id: str) -> Session | None:
        """Switch to a specific session."""
        async with get_session() as db:
            # Deactivate all
            stmt = select(Session).where(
                Session.user_id == user_id,
                Session.is_active == True,
            )
            result = await db.execute(stmt)
            for s in result.scalars().all():
                s.is_active = False
                db.add(s)

            # Activate target
            stmt = select(Session).where(Session.id == session_id)
            result = await db.execute(stmt)
            target = result.scalars().first()
            if target and target.user_id == user_id:
                target.is_active = True
                db.add(target)
                await db.commit()
                return target
            await db.commit()
            return None

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[Session]:
        """List user's sessions, most recent first."""
        async with get_session() as db:
            stmt = (
                select(Session)
                .where(Session.user_id == user_id)
                .order_by(col(Session.updated_at).desc())
                .limit(limit)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and its messages."""
        async with get_session() as db:
            # Delete messages first
            stmt = select(SessionMessage).where(SessionMessage.session_id == session_id)
            result = await db.execute(stmt)
            for msg in result.scalars().all():
                await db.delete(msg)

            # Delete session
            stmt = select(Session).where(Session.id == session_id)
            result = await db.execute(stmt)
            session = result.scalars().first()
            if session:
                await db.delete(session)
                await db.commit()
                return True
            await db.commit()
            return False

    async def clear_session(self, session_id: str) -> int:
        """Clear all messages in a session. Returns count deleted."""
        async with get_session() as db:
            stmt = select(SessionMessage).where(SessionMessage.session_id == session_id)
            result = await db.execute(stmt)
            messages = result.scalars().all()
            count = len(messages)
            for msg in messages:
                await db.delete(msg)
            await db.commit()
            return count

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to a session."""
        async with get_session() as db:
            msg = SessionMessage(session_id=session_id, role=role, content=content)
            db.add(msg)

            # Update session's updated_at
            stmt = select(Session).where(Session.id == session_id)
            result = await db.execute(stmt)
            session = result.scalars().first()
            if session:
                session.updated_at = datetime.now(timezone.utc)
                db.add(session)

            await db.commit()

    async def get_context(self, session_id: str, max_turns: int | None = None) -> list[dict[str, str]]:
        """Get recent messages as LLM-compatible messages list.

        Returns at most max_turns * 2 messages (each turn = user + assistant).
        """
        limit = (max_turns or self._max_turns) * 2
        async with get_session() as db:
            stmt = (
                select(SessionMessage)
                .where(SessionMessage.session_id == session_id)
                .order_by(col(SessionMessage.created_at).desc())
                .limit(limit)
            )
            result = await db.execute(stmt)
            messages = list(result.scalars().all())

        # Reverse to chronological order
        messages.reverse()
        return [{"role": m.role, "content": m.content} for m in messages]

    async def get_message_count(self, session_id: str) -> int:
        """Count messages in a session."""
        async with get_session() as db:
            from sqlalchemy import func
            stmt = select(func.count(SessionMessage.id)).where(
                SessionMessage.session_id == session_id
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none() or 0

    async def set_pending_action(self, session_id: str, action: dict | list | None) -> None:
        """Set or clear pending action(s) on a session.

        Accepts a single dict (legacy), a list of dicts (batch), or None to clear.
        Internally stored as a wrapper: {"actions": [...], "expires_at": "..."}.
        """
        from datetime import datetime, timezone
        async with get_session() as db:
            stmt = select(Session).where(Session.id == session_id)
            result = await db.execute(stmt)
            session = result.scalars().first()
            if session:
                if action is None:
                    session.pending_action = None
                    session.pending_expires_at = None
                elif isinstance(action, list):
                    # Batch: list of pending actions
                    expires = None
                    for a in action:
                        if "expires_at" in a:
                            expires = a["expires_at"]
                    session.pending_action = {"actions": action}
                    if expires:
                        try:
                            session.pending_expires_at = datetime.fromisoformat(expires)
                        except (ValueError, TypeError):
                            session.pending_expires_at = None
                    else:
                        session.pending_expires_at = None
                else:
                    # Single dict (legacy compat) — wrap in list
                    session.pending_action = {"actions": [action]}
                    if "expires_at" in action:
                        try:
                            session.pending_expires_at = datetime.fromisoformat(action["expires_at"])
                        except (ValueError, TypeError):
                            session.pending_expires_at = None
                    else:
                        session.pending_expires_at = None
                db.add(session)
                await db.commit()

    async def get_pending_actions(self, session_id: str) -> list[dict] | None:
        """Get pending actions for a session, if not expired.

        Returns a list of pending action dicts, or None if none/expired.
        """
        async with get_session() as db:
            stmt = select(Session).where(Session.id == session_id)
            result = await db.execute(stmt)
            session = result.scalars().first()
            if not session or not session.pending_action:
                return None
            # Check expiry
            if session.pending_expires_at:
                from datetime import datetime, timezone
                if datetime.now(timezone.utc) > session.pending_expires_at.replace(tzinfo=timezone.utc):
                    session.pending_action = None
                    session.pending_expires_at = None
                    db.add(session)
                    await db.commit()
                    return None
            # Unwrap: support both old format (single dict) and new ({"actions": [...]})
            data = session.pending_action
            if isinstance(data, dict) and "actions" in data:
                return data["actions"]
            # Legacy single dict
            return [data]

    async def get_pending_action(self, session_id: str) -> dict | None:
        """Legacy compat: get first pending action."""
        actions = await self.get_pending_actions(session_id)
        return actions[0] if actions else None

    async def generate_title(self, session_id: str) -> str | None:
        """Generate a title for the session based on the first message."""
        async with get_session() as db:
            stmt = (
                select(SessionMessage)
                .where(SessionMessage.session_id == session_id, SessionMessage.role == "user")
                .order_by(SessionMessage.created_at)
                .limit(1)
            )
            result = await db.execute(stmt)
            first_msg = result.scalars().first()

        if not first_msg:
            return None

        try:
            from order_guard.engine.llm_client import LLMClient
            llm = LLMClient()
            response = await llm.completion(
                [
                    {"role": "system", "content": "为以下对话生成一个5-10字的简短标题。只输出标题，不要引号或标点。"},
                    {"role": "user", "content": first_msg.content[:200]},
                ],
                max_tokens=20,
                temperature=0,
            )
            title = response.content.strip()[:30]
            if title:
                async with get_session() as db:
                    stmt = select(Session).where(Session.id == session_id)
                    result = await db.execute(stmt)
                    session = result.scalars().first()
                    if session:
                        session.title = title
                        db.add(session)
                        await db.commit()
                return title
        except Exception as e:
            logger.debug("Failed to generate session title: {}", e)

        return None
