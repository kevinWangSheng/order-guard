# T24: 飞书 Bot 对话

## Context
目前 OrderGuard 只有"自动巡检"模式（定时 → 分析 → 推送）。架构设计中的"模式二：对话查询"还未实现。本任务接入飞书 Bot，让员工在群里 @机器人 提问，AI Agent 自动查数据并回复。

## Scope

### In Scope
- 飞书开放平台应用创建指南
- 飞书 Event 回调接入（接收 @机器人 消息）
- 消息 → Agent 路由（根据问题选择 MCP Server）
- Agent 查数据 → 分析 → 飞书卡片回复
- 多轮对话（上下文保持）
- 基础权限控制（谁能查什么数据源）

### Not In Scope
- 飞书审批流（不做）
- 飞书文档/表格操作（不做）
- 企业微信 Bot（暂无 Webhook）

## Design

### 飞书 Bot 接入流程
```
1. 用户在飞书开放平台创建应用 → 获取 App ID + App Secret
2. 配置事件订阅 → 指向 OrderGuard 的回调 URL
3. OrderGuard 启动 FastAPI 路由接收消息
4. 收到 @消息 → 启动 Agent → 查数据 → 回复
```

### FastAPI 路由
```python
@router.post("/api/feishu/event")
async def feishu_event_handler(request: Request):
    body = await request.json()

    # 飞书 URL 验证（首次配置）
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}

    # 消息事件
    event = body.get("event", {})
    if event.get("message", {}).get("message_type") == "text":
        user_id = event["sender"]["sender_id"]["user_id"]
        text = extract_text(event["message"]["content"])
        chat_id = event["message"]["chat_id"]

        # 异步处理，先返回 200
        asyncio.create_task(handle_user_query(user_id, text, chat_id))

    return {"code": 0}
```

### 消息处理流程
```python
async def handle_user_query(user_id: str, text: str, chat_id: str):
    # 1. 权限检查
    allowed_servers = get_user_permissions(user_id)
    if not allowed_servers:
        await reply_text(chat_id, "你没有权限查询数据，请联系管理员。")
        return

    # 2. Agent 路由 — 根据问题选择 MCP Server
    #    简单方案：用户指定数据源，如"@bot 查一下仓库的库存"
    #    高级方案：AI 根据问题自动选择数据源
    mcp_server = route_to_server(text, allowed_servers)

    # 3. 获取对话上下文（多轮对话）
    context = get_conversation_context(chat_id, user_id)

    # 4. 启动 Agent
    agent = Agent(llm_client, mcp_manager.get_connection(mcp_server))
    result = await agent.run(
        rule_prompt=text,
        system_prompt=CHAT_SYSTEM_PROMPT,
        history=context,
    )

    # 5. 回复飞书卡片
    card = build_chat_reply_card(result)
    await reply_card(chat_id, card)

    # 6. 保存对话上下文
    save_conversation_context(chat_id, user_id, text, result)
```

### 对话上下文管理
```python
class ConversationManager:
    """管理多轮对话上下文"""

    def __init__(self, max_turns: int = 10, ttl_minutes: int = 30):
        self._max_turns = max_turns     # 最多保留 10 轮
        self._ttl = ttl_minutes         # 30 分钟过期

    def get_context(self, chat_id: str, user_id: str) -> list[dict]:
        """获取对话历史（用于传给 Agent）"""

    def add_turn(self, chat_id: str, user_id: str, question: str, answer: str):
        """添加一轮对话"""

    def clear(self, chat_id: str, user_id: str):
        """清除对话上下文"""
```

### 权限控制配置
```yaml
feishu_bot:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
  verification_token: "${FEISHU_VERIFICATION_TOKEN}"

  # 权限配置：谁能查什么数据源
  permissions:
    - user_ids: ["*"]                    # 所有人
      mcp_servers: ["test-warehouse"]    # 只能查测试库

    - user_ids: ["u_admin1", "u_admin2"] # 管理员
      mcp_servers: ["production-erp"]    # 可以查生产库
```

### 飞书应用创建步骤（文档内容）
```
1. 访问 open.feishu.cn → 开发者后台
2. 创建企业自建应用
3. 添加机器人能力
4. 配置事件订阅：
   - 请求地址: https://your-domain/api/feishu/event
   - 订阅事件: im.message.receive_v1（接收消息）
5. 配置权限：
   - im:message（获取消息内容）
   - im:message:send_as_bot（以机器人身份发消息）
6. 发布应用
7. 将 App ID / App Secret / Verification Token 填入 .env
```

### Key Decisions
- 消息处理异步化（先返回 200，后台处理），避免飞书超时
- 对话上下文保存在内存中（可选持久化到 DB），30 分钟 TTL
- 权限控制基于飞书 user_id，配置在 YAML 中
- 回复使用飞书消息卡片（复用 T12 的卡片构建能力）
- Agent 路由简单方案先做（用户指定数据源），后续可增强为 AI 自动路由

## Dependencies
- T16（MCP Client）— MCPManager
- T17（AI Agent）— Agent
- T12（飞书消息卡片）— 卡片构建
- T20（DBHub）— 数据库连接
- 飞书开放平台应用（用户自行创建，免费）

## File Changes
- `src/order_guard/api/feishu.py` — 飞书 Event 回调路由
- `src/order_guard/api/chat.py` — 对话处理逻辑
- `src/order_guard/api/conversation.py` — 对话上下文管理
- `src/order_guard/api/permissions.py` — 权限控制
- `src/order_guard/config/settings.py` — FeishuBotConfig
- `src/order_guard/main.py` — 注册飞书路由
- `config.example.yaml` — 飞书 Bot 配置示例
- `.env.example` — 飞书应用凭证
- `docs/feishu-bot-setup.md` — 飞书 Bot 创建指南
- `tests/test_feishu_bot.py` — 单元测试

## Tasks
- [ ] T24.1: 飞书 Bot 配置模型 + Settings 集成
- [ ] T24.2: 飞书 Event 回调路由（URL 验证 + 消息接收）
- [ ] T24.3: 消息处理流程（权限检查 → Agent 路由 → 执行 → 回复）
- [ ] T24.4: 对话上下文管理（多轮对话）
- [ ] T24.5: 权限控制（user_id → 可查数据源映射）
- [ ] T24.6: 飞书卡片格式回复（复用 T12）
- [ ] T24.7: 编写飞书 Bot 创建指南文档
- [ ] T24.8: 更新 config.example.yaml 和 .env.example
- [ ] T24.9: 编写单元测试
