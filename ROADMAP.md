# Roadmap

## MVP v1: 端到端巡检流程

目标：架构跑通，Mock 数据源 → 指标计算 → LLM 分析 → Webhook 推送，支持定时任务和 CLI 手动触发。

### 基础层
- [x] **T01: 项目骨架** — Python 包结构、依赖、FastAPI/CLI 入口、Docker
- [x] **T02: 配置管理** — Pydantic Settings + YAML + 环境变量覆盖
- [x] **T03: 存储层** — SQLModel + SQLite + Alembic 迁移

### 核心引擎
- [x] **T04: 数据对接** — Adapter 接口 + Mock 数据源
- [ ] **T05: 指标计算** — 数据预处理 + 结构化摘要生成
- [ ] **T06: AI 层** — LiteLLM 封装 + Prompt 组装 + JSON Schema 输出
- [ ] **T07: 规则管理** — Prompt 模板加载 + CRUD + 示例规则

### 集成层
- [ ] **T08: 告警推送** — 通用 Webhook + 重试 + 推送记录
- [ ] **T09: 调度层** — APScheduler + 完整检测流程编排
- [ ] **T10: CLI 入口** — serve / run / rules / history / status 命令

### 任务依赖关系
```
T01 → T02 → T04 → T05 → T06
T01 → T03 → T07（T02也依赖）
              T08（T03, T06 依赖）
              T09（T02-T08 全部依赖）
              T10（T09 依赖）
```

## 后续版本（待排期）

### v2: 真实数据源 + 飞书/企业微信
- [ ] NetSuite Connector（MCP/REST API）
- [ ] 飞书 Webhook 推送（特定消息格式）
- [ ] 企业微信 Webhook 推送
- [ ] 告警静默期/去重

### v3: Bot 对话接入
- [ ] 飞书 Bot 双向对话
- [ ] 企业微信 Bot 双向对话
- [ ] 对话上下文管理
- [ ] 权限控制

### v4: 多 LLM + Agent 增强
- [ ] 多 LLM 切换（LiteLLM 已内置）
- [ ] Agent SDK 集成（OpenAI Agents SDK / Pydantic AI）
- [ ] 规则版本管理

### v5: Web 管理界面
- [ ] Dashboard
- [ ] 规则可视化配置
- [ ] 告警历史查看
- [ ] 手动触发检测

---
Legend: [ ] Todo | [-] In Progress | [x] Completed
