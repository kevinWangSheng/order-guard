# P1: 告警闭环 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] Alert 表新增 resolution 字段（nullable，可选值 handled/ignored/false_positive）
- [ ] Alert 表新增 resolved_by 字段（string）
- [ ] Alert 表新增 resolved_at 字段（datetime，nullable）
- [ ] Alert 表新增 note 字段（string）
- [ ] Alembic upgrade/downgrade 执行无报错

### 2. handle_alert — 单条处理
- [ ] 传 alert_id + resolution="handled" → 告警 resolution 更新
- [ ] resolved_at 自动设置为当前时间
- [ ] note 正确保存
- [ ] alert_id 不存在 → error + hint
- [ ] resolution 不在枚举中 → error + 可选值

### 3. handle_alert — 批量处理
- [ ] 传 rule_id → 批量处理该规则所有未处理告警
- [ ] 传 rule_id + time_range → 限定时间范围内的告警
- [ ] 返回受影响条数
- [ ] 已处理的告警不被重复处理
- [ ] alert_id 和 rule_id 都不传 → error + hint

### 4. handle_alert — 重新标记
- [ ] 已标记为 handled 的告警可以改为 false_positive
- [ ] resolved_at 更新为新的时间

### 5. get_alert_stats — 基本统计
- [ ] 返回 total（总告警数）
- [ ] 返回 by_severity（按级别分布：critical/warning/info）
- [ ] 返回 by_resolution（按处理状态分布：handled/ignored/false_positive/unresolved）
- [ ] 返回 unresolved_count（未处理数）
- [ ] 返回 resolution_rate（处理率百分比）

### 6. get_alert_stats — 高级统计
- [ ] 返回 avg_resolution_time_hours（平均处理时间）
- [ ] 返回 top_rules（告警数 top 5 规则，含名称和数量）
- [ ] 传 rule_id → 只统计该规则
- [ ] 传 time_range → 限定时间范围

### 7. get_alert_stats — 边界
- [ ] 无告警数据 → data 含全零统计 + hint 说明
- [ ] 无已处理告警 → resolution_rate = 0，avg_resolution_time 为 null

### 8. list_alerts 增强
- [ ] 返回数据增加 resolution 字段
- [ ] 返回数据增加 resolved_at 字段

### 9. Agent 集成
- [ ] handle_alert 和 get_alert_stats 注册到 Agent 工具集
- [ ] Agent 能通过对话正确调用这两个工具

### 10. 返回信封
- [ ] 所有工具成功返回 `{"data": ..., "hint": "..."}`
- [ ] 所有工具失败返回 `{"error": "...", "hint": "..."}`

### 11. 单元测试
```bash
uv run pytest tests/test_alert_tools.py -v
```
- [ ] 测试通过

### 12. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
