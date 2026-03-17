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

## 核心功能

### 已实现

| 功能 | 说明 | 版本 |
|------|------|------|
| **统一 AI Agent** | 19 个工具，一个 Agent 处理所有请求（查询、规则、告警、报告） | v4 |
| **多数据源接入** | 通过 MCP 协议连接 MySQL / PostgreSQL / SQLite，支持多库同时查询 | v3 |
| **自然语言查数据** | 用户描述需求 → AI 自动生成 SQL → 查询 → 返回分析结果 | v2 |
| **自然语言配规则** | 对话描述监控需求 → AI 理解数据结构 → 生成规则 → 用户确认生效 | v4 |
| **定时异常检测** | Cron 定时执行规则 → AI 分析数据 → 异常自动推送告警 | v1 |
| **飞书 Bot 对话** | @机器人查数据、管规则、看告警，支持多轮对话和会话管理 | v3 |
| **告警推送** | 飞书消息卡片 / 通用 Webhook，支持去重、静默期、批量合并 | v2 |
| **定时经营报告** | 日报 / 周报自动生成，自定义章节和 KPI 指标 | v4 |
| **Schema 防幻觉** | 自动注入表结构到 AI 上下文，敏感表/字段黑名单过滤 | v3 |
| **查询审计** | 记录所有 AI 执行的 SQL，可追溯、可统计 | v3 |
| **LLM 用量追踪** | Token 消耗、成本估算、按规则/模型分组统计 | v5 |
| **数据源健康监控** | 定时探活，连续失败自动告警，24h 可用率统计 | v5 |
| **告警闭环** | 告警可标记处理状态（已处理/忽略/误报），统计面板 | v5 |
| **规则效果评估** | 触发次数、误报率、执行成功率，智能调优建议 | v5 |
| **业务知识注入** | 配置公司特有业务上下文，AI 分析更懂业务 | v4 |
| **CLI 管理工具** | 15+ 命令覆盖规则、告警、报告、会话、查询审计 | v1 |
| **Docker 一键部署** | Dockerfile + docker-compose.yml，开箱即用 | v1 |

### 计划中

| 功能 | 说明 | 状态 |
|------|------|------|
| 飞书文档 MCP 连接 | 自动同步飞书表格/文档中的业务上下文 | 规划中 |
| Google Sheet 连接 | 读取促销日历、备货计划等运营表格 | 规划中 |
| 企业微信 Bot | 复用统一 Agent，支持企微双向对话 | 规划中 |
| Web 管理界面 | Dashboard、规则可视化、告警历史 | 规划中 |
| 多 Agent 协作 | 跨数据源联合分析 | 规划中 |

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│  触达层 — 飞书 Bot / CLI / Webhook 告警推送               │
├─────────────────────────────────────────────────────────┤
│  调度层 — APScheduler (Cron) / 事件触发 / 手动触发        │
├─────────────────────────────────────────────────────────┤
│  统一 Agent — 19 工具 (数据查询 / 规则 / 告警 / 报告)     │
├─────────────────────────────────────────────────────────┤
│  数据访问层 — MCP 协议 → DBHub → MySQL / PG / SQLite     │
├─────────────────────────────────────────────────────────┤
│  存储层 — SQLModel + Alembic (12 张业务表)                │
└─────────────────────────────────────────────────────────┘
```

**技术栈**：Python 3.11+ / FastAPI / SQLModel / LiteLLM (100+ 模型) / APScheduler / MCP / Typer / Docker

---

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (推荐) 或 pip
- Node.js 18+ (DBHub 数据库连接需要)
- LLM API Key (OpenAI / Claude / DeepSeek 等任意一个)

### 安装

```bash
git clone https://github.com/your-org/order-guard.git
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

编辑 `config.yaml`，添加数据源：

```yaml
mcp_servers:
  - name: "my-database"
    type: "dbhub"
    databases:
      - alias: "erp"
        dsn: "mysql://readonly_user:${DB_PASSWORD}@host:3306/erp_db"
    security:
      readonly: true
      max_rows: 1000
    enabled: true
```

### 启动

```bash
# 启动服务（API + 定时任务 + 飞书 Bot）
uv run order-guard serve

# 或 Docker 部署
docker compose up -d
```

### CLI 常用命令

```bash
# 查看系统状态
uv run order-guard status

# 列出所有规则
uv run order-guard rules list

# 手动执行检测
uv run order-guard run --rule-id <rule-id>

# 查看告警历史
uv run order-guard history --limit 20

# 查看查询审计
uv run order-guard queries --last 10
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [数据库连接指南](docs/database-setup.md) | MySQL / PostgreSQL / SQLite 配置详解 |
| [飞书 Bot 创建指南](docs/feishu-bot-setup.md) | 飞书应用创建、权限配置、事件订阅 |
| [配置参考](config.example.yaml) | 完整配置文件示例及注释 |

---

## 项目结构

```
order-guard/
├── src/order_guard/
│   ├── main.py              # FastAPI 入口
│   ├── cli.py               # CLI 命令 (15+)
│   ├── config/              # Pydantic Settings + YAML
│   ├── models/              # 12 张 SQLModel 业务表
│   ├── engine/              # AI Agent + 规则 + 报告
│   ├── tools/               # 19 个 Agent 工具
│   ├── data_access/         # 统一数据访问层 (MCP/SQL)
│   ├── mcp/                 # MCP 协议客户端
│   ├── api/                 # 飞书 Bot 接入
│   ├── alerts/              # 告警推送 + 去重
│   ├── scheduler/           # APScheduler 定时任务
│   └── storage/             # 数据库初始化
├── docs/                    # 文档
├── tests/                   # 测试 (575+ 用例)
├── config.example.yaml      # 配置模板
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
