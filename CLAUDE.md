# OrderGuard

企业数据智能监控中台。汇聚公司内部/外部多个数据源，通过 AI（LLM）按业务规则进行分析判断，异常时自动告警推送，同时支持员工通过聊天工具实时查询。

## Key Documents

| 文档 | 用途 |
|------|------|
| `ARCHITECTURE.md` | 五层架构设计 + 技术栈 + 目录结构 |
| `ROADMAP.md` | MVP v1 任务列表（T01-T10）+ 后续版本规划 |
| `feature_list.json` | 任务跟踪 + 验收步骤 |
| `PRD.md` | 产品需求文档（最终产品，非 MVP 范围） |
| `SETUP.md` | 开发环境准备 + API Key 说明 |
| `specs/` | 每个任务的 plan.md（设计）+ eval.md（验收标准） |
| `claude-progress.txt` | 开发进度日志 |

## Session Protocol

Every session MUST start with:
1. Run `pwd` to confirm working directory
2. Read `claude-progress.txt` for current project state
3. Read `ROADMAP.md` for task priorities
4. Read `feature_list.json` for incomplete features
5. Read the relevant `specs/{task}/plan.md` for current task details
6. Work on the highest-priority incomplete task

Every session MUST end with:
1. Update `claude-progress.txt` with work completed this session
2. Update `ROADMAP.md` task statuses
3. Update `feature_list.json` passes field for verified tasks
4. Commit code with descriptive commit message
5. Ensure project is in a runnable state

## Verification Rules

- Tasks are ONLY complete after passing acceptance tests in `specs/{task}/eval.md`
- NEVER self-declare "done" without verification
- NEVER delete or modify test steps in feature_list.json (only change `passes` field)
- NEVER skip testing to speed up progress

## Boundaries

- Only implement tasks defined in ROADMAP.md -- do not add unrequested features
- When requirements are unclear, ASK -- do not assume
- Work on one task at a time
- When blocked, update progress file and explain the blocker

## Git Workflow

- One commit per task
- Commit message format: `{type}: {description} [#{task-id}]`
- Types: feat / fix / refactor / test / docs
- Every commit must leave the project in a runnable state

## Tech Stack

- Python 3.11+ / uv
- Web: FastAPI
- ORM: SQLModel + Alembic
- DB: SQLite (default) / PostgreSQL (optional)
- LLM: LiteLLM（支持 OpenAI / Claude / DeepSeek / Qwen 等 100+ 模型）
- Scheduler: APScheduler 4.x
- Config: Pydantic Settings + YAML + env vars
- CLI: Typer
- HTTP: httpx
- Log: loguru
- Deploy: Docker + Docker Compose

## Project Structure

```
order-guard/
├── src/
│   └── order_guard/
│       ├── main.py              # FastAPI 入口
│       ├── cli.py               # Typer CLI 入口
│       ├── config/              # Pydantic Settings
│       ├── models/              # SQLModel 模型 + API Schema
│       ├── connectors/          # 数据源 Adapter（base + mock）
│       ├── engine/              # 指标计算 + 规则管理 + LLM 分析
│       ├── alerts/              # 告警推送（base + webhook）
│       ├── scheduler/           # APScheduler 任务
│       ├── storage/             # DB 初始化 + Session
│       └── api/                 # 预留 Web UI / Bot 路由
├── specs/                       # 任务 Spec（plan + eval）
├── tests/
├── config.example.yaml
├── alembic/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env                         # 本地环境变量（不提交 Git）
└── .env.example
```
