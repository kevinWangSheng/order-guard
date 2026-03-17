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

### Implemented

| Feature | Description | Version |
|---------|-------------|---------|
| **Unified AI Agent** | 19 tools, one agent handles everything (queries, rules, alerts, reports) | v4 |
| **Multi-database support** | MySQL / PostgreSQL / SQLite via MCP protocol, multi-DB concurrent queries | v3 |
| **Natural language queries** | Describe your need → AI generates SQL → queries → returns analysis | v2 |
| **Chat-based rule config** | Describe monitoring needs → AI understands schema → generates rules → user confirms | v4 |
| **Scheduled anomaly detection** | Cron-scheduled rules → AI analyzes data → auto-push alerts on anomalies | v1 |
| **Feishu Bot** | @bot to query data, manage rules, view alerts; multi-turn conversations | v3 |
| **Alert push** | Feishu message cards / generic webhook, with dedup, silence window, batch merge | v2 |
| **Scheduled reports** | Auto-generated daily/weekly reports with customizable sections and KPIs | v4 |
| **Schema anti-hallucination** | Auto-inject table schemas into AI context, sensitive table/column blocklist | v3 |
| **Query audit** | Log all AI-executed SQL queries, traceable and queryable | v3 |
| **LLM usage tracking** | Token consumption, cost estimation, grouped by rule/model | v5 |
| **Datasource health monitoring** | Periodic health checks, auto-alert on consecutive failures, 24h uptime stats | v5 |
| **Alert lifecycle** | Mark alerts as handled/ignored/false-positive, stats dashboard | v5 |
| **Rule effectiveness** | Trigger count, false positive rate, execution success rate, tuning suggestions | v5 |
| **Business context injection** | Configure company-specific context to improve AI analysis accuracy | v4 |
| **CLI management** | 15+ commands covering rules, alerts, reports, sessions, query audit | v1 |
| **Docker deployment** | Dockerfile + docker-compose.yml, ready to deploy | v1 |

### Planned

| Feature | Description | Status |
|---------|-------------|--------|
| Feishu Docs MCP | Auto-sync business context from Feishu spreadsheets/docs | Planned |
| Google Sheets MCP | Read promotion calendars, stocking plans from operational spreadsheets | Planned |
| WeCom Bot | Reuse unified Agent for WeChat Work bidirectional chat | Planned |
| Web Dashboard | Visual dashboard for rules, alerts, and system status | Planned |
| Multi-Agent collaboration | Cross-datasource joint analysis | Planned |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Interface — Feishu Bot / CLI / Webhook Alert Push       │
├─────────────────────────────────────────────────────────┤
│  Scheduler — APScheduler (Cron) / Event / Manual         │
├─────────────────────────────────────────────────────────┤
│  Unified Agent — 19 Tools (Query / Rules / Alerts / ...)│
├─────────────────────────────────────────────────────────┤
│  Data Access — MCP Protocol → DBHub → MySQL / PG / SQLite│
├─────────────────────────────────────────────────────────┤
│  Storage — SQLModel + Alembic (12 business tables)       │
└─────────────────────────────────────────────────────────┘
```

**Tech Stack**: Python 3.11+ / FastAPI / SQLModel / LiteLLM (100+ models) / APScheduler / MCP / Typer / Docker

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Node.js 18+ (required for DBHub database connections)
- LLM API Key (any of: OpenAI / Claude / DeepSeek / etc.)

### Installation

```bash
git clone https://github.com/your-org/order-guard.git
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

Edit `config.yaml` to add your data source:

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

### Launch

```bash
# Start server (API + scheduler + Feishu bot)
uv run order-guard serve

# Or deploy with Docker
docker compose up -d
```

### CLI Commands

```bash
# System status
uv run order-guard status

# List rules
uv run order-guard rules list

# Run detection manually
uv run order-guard run --rule-id <rule-id>

# View alert history
uv run order-guard history --limit 20

# View query audit log
uv run order-guard queries --last 10
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Database Setup Guide](docs/database-setup.md) | MySQL / PostgreSQL / SQLite configuration |
| [Feishu Bot Setup Guide](docs/feishu-bot-setup.md) | Feishu app creation, permissions, event subscription |
| [Configuration Reference](config.example.yaml) | Full config file with comments |

---

## Project Structure

```
order-guard/
├── src/order_guard/
│   ├── main.py              # FastAPI entry point
│   ├── cli.py               # CLI commands (15+)
│   ├── config/              # Pydantic Settings + YAML
│   ├── models/              # 12 SQLModel tables
│   ├── engine/              # AI Agent + rules + reports
│   ├── tools/               # 19 Agent tools
│   ├── data_access/         # Unified data access (MCP/SQL)
│   ├── mcp/                 # MCP protocol client
│   ├── api/                 # Feishu Bot integration
│   ├── alerts/              # Alert dispatch + dedup
│   ├── scheduler/           # APScheduler jobs
│   └── storage/             # Database initialization
├── docs/                    # Documentation
├── tests/                   # Tests (575+ cases)
├── config.example.yaml      # Config template
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
