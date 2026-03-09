"""Feishu Bot event callback route."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi import APIRouter, Request
from loguru import logger

from order_guard.api.conversation import ConversationManager
from order_guard.api.permissions import get_allowed_servers, route_to_server

router = APIRouter()

# In-memory conversation manager (initialized in setup_feishu_bot)
_conversation_mgr: ConversationManager | None = None
_processed_event_ids: set[str] = set()  # Dedup repeated events
_MAX_DEDUP_SIZE = 1000


CHAT_SYSTEM_PROMPT = """你是 OrderGuard 数据助手。用户通过飞书群向你提问，你可以使用工具查询数据库来回答。

工作流程：
1. 理解用户的问题
2. 使用工具查询相关数据
3. 用简洁的中文回答用户

注意事项：
- 先了解数据库结构，不要猜测表名
- SQL 查询只用 SELECT
- 回答要简洁明了，用数字说话
- 如果数据量大，先用 COUNT 评估再查
- 直接回答问题，不需要输出 JSON 格式

最终回复要求：
- 用自然语言回答用户问题
- 如果发现异常数据，明确指出
- 给出具体数字和建议"""


def setup_feishu_bot(app_state: Any, config: Any) -> None:
    """Initialize feishu bot components."""
    global _conversation_mgr
    _conversation_mgr = ConversationManager(
        max_turns=config.max_turns,
        context_ttl_minutes=config.context_ttl_minutes,
    )


@router.post("/api/feishu/event")
async def feishu_event_handler(request: Request):
    """Handle Feishu event callbacks."""
    body = await request.json()

    # URL verification (first-time setup)
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    # Schema v2 event
    header = body.get("header", {})
    event_id = header.get("event_id", "")
    event_type = header.get("event_type", "")

    # Dedup: feishu may retry the same event
    if event_id:
        if event_id in _processed_event_ids:
            return {"code": 0, "msg": "duplicate event"}
        _processed_event_ids.add(event_id)
        if len(_processed_event_ids) > _MAX_DEDUP_SIZE:
            _processed_event_ids.clear()

    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        message = event.get("message", {})

        if message.get("message_type") == "text":
            sender = event.get("sender", {})
            user_id = sender.get("sender_id", {}).get("user_id", "")
            chat_id = message.get("chat_id", "")

            # Parse text content
            try:
                content = json.loads(message.get("content", "{}"))
                text = content.get("text", "").strip()
            except (json.JSONDecodeError, TypeError):
                text = ""

            # Remove @mentions
            mentions = message.get("mentions", [])
            for m in mentions:
                key = m.get("key", "")
                if key:
                    text = text.replace(key, "").strip()

            if text and chat_id:
                # Process async — return 200 immediately
                asyncio.create_task(
                    _handle_user_query(request.app, user_id, text, chat_id)
                )

    return {"code": 0}


async def _handle_user_query(app: Any, user_id: str, text: str, chat_id: str) -> None:
    """Process user query: permission check → Agent → reply."""
    try:
        from order_guard.config import get_settings

        settings = get_settings()
        bot_config = settings.feishu_bot

        # 1. Permission check
        allowed_servers = get_allowed_servers(user_id, bot_config)
        if not allowed_servers:
            await _reply_text(bot_config, chat_id, "抱歉，你没有权限查询数据。请联系管理员。")
            return

        # 2. Route to MCP server
        server_name = route_to_server(text, allowed_servers)
        if not server_name:
            await _reply_text(bot_config, chat_id, "未找到可用的数据源。")
            return

        # 3. Get MCP connection
        mcp_manager = app.state.mcp_manager
        try:
            mcp_conn = mcp_manager.get_connection(server_name)
            if not mcp_conn.is_connected():
                await mcp_conn.connect()
        except Exception as e:
            await _reply_text(bot_config, chat_id, f"数据源连接失败: {e}")
            return

        # 4. Get conversation context
        context_messages = []
        if _conversation_mgr:
            context_messages = _conversation_mgr.get_context(chat_id, user_id)

        # 5. Run Agent
        from order_guard.engine.agent import Agent, AgentConfig
        from order_guard.engine.llm_client import LLMClient

        agent = Agent(
            llm_client=LLMClient(),
            mcp_connection=mcp_conn,
            config=AgentConfig(inject_schema=True, validate_sql=True),
        )

        # Build prompt with context
        if context_messages:
            context_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
                for m in context_messages[-6:]  # Last 3 turns
            )
            full_prompt = f"对话历史:\n{context_text}\n\n用户新问题: {text}"
        else:
            full_prompt = text

        result = await agent.run(
            rule_prompt=full_prompt,
            system_prompt=CHAT_SYSTEM_PROMPT,
        )

        # 6. Reply
        reply_text = result.summary or "分析完成，未发现异常。"
        if result.alerts:
            # Build alert summary
            parts = [reply_text, ""]
            for a in result.alerts:
                emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(a.severity, "")
                parts.append(f"{emoji} {a.title}: {a.reason}")
                if a.suggestion:
                    parts.append(f"  建议: {a.suggestion}")
            reply_text = "\n".join(parts)

        await _reply_text(bot_config, chat_id, reply_text)

        # 7. Save conversation context
        if _conversation_mgr:
            _conversation_mgr.add_turn(chat_id, user_id, text, reply_text)

    except Exception as e:
        logger.error("Failed to handle user query: {}", e)
        try:
            from order_guard.config import get_settings
            bot_config = get_settings().feishu_bot
            await _reply_text(bot_config, chat_id, f"处理出错: {str(e)[:100]}")
        except Exception:
            pass


async def _reply_text(bot_config: Any, chat_id: str, text: str) -> None:
    """Send a text reply to a Feishu chat via API."""
    app_id = bot_config.app_id
    app_secret = bot_config.app_secret.get_secret_value() if hasattr(bot_config.app_secret, 'get_secret_value') else str(bot_config.app_secret)

    if not app_id or not app_secret:
        logger.warning("Feishu bot not configured (missing app_id/app_secret)")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get tenant access token
            token_resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_data = token_resp.json()
            token = token_data.get("tenant_access_token", "")

            if not token:
                logger.error("Failed to get Feishu tenant token: {}", token_data)
                return

            # Send message
            await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
    except Exception as e:
        logger.error("Failed to send Feishu reply: {}", e)
