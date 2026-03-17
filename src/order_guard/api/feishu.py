"""Feishu Bot — unified Agent mode (v4+).

Simplified flow: message → check pending → call Agent → reply.
No intent classification — Agent decides which tools to use.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Request
from loguru import logger

from order_guard.api.file_handler import (
    FileContext, download_feishu_file, parse_file, build_file_context_prompt,
)
from order_guard.api.permissions import get_allowed_servers
from order_guard.api.session import SessionManager

router = APIRouter()

# Session manager (initialized in setup_feishu_bot)
_session_mgr: SessionManager | None = None
_processed_event_ids: set[str] = set()  # Dedup repeated events
_MAX_DEDUP_SIZE = 1000

# Reference to app state (set during setup, used by WS handler)
_app_state: Any = None

# Processing indicator emoji
PROCESSING_EMOJI = "OnIt"  # 🫡 "收到" reaction

def setup_feishu_bot(app_state: Any, config: Any) -> None:
    """Initialize feishu bot components."""
    global _session_mgr, _app_state
    _app_state = app_state
    _session_mgr = SessionManager(max_turns=config.max_turns)


# ---------------------------------------------------------------------------
# Feishu API helpers — token, reaction, reply
# ---------------------------------------------------------------------------

async def _get_tenant_token(app_id: str, app_secret: str, client: httpx.AsyncClient) -> str:
    """Get Feishu tenant access token."""
    resp = await client.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    data = resp.json()
    token = data.get("tenant_access_token", "")
    if not token:
        logger.error("Failed to get Feishu tenant token: {}", data)
    return token


async def _add_reaction(token: str, message_id: str, emoji_type: str, client: httpx.AsyncClient) -> str | None:
    """Add a reaction emoji to a message. Returns reaction_id for later removal."""
    resp = await client.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
        headers={"Authorization": f"Bearer {token}"},
        json={"reaction_type": {"emoji_type": emoji_type}},
    )
    data = resp.json()
    if data.get("code") == 0:
        reaction_id = data.get("data", {}).get("reaction_id", "")
        logger.info("Reaction added: {} on message {}", emoji_type, message_id)
        return reaction_id
    else:
        logger.warning("Failed to add reaction: {}", data)
        return None


async def _remove_reaction(token: str, message_id: str, reaction_id: str, client: httpx.AsyncClient) -> None:
    """Remove a reaction from a message."""
    resp = await client.delete(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    if data.get("code") == 0:
        logger.info("Reaction removed from message {}", message_id)
    else:
        logger.warning("Failed to remove reaction: {}", data)


async def _reply_text(bot_config: Any, chat_id: str, text: str, message_id: str = "") -> None:
    """Send a text reply to a Feishu chat via API."""
    app_id = bot_config.app_id
    app_secret = bot_config.app_secret.get_secret_value() if hasattr(bot_config.app_secret, 'get_secret_value') else str(bot_config.app_secret)

    if not app_id or not app_secret:
        logger.warning("Feishu bot not configured (missing app_id/app_secret)")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _get_tenant_token(app_id, app_secret, client)
            if not token:
                return

            body = {
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            }

            if message_id:
                logger.info("Sending Feishu reply to message {}: {}", message_id, text[:80])
                resp = await client.post(
                    f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
            else:
                logger.info("Sending Feishu message to chat {}: {}", chat_id, text[:80])
                body["receive_id"] = chat_id
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )

            resp_data = resp.json()
            if resp_data.get("code") != 0:
                logger.error("Feishu send message failed: {}", resp_data)
            else:
                logger.info("Feishu reply sent successfully")
    except Exception as e:
        logger.error("Failed to send Feishu reply: {}", e)


# ---------------------------------------------------------------------------
# WebSocket Long Connection Mode (lark-oapi SDK)
# ---------------------------------------------------------------------------

def start_feishu_ws(app_state: Any, config: Any) -> None:
    """Start Feishu WebSocket long connection in a background thread."""
    app_id = config.app_id
    app_secret = config.app_secret.get_secret_value() if hasattr(config.app_secret, 'get_secret_value') else str(config.app_secret)

    if not app_id or not app_secret:
        logger.warning("Feishu bot not configured (missing app_id/app_secret)")
        return

    try:
        main_loop = asyncio.get_running_loop()
    except RuntimeError:
        main_loop = asyncio.get_event_loop()

    def _run_ws():
        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        except ImportError:
            logger.error("lark-oapi not installed. Run: uv add lark-oapi")
            return

        import lark_oapi.ws.client as _ws_client_mod
        _ws_client_mod.loop = ws_loop

        def handle_message(data: P2ImMessageReceiveV1) -> None:
            try:
                event = data.event
                message = event.message
                sender = event.sender

                if message.message_type != "text":
                    return

                sid = sender.sender_id
                user_id = sid.user_id or sid.open_id or sid.union_id or ""
                chat_id = message.chat_id or ""
                message_id = message.message_id or ""

                try:
                    content = json.loads(message.content or "{}")
                    text = content.get("text", "").strip()
                except (json.JSONDecodeError, TypeError):
                    text = ""

                if message.mentions:
                    for m in message.mentions:
                        if m.key:
                            text = text.replace(m.key, "").strip()

                if text and chat_id:
                    logger.info("Feishu WS message from {}: {}", user_id, text[:50])
                    future = asyncio.run_coroutine_threadsafe(
                        _handle_user_query_ws(user_id, text, chat_id, message_id),
                        main_loop,
                    )
                    def _on_done(f):
                        exc = f.exception()
                        if exc:
                            logger.error("Feishu query handler error: {}", exc)
                    future.add_done_callback(_on_done)
            except Exception as e:
                logger.error("Error handling WS message: {}", e)

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(handle_message) \
            .build()

        cli = lark.ws.Client(
            app_id=app_id,
            app_secret=app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
            auto_reconnect=True,
        )

        logger.info("Feishu WebSocket long connection starting...")
        try:
            cli.start()
        except Exception as e:
            logger.error("Feishu WebSocket connection failed: {}", e)

    thread = threading.Thread(target=_run_ws, daemon=True, name="feishu-ws")
    thread.start()
    logger.info("Feishu WebSocket thread started")


async def _handle_user_query_ws(user_id: str, text: str, chat_id: str, message_id: str = "") -> None:
    """Process user query from WebSocket mode."""
    await _handle_user_query_impl(_app_state, user_id, text, chat_id, message_id)


# ---------------------------------------------------------------------------
# HTTP Callback Mode
# ---------------------------------------------------------------------------

@router.post("/api/feishu/event")
async def feishu_event_handler(request: Request):
    """Handle Feishu event callbacks (HTTP mode)."""
    body = await request.json()

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    header = body.get("header", {})
    event_id = header.get("event_id", "")
    event_type = header.get("event_type", "")

    if event_id:
        if event_id in _processed_event_ids:
            return {"code": 0, "msg": "duplicate event"}
        _processed_event_ids.add(event_id)
        if len(_processed_event_ids) > _MAX_DEDUP_SIZE:
            _processed_event_ids.clear()

    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        message = event.get("message", {})
        msg_type = message.get("message_type", "")

        if msg_type in ("text", "file"):
            sender = event.get("sender", {})
            user_id = sender.get("sender_id", {}).get("user_id", "")
            chat_id = message.get("chat_id", "")
            message_id = message.get("message_id", "")

            text = ""
            try:
                content = json.loads(message.get("content", "{}"))
                text = content.get("text", "").strip()
            except (json.JSONDecodeError, TypeError):
                pass

            mentions = message.get("mentions", [])
            for m in mentions:
                key = m.get("key", "")
                if key:
                    text = text.replace(key, "").strip()

            file_context = None
            if msg_type == "file":
                try:
                    content_data = json.loads(message.get("content", "{}"))
                    file_key = content_data.get("file_key", "")
                    file_name = content_data.get("file_name", "")
                    if file_key and file_name:
                        file_context = await _process_file_message(
                            request.app.state, message_id, file_key, file_name,
                        )
                        if not text:
                            text = f"请分析这个文件: {file_name}"
                except Exception as e:
                    logger.warning("Failed to process file: {}", e)

            if text and chat_id:
                asyncio.create_task(
                    _handle_user_query_impl(
                        request.app.state, user_id, text, chat_id,
                        message_id, file_context=file_context,
                    )
                )

    return {"code": 0}


# ---------------------------------------------------------------------------
# Shared query handler — unified Agent flow
# ---------------------------------------------------------------------------

async def _handle_user_query(app: Any, user_id: str, text: str, chat_id: str) -> None:
    """Legacy wrapper — delegates to impl."""
    await _handle_user_query_impl(app.state if hasattr(app, 'state') else app, user_id, text, chat_id)


async def _handle_user_query_impl(
    app_state: Any, user_id: str, text: str, chat_id: str,
    message_id: str = "", file_context: FileContext | None = None,
) -> None:
    """Process user query: reaction → check pending → Agent → reply → remove reaction."""
    logger.info("Processing query: user={}, chat={}, text={!r}", user_id, chat_id, text[:50])

    from order_guard.config import get_settings
    settings = get_settings()
    bot_config = settings.feishu_bot
    app_id = bot_config.app_id
    app_secret = bot_config.app_secret.get_secret_value() if hasattr(bot_config.app_secret, 'get_secret_value') else str(bot_config.app_secret)

    # 0. Normalize slash commands
    normalized_text = text.replace("／", "/")
    if not normalized_text.startswith("/"):
        bare = normalized_text.split()[0].lower() if normalized_text.strip() else ""
        if bare in ("new", "list", "switch", "delete", "clear"):
            normalized_text = "/" + normalized_text.strip()

    # 0.1. Check for slash commands
    if normalized_text.startswith("/"):
        # /init and /init-rules fall through to Agent with special prompt
        bare_cmd = normalized_text.strip().split(maxsplit=1)[0].lower()
        if bare_cmd in ("/init", "/init-rules"):
            from order_guard.engine.prompts import INIT_RULES_PROMPT
            text = INIT_RULES_PROMPT
        else:
            reply = await _handle_slash_command(user_id, chat_id, normalized_text)
            if reply:
                await _reply_text(bot_config, chat_id, reply, message_id)
                return

    # 0.5. Add processing reaction
    reaction_id = None
    if message_id and app_id and app_secret:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token = await _get_tenant_token(app_id, app_secret, client)
                if token:
                    reaction_id = await _add_reaction(token, message_id, PROCESSING_EMOJI, client)
        except Exception as e:
            logger.warning("Failed to add processing reaction: {}", e)

    try:
        # 1. Get or create session
        session = None
        context_messages: list[dict] = []
        if _session_mgr:
            session = await _session_mgr.get_or_create_active(user_id, chat_id)

            # 1b. Check session timeout — auto-archive if idle too long
            timeout_minutes = bot_config.session_timeout_minutes if bot_config else 30
            if timeout_minutes > 0 and await _session_mgr.is_session_timed_out(
                session.id, timeout_minutes
            ):
                logger.info("Session {} timed out, creating new session", session.id[:8])
                session = await _session_mgr.create_session(user_id, chat_id)

            context_messages = await _session_mgr.get_context(session.id)

        # 2. Build augmented text with file context
        augmented_text = text
        if file_context:
            augmented_text = build_file_context_prompt(file_context, text)
            logger.info("File context injected: {} ({} rows)", file_context.file_name, file_context.row_count)

        # 3. Call unified Agent (no intent classification)
        reply, _ = await _run_unified_agent(app_state, bot_config, user_id, augmented_text, context_messages)

        await _reply_text(bot_config, chat_id, reply, message_id)

        # 4. Save to session
        if _session_mgr and session:
            await _session_mgr.add_message(session.id, "user", text)
            await _session_mgr.add_message(session.id, "assistant", reply)
            msg_count = await _session_mgr.get_message_count(session.id)
            if msg_count <= 2 and session.title == "新会话":
                asyncio.create_task(_session_mgr.generate_title(session.id))

    except Exception as e:
        logger.error("Failed to handle user query: {}", e)
        try:
            await _reply_text(bot_config, chat_id, f"处理出错: {str(e)[:100]}", message_id)
        except Exception:
            pass
    finally:
        if reaction_id and message_id:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    token = await _get_tenant_token(app_id, app_secret, client)
                    if token:
                        await _remove_reaction(token, message_id, reaction_id, client)
            except Exception as e:
                logger.warning("Failed to remove processing reaction: {}", e)


# ---------------------------------------------------------------------------
# Unified Agent call
# ---------------------------------------------------------------------------

async def _run_unified_agent(
    app_state: Any,
    bot_config: Any,
    user_id: str,
    text: str,
    context_messages: list[dict],
) -> tuple[str, None]:
    """Call unified Agent with all tools. Returns (reply, None)."""
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient
    from order_guard.engine.prompts import build_unified_prompt
    from order_guard.tools import (
        rule_tools, context_tools, alert_tools, data_tools,
        health_tools, report_tools, usage_tools,
    )

    dal = getattr(app_state, "data_access_layer", None)
    scheduler = getattr(app_state, "scheduler", None)
    mcp_manager = getattr(app_state, "mcp_manager", None)

    # Configure tool dependencies
    rule_tools.configure(
        scheduler=scheduler,
        data_access_layer=dal,
        mcp_manager=mcp_manager,
    )
    data_tools.configure(data_access_layer=dal)
    health_tools.configure(mcp_manager=mcp_manager)
    report_tools.configure(
        scheduler=scheduler,
        data_access_layer=dal,
        mcp_manager=mcp_manager,
    )

    # Collect all tools
    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )

    all_executors = {}
    all_executors.update(data_tools.TOOL_EXECUTORS)
    all_executors.update(rule_tools.TOOL_EXECUTORS)
    all_executors.update(context_tools.TOOL_EXECUTORS)
    all_executors.update(alert_tools.TOOL_EXECUTORS)
    all_executors.update(health_tools.TOOL_EXECUTORS)
    all_executors.update(report_tools.TOOL_EXECUTORS)
    all_executors.update(usage_tools.TOOL_EXECUTORS)

    # Build business context
    biz_context = ""
    try:
        from order_guard.tools.context_tools import build_context_injection
        biz_context = await build_context_injection()
    except Exception as e:
        logger.debug("Failed to build context injection: {}", e)

    system_prompt = build_unified_prompt(biz_context)

    agent = Agent(
        llm_client=LLMClient(),
        data_access_layer=dal,
        config=AgentConfig(
            inject_business_context=False,  # Already in system prompt
        ),
        tools=all_tools,
        tool_executors=all_executors,
    )

    result = await agent.run_unified(
        user_message=text,
        system_prompt=system_prompt,
        context_messages=context_messages,
        trigger_type="chat",
        user_id=user_id,
        session_id=chat_id,
    )

    return (
        result.response or "抱歉，我没有理解你的意思。",
        None,  # No pending actions — tools execute directly
    )


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------

async def _handle_slash_command(user_id: str, chat_id: str, text: str) -> str | None:
    """Handle /commands. Returns reply text, or None if not a recognized command."""
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not _session_mgr:
        return "会话管理未初始化。"

    if cmd == "/new":
        session = await _session_mgr.create_session(user_id, chat_id)
        return f"已创建新会话 (ID: {session.id[:8]})"

    elif cmd == "/list":
        sessions = await _session_mgr.list_sessions(user_id)
        if not sessions:
            return "没有历史会话。"
        lines = ["会话列表:"]
        for s in sessions:
            active = " ← 当前" if s.is_active else ""
            msg_count = await _session_mgr.get_message_count(s.id)
            lines.append(f"  {s.id[:8]} | {s.title} | {msg_count}条消息 | {s.updated_at.strftime('%m-%d %H:%M')}{active}")
        return "\n".join(lines)

    elif cmd == "/switch":
        if not arg:
            return "用法: /switch <会话ID前8位>"
        sessions = await _session_mgr.list_sessions(user_id, limit=100)
        target = None
        for s in sessions:
            if s.id.startswith(arg):
                target = s
                break
        if not target:
            return f"未找到会话: {arg}"
        result = await _session_mgr.switch_session(user_id, target.id)
        if result:
            return f"已切换到会话: {result.title} ({result.id[:8]})"
        return "切换失败。"

    elif cmd == "/delete":
        if not arg:
            return "用法: /delete <会话ID前8位>"
        sessions = await _session_mgr.list_sessions(user_id, limit=100)
        target = None
        for s in sessions:
            if s.id.startswith(arg):
                target = s
                break
        if not target:
            return f"未找到会话: {arg}"
        await _session_mgr.delete_session(target.id)
        return f"已删除会话: {target.title} ({target.id[:8]})"

    elif cmd == "/clear":
        session = await _session_mgr.get_active_session(user_id, chat_id)
        if not session:
            return "没有活跃会话。"
        count = await _session_mgr.clear_session(session.id)
        return f"已清空当前会话 ({count} 条消息)"

    elif cmd in ("/init", "/init-rules"):
        # Trigger init-rules via unified Agent — not a quick reply, needs async Agent call
        return None  # Fall through to normal Agent flow with INIT_RULES_PROMPT

    elif cmd == "/help":
        return (
            "OrderGuard 数据助手\n\n"
            "直接发消息即可查询数据、创建规则、管理业务知识。\n\n"
            "可用命令:\n"
            "  /init — 扫描数据源，自动推荐监控规则\n"
            "  /new — 创建新会话\n"
            "  /list — 查看历史会话\n"
            "  /switch <ID> — 切换会话\n"
            "  /delete <ID> — 删除会话\n"
            "  /clear — 清空当前会话\n"
            "  /help — 显示此帮助"
        )

    return None


# ---------------------------------------------------------------------------
# File message processing
# ---------------------------------------------------------------------------

async def _process_file_message(
    app_state: Any, message_id: str, file_key: str, file_name: str,
) -> FileContext | None:
    """Download and parse a file attachment from Feishu."""
    from order_guard.config import get_settings
    settings = get_settings()
    bot_config = settings.feishu_bot

    app_id = bot_config.app_id
    app_secret = bot_config.app_secret.get_secret_value() if hasattr(bot_config.app_secret, 'get_secret_value') else str(bot_config.app_secret)

    try:
        content = await download_feishu_file(message_id, file_key, app_id, app_secret)
        file_ctx = parse_file(content, file_name)
        logger.info("File parsed: {} ({} rows, {} columns)", file_name, file_ctx.row_count, len(file_ctx.columns))
        return file_ctx
    except ValueError as e:
        logger.warning("File processing error: {}", e)
        raise
    except Exception as e:
        logger.error("Failed to process file: {}", e)
        raise ValueError(f"文件处理失败: {str(e)[:100]}")
