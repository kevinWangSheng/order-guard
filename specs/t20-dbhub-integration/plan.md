# T20: DBHub 集成

## Context
v2 使用 SQLite MCP Server 跑通了 MCP Agent 链路，但官方 MCP 数据库 Server 已被归档（有 SQL 注入漏洞）。DBHub（Bytebase 开源）是目前最成熟的数据库 MCP Server，内置 readonly、row limit、timeout 安全特性，支持 MySQL/PostgreSQL/SQLite/Oracle/MSSQL。

本任务将 DBHub 作为 OrderGuard 的标准数据库 MCP Server，替代官方实现。

## Scope

### In Scope
- DBHub 作为数据库 MCP Server 的标准方案
- 配置化支持 MySQL / PostgreSQL / SQLite
- 安全配置：readonly、max_rows、query_timeout
- MCPManager 适配 DBHub 的工具集（execute_sql、search_objects）
- 多数据库实例配置（一个 DBHub 进程可连多个库）
- 本地 SQLite 测试验证 + MySQL/PostgreSQL 文档说明

### Not In Scope
- Schema 防幻觉增强（T21）
- 热冷数据管理（T22）
- 查询审计（T23）
- 自建 MCP Server（使用现成 DBHub）

## Design

### DBHub 架构位置
```
用户的数据库 (MySQL/PG/SQLite)
        ↕
  DBHub MCP Server（安全层：readonly + timeout + row limit）
        ↕ MCP 协议 (stdio)
  OrderGuard MCPManager
        ↕
  AI Agent → 调用 execute_sql / search_objects
```

### 配置设计
```yaml
mcp_servers:
  # DBHub 连接本地 SQLite（开发测试）
  - name: "test-warehouse"
    type: "dbhub"
    transport: "stdio"
    databases:
      - alias: "warehouse"
        dsn: "sqlite:///data/test_warehouse.db"
    security:
      readonly: true
      max_rows: 1000
      query_timeout: 5

  # DBHub 连接 MySQL（生产）
  - name: "production-erp"
    type: "dbhub"
    transport: "stdio"
    databases:
      - alias: "erp"
        dsn: "mysql://readonly_user:${DB_PASSWORD}@host:3306/erp_db"
    security:
      readonly: true
      max_rows: 500
      query_timeout: 10
```

### MCPManager 适配
DBHub 暴露的工具：
- `execute_sql` — 执行 SQL 查询（受 readonly / max_rows / timeout 约束）
- `search_objects` — 渐进式 schema 探索（表、列、索引）

OrderGuard 的 MCPManager 需要：
1. 根据 `type: "dbhub"` 自动构建 DBHub 启动命令
2. 传递安全参数（`--readonly`、`--max-rows` 等）
3. 生成 `dbhub.toml` 配置文件供 DBHub 读取
4. 管理 DBHub 进程生命周期

### DBHub 启动命令生成
```python
# 根据配置自动生成 DBHub 启动命令
def build_dbhub_command(config: MCPServerConfig) -> list[str]:
    cmd = ["npx", "-y", "@bytebase/dbhub"]

    for db in config.databases:
        cmd.extend(["--dsn", db.dsn])

    if config.security.readonly:
        cmd.append("--readonly")

    # 或通过 dbhub.toml 配置
    return cmd
```

### Key Decisions
- 使用 DBHub 而非自建：节省开发量，安全特性已内置
- DBHub 通过 stdio 模式连接（OrderGuard 启动子进程）
- 安全参数在 OrderGuard 配置中统一管理，自动传给 DBHub
- 开发者自行安装数据库，OrderGuard 只负责连接
- Node.js 是前置依赖（DBHub 是 TypeScript 实现）

## Dependencies
- T16（MCP Client 基础层）— MCPManager
- T17（AI Agent）— Agent 工具调用
- Node.js 环境（npx）
- DBHub 包（@bytebase/dbhub）

## File Changes
- `src/order_guard/mcp/dbhub.py` — DBHub 配置生成 + 启动管理
- `src/order_guard/mcp/manager.py` — 适配 DBHub 类型
- `src/order_guard/mcp/models.py` — DBHubConfig 模型
- `src/order_guard/config/settings.py` — 数据库安全配置段
- `config.example.yaml` — DBHub 配置示例
- `docs/database-setup.md` — 数据库安装配置指南（MySQL/PG/SQLite）
- `tests/test_dbhub.py` — 单元测试（SQLite）

## Tasks
- [ ] T20.1: 添加 DBHub 配置模型（DBHubConfig + SecurityConfig）
- [ ] T20.2: 实现 DBHub 启动命令 / toml 配置生成
- [ ] T20.3: MCPManager 适配 type="dbhub" 自动启动 DBHub
- [ ] T20.4: 用 SQLite 本地端到端验证（DBHub → Agent → 分析 → 告警）
- [ ] T20.5: 编写 MySQL / PostgreSQL 配置文档和连接指南
- [ ] T20.6: 更新 config.example.yaml
- [ ] T20.7: 编写单元测试
