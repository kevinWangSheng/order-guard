# P2: LLM 用量追踪 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] LLMUsageLog 表创建成功
- [ ] 字段完整：model, prompt_tokens, completion_tokens, total_tokens, cost_estimate_usd, trigger_type, rule_id, user_id, session_id, duration_ms, tool_calls_count, iterations, created_at
- [ ] Alembic upgrade/downgrade 执行无报错

### 2. 自动记录 — 规则巡检
- [ ] 定时巡检执行后，LLMUsageLog 写入一条记录
- [ ] trigger_type = "rule"
- [ ] rule_id 正确关联
- [ ] token 数量 > 0
- [ ] cost_estimate_usd 正确计算（非零，符合模型价格）

### 3. 自动记录 — 对话查询
- [ ] 飞书 Bot 对话完成后，LLMUsageLog 写入记录
- [ ] trigger_type = "chat"
- [ ] user_id 和 session_id 正确关联

### 4. 自动记录 — 报告生成
- [ ] 报告生成完成后，LLMUsageLog 写入记录
- [ ] trigger_type = "report"

### 5. 成本估算
- [ ] 已知模型（如 gpt-4o）→ 精确匹配价格
- [ ] 前缀匹配（如 gpt-4o-2024-xxx）→ 匹配 gpt-4o 价格
- [ ] 未知模型 → cost_estimate_usd = 0
- [ ] custom_pricing 配置覆盖内置价格

### 6. get_usage_stats — 基本统计
- [ ] 默认返回最近 7 天统计
- [ ] 返回 total_tokens 和 total_cost_usd
- [ ] 返回 request_count（调用次数）

### 7. get_usage_stats — 分组统计
- [ ] group_by="rule" → 按规则分组，含规则名称
- [ ] group_by="trigger_type" → 按触发类型分组（chat/rule/report）
- [ ] group_by="model" → 按模型分组
- [ ] group_by="day" → 按天分组，含日期

### 8. get_usage_stats — 筛选
- [ ] time_range="24h" → 只统计最近 24 小时
- [ ] time_range="30d" → 统计最近 30 天
- [ ] rule_id 传入 → 只统计该规则

### 9. get_usage_stats — 边界
- [ ] 无用量数据 → data 含全零 + hint 说明
- [ ] hint 包含人类可读的摘要（如"最近7天共消耗 X tokens，估算成本 $Y"）

### 10. Agent 集成
- [ ] get_usage_stats 注册到 Agent 工具集
- [ ] Agent 能通过对话正确调用

### 11. 返回信封
- [ ] 成功返回 `{"data": ..., "hint": "..."}`
- [ ] 失败返回 `{"error": "...", "hint": "..."}`

### 12. 单元测试
```bash
uv run pytest tests/test_usage_tools.py -v
```
- [ ] 测试通过

### 13. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
