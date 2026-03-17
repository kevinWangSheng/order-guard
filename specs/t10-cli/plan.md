# T10: CLI 入口

## Context
提供命令行工具，支持手动触发检测、查看系统状态、管理规则等。使用 Typer 构建，和 FastAPI 风格一致。

## Scope
### In Scope
- `order-guard run` — 手动执行一次完整检测流程
- `order-guard serve` — 启动 FastAPI 服务（含定时任务）
- `order-guard rules list` — 列出所有规则
- `order-guard rules show <rule-id>` — 查看规则详情
- `order-guard history` — 查看最近告警历史
- `order-guard status` — 查看系统状态（数据源连接、任务状态等）

### Not In Scope
- 交互式对话模式（后续版本）
- 规则编辑命令（后续版本，通过 YAML 或 Web UI）

## Design
### 命令结构
```
order-guard
├── version           # 版本号
├── serve             # 启动服务（FastAPI + Scheduler）
├── run               # 手动执行一次检测
│   ├── --rule-id     # 指定规则（可选，默认执行所有启用规则）
│   └── --dry-run     # 只分析不推送（调试用）
├── rules
│   ├── list          # 列出规则
│   └── show <id>     # 规则详情
├── history           # 告警历史
│   ├── --limit       # 条数（默认 20）
│   └── --severity    # 按级别筛选
└── status            # 系统状态
```

### Key Decisions
- Typer 构建，自动生成 --help
- `run` 命令复用 T09 的 run_detection_job()，只是触发方式不同
- `--dry-run` 模式只输出分析结果到终端，不推送 Webhook，方便调试规则

## Dependencies
- T9（调度层）— run 命令复用任务编排逻辑
- T3（存储层）— history 和 rules 命令查询数据库
- T2（配置管理）— serve 命令加载配置

## Tasks
- [ ] T10.1: 实现 `serve` 命令 — 启动 FastAPI + APScheduler
- [ ] T10.2: 实现 `run` 命令 — 手动触发检测（支持 --rule-id 和 --dry-run）
- [ ] T10.3: 实现 `rules list / show` 命令
- [ ] T10.4: 实现 `history` 命令（查询 alerts 表）
- [ ] T10.5: 实现 `status` 命令（数据源健康检查 + 任务状态）
