# T20: DBHub 集成 — 验收标准

## 验收步骤

### 1. DBHub 安装
```bash
npx -y @bytebase/dbhub --version
```
- [ ] DBHub 可通过 npx 正常安装和启动

### 2. 配置解析
- [ ] config.example.yaml 包含 DBHub 配置示例（SQLite + MySQL + PG）
- [ ] DBHubConfig 正确解析 dsn、security 参数
- [ ] 环境变量引用（${DB_PASSWORD}）正确替换

### 3. SQLite 本地验证
```bash
uv run order-guard run --rule rule-mcp-inventory --dry-run
```
- [ ] DBHub 通过 stdio 启动并连接 SQLite 测试数据库
- [ ] Agent 能调用 search_objects 探索 schema
- [ ] Agent 能调用 execute_sql 查询数据
- [ ] readonly 模式下 UPDATE/DELETE 被拒绝
- [ ] 查询超时保护生效
- [ ] max_rows 限制生效

### 4. 安全配置
- [ ] readonly=true 时写操作被 DBHub 拒绝
- [ ] 查询超过 query_timeout 秒时被终止
- [ ] 返回行数超过 max_rows 时被截断

### 5. 多数据库配置
- [ ] 配置中可定义多个 DBHub 实例
- [ ] 不同规则绑定不同数据库实例

### 6. 文档
- [ ] docs/database-setup.md 包含 MySQL / PostgreSQL / SQLite 连接指南

### 7. 单元测试
```bash
uv run pytest tests/test_dbhub.py -v
```
- [ ] 测试通过
