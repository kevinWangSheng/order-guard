# N13: 飞书入口重构

## Context
feishu.py 当前 ~964 行，包含意图分类、5 个独立 handler、规则确认流程等逻辑。v4 改版去掉意图分类后，feishu.py 应大幅简化：只负责飞书事件处理 + pending 确认 + 调用统一 Agent。

## Scope

### In Scope
- 删除意图分类（INTENT_CLASSIFY_PROMPT + _classify_intent）
- 删除独立 handler（_handle_create_rule / _handle_manage_rule / _handle_update / _handle_general_chat）
- 简化主流程：消息 → 检查 pending → 调 Agent → 回复
- pending_action 确认流程适配新结构（从 Agent 返回的 pending_action）
- 删除 rule_agent.py（逻辑已迁移到工具）

### Not In Scope
- 飞书 SDK 相关代码不动（事件接收、消息发送、reaction）
- 权限检查逻辑不动
- slash command 处理不动（/new 保留）

## Design

### 重构后的主流程

```python
async def _handle_user_query_impl(event, user_id, chat_id, message_text):
    session = session_manager.get_or_create_active(user_id, chat_id)

    # 1. 检查 pending_action
    pending = session_manager.get_pending_action(session.id)
    if pending and not is_pending_expired(pending):
        return await _handle_pending_confirmation(event, session, message_text, pending)

    # 2. 直接调 Agent（不再分类意图）
    business_context = build_context_injection()  # N10
    system_prompt = build_system_prompt(business_context)
    context_messages = session_manager.get_context(session.id)

    agent = Agent(
        llm_client=llm_client,
        tools=ALL_TOOLS,
        tool_executors=ALL_EXECUTORS,
        config=AgentConfig(write_confirmation=True)
    )

    result = await agent.run(
        user_message=message_text,
        system_prompt=system_prompt,
        context_messages=context_messages
    )

    # 3. 如果有 pending_action，存到 session
    if result.pending_action:
        session_manager.set_pending_action(session.id, result.pending_action)

    # 4. 保存对话历史 + 回复
    session_manager.add_message(session.id, "user", message_text)
    session_manager.add_message(session.id, "assistant", result.response)

    await _reply_text(event, result.response)
```

### Pending 确认流程

```python
async def _handle_pending_confirmation(event, session, message_text, pending):
    if is_confirmation(message_text):
        # 执行被拦截的写操作
        tool_name = pending["tool_name"]
        args = pending["args"]
        result = await ALL_EXECUTORS[tool_name](**args)

        # 清除 pending
        session_manager.clear_pending_action(session.id)

        # 格式化结果回复
        if "data" in result:
            reply = f"✅ 操作完成\n{_format_result(tool_name, result['data'])}"
        else:
            reply = f"❌ 操作失败：{result['error']}"

        session_manager.add_message(session.id, "assistant", reply)
        await _reply_text(event, reply)

    elif is_cancellation(message_text):
        session_manager.clear_pending_action(session.id)
        await _reply_text(event, "已取消操作。")

    else:
        # 用户既没确认也没取消，可能在追问
        # 清除 pending，正常走 Agent 流程
        session_manager.clear_pending_action(session.id)
        # 回到正常流程...
```

### 删除的代码
- `INTENT_CLASSIFY_PROMPT`（L44-73）
- `DATA_QUERY_PROMPT`（L75-95）
- `GENERAL_CHAT_PROMPT`（L97-109）
- `_classify_intent()`（L626-656）
- 意图路由 switch（L499-522）
- `_handle_create_rule()`
- `_handle_manage_rule()`
- `_handle_update()`
- `_handle_general_chat()`
- `src/order_guard/engine/rule_agent.py`（整个文件）

### 保留的代码
- 飞书事件接收（`feishu_event_handler`、webhook 处理）
- 消息发送（`_reply_text`、reaction 操作）
- 权限检查（`get_allowed_servers`）
- slash command 处理（`/new`、`/help`）
- `is_confirmation()` / `is_cancellation()` / `is_pending_expired()`

### Key Decisions
- pending 确认中，用户发了不相关的消息 → 清除 pending，走正常 Agent 流程（不死等确认）
- feishu.py 不再包含任何业务逻辑，只做：事件处理 + 会话管理 + 调 Agent + 回复
- rule_agent.py 整个删除，N4 的逻辑已被 N9（工具）+ N12（Agent）替代

## Dependencies
- N12（统一 Agent 改造）— Agent 必须已支持 12 工具 + 写拦截
- N9/N10/N11 — 工具函数已就绪

## File Changes
- `src/order_guard/api/feishu.py` — 大幅删减 + 重构主流程
- `src/order_guard/engine/rule_agent.py` — 删除
- `tests/test_feishu.py` — 更新测试

## Tasks
- [ ] N13.1: 删除意图分类相关代码（prompt + 函数 + 路由）
- [ ] N13.2: 删除独立 handler 函数
- [ ] N13.3: 删除 rule_agent.py
- [ ] N13.4: 实现新主流程（消息 → 检查 pending → 调 Agent → 回复）
- [ ] N13.5: 适配 pending_action 确认流程（从 AgentResult 获取）
- [ ] N13.6: 更新测试
- [ ] N13.7: 端到端验证（飞书对话全流程）
