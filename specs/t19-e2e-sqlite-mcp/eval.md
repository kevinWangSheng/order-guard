# T19: 端到端验证（SQLite MCP） — 验收标准

## 验收步骤

### 1. 测试数据库
```bash
uv run python scripts/create_test_db.py
```
- [ ] data/test_warehouse.db 创建成功
- [ ] 包含 products, inventory, orders, daily_sales 四张表
- [ ] 包含 5+ 个 SKU 的模拟数据（含正常/缺货/积压/高退货率场景）

### 2. SQLite MCP Server 连接
```bash
# 确认 MCP Server 可启动
npx -y @modelcontextprotocol/server-sqlite data/test_warehouse.db
```
- [ ] MCP Server 启动无报错

### 3. 端到端执行
```bash
uv run order-guard run --rule rule-mcp-inventory --dry-run
```
- [ ] 规则加载成功，识别为 MCP 类型
- [ ] MCP Server 连接成功
- [ ] Agent 自动执行工具调用（可从日志看到 list_tables → describe → read_query 调用链）
- [ ] Agent 输出包含结构化分析结果
- [ ] 识别出缺货风险 SKU（severity: critical）
- [ ] 识别出积压风险 SKU（severity: warning）
- [ ] dry-run 模式不推送告警

### 4. 告警推送（非 dry-run）
```bash
uv run order-guard run --rule rule-mcp-inventory
```
- [ ] 告警推送到配置的 Webhook（飞书）
- [ ] 告警内容包含 SKU、severity、原因、建议

### 5. 日志验证
- [ ] loguru 日志记录完整的 Agent 执行过程
- [ ] 包含每次工具调用的名称和耗时
- [ ] 包含 token 用量统计

### 6. e2e 测试
```bash
uv run pytest tests/e2e/test_mcp_e2e.py -v
```
- [ ] 端到端测试通过
