# P3: 规则效果评估 — 验收标准

## 验收步骤

### 1. list_rules 增强
- [ ] 返回新增字段：trigger_count_7d, false_positive_count_7d, false_positive_rate
- [ ] 返回新增字段：last_triggered_at, run_count_7d, run_success_rate
- [ ] 无告警时统计字段为 0（不是 null）
- [ ] 无 P1 数据（resolution 全为 null）时 false_positive_rate = 0

### 2. list_rules — 智能 hint
- [ ] 存在误报率 > 30% 的规则 → hint 提示调整
- [ ] 存在 7 天未触发的规则 → hint 提示确认
- [ ] 存在执行成功率 < 90% 的规则 → hint 提示检查
- [ ] 所有规则表现正常 → hint 显示整体概况

### 3. get_rule_stats — 基本信息
- [ ] 返回规则基本信息（name, schedule, enabled, created_at, source）
- [ ] rule_id 不存在 → error + hint

### 4. get_rule_stats — 执行统计
- [ ] total_runs：总运行次数
- [ ] success_runs / failed_runs：成功/失败次数
- [ ] success_rate：成功率
- [ ] avg_duration_ms：平均执行耗时

### 5. get_rule_stats — 告警统计
- [ ] total_alerts：总告警数
- [ ] by_severity：按 critical/warning/info 分布
- [ ] by_resolution：按 handled/ignored/false_positive/unresolved 分布
- [ ] false_positive_rate：误报率

### 6. get_rule_stats — 趋势
- [ ] trend：最近 N 天每天的告警数（数组）
- [ ] time_range="7d" → 7 个数据点
- [ ] time_range="30d" → 30 个数据点

### 7. get_rule_stats — Token 用量（可选）
- [ ] 如果 P2 完成：返回 total_tokens, total_cost_usd
- [ ] 如果 P2 未完成：字段为 null，不报错

### 8. 边界情况
- [ ] 新建规则（无任何执行/告警数据）→ 统计全零 + hint 提示"尚未执行"
- [ ] 已删除规则 → error + hint
- [ ] time_range="90d" → 统计最近 90 天

### 9. Agent 集成
- [ ] get_rule_stats 注册到 Agent 工具集
- [ ] Agent 能通过对话正确调用

### 10. 返回信封
- [ ] 成功返回 `{"data": ..., "hint": "..."}`
- [ ] 失败返回 `{"error": "...", "hint": "..."}`

### 11. 单元测试
```bash
uv run pytest tests/test_rule_tools.py -v
```
- [ ] 测试通过

### 12. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
