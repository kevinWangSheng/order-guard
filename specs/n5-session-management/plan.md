# N5: 会话管理

## Context
当前飞书 Bot 的对话管理是内存级别的（ConversationManager），重启后丢失，且只有一个连续的对话流。随着对话增多，context 必然溢出。

本任务实现完整的会话管理：支持创建新会话、切换历史会话、会话持久化到 DB、context 自动截断。

## Scope

### In Scope
- 会话 CRUD：创建、列表、切换、删除
- 会话持久化到 DB（重启不丢失）
- 会话标题自动生成（LLM 根据首轮对话）
- 最近 N 轮 context 截断（旧消息存 DB 但不发给 LLM）
- 飞书 Bot 命令支持：/new /list /switch /delete
- CLI 命令支持：sessions list / sessions delete

### Not In Scope
- 会话摘要压缩（复杂度高，效果不确定）
- RAG 检索历史对话
- 会话分享 / 导出
- 多用户共享会话

## Design

### DB 模型
```python
class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)              # UUID
    user_id: str = Field(index=True)                # 用户标识
    chat_id: str = Field(default="")                # 飞书群/聊天 ID
    title: str = Field(default="新会话")             # 自动生成的标题
    is_active: bool = Field(default=True)           # 当前激活的会话
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class SessionMessage(SQLModel, table=True):
    __tablename__ = "session_messages"

    id: str = Field(primary_key=True)               # UUID
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: str                                        # "user" | "assistant"
    content: str                                     # 消息内容
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### SessionManager
```python
class SessionManager:
    """会话管理器，替代现有的 ConversationManager"""

    async def create_session(self, user_id: str, chat_id: str = "") -> Session:
        """创建新会话，将之前的 active 会话设为非 active"""

    async def get_active_session(self, user_id: str, chat_id: str = "") -> Session | None:
        """获取用户当前激活的会话，没有则自动创建"""

    async def switch_session(self, user_id: str, session_id: str) -> Session:
        """切换到指定会话"""

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[Session]:
        """列出用户的历史会话"""

    async def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有消息"""

    async def add_message(self, session_id: str, role: str, content: str):
        """添加消息到会话"""

    async def get_context(self, session_id: str, max_turns: int = 10) -> list[dict]:
        """获取最近 N 轮对话作为 LLM messages
        返回格式：[{"role": "user", "content": "..."}, ...]
        """

    async def generate_title(self, session_id: str, llm_client: LLMClient):
        """基于首轮对话让 LLM 生成标题（5-10个字）"""
```

### 飞书 Bot 命令
```
/new          → create_session() → "已创建新会话"
/list         → list_sessions() → 展示会话列表（标题 + 时间）
/switch {id}  → switch_session() → "已切换到会话: {title}"
/delete {id}  → delete_session() → "已删除会话: {title}"
/clear        → delete current session messages → "已清空当前会话"
```

命令识别：在飞书 Bot 的消息处理中，先检查是否以 `/` 开头，是则走命令处理，否则走正常对话。

### Context 截断策略
```python
async def get_context(self, session_id: str, max_turns: int = 10):
    # 从 DB 取最近 max_turns 条消息
    messages = await self._get_recent_messages(session_id, limit=max_turns * 2)
    # 转为 LLM messages 格式
    return [{"role": m.role, "content": m.content} for m in messages]
```

- `max_turns` 从 Settings.feishu_bot.max_turns 读取（已有配置，默认 10）
- 旧消息保留在 DB 中，但不发给 LLM
- 用户可以通过 /list 查看完整历史

### 适配飞书 Bot
替换现有的 `ConversationManager` 为 `SessionManager`：

```python
# Before (v3):
context = self._conversation_manager.get_context(chat_id, user_id)

# After (v4):
session = await self._session_manager.get_active_session(user_id, chat_id)
context = await self._session_manager.get_context(session.id)
```

### Key Decisions
- 会话按 user_id 隔离（不同用户各自的会话）
- 每个用户同一时间只有一个 active 会话
- 会话标题在第一轮对话后异步生成（不阻塞回复）
- ConversationManager 废弃，完全被 SessionManager 替代
- CLI sessions 命令主要用于调试和管理

## Dependencies
- T24（飞书 Bot）— 需要适配命令处理
- T03（存储层）— DB 迁移

## File Changes
- `src/order_guard/api/session.py` — SessionManager 核心实现（新文件）
- `src/order_guard/models/tables.py` — Session + SessionMessage 模型
- `src/order_guard/api/feishu.py` — 替换 ConversationManager，增加命令处理
- `src/order_guard/api/conversation.py` — 废弃（或保留为兼容层）
- `src/order_guard/cli.py` — 新增 sessions 命令组
- `src/order_guard/config/settings.py` — 复用 max_turns 配置
- `alembic/versions/` — DB 迁移（sessions + session_messages 表）
- `tests/test_session_manager.py` — 单元测试

## Tasks
- [ ] N5.1: DB 模型（Session + SessionMessage）+ Alembic 迁移
- [ ] N5.2: SessionManager 核心实现（CRUD + get_context + 标题生成）
- [ ] N5.3: 飞书 Bot 适配（替换 ConversationManager + 命令处理）
- [ ] N5.4: CLI sessions 命令（list / delete）
- [ ] N5.5: 废弃 ConversationManager
- [ ] N5.6: 编写单元测试
