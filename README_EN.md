# OrderGuard — AI Business Assistant

> Connect your databases, query data in natural language, set up monitoring rules via chat, and receive automated alerts. No code required.

English | [中文](./README.md)

---

## What is OrderGuard?

OrderGuard is an open-source **AI-powered business assistant** that connects to your enterprise databases via Feishu Bot (or CLI), enabling operations, finance, and management teams to:

- **Query data in natural language** — "What are the top SKUs by return rate in the last 7 days?"
- **Set up monitoring rules via chat** — "Check inventory below safety stock every day at 9am"
- **Receive automated alerts** — Low inventory, return rate spikes, sales anomalies — pushed to Feishu/Webhook in real time
- **Generate scheduled reports** — Daily/weekly business reports with customizable KPIs

No SQL needed. No ERP dashboard login. Just @mention the bot in your chat group.

---

## Demo

### Natural Language Data Query
> "Show me the best-selling products"

<img src="docs/screenshots/query-sales-top10.png" width="600" alt="Sales data query Top 10">

### Conversational Rule Creation
> "Initialize monitoring rules for me" → Confirm to batch create

<img src="docs/screenshots/rule-create-confirm.png" width="600" alt="Batch create 16 monitoring rules">

<img src="docs/screenshots/rule-details.png" width="600" alt="Rule details">

### Automated Alert Push
> Scheduled inventory risk detection, anomalies pushed directly to chat

<img src="docs/screenshots/alert-replenishment.png" width="600" alt="Replenishment plan alert">

<img src="docs/screenshots/alert-inventory.png" width="600" alt="Inventory alert SKU list">

---

## Features

| Feature | Description | Status |
|---------|-------------|--------|
| Unified AI Agent | 19 tools, one Agent handles queries, rules, alerts, reports, and more | ✅ Done |
| Multi-database support | MySQL / PostgreSQL / SQLite via MCP + DBHub, multi-DB concurrent queries | ✅ Done |
| Natural language queries | Describe your need → AI generates SQL → executes → returns structured analysis | ✅ Done |
| Chat-based rule config | Describe monitoring needs → AI reads table schema → generates rules → user confirms | ✅ Done |
| Scheduled anomaly detection | Cron-scheduled rules → Agent queries + analyzes → auto-push alerts | ✅ Done |
| Feishu Bot | WebSocket long-connection / HTTP callback dual mode, multi-turn chat, persistent sessions | ✅ Done |
| Alert push | Feishu interactive cards / WeCom / generic webhook, with dedup + silence window + batch merge | ✅ Done |
| Scheduled reports | Daily/weekly auto-generated reports with customizable sections and KPIs | ✅ Done |
| Schema anti-hallucination | Auto-inject table schemas into Agent context, sensitive table/column blocklist, cold data marking | ✅ Done |
| SQL safety validation | Write-operation blocking, auto LIMIT, table/column existence check (SQLGlot parsing) | ✅ Done |
| Query audit | Full logging of all Agent-executed SQL (duration, rows, status) | ✅ Done |
| LLM usage tracking | Token consumption + cost estimation, grouped by rule/model/trigger type | ✅ Done |
| Datasource health monitoring | Periodic probing, auto-alert on consecutive failures + recovery notice, 24h uptime | ✅ Done |
| Alert lifecycle | Mark as handled/ignored/false-positive, alert stats dashboard | ✅ Done |
| Rule effectiveness | Trigger count, false positive rate, success rate, tuning suggestions | ✅ Done |
| Business context injection | Config file + chat-added dynamic context, injected into Agent for better analysis | ✅ Done |
| CLI management | 15+ commands: serve / run / rules / history / queries / reports / sessions / status | ✅ Done |
| Docker deployment | Dockerfile + docker-compose.yml, single-container deployment | ✅ Done |
| Feishu Docs MCP | Auto-sync business context from Feishu spreadsheets/docs | 📋 Planned |
| Google Sheets MCP | Read promotion calendars, stocking plans from operational spreadsheets | 📋 Planned |
| WeCom Bot | Reuse unified Agent for WeChat Work bidirectional chat | 📋 Planned |
| Web Dashboard | Visual dashboard for rules, alerts, and system status | 📋 Planned |
| Multi-Agent collaboration | Cross-datasource joint analysis | 📋 Planned |

---

## Architecture

### Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Interface Layer                                              │
│  Feishu Bot (WebSocket/HTTP) │ CLI (Typer) │ Webhook Alerts   │
├──────────────────────────────────────────────────────────────┤
│  Scheduler Layer                                              │
│  APScheduler: Detection(Cron) │ Reports(Cron) │ Health(Timer) │
├──────────────────────────────────────────────────────────────┤
│  Unified Agent (LiteLLM — 100+ LLM models)                   │
│  ┌──────────┬───────┬────────┬─────────┬──────────────────┐  │
│  │Data Query │Rules  │Alerts  │Reports  │Health/Usage/Ctx  │  │
│  │3 tools    │6 tools│3 tools │2 tools  │3 tools           │  │
│  └──────────┴───────┴────────┴─────────┴──────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  Data Access Layer (DAL)                                      │
│  Fixed 3-tool API: list_datasources / get_schema / query      │
│  SQL validation → write blocking → auto LIMIT → schema filter │
├──────────────────────────────────────────────────────────────┤
│  MCP Protocol Layer                                           │
│  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ DBHub (stdio)     │  │ Generic MCP Server (stdio / SSE) │  │
│  │ MySQL/PG/SQLite   │  │ Any MCP-compatible service       │  │
│  └──────────────────┘  └──────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  Storage: SQLModel + Alembic │ SQLite (default) / PostgreSQL  │
│  12 tables: rules/alerts/tasks/queries/sessions/context/...   │
└──────────────────────────────────────────────────────────────┘
```

### Data Source Integration

All data access goes through **MCP protocol** — the Agent never connects to databases directly:

```
Agent → DAL (3 tools) → MCP Client → DBHub → MySQL / PostgreSQL / SQLite
                         MCP Client → Generic MCP Server → Any service
```

**Supported connection methods:**

| Method | Protocol | Use Case |
|--------|----------|----------|
| DBHub + SQL Database | MCP stdio | MySQL / PostgreSQL / SQLite (recommended) |
| Generic MCP Server (local) | MCP stdio | Any local MCP-compatible tool/service |
| Remote MCP Server | MCP SSE | Remote API / SaaS MCP wrappers |

DBHub is open-sourced by [Bytebase](https://github.com/bytebase/dbhub), with built-in readonly + row limit + query timeout security.

### Agent Tools

The Agent has **19 tools** across 7 categories:

| Category | Tools | Description |
|----------|-------|-------------|
| Data Query (3) | `list_datasources` / `get_schema` / `query` | List sources, view schemas, execute SQL |
| Rule Management (6) | `list_rules` / `create_rule` / `update_rule` / `delete_rule` / `test_rule` / `get_rule_stats` | CRUD + test execution + effectiveness stats |
| Alert Management (3) | `list_alerts` / `handle_alert` / `get_alert_stats` | View + resolve + stats dashboard |
| Context Management (3) | `list_context` / `add_context` / `delete_context` | Dynamic business knowledge management |
| Report Management (2) | `manage_report` / `preview_report` | Configure + preview generation |
| Datasource Health (1) | `check_health` | Probe + latency + uptime |
| LLM Usage (1) | `get_usage_stats` | Token consumption + cost stats |

### Alert Channels

| Channel | Format | Description |
|---------|--------|-------------|
| Feishu Webhook | Interactive message card | Severity color-coded, grouped by level |
| WeCom Webhook | Markdown message | Auto-detected by URL pattern |
| Generic Webhook | JSON payload | Compatible with any HTTP POST endpoint |

Built-in **deduplication**: same rule + severity + title won't repeat within the silence window (default 30 minutes).

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Node.js 18+ (required for DBHub database connections)
- LLM API Key (any of: OpenAI / Claude / DeepSeek / etc.)

### Installation

```bash
git clone https://github.com/kevinWangSheng/order-guard.git
cd order-guard

# Install dependencies
uv sync

# Copy config files
cp .env.example .env
cp config.example.yaml config.yaml
```

### Configuration

Edit `.env` with your LLM API key:

```bash
OG_LLM_API_KEY=your-api-key-here
OG_LLM_MODEL=openai/gpt-4o          # or claude-3-5-sonnet-20241022, deepseek/deepseek-chat, etc.
```

Edit `config.yaml` to add your data source (MySQL example):

```yaml
mcp_servers:
  - name: "my-database"
    type: "dbhub"
    databases:
      - alias: "erp"
        dsn: "mysql://readonly_user:${DB_PASSWORD}@host:3306/erp_db"
        query_timeout: 10
    security:
      readonly: true       # Enforced at DBHub level
      max_rows: 1000       # Max rows per query
    schema_filter:          # Optional: hide sensitive tables/columns
      blocked_tables: ["users", "credentials"]
      blocked_columns: ["password", "id_card"]
    enabled: true
```

### Launch

```bash
# Start server (FastAPI + APScheduler + Feishu Bot)
uv run order-guard serve

# Or deploy with Docker
docker compose up -d
```

### CLI Commands

```bash
uv run order-guard status                    # System health overview
uv run order-guard rules list                # List all rules
uv run order-guard run --rule-id <id>        # Run detection manually
uv run order-guard history --limit 20        # Alert history
uv run order-guard queries --last 10         # Query audit log
uv run order-guard reports list              # Report list
uv run order-guard sessions list             # Session list
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Database Setup Guide](docs/database-setup.md) | MySQL / PostgreSQL / SQLite config, readonly accounts, multi-DB setup |
| [Feishu Bot Setup Guide](docs/feishu-bot-setup.md) | Feishu app creation, permissions, event subscription, WebSocket config |
| [Configuration Reference](config.example.yaml) | Full config file with comments |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ / uv |
| Web | FastAPI + Uvicorn |
| ORM | SQLModel + Alembic |
| Database | SQLite (default) / PostgreSQL (optional) |
| LLM | LiteLLM (unified access to OpenAI / Claude / DeepSeek / Qwen and 100+ models) |
| Data Access | MCP Protocol + DBHub (Bytebase open-source database MCP Server) |
| Scheduler | APScheduler 4.x (async, in-process) |
| Bot | Feishu lark-oapi SDK (WebSocket long-connection + HTTP callback) |
| Alerts | httpx + Webhook (Feishu cards / WeCom / generic JSON) |
| Config | Pydantic Settings + YAML + env vars |
| CLI | Typer |
| Logging | loguru |
| Deploy | Docker + Docker Compose |

---

## Project Structure

```
order-guard/
├── src/order_guard/
│   ├── main.py              # FastAPI entry + lifecycle management
│   ├── cli.py               # Typer CLI (15+ commands)
│   ├── config/              # Pydantic Settings + YAML loader
│   ├── models/              # 12 SQLModel table definitions
│   ├── engine/              # Agent core + LLM client + rules + reports
│   ├── tools/               # 7 modules / 19 Agent tools
│   ├── data_access/         # Unified DAL (SQL/MCP adapters)
│   ├── mcp/                 # MCP client + DBHub + schema loader/validator
│   ├── api/                 # Feishu Bot + session + permissions
│   ├── alerts/              # Alert dispatcher + webhook channels + dedup
│   ├── scheduler/           # APScheduler job registration + implementations
│   └── storage/             # Database init + session management
├── docs/                    # Documentation + screenshots
├── tests/                   # Tests (575+ cases)
├── config.example.yaml
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest tests/ -x

# Database migrations
uv run alembic upgrade head
```

---

## License

[MIT](LICENSE)
