# T21: Schema 防幻觉

## Context
AI Agent 查询数据库时最常见的问题是"字段幻觉"——编造不存在的表名或字段名导致 SQL 报错。DBHub 的 search_objects 工具提供了 schema 探索能力，但需要在 OrderGuard 侧做增强：将 schema 信息主动注入 Agent context，并在查询前校验。

## Scope

### In Scope
- Agent 启动时自动加载目标数据库的 schema 信息
- Schema 信息注入 Agent system prompt（表名、字段名、类型、注释、外键）
- 查询前字段校验（可选：用 SQLGlot 提取表/字段引用与真实 schema 比对）
- 敏感表/字段黑名单过滤（不暴露给 AI）
- 索引信息注入（帮助 AI 写高效查询）
- 样例数据展示（帮助 AI 理解数据格式）

### Not In Scope
- 热冷数据管理（T22）
- 查询审计（T23）

## Design

### Schema 注入流程
```
Agent 启动
  ↓
1. 调用 search_objects → 拿到所有表
  ↓
2. 过滤黑名单表/字段
  ↓
3. 对每张表获取：字段名、类型、注释、外键、索引
  ↓
4. 构建 schema context 文本
  ↓
5. 注入 Agent system prompt
  ↓
Agent 开始工作（已知所有可用表和字段）
```

### Schema Context 格式
```
## 可用数据库: warehouse

### 表: products
| 字段 | 类型 | 说明 |
|------|------|------|
| sku | VARCHAR(50) | 商品编码（主键） |
| name | VARCHAR(200) | 商品名称 |
| category | VARCHAR(100) | 商品分类 |
| unit_cost | DECIMAL(10,2) | 成本价 |
| unit_price | DECIMAL(10,2) | 售价 |

索引: PRIMARY(sku), idx_category(category)
样例数据 (3行):
  sku=SKU-001, name=无线蓝牙耳机, category=电子产品, unit_cost=45.00, unit_price=129.00
  ...

### 表: orders
| 字段 | 类型 | 说明 |
|------|------|------|
| order_id | VARCHAR(50) | 订单号 |
| sku | VARCHAR(50) | 商品编码（外键 → products.sku） |
| status | VARCHAR(20) | 状态: pending|shipped|delivered|returned|cancelled |
| order_date | DATE | 下单日期 |

索引: PRIMARY(id), idx_sku(sku), idx_order_date(order_date)
```

### 敏感信息过滤配置
```yaml
mcp_servers:
  - name: "production-erp"
    type: "dbhub"
    schema_filter:
      blocked_tables: ["users", "admin_logs", "credentials"]
      blocked_columns: ["password", "id_card", "bank_account", "phone"]
      # 被过滤的表/字段不会出现在 schema context 中，AI 不知道它们存在
```

### 查询前字段校验（可选增强）
```python
import sqlglot

def validate_query(sql: str, schema: SchemaInfo) -> ValidationResult:
    """校验 SQL 引用的表/字段是否真实存在"""
    try:
        parsed = sqlglot.parse_one(sql)
    except sqlglot.errors.ParseError as e:
        return ValidationResult(valid=False, error=f"SQL 语法错误: {e}")

    # 提取引用的表
    for table in parsed.find_all(sqlglot.exp.Table):
        if table.name not in schema.tables:
            return ValidationResult(
                valid=False,
                error=f"表 '{table.name}' 不存在。可用表: {schema.table_names}"
            )

    # 提取引用的字段
    for column in parsed.find_all(sqlglot.exp.Column):
        if column.table and column.name not in schema.get_columns(column.table):
            return ValidationResult(
                valid=False,
                error=f"字段 '{column.table}.{column.name}' 不存在"
            )

    return ValidationResult(valid=True)
```

### Key Decisions
- Schema 信息在 Agent 启动时一次性加载，而非每次查询前重新获取
- 黑名单过滤在 OrderGuard 侧（不依赖 DBHub），从 schema context 中移除后 AI 无法感知
- 索引信息注入帮助 AI 写出高效 WHERE 条件
- 样例数据用 `LIMIT 3` 获取，帮助 AI 理解数据格式和值域
- SQLGlot 校验为可选增强（增加延迟但减少无效查询）
- 添加 sqlglot 为新依赖

## Dependencies
- T20（DBHub 集成）— search_objects 工具
- T17（AI Agent）— Agent system prompt 扩展
- sqlglot（新增依赖，SQL 解析和校验）

## File Changes
- `src/order_guard/mcp/schema.py` — Schema 加载、过滤、格式化
- `src/order_guard/mcp/validator.py` — SQL 校验（SQLGlot）
- `src/order_guard/mcp/models.py` — SchemaFilterConfig
- `src/order_guard/engine/agent.py` — Agent 启动时注入 schema context
- `src/order_guard/config/settings.py` — schema_filter 配置
- `config.example.yaml` — 黑名单配置示例
- `pyproject.toml` — 添加 sqlglot 依赖
- `tests/test_schema.py` — 单元测试

## Tasks
- [ ] T21.1: 实现 SchemaLoader — 通过 MCP 工具加载 schema 信息
- [ ] T21.2: 实现敏感表/字段黑名单过滤
- [ ] T21.3: 实现 schema context 文本格式化（含索引、外键、样例数据）
- [ ] T21.4: Agent 启动时自动注入 schema context 到 system prompt
- [ ] T21.5: 实现 SQL 查询前字段校验（SQLGlot）
- [ ] T21.6: 更新 config.example.yaml 配置示例
- [ ] T21.7: 编写单元测试
