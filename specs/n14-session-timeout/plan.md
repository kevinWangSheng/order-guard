# N14: 会话超时归档

## Context
当前会话需要用户手动 `/new` 创建新会话。运营人员不太会用 slash command，导致所有对话堆在一个会话里，上下文越来越长。

本任务加入 30 分钟超时自动归档：用户超过 30 分钟没发消息，下次发消息时自动创建新会话。

## Scope

### In Scope
- 30 分钟不活跃自动新建会话
- 超时时间可配置（config.yaml）
- /new 保留（手动重置）

### Not In Scope
- 话题自动检测
- 对话记忆检索（v5）
- 会话总结归档（v5）

## Design

### 实现方式

在消息处理入口，检查上一条消息的时间：

```python
async def _handle_user_query_impl(event, user_id, chat_id, message_text):
    session = session_manager.get_or_create_active(user_id, chat_id)

    # 检查是否超时
    if session_manager.is_session_timed_out(session.id):
        # 自动创建新会话
        session = session_manager.create_session(user_id, chat_id)

    # 后续流程...
```

### SessionManager 扩展

```python
class SessionManager:
    def is_session_timed_out(self, session_id: int) -> bool:
        """检查会话是否超过 30 分钟无活动"""
        last_message = self.get_last_message(session_id)
        if not last_message:
            return False
        timeout = settings.session_timeout_minutes  # 默认 30
        return datetime.utcnow() - last_message.created_at > timedelta(minutes=timeout)
```

### 配置

```yaml
# config.yaml
session_timeout_minutes: 30  # 会话超时时间（分钟），0 表示不超时
```

### Key Decisions
- 超时检查在消息处理入口做，不需要后台定时器
- 超时后静默创建新会话，不需要通知用户
- /new 保留，超时机制是补充而非替代
- 旧会话不删除，只是不再 active

## Dependencies
- N5（会话管理）— 复用 SessionManager
- N13（飞书重构）— 在重构后的主流程中加入超时检查

## File Changes
- `src/order_guard/api/session.py` — 新增 is_session_timed_out + get_last_message
- `src/order_guard/config/settings.py` — 新增 session_timeout_minutes
- `src/order_guard/api/feishu.py` — 主流程加超时检查
- `config.example.yaml` — 新增配置项
- `tests/test_session.py` — 单元测试

## Tasks
- [ ] N14.1: Settings 新增 session_timeout_minutes 配置
- [ ] N14.2: SessionManager 新增 is_session_timed_out / get_last_message
- [ ] N14.3: feishu.py 主流程加入超时检查
- [ ] N14.4: config.example.yaml 添加示例
- [ ] N14.5: 编写单元测试
