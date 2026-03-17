# T19: 端到端验证（SQLite MCP）

## Context
T16-T18 完成了 MCP Client + Agent + Pipeline 的全部代码。本任务通过 SQLite MCP Server 在本地完整验证全链路：创建测试数据库 → 配置 MCP Server → Agent 自动探索表结构 → 写 SQL 取数 → 分析 → 告警。

## Scope

### In Scope
- 创建测试用 SQLite 数据库（含库存/订单/销售表 + 模拟数据）
- 配置 SQLite MCP Server（`@modelcontextprotocol/server-sqlite`）
- 编写 MCP 类型的测试规则
- 端到端执行验证（CLI `run` 命令）
- 编写 e2e 测试脚本

### Not In Scope
- MySQL / PostgreSQL MCP Server 验证（用户自行配置）
- 其他 SaaS MCP Server 验证

## Design

### 测试数据库设计
```sql
-- data/test_warehouse.db

CREATE TABLE products (
    sku TEXT PRIMARY KEY,
    name TEXT,
    category TEXT,
    unit_cost REAL,
    unit_price REAL
);

CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT,
    warehouse TEXT,
    quantity INTEGER,
    reorder_point INTEGER,
    lead_time_days INTEGER,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    sku TEXT,
    quantity INTEGER,
    status TEXT,          -- shipped / delivered / returned / cancelled
    order_date TEXT,
    delivery_date TEXT
);

CREATE TABLE daily_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT,
    sale_date TEXT,
    quantity_sold INTEGER,
    revenue REAL
);
```

### 测试数据（模拟场景）
| SKU | 场景 | 预期 Agent 判断 |
|-----|------|----------------|
| SKU-001 | 库存充足，正常销售 | info |
| SKU-002 | 库存低于安全线，销量稳定 | critical（缺货风险） |
| SKU-003 | 库存大量积压，销量极低 | warning（积压风险） |
| SKU-004 | 退货率 > 15% | critical（退货异常） |
| SKU-005 | 近期销量骤增，库存即将不足 | warning（需关注） |

### 测试规则配置
```yaml
rules:
  - id: "rule-mcp-inventory"
    name: "仓库库存智能检查"
    connector_type: "mcp"
    mcp_server: "test-warehouse"
    prompt: |
      请检查仓库数据库中的库存状况：
      1. 查看有哪些商品及其库存数量
      2. 结合近期销售数据，计算每个 SKU 的库存可售天数
      3. 与安全库存（reorder_point）对比，判断是否需要补货
      4. 找出积压商品（库存可售天数 > 90 天）
      5. 检查退货率异常的 SKU

      对每个异常 SKU 输出 severity（critical/warning/info）、原因和建议。
    enabled: true

mcp_servers:
  - name: "test-warehouse"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-sqlite", "data/test_warehouse.db"]
    enabled: true
```

### 验证链路
```
1. CLI: uv run order-guard run --rule rule-mcp-inventory --dry-run
2. 系统加载规则 → 发现 connector_type=mcp
3. MCPManager 连接 test-warehouse（SQLite MCP Server）
4. Agent 启动，拿到工具列表（list_tables, describe_table, read_query 等）
5. Agent 循环：
   - list_tables → 发现 products, inventory, orders, daily_sales
   - describe_table(inventory) → 看到字段
   - read_query(SELECT ...) → 取库存数据
   - read_query(SELECT ...) → 取销售数据
   - ... 分析 ...
6. Agent 输出 AnalyzerOutput（包含 SKU-002 critical, SKU-003 warning 等）
7. dry-run 模式：打印结果，不推送
```

## Dependencies
- T14（Pipeline 适配）— 完整 pipeline
- Node.js（npx 运行 SQLite MCP Server）

## File Changes
- `data/test_warehouse.db` — 测试 SQLite 数据库
- `scripts/create_test_db.py` — 创建测试数据的脚本
- `config.yaml`（或测试配置）— MCP Server + 规则配置
- `tests/e2e/test_mcp_e2e.py` — 端到端测试
- `specs/t15-e2e-sqlite-mcp/` — 验证文档

## Tasks
- [ ] T19.1: 编写 create_test_db.py 脚本创建测试数据库 + 插入模拟数据
- [ ] T19.2: 配置 SQLite MCP Server + MCP 测试规则
- [ ] T19.3: CLI 端到端运行 --dry-run 验证全链路
- [ ] T19.4: 验证 Agent 工具调用日志（list_tables → describe → query）
- [ ] T19.5: 验证输出包含预期的异常 SKU 告警
- [ ] T19.6: 编写 e2e 测试脚本
