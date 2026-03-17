# P4: 数据源健康监控 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] DataSourceHealthLog 表创建成功
- [ ] 字段完整：datasource_id, status, latency_ms, error, tool_count, created_at
- [ ] Alembic upgrade/downgrade 执行无报错

### 2. 配置
- [ ] config.example.yaml 包含 health_check 配置段
- [ ] 默认值合理：interval=5min, timeout=10s, threshold=3, retention=72h
- [ ] enabled=false 时不注册定时 Job

### 3. 健康检查 — 正常数据源
- [ ] SQL 数据源：通过 execute_sql 执行 SELECT 1 成功
- [ ] MCP 数据源：调用 list_tools 成功
- [ ] 记录 status="healthy"，latency_ms > 0
- [ ] tool_count 正确反映可用工具数

### 4. 健康检查 — 异常数据源
- [ ] 连接被拒绝 → status="unhealthy"，error 含错误描述
- [ ] 超时 → status="timeout"，latency_ms >= timeout_seconds * 1000
- [ ] 数据源 ID 不存在 → error + hint

### 5. 定时健康检查 Job
- [ ] FastAPI 启动后 Job 自动注册
- [ ] 按 interval_minutes 定时执行
- [ ] 每次检查所有已连接数据源
- [ ] 检查结果写入 DataSourceHealthLog

### 6. 连续失败告警
- [ ] 连续失败次数 < threshold → 不推送
- [ ] 连续失败次数 >= threshold → 推送告警
- [ ] 告警内容包含：数据源名称、连续失败次数、错误信息、受影响规则列表
- [ ] 持续失败不重复推送（每 threshold 次推送一次，或使用告警静默期）

### 7. 恢复通知
- [ ] 从 unhealthy 恢复到 healthy → 推送恢复通知
- [ ] 通知包含：故障持续时间
- [ ] 一直 healthy 的数据源不推送恢复通知

### 8. check_health 工具 — 单个数据源
- [ ] 传 datasource_id → 返回该数据源状态
- [ ] 返回：status, latency_ms, last_check, uptime_24h
- [ ] uptime_24h 计算正确（healthy 次数 / 总检查次数）

### 9. check_health 工具 — 全部数据源
- [ ] 不传参数 → 检查所有数据源
- [ ] 返回所有数据源的状态列表
- [ ] hint 汇总（如"3 个数据源全部正常"或"1 个数据源异常"）

### 10. 日志清理
- [ ] 超过 retention_hours 的日志自动清理
- [ ] 清理不影响当前统计

### 11. Agent 集成
- [ ] check_health 注册到 Agent 工具集
- [ ] Agent 能通过对话正确调用

### 12. 返回信封
- [ ] 成功返回 `{"data": ..., "hint": "..."}`
- [ ] 失败返回 `{"error": "...", "hint": "..."}`

### 13. 单元测试
```bash
uv run pytest tests/test_health_tools.py -v
```
- [ ] 测试通过

### 14. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
