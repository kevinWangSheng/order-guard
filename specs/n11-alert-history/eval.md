# N11: 告警历史工具 — 验收标准

## 验收步骤

### 1. list_alerts — 基本查询
- [ ] 无参数调用 → 返回最近 20 条告警（时间倒序）
- [ ] 返回字段：id, rule_name, severity, summary, created_at
- [ ] 无告警时 → data 为空列表 + hint 说明

### 2. list_alerts — 筛选
- [ ] 传 rule_id → 只返回该规则的告警
- [ ] 传 time_range="24h" → 只返回最近 24 小时
- [ ] 传 limit=5 → 最多返回 5 条
- [ ] 组合筛选（rule_id + time_range）正常工作

### 3. 错误处理
- [ ] rule_id 不存在 → 返回空列表（不报错，因为规则可能已删除）
- [ ] time_range 不在 enum 中 → error + 可选值

### 4. 返回信封
- [ ] 成功返回 `{"data": [...], "hint": "..."}`
- [ ] hint 包含统计摘要（条数、级别分布）

### 5. 单元测试
```bash
uv run pytest tests/test_alert_tools.py -v
```
- [ ] 测试通过

### 6. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
