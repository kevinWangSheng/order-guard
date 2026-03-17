# N13: 飞书入口重构 — 验收标准

## 验收步骤

### 1. 代码清理
- [ ] INTENT_CLASSIFY_PROMPT 已删除
- [ ] DATA_QUERY_PROMPT / GENERAL_CHAT_PROMPT 已删除
- [ ] _classify_intent() 已删除
- [ ] 意图路由 switch 已删除
- [ ] _handle_create_rule / _handle_manage_rule / _handle_update / _handle_general_chat 已删除
- [ ] rule_agent.py 已删除
- [ ] 无死代码残留

### 2. 数据查询（原 QUERY 路径）
```
用户："查一下昨天的退货数据"
```
- [ ] Agent 自行调 list_datasources → get_schema → query
- [ ] 返回数据分析结果

### 3. 规则创建（原 CREATE_RULE 路径）
```
用户："帮我监控库存低于10的产品，每天9点检查"
```
- [ ] Agent 调 list_datasources → get_schema → create_rule
- [ ] create_rule 被拦截，返回变更预览
- [ ] 用户收到确认消息
- [ ] 用户回复"确认" → 规则创建成功 + 调度注册
- [ ] 用户回复"取消" → 操作取消

### 4. 规则管理（原 MANAGE_RULE 路径）
```
用户："看看现在有什么规则"
```
- [ ] Agent 调 list_rules → 返回规则列表

```
用户："删掉退货率那个规则"
```
- [ ] Agent 调 list_rules → delete_rule
- [ ] delete_rule 被拦截 → 确认后删除

### 5. 业务知识（原 UPDATE 路径）
```
用户："记住，我们3月在做满减促销"
```
- [ ] Agent 调 add_context → 被拦截 → 确认后保存

### 6. 普通对话（原 CHAT 路径）
```
用户："你好"
```
- [ ] Agent 不调任何工具，直接回复

### 7. Pending 确认
- [ ] 确认后执行成功 → 回复结果
- [ ] 确认后执行失败 → 回复错误
- [ ] 取消 → 回复已取消
- [ ] 发不相关消息 → 清除 pending，正常对话
- [ ] 5 分钟过期 → 自动清除

### 8. 保留功能不受影响
- [ ] /new 正常创建新会话
- [ ] /help 正常返回帮助
- [ ] 权限检查正常工作
- [ ] 消息 reaction 正常

### 9. 单元测试
```bash
uv run pytest tests/test_feishu.py -v
```
- [ ] 测试通过

### 10. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
