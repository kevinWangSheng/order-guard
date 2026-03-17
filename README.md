# OrderGuard — AI 经营助手

> 连接企业数据库，用自然语言查数据、配规则、收告警。对话即操作，无需写代码。

[English](./README_EN.md) | 中文

---

## 这是什么？

OrderGuard 是一个开源的 **AI 经营助手**，通过飞书 Bot（或 CLI）连接企业数据库，让运营、财务、管理层可以：

- **用自然语言查数据** — "最近 7 天退货率最高的 SKU 是哪些？"
- **用对话配监控规则** — "帮我每天 9 点检查库存低于安全线的商品"
- **自动收异常告警** — 库存不足、退货率飙升、销售异常波动，飞书群实时推送
- **定时生成经营报告** — 日报 / 周报自动汇总关键指标

不需要写 SQL，不需要登录 ERP 后台，在飞书群里 @机器人 就能搞定。

---

## 效果演示

### 自然语言查询数据
> "帮我查一下哪样东西卖得最好"

<img src="docs/screenshots/query-sales-top10.png" width="600" alt="销售数据查询 Top 10">

### 对话式创建监控规则
> "帮我初始化一下对应的规则" → 确认后一键批量创建

<img src="docs/screenshots/rule-create-confirm.png" width="600" alt="批量创建 16 条监控规则">

<img src="docs/screenshots/rule-details.png" width="600" alt="规则详情展示">

### 自动告警推送
> 定时检测库存风险，异常直接推到飞书群

<img src="docs/screenshots/alert-replenishment.png" width="600" alt="补货计划告警">

<img src="docs/screenshots/alert-inventory.png" width="600" alt="库存告警 SKU 清单">

### Bot 欢迎界面

<img src="docs/screenshots/bot-welcome.png" width="600" alt="OrderGuard 数据助手">

---

## 功能列表

| 功能 | 说明 | 状态 |
|------|------|------|
| 统一 AI Agent | 19 个工具，一个 Agent 处理查询、规则、告警、报告等所有请求 | ✅ 已实现 |
| 多数据库接入 | 通过 MCP + DBHub 连接 MySQL / PostgreSQL / SQLite，多库同时查询 | ✅ 已实现 |
| 自然语言查数据 | 描述需求 → AI 自动生成 SQL → 执行查询 → 结构化分析结果 | ✅ 已实现 |
| 自然语言配规则 | 对话描述监控需求 → AI 读取表结构 → 生成规则 → 用户确认后生效 | ✅ 已实现 |
| 定时异常检测 | Cron 表达式调度 → Agent 查数据 + 分析 → 异常自动推送告警 | ✅ 已实现 |
| 飞书 Bot 对话 | WebSocket 长连接 / HTTP 回调双模式，多轮对话，会话持久化 | ✅ 已实现 |
| 告警推送 | 飞书交互卡片 / 企业微信 / 通用 Webhook，去重 + 静默期 + 批量合并 | ✅ 已实现 |
| 定时经营报告 | 日报 / 周报 Cron 自动生成，自定义章节和 KPI 指标 | ✅ 已实现 |
| Schema 防幻觉 | 表结构自动注入 Agent 上下文，敏感表/字段黑名单，冷数据标记 | ✅ 已实现 |
| SQL 安全校验 | 写操作拦截、自动加 LIMIT、表/字段存在性验证（SQLGlot 解析） | ✅ 已实现 |
| 查询审计 | 所有 Agent 执行的 SQL 全量记录（耗时、行数、状态） | ✅ 已实现 |
| LLM 用量追踪 | Token 消耗 + 成本估算，按规则/模型/触发类型分组统计 | ✅ 已实现 |
| 数据源健康监控 | 定时探活，连续失败自动告警 + 恢复通知，24h 可用率 | ✅ 已实现 |
| 告警闭环管理 | 标记已处理/忽略/误报，告警统计面板 | ✅ 已实现 |
| 规则效果评估 | 触发次数、误报率、执行成功率，智能调优建议 | ✅ 已实现 |
| 业务知识注入 | 配置文件 + 对话动态添加，注入 Agent 上下文提升分析准确度 | ✅ 已实现 |
| CLI 管理工具 | 15+ 命令：serve / run / rules / history / queries / reports / sessions / status | ✅ 已实现 |
| Docker 部署 | Dockerfile + docker-compose.yml，单容器部署 | ✅ 已实现 |
| 飞书文档 MCP | 自动同步飞书表格/文档中的业务上下文 | 📋 计划中 |
| Google Sheet MCP | 读取促销日历、备货计划等运营表格 | 📋 计划中 |
| 企业微信 Bot | 复用统一 Agent，企微双向对话 | 📋 计划中 |
| Web 管理界面 | Dashboard、规则可视化、告警时间线 | 📋 计划中 |
| 多 Agent 协作 | 跨数据源联合分析 | 📋 计划中 |

---

## 架构

### 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  交互层                                                       │
│  飞书 Bot (WebSocket/HTTP) │ CLI (Typer) │ Webhook 告警推送    │
├──────────────────────────────────────────────────────────────┤
│  调度层                                                       │
│  APScheduler: 规则检测(Cron) │ 报告生成(Cron) │ 健康探活(定时)  │
├──────────────────────────────────────────────────────────────┤
│  统一 Agent (LiteLLM — 支持 100+ LLM 模型)                    │
│  ┌─────────┬──────────┬──────────┬──────────┬─────────────┐  │
│  │数据查询  │规则管理   │告警管理   │报告管理   │健康/用量/上下文│  │
│  │3 工具    │6 工具    │3 工具    │2 工具    │3 工具        │  │
│  └─────────┴──────────┴──────────┴──────────┴─────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  统一数据访问层 (DAL)                                          │
│  固定 3 个工具接口: list_datasources / get_schema / query      │
│  SQL 校验 → 写拦截 → 自动 LIMIT → Schema 过滤 → 审计日志       │
├──────────────────────────────────────────────────────────────┤
│  MCP 协议层                                                   │
│  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ DBHub (stdio)     │  │ 通用 MCP Server (stdio / SSE)    │  │
│  │ MySQL / PG / SQLite│  │ 任意 MCP 兼容服务               │  │
│  └──────────────────┘  └──────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  存储层: SQLModel + Alembic │ SQLite(默认) / PostgreSQL(可选)  │
│  12 张业务表: 规则/告警/任务/查询日志/会话/上下文/报告/用量/健康  │
└──────────────────────────────────────────────────────────────┘
```

### 数据源接入

所有数据访问统一走 **MCP 协议**，Agent 不直接连数据库：

```
Agent → DAL (3 工具) → MCP Client → DBHub → MySQL / PostgreSQL / SQLite
                       MCP Client → 通用 MCP Server → 任意服务
```

**支持的接入方式：**

| 方式 | 协议 | 适用场景 |
|------|------|---------|
| DBHub + SQL 数据库 | MCP stdio | MySQL / PostgreSQL / SQLite（推荐） |
| 通用 MCP Server (本地) | MCP stdio | 任何支持 MCP 的本地工具/服务 |
| 远程 MCP Server | MCP SSE | 远程 API / SaaS 的 MCP 封装 |

DBHub 由 [Bytebase](https://github.com/bytebase/dbhub) 开源，内置 readonly + row limit + query timeout 安全特性。

### Agent 工具一览

Agent 共 **19 个工具**，分 7 类：

| 分类 | 工具 | 说明 |
|------|------|------|
| 数据查询 (3) | `list_datasources` / `get_schema` / `query` | 列出数据源、查看表结构、执行 SQL |
| 规则管理 (6) | `list_rules` / `create_rule` / `update_rule` / `delete_rule` / `test_rule` / `get_rule_stats` | 规则 CRUD + 测试执行 + 效果统计 |
| 告警管理 (3) | `list_alerts` / `handle_alert` / `get_alert_stats` | 告警查看 + 处理闭环 + 统计面板 |
| 上下文管理 (3) | `list_context` / `add_context` / `delete_context` | 业务知识动态管理 |
| 报告管理 (2) | `manage_report` / `preview_report` | 报告配置 + 预览生成 |
| 数据源健康 (1) | `check_health` | 探活 + 延迟 + 可用率 |
| LLM 用量 (1) | `get_usage_stats` | Token 消耗 + 成本统计 |

### 告警通道

| 通道 | 格式 | 说明 |
|------|------|------|
| 飞书 Webhook | 交互式消息卡片 | severity 颜色标识，按级别分组展示 |
| 企业微信 Webhook | Markdown 消息 | 自动识别 URL 格式 |
| 通用 Webhook | JSON Payload | 适配任意支持 HTTP POST 的系统 |

告警推送内置 **去重机制**：同规则 + 同级别 + 同标题在静默窗口（默认 30 分钟）内不重复推送。

---

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- Node.js 18+（DBHub 数据库连接需要）
- LLM API Key（OpenAI / Claude / DeepSeek 等任意一个）

### 安装

```bash
git clone https://github.com/kevinWangSheng/order-guard.git
cd order-guard

# 安装依赖
uv sync

# 复制配置文件
cp .env.example .env
cp config.example.yaml config.yaml
```

### 配置

编辑 `.env`，填入 LLM API Key：

```bash
OG_LLM_API_KEY=your-api-key-here
OG_LLM_MODEL=openai/gpt-4o          # 或 claude-3-5-sonnet-20241022, deepseek/deepseek-chat 等
```

编辑 `config.yaml`，添加数据源（以 MySQL 为例）：

```yaml
mcp_servers:
  - name: "my-database"
    type: "dbhub"
    databases:
      - alias: "erp"
        dsn: "mysql://readonly_user:${DB_PASSWORD}@host:3306/erp_db"
        query_timeout: 10
    security:
      readonly: true       # DBHub 层强制只读
      max_rows: 1000       # 单次查询最大返回行数
    schema_filter:          # 可选：屏蔽敏感表/字段
      blocked_tables: ["users", "credentials"]
      blocked_columns: ["password", "id_card"]
    enabled: true
```

### 启动

```bash
# 启动服务（FastAPI + APScheduler + 飞书 Bot）
uv run order-guard serve

# 或 Docker 部署
docker compose up -d
```

### CLI 常用命令

```bash
uv run order-guard status                    # 系统状态总览
uv run order-guard rules list                # 列出所有规则
uv run order-guard run --rule-id <id>        # 手动执行检测
uv run order-guard history --limit 20        # 告警历史
uv run order-guard queries --last 10         # 查询审计日志
uv run order-guard reports list              # 报告列表
uv run order-guard sessions list             # 会话列表
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [数据库连接指南](docs/database-setup.md) | MySQL / PostgreSQL / SQLite 配置，只读账号创建，多库配置 |
| [飞书 Bot 创建指南](docs/feishu-bot-setup.md) | 飞书应用创建、权限配置、事件订阅、WebSocket 配置 |
| [配置参考](config.example.yaml) | 完整配置文件示例及注释 |

---

## 技术栈

| 层 | 技术 |
|---|------|
| 语言 | Python 3.11+ / uv |
| Web | FastAPI + Uvicorn |
| ORM | SQLModel + Alembic |
| 数据库 | SQLite（默认）/ PostgreSQL（可选） |
| LLM | LiteLLM（统一调用 OpenAI / Claude / DeepSeek / Qwen 等 100+ 模型） |
| 数据接入 | MCP 协议 + DBHub（Bytebase 开源数据库 MCP Server） |
| 定时任务 | APScheduler 4.x（async，进程内嵌入） |
| Bot | 飞书 lark-oapi SDK（WebSocket 长连接 + HTTP 回调） |
| 告警 | httpx + Webhook（飞书卡片 / 企微 / 通用 JSON） |
| 配置 | Pydantic Settings + YAML + 环境变量 |
| CLI | Typer |
| 日志 | loguru |
| 部署 | Docker + Docker Compose |

---

## 项目结构

```
order-guard/
├── src/order_guard/
│   ├── main.py              # FastAPI 入口 + 生命周期管理
│   ├── cli.py               # Typer CLI (15+ 命令)
│   ├── config/              # Pydantic Settings + YAML 加载
│   ├── models/              # 12 张 SQLModel 表定义
│   ├── engine/              # Agent 核心 + LLM 客户端 + 规则 + 报告
│   ├── tools/               # 7 个模块 / 19 个 Agent 工具
│   ├── data_access/         # 统一数据访问层 (DAL + SQL/MCP 适配器)
│   ├── mcp/                 # MCP 客户端 + DBHub + Schema 加载/校验
│   ├── api/                 # 飞书 Bot + 会话管理 + 权限控制
│   ├── alerts/              # 告警分发 + Webhook 通道 + 去重
│   ├── scheduler/           # APScheduler 任务注册 + 作业实现
│   └── storage/             # 数据库初始化 + Session 管理
├── docs/                    # 文档 + 截图
├── tests/                   # 测试 (575+ 用例)
├── config.example.yaml
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest tests/ -x

# 数据库迁移
uv run alembic upgrade head
```

---

## License

[MIT](LICENSE)
