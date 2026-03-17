# N1: 统一数据访问层 — 验收标准

## 验收步骤

### 1. 固定工具集
- [ ] Agent 只看到 3 个数据查询工具：list_datasources / get_schema / query
- [ ] 工具数量不随 mcp_servers 配置中的数据源数量变化
- [ ] 每个工具的参数定义清晰（JSON Schema）

### 2. list_datasources
```
Agent 调用 list_datasources()
```
- [ ] 返回所有已配置且已连接的数据源列表
- [ ] 每个数据源包含 id、name、type、description
- [ ] 未连接的数据源不出现在列表中

### 3. get_schema
```
Agent 调用 get_schema(datasource_id="erp-mysql")
Agent 调用 get_schema(datasource_id="erp-mysql", table_name="orders")
```
- [ ] 不传 table_name: 返回该数据源所有表的列表
- [ ] 传 table_name: 返回字段详情（名称、类型、注释）
- [ ] 无效的 datasource_id 返回清晰的错误信息
- [ ] 无效的 table_name 返回清晰的错误信息

### 4. query
```
Agent 调用 query(datasource_id="erp-mysql", sql="SELECT * FROM orders LIMIT 10")
```
- [ ] SQL 正确执行并返回结果
- [ ] readonly 保护仍然生效（DBHub 安全特性）
- [ ] max_rows 限制仍然生效
- [ ] query_timeout 超时保护仍然生效
- [ ] SQL 校验（SQLGlot）仍然生效
- [ ] 查询审计（QueryLog）仍然记录

### 5. 多数据源路由
- [ ] 配置 2+ 个数据源（如 MySQL + SQLite）
- [ ] Agent 能正确路由到不同数据源
- [ ] 对不同数据源的操作互不干扰

### 6. 现有功能回归
```bash
# 定时规则执行
uv run order-guard run --rule rule-mcp-inventory --dry-run

# 飞书 Bot 对话（如已配置）
# 发送 "@机器人 查一下库存情况"
```
- [ ] 所有现有规则正常执行
- [ ] 飞书 Bot 对话正常工作
- [ ] CLI run 命令正常工作
- [ ] 告警推送格式不变

### 7. 单元测试
```bash
uv run pytest tests/test_data_access.py -v
```
- [ ] 测试通过
- [ ] 覆盖：工具定义、路由逻辑、SQLAdapter、MCPAdapter、错误处理

### 8. 全量测试回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
