# T21: Schema 防幻觉 — 验收标准

## 验收步骤

### 1. Schema 加载
- [ ] Agent 启动时自动加载数据库 schema（表名、字段、类型、注释）
- [ ] 外键关系正确解析
- [ ] 索引信息包含在 schema context 中
- [ ] 样例数据（LIMIT 3）包含在 schema context 中

### 2. 敏感信息过滤
- [ ] blocked_tables 中的表不出现在 schema context
- [ ] blocked_columns 中的字段不出现在 schema context
- [ ] AI 无法查询被过滤的表/字段（因为不知道它们存在）

### 3. Schema 注入效果
```bash
uv run order-guard run --rule rule-mcp-inventory --dry-run
```
- [ ] Agent 日志显示 schema context 已注入
- [ ] Agent 使用真实存在的表名和字段名（不再编造）
- [ ] Agent 查询考虑索引（WHERE 条件使用有索引的字段）

### 4. SQL 校验（可选）
- [ ] 引用不存在的表时返回清晰错误提示
- [ ] 引用不存在的字段时返回清晰错误提示
- [ ] SQL 语法错误时返回错误提示

### 5. 单元测试
```bash
uv run pytest tests/test_schema.py -v
```
- [ ] 测试通过
