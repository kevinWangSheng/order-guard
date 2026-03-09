"""Tests for T24: Feishu Bot — event callback, conversation, permissions."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from order_guard.api.conversation import ConversationManager
from order_guard.api.permissions import get_allowed_servers, route_to_server
from order_guard.config.settings import FeishuBotConfig, FeishuBotPermission


# ---------------------------------------------------------------------------
# Conversation Manager tests
# ---------------------------------------------------------------------------

class TestConversationManager:
    def test_empty_context(self):
        mgr = ConversationManager()
        ctx = mgr.get_context("chat1", "user1")
        assert ctx == []

    def test_add_and_get_turns(self):
        mgr = ConversationManager()
        mgr.add_turn("chat1", "user1", "查库存", "库存正常")
        mgr.add_turn("chat1", "user1", "哪个缺货？", "SKU-001 缺货")

        ctx = mgr.get_context("chat1", "user1")
        assert len(ctx) == 4  # 2 turns * 2 messages each
        assert ctx[0] == {"role": "user", "content": "查库存"}
        assert ctx[1] == {"role": "assistant", "content": "库存正常"}
        assert ctx[2] == {"role": "user", "content": "哪个缺货？"}
        assert ctx[3] == {"role": "assistant", "content": "SKU-001 缺货"}

    def test_max_turns_trimming(self):
        mgr = ConversationManager(max_turns=2)
        mgr.add_turn("chat1", "user1", "q1", "a1")
        mgr.add_turn("chat1", "user1", "q2", "a2")
        mgr.add_turn("chat1", "user1", "q3", "a3")

        ctx = mgr.get_context("chat1", "user1")
        assert len(ctx) == 4  # Only last 2 turns
        assert ctx[0]["content"] == "q2"

    def test_ttl_expiry(self):
        mgr = ConversationManager(ttl_minutes=0)  # 0 = expire immediately

        # Manually set timestamp in the past
        mgr.add_turn("chat1", "user1", "old", "old")
        mgr._conversations["chat1:user1"][0].timestamp = time.time() - 1

        ctx = mgr.get_context("chat1", "user1")
        assert ctx == []

    def test_separate_users(self):
        mgr = ConversationManager()
        mgr.add_turn("chat1", "user1", "q1", "a1")
        mgr.add_turn("chat1", "user2", "q2", "a2")

        ctx1 = mgr.get_context("chat1", "user1")
        ctx2 = mgr.get_context("chat1", "user2")
        assert len(ctx1) == 2
        assert len(ctx2) == 2
        assert ctx1[0]["content"] == "q1"
        assert ctx2[0]["content"] == "q2"

    def test_clear(self):
        mgr = ConversationManager()
        mgr.add_turn("chat1", "user1", "q1", "a1")
        mgr.clear("chat1", "user1")

        ctx = mgr.get_context("chat1", "user1")
        assert ctx == []

    def test_active_count(self):
        mgr = ConversationManager()
        assert mgr.active_count() == 0

        mgr.add_turn("chat1", "user1", "q1", "a1")
        assert mgr.active_count() == 1

        mgr.add_turn("chat1", "user2", "q2", "a2")
        assert mgr.active_count() == 2


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_wildcard_permission(self):
        config = FeishuBotConfig(permissions=[
            FeishuBotPermission(user_ids=["*"], mcp_servers=["test-db"]),
        ])
        allowed = get_allowed_servers("any_user", config)
        assert allowed == ["test-db"]

    def test_specific_user_permission(self):
        config = FeishuBotConfig(permissions=[
            FeishuBotPermission(user_ids=["admin1"], mcp_servers=["prod-db"]),
        ])
        assert get_allowed_servers("admin1", config) == ["prod-db"]
        assert get_allowed_servers("random_user", config) == []

    def test_multiple_permissions_combined(self):
        config = FeishuBotConfig(permissions=[
            FeishuBotPermission(user_ids=["*"], mcp_servers=["test-db"]),
            FeishuBotPermission(user_ids=["admin1"], mcp_servers=["prod-db"]),
        ])
        allowed = get_allowed_servers("admin1", config)
        assert "test-db" in allowed
        assert "prod-db" in allowed

    def test_no_permissions(self):
        config = FeishuBotConfig(permissions=[])
        assert get_allowed_servers("user1", config) == []

    def test_route_to_server_by_name(self):
        result = route_to_server("查一下 production-erp 的库存", ["test-db", "production-erp"])
        assert result == "production-erp"

    def test_route_to_server_default(self):
        result = route_to_server("查库存", ["test-db", "production-erp"])
        assert result == "test-db"  # Falls back to first

    def test_route_to_server_empty(self):
        result = route_to_server("查库存", [])
        assert result is None


# ---------------------------------------------------------------------------
# Feishu event handler tests
# ---------------------------------------------------------------------------

class TestFeishuEventHandler:
    def _make_app(self):
        """Create a test FastAPI app with feishu router."""
        from fastapi import FastAPI
        from order_guard.api.feishu import router

        app = FastAPI()
        app.include_router(router)

        # Mock app state
        app.state.mcp_manager = MagicMock()

        return app

    def test_url_verification(self):
        app = self._make_app()
        client = TestClient(app)

        resp = client.post("/api/feishu/event", json={
            "type": "url_verification",
            "challenge": "test_challenge_123",
        })
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "test_challenge_123"

    def test_message_event_returns_200(self):
        app = self._make_app()
        client = TestClient(app)

        event_body = {
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "u_001"}},
                "message": {
                    "message_type": "text",
                    "chat_id": "oc_001",
                    "content": json.dumps({"text": "查库存"}),
                    "mentions": [],
                },
            },
        }

        # Should return 200 immediately (async processing)
        with patch("order_guard.api.feishu._handle_user_query", new_callable=AsyncMock):
            resp = client.post("/api/feishu/event", json=event_body)

        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    def test_duplicate_event_dedup(self):
        app = self._make_app()
        client = TestClient(app)

        event_body = {
            "header": {
                "event_id": "evt_dedup_test",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "u_001"}},
                "message": {
                    "message_type": "text",
                    "chat_id": "oc_001",
                    "content": json.dumps({"text": "test"}),
                    "mentions": [],
                },
            },
        }

        with patch("order_guard.api.feishu._handle_user_query", new_callable=AsyncMock):
            resp1 = client.post("/api/feishu/event", json=event_body)
            resp2 = client.post("/api/feishu/event", json=event_body)

        assert resp1.json()["code"] == 0
        assert resp2.json()["msg"] == "duplicate event"

    def test_non_text_message_ignored(self):
        app = self._make_app()
        client = TestClient(app)

        event_body = {
            "header": {
                "event_id": "evt_img",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "u_001"}},
                "message": {
                    "message_type": "image",
                    "chat_id": "oc_001",
                },
            },
        }

        resp = client.post("/api/feishu/event", json=event_body)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------

class TestFeishuBotConfig:
    def test_default_config(self):
        config = FeishuBotConfig()
        assert config.enabled is False
        assert config.max_turns == 10
        assert config.context_ttl_minutes == 30

    def test_custom_config(self):
        config = FeishuBotConfig(
            enabled=True,
            app_id="test_id",
            max_turns=5,
            permissions=[
                FeishuBotPermission(user_ids=["*"], mcp_servers=["db1"]),
            ],
        )
        assert config.enabled is True
        assert config.app_id == "test_id"
        assert len(config.permissions) == 1
