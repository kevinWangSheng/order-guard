# N5: 会话管理 — 验收标准

## 验收步骤

### 1. DB 模型
```bash
uv run alembic upgrade head
```
- [ ] sessions 表创建成功
- [ ] session_messages 表创建成功
- [ ] 外键关系正确

### 2. 会话 CRUD
- [ ] create_session 创建新会话，之前的 active 会话自动切为非 active
- [ ] get_active_session 返回当前激活会话，没有则自动创建
- [ ] list_sessions 返回用户历史会话列表，按更新时间倒序
- [ ] switch_session 正确切换 active 状态
- [ ] delete_session 删除会话及其所有消息

### 3. 飞书 Bot 命令
```
/new    → 创建新会话，回复确认
/list   → 展示会话列表（标题 + 时间 + 消息数）
/switch {id} → 切换会话，回复确认
/delete {id} → 删除会话，回复确认
/clear  → 清空当前会话消息
```
- [ ] 所有命令正常响应
- [ ] 无效的 session_id 返回友好提示
- [ ] 命令不区分大小写

### 4. 对话上下文
- [ ] 新会话开始时 context 为空
- [ ] 多轮对话后 context 正确保持
- [ ] 超过 max_turns 后旧消息不发给 LLM
- [ ] 旧消息仍保存在 DB 中
- [ ] 切换会话后 context 切换到对应会话

### 5. 会话标题
- [ ] 首轮对话后自动生成标题（5-10 个字）
- [ ] 标题生成不阻塞回复

### 6. 持久化
- [ ] 重启应用后会话和消息仍然存在
- [ ] 重启后 active session 正确恢复

### 7. CLI 命令
```bash
uv run order-guard sessions list
uv run order-guard sessions delete {session_id}
```
- [ ] 命令正常执行

### 8. 单元测试
```bash
uv run pytest tests/test_session_manager.py -v
```
- [ ] 测试通过

### 9. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
