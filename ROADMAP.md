# Roadmap

## MVP v1: 端到端巡检流程

目标：架构跑通，Mock 数据源 → 指标计算 → LLM 分析 → Webhook 推送，支持定时任务和 CLI 手动触发。

### 基础层
- [x] **T01: 项目骨架** — Python 包结构、依赖、FastAPI/CLI 入口、Docker
- [x] **T02: 配置管理** — Pydantic Settings + YAML + 环境变量覆盖
- [x] **T03: 存储层** — SQLModel + SQLite + Alembic 迁移

### 核心引擎
- [x] **T04: 数据对接** — Adapter 接口 + Mock 数据源
- [x] **T05: 指标计算** — 数据预处理 + 结构化摘要生成
- [x] **T06: AI 层** — LiteLLM 封装 + Prompt 组装 + JSON Schema 输出
- [x] **T07: 规则管理** — Prompt 模板加载 + CRUD + 示例规则

### 集成层
- [x] **T08: 告警推送** — 通用 Webhook + 重试 + 推送记录
- [x] **T09: 调度层** — APScheduler + 完整检测流程编排
- [x] **T10: CLI 入口** — serve / run / rules / history / status 命令

### 任务依赖关系
```
T01 → T02 → T04 → T05 → T06
T01 → T03 → T07（T02也依赖）
              T08（T03, T06 依赖）
              T09（T02-T08 全部依赖）
              T10（T09 依赖）
```

## v2: MCP 数据源 + 体验优化

### 数据源接入（已完成）
- [x] **T11: CSV/Excel Connector** — 支持 CSV/Excel 文件导入，字段映射，自动编码检测

### 告警优化（已完成）
- [x] **T12: 飞书消息卡片** — 交互式卡片格式，severity 颜色标识，多告警合并展示
- [x] **T13: 告警合并推送** — send_batch 一次推送，单条消息包含所有告警
- [x] **T14: 告警静默期/去重** — silence_minutes 配置，同规则同级别同标题在窗口内不重复推送

### MCP 数据源接入（已完成）
- [x] **T16: MCP Client 基础层** — MCP SDK 集成，stdio/SSE 传输，多 Server 连接管理，工具发现
- [x] **T17: AI Agent 工具调用** — LLM function calling + Agent 循环（思考→工具调用→结果→分析）
- [x] **T18: MCP Connector + Pipeline 适配** — 规则绑定 MCP Server，pipeline 分支（传统 vs MCP Agent）
- [x] **T19: 端到端验证（SQLite MCP）** — 测试数据库 + SQLite MCP Server 全链路验证

### 任务依赖关系
```
T02 → T16(MCP Client) → T17(AI Agent) → T18(Pipeline) → T19(E2E 验证)
T06 ────────────────────→ T17
T09 ────────────────────────────────────→ T18
```

## v3: 生产数据源 + 飞书 Bot

### 数据库安全接入
- [x] **T20: DBHub 集成** — DBHub 作为数据库 MCP Server，配置化支持 MySQL/PG/SQLite，readonly + timeout + row limit
- [x] **T21: Schema 防幻觉** — schema 自动注入 Agent context，敏感表/字段黑名单，SQL 字段校验（SQLGlot）
- [x] **T22: 热冷数据 + 查询优化** — 规则时间窗口，大表分步查询策略，冷数据表标记，查询缓存
- [x] **T23: 查询审计** — query_logs 记录所有 AI 执行的 SQL，CLI 查看，异常查询检测

### 飞书 Bot 对话
- [x] **T24: 飞书 Bot 对话** — Event 回调接入，@机器人查数据，多轮对话，权限控制

### 架构清理
- [x] **T25: 清理传统数据流** — 移除 v1 传统流程（MetricsEngine / SummaryBuilder / 固定 Connector），全面切换 MCP Agent

### 任务依赖关系
```
T16 → T20(DBHub) → T21(Schema 防幻觉) → T22(热冷数据)
T17 ──────────────→ T21
T20 → T23(查询审计)
T20 + T12 → T24(飞书 Bot)
T20-T24 全部完成 → T25(清理传统数据流)
```

## v4: MCP 规范检测 + Agent 增强
- [ ] 第三方 MCP Server 安全规范检测（不规范提醒）
- [ ] 多 Agent 协作（跨数据源联合分析）
- [ ] Agent SDK 集成（OpenAI Agents SDK / Pydantic AI）
- [ ] 规则版本管理

## v5: Web 管理界面
- [ ] Dashboard
- [ ] 规则可视化配置
- [ ] 告警历史查看
- [ ] 手动触发检测

---
Legend: [ ] Todo | [-] In Progress | [x] Completed
