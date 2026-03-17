# T01: 项目骨架

## Context
搭建 OrderGuard 项目的基础结构，包括 Python 包、依赖管理、目录规范、Docker 基础配置。所有后续任务都依赖此任务。

## Scope
### In Scope
- pyproject.toml（uv 包管理）
- src/order_guard/ 目录结构
- 基础依赖安装（FastAPI, SQLModel, LiteLLM, APScheduler, httpx, typer, loguru）
- Dockerfile + docker-compose.yml 基础版
- .env.example
- 基础 main.py（FastAPI 空应用能启动）
- 基础 cli.py（Typer 空命令能执行）
- loguru 日志配置

### Not In Scope
- 具体业务逻辑实现
- 测试框架配置（跟随各模块逐步添加）

## Design
### 目录结构
```
order-guard/
├── src/
│   └── order_guard/
│       ├── __init__.py
│       ├── main.py              # FastAPI 入口
│       ├── cli.py               # Typer CLI 入口
│       ├── config/
│       ├── models/
│       ├── connectors/
│       ├── engine/
│       ├── alerts/
│       ├── scheduler/
│       ├── storage/
│       └── api/
├── specs/
├── tests/
├── config.example.yaml
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

### Key Decisions
- 使用 uv 而非 pip/poetry，2026 主流包管理工具
- src layout（src/order_guard/）而非 flat layout，避免导入冲突
- FastAPI 作为统一 Web 框架，后续 Bot 回调和 Web UI 都在此基础上扩展

## Dependencies
无，这是第一个任务

## Tasks
- [ ] T1.1: 创建 pyproject.toml，定义项目元信息和依赖
- [ ] T1.2: 创建 src/order_guard/ 完整目录结构（含 __init__.py）
- [ ] T1.3: 实现 main.py — FastAPI 空应用 + 健康检查端点
- [ ] T1.4: 实现 cli.py — Typer 基础命令（version, health）
- [ ] T1.5: 配置 loguru 日志（控制台 + JSON 文件输出）
- [ ] T1.6: 创建 Dockerfile + docker-compose.yml
- [ ] T1.7: 创建 .env.example
