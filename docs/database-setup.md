# 数据库连接指南 / Database Setup Guide

OrderGuard 通过 [DBHub](https://github.com/bytebase/dbhub)（Bytebase 开源）连接数据库。DBHub 是一个通用的数据库 MCP Server，内置安全特性（readonly、row limit、query timeout），支持 MySQL、PostgreSQL、SQLite、SQL Server、MariaDB。

## 前置条件 / Prerequisites

- Node.js 18+（DBHub 通过 `npx` 自动安装）
- 数据库账号（建议使用只读账号）

```bash
# 验证 Node.js 可用
node --version   # v18+
npx --version
```

## 快速开始 / Quick Start

### 1. SQLite（开发测试）

最简配置，无需安装数据库：

```yaml
# config.yaml
mcp_servers:
  - name: "test-db"
    type: "dbhub"
    databases:
      - alias: "warehouse"
        dsn: "sqlite:///data/test_warehouse.db"
    security:
      readonly: true
      max_rows: 1000
```

```bash
# 运行检测
uv run order-guard run --rule rule-mcp-inventory --dry-run
```

### 2. MySQL

```yaml
mcp_servers:
  - name: "mysql-erp"
    type: "dbhub"
    databases:
      - alias: "erp"
        dsn: "mysql://readonly_user:${DB_PASSWORD}@hostname:3306/erp_db"
        query_timeout: 10
    security:
      readonly: true
      max_rows: 500
```

**创建只读账号：**

```sql
CREATE USER 'orderguard_ro'@'%' IDENTIFIED BY 'your_password';
GRANT SELECT ON erp_db.* TO 'orderguard_ro'@'%';
FLUSH PRIVILEGES;
```

### 3. PostgreSQL

```yaml
mcp_servers:
  - name: "pg-analytics"
    type: "dbhub"
    databases:
      - alias: "analytics"
        dsn: "postgres://readonly:${PG_PASSWORD}@pg-host:5432/analytics?sslmode=require"
        query_timeout: 15
    security:
      readonly: true
      max_rows: 2000
```

**创建只读账号：**

```sql
CREATE ROLE orderguard_ro WITH LOGIN PASSWORD 'your_password';
GRANT CONNECT ON DATABASE analytics TO orderguard_ro;
GRANT USAGE ON SCHEMA public TO orderguard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO orderguard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO orderguard_ro;
```

## 配置说明 / Configuration Reference

### databases

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `alias` | string | 是 | 数据库别名，用于 DBHub 内部标识 |
| `dsn` | string | 是 | 数据库连接串，支持 `${ENV_VAR}` 引用 |
| `query_timeout` | int | 否 | 查询超时（秒），默认无限制 |

### security

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `readonly` | bool | `true` | 只允许 SELECT 查询，拒绝写操作 |
| `max_rows` | int | `1000` | 单次查询最大返回行数 |

### DSN 格式

```
sqlite:///path/to/database.db
mysql://user:password@host:3306/dbname
postgres://user:password@host:5432/dbname?sslmode=require
sqlserver://user:password@host:1433/dbname
mariadb://user:password@host:3306/dbname
```

## 安全建议 / Security Best Practices

1. **使用只读账号** — 始终创建专用只读数据库账号
2. **启用 readonly** — `security.readonly: true`（默认开启），DBHub 会拒绝所有写操作
3. **限制返回行数** — `security.max_rows` 防止意外全表扫描
4. **设置查询超时** — `query_timeout` 防止慢查询阻塞
5. **密码用环境变量** — 使用 `${DB_PASSWORD}` 引用，不要明文写在配置中
6. **网络隔离** — 数据库仅对 OrderGuard 服务器开放连接

## 多数据库配置 / Multi-Database Setup

一个 DBHub 实例可以连接多个数据库，不同规则绑定不同数据库：

```yaml
mcp_servers:
  - name: "erp-db"
    type: "dbhub"
    databases:
      - alias: "orders"
        dsn: "mysql://ro:${DB_PASS}@host:3306/orders_db"
      - alias: "inventory"
        dsn: "mysql://ro:${DB_PASS}@host:3306/inventory_db"
    security:
      readonly: true
      max_rows: 1000

  - name: "analytics-db"
    type: "dbhub"
    databases:
      - alias: "analytics"
        dsn: "postgres://ro:${PG_PASS}@pg:5432/analytics"
    security:
      readonly: true
      max_rows: 2000
```

```yaml
# rules.yaml — 不同规则绑定不同 MCP Server
rules:
  - id: rule-inventory
    connector_type: mcp
    mcp_server: "erp-db"
    prompt_template: "分析库存数据..."

  - id: rule-analytics
    connector_type: mcp
    mcp_server: "analytics-db"
    prompt_template: "分析销售趋势..."
```

## 故障排查 / Troubleshooting

### DBHub 启动失败

```bash
# 手动测试 DBHub 连接
npx -y @bytebase/dbhub --dsn "sqlite:///data/test.db" --transport stdio
```

### 连接被拒绝

- 检查数据库是否允许远程连接
- 检查防火墙规则
- 检查用户名密码是否正确

### 查询超时

- 增大 `query_timeout` 值
- 优化 SQL 查询（添加索引、WHERE 条件）
- 减小 `max_rows` 限制
