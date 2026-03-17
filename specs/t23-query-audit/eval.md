# T23: 查询审计 — 验收标准

## 验收步骤

### 1. 数据模型
```bash
uv run alembic upgrade head
```
- [ ] query_logs 表创建成功
- [ ] 迁移 upgrade/downgrade 无报错

### 2. 查询记录
```bash
uv run order-guard run --rule rule-mcp-inventory --dry-run
```
- [ ] Agent 执行的每条 SQL 自动记录到 query_logs
- [ ] 记录包含：sql、status、rows_returned、duration_ms、rule_id、mcp_server
- [ ] 成功查询 status="success"
- [ ] 失败查询 status="error" 且有 error 信息

### 3. CLI 查看
```bash
uv run order-guard queries --last 10
```
- [ ] 正确显示最近查询历史
- [ ] --rule 过滤生效
- [ ] --status 过滤生效
- [ ] --stats 显示统计摘要

### 4. 异常查询
- [ ] 超时查询记录 status="timeout"
- [ ] 被拒绝查询（如 UPDATE）记录 status="rejected"

### 5. 单元测试
```bash
uv run pytest tests/test_query_audit.py -v
```
- [ ] 测试通过
