# N12: 统一 Agent 改造 — 验收标准

## 验收步骤

### 1. 工具注册
- [ ] Agent 初始化时注册 12 个工具
- [ ] 每个工具的 Tool Schema 正确传递给 LLM
- [ ] LLM 可以调用任意工具并收到结果

### 2. 写操作拦截 — create_rule
```
LLM 调用 create_rule(name="测试规则", ...)
```
- [ ] 工具不实际执行
- [ ] Agent 返回 pending_action（包含 tool_name, args, preview, expires_at）
- [ ] LLM 收到 pending_confirmation 状态 + 变更预览
- [ ] LLM 生成确认消息（包含变更描述）

### 3. 写操作拦截 — update_rule
```
LLM 调用 update_rule(rule_id=1, changes={"schedule": "0 */2 * * *"})
```
- [ ] 拦截 + preview 显示具体变更字段
- [ ] pending_action 包含完整参数

### 4. 写操作拦截 — delete_rule / delete_context
- [ ] delete_rule 被拦截
- [ ] delete_context 被拦截
- [ ] preview 内容准确

### 5. 非写操作不拦截
- [ ] list_rules 直接执行
- [ ] query 直接执行
- [ ] list_context 直接执行
- [ ] list_alerts 直接执行

### 6. AgentResult 结构
- [ ] 正常对话：response 有值，pending_action 为 None
- [ ] 写操作拦截：response 有确认消息，pending_action 有值
- [ ] tool_calls_log 记录本次所有工具调用

### 7. 统一 System Prompt
- [ ] 包含身份定义
- [ ] 包含业务知识（动态注入）
- [ ] 包含工具调用策略
- [ ] 包含对话策略
- [ ] 包含确认策略

### 8. data_tools 迁移
- [ ] list_datasources / get_schema / query 在 tools/data_tools.py 中可用
- [ ] 返回格式统一为 {data, hint} / {error, hint}
- [ ] SQL 只读校验保持不变

### 9. 单元测试
```bash
uv run pytest tests/test_agent.py -v
```
- [ ] 测试通过（含写拦截测试）

### 10. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
