# T22: 热冷数据 + 查询优化 — 验收标准

## 验收步骤

### 1. 时间窗口
- [ ] 规则 YAML 中可配置 data_window（如 "7d"、"30d"、"90d"）
- [ ] Agent prompt 中自动包含时间约束提示
- [ ] Agent 生成的 SQL 包含时间范围 WHERE 条件

### 2. 大表查询策略
- [ ] Agent 对大表先 COUNT 评估数据量
- [ ] 数据量大时使用 LIMIT 采样 + GROUP BY 聚合
- [ ] 不出现无 WHERE 无 LIMIT 的全表扫描

### 3. 冷数据表
- [ ] 配置的 cold_tables 在 schema context 中标记为归档
- [ ] Agent 默认不查冷数据表
- [ ] 明确需要历史数据时 Agent 仍可访问冷数据表

### 4. 查询缓存（可选）
- [ ] 相同 SQL 在 TTL 内返回缓存结果
- [ ] TTL 过期后重新查询
- [ ] 缓存可通过配置关闭

### 5. 端到端验证
```bash
uv run order-guard run --rule rule-inventory-check --dry-run
```
- [ ] 查询日志显示时间约束生效
- [ ] 无超时或大结果集问题

### 6. 单元测试
```bash
uv run pytest tests/test_hot_cold.py -v
```
- [ ] 测试通过
