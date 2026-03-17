# OrderGuard 架构设计文档

> 状态：设计中 | 最后更新：2026-03-07

## 产品定位

企业数据智能监控中台。汇聚公司内部/外部多个数据源，通过 AI（LLM）按业务规则进行分析判断，异常时自动告警推送，同时支持员工通过聊天工具实时查询。

## 两种核心模式

### 模式一：自动巡检（定时推送）
```
定时触发 → 读取业务规则（Prompt） → 拉取多源数据 → 指标预计算 → LLM 分析判断 → 告警推送
```
- 业务人员用自然语言写规则
- 系统定时执行，异常直接推送到飞书/企业微信群
- 业务人员可随时修改 Prompt 优化规则，无需写代码

### 模式二：对话查询（交互式）
```
员工提问（@机器人） → AI 查数据 + 分析 → 返回结构化结果
```
- 员工在飞书/企业微信群里直接 @机器人 提问
- AI 自动查 ERP 数据并分析回复

---

## 整体架构（五层）

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Layer 1 — 触达层（双向）                                     │
│                                                             │
│  输出（告警推送）：飞书 Webhook / 企业微信 Webhook / Slack /    │
│                   通用 Webhook                               │
│  输入（对话查询）：飞书 Bot / 企业微信 Bot / Slack Bot          │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 2 — 调度层                                            │
│                                                             │
│  - 定时任务（Cron 表达式配置）                                 │
│  - 事件触发（Bot 收到消息）                                    │
│  - 手动触发（CLI / API / Web UI）                             │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 3 — 核心引擎层                                        │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐     │
│  │ 数据对接     │  │ 指标计算     │  │ 规则管理         │     │
│  │             │  │             │  │                 │     │
│  │ - MCP 协议  │  │ - 数据预处理 │  │ - Prompt 模板   │     │
│  │ - REST API  │  │ - 指标聚合   │  │ - 规则配置      │     │
│  │ - 自定义    │  │ - 结构化摘要 │  │ - 版本管理      │     │
│  │   Adapter   │  │   生成      │  │                 │     │
│  └─────────────┘  └─────────────┘  └─────────────────┘     │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 4 — AI 层                                            │
│                                                             │
│  - LLM 调用（Claude / OpenAI / 可切换）                      │
│  - 结构化输出（JSON Schema 约束）                             │
│  - Token 成本控制                                            │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 5 — 存储层                                            │
│                                                             │
│  - 规则配置持久化                                             │
│  - 告警历史记录                                               │
│  - 任务执行日志                                               │
│  - 对话记录（可选）                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 各层职责说明

### Layer 1 — 触达层
系统与外部用户的交互入口和出口，**双向通道**。

| 方向 | 渠道 | 协议 |
|------|------|------|
| 推送（出） | 飞书群 | Webhook POST |
| 推送（出） | 企业微信群 | Webhook POST |
| 推送（出） | Slack 频道 | Webhook POST |
| 推送（出） | 任意系统 | 通用 Webhook（标准 JSON） |
| 对话（进） | 飞书 Bot | 飞书 Open API / Event Callback |
| 对话（进） | 企业微信 Bot | 企业微信回调接口 |
| 对话（进） | Slack Bot | Slack Events API |

### Layer 2 — 调度层
控制"什么时候执行分析"。

| 触发方式 | 说明 |
|---------|------|
| 定时任务 | Cron 表达式，如每天 9:00 执行库存检查 |
| 事件触发 | Bot 收到员工消息时触发查询 |
| 手动触发 | 通过 CLI 命令 / API 接口 / Web UI 按钮 |

### Layer 3 — 核心引擎层
系统的核心价值所在，分三个子模块：

**数据对接**：通过 MCP 协议或 REST API 连接各类数据源。可扩展 Adapter 接入新数据源。
- 已知数据源：NetSuite（订单/库存/客户/财务/历史销量）、领星 ERP（物流数据）、Google Drive（规则文档）、其他 API（Review/Ranking/Listing）

**指标计算**：对原始数据做预处理，生成结构化摘要。代码计算保证准确性，LLM 不负责算数。
- 例如：日均销量、库存可售天数、退货率、同比环比变化等

**规则管理**：管理业务规则（Prompt 模板）。业务人员用自然语言编写，系统存储和版本管理。

### Layer 4 — AI 层
调用 LLM 做智能分析判断。接收"结构化数据摘要 + 业务规则 Prompt"，输出结构化的分析结果。

关键设计原则：
- **LLM 只做判断和建议，不做计算**——数字由 Layer 3 的指标计算模块算好
- **输出格式约束**——通过 JSON Schema 约束 LLM 输出，保证下游可靠解析
- **LLM 可切换**——支持 Claude / OpenAI 等多个 LLM 后端
- **成本控制**——Token 限制、缓存策略、避免重复调用

### Layer 5 — 存储层
持久化系统运行数据。

| 存储内容 | 用途 |
|---------|------|
| 规则配置 | 业务规则 Prompt、数据源配置、推送渠道配置 |
| 告警历史 | 每次告警的完整记录，便于回溯和统计 |
| 执行日志 | 定时任务执行记录、成功/失败/耗时 |
| 对话记录 | Bot 对话历史（可选，用于上下文保持） |

---

## 数据流

### 自动巡检流程
```
1. 调度层：Cron 触发任务
2. 核心引擎：读取该任务关联的业务规则（Prompt）
3. 核心引擎：通过数据对接模块拉取所需数据（MCP/API）
4. 核心引擎：指标计算，生成结构化数据摘要
5. AI 层：数据摘要 + 规则 Prompt → LLM → 结构化分析结果（JSON）
6. 核心引擎：解析 LLM 输出，判断是否需要告警
7. 触达层：推送告警到配置的渠道（飞书/企业微信等）
8. 存储层：记录告警历史和执行日志
```

### 对话查询流程
```
1. 触达层：Bot 收到员工消息
2. 调度层：事件触发，创建查询任务
3. 核心引擎：解析用户意图，确定需要哪些数据
4. 核心引擎：拉取数据 → 指标计算 → 生成摘要
5. AI 层：用户问题 + 数据摘要 → LLM → 分析回复
6. 触达层：Bot 回复员工
7. 存储层：记录对话（可选）
```

---

## 技术栈（已确认）

| 层 | 技术 | 说明 |
|---|------|------|
| 语言 | Python 3.11+ | LLM 生态最好，SDK 全 |
| Web 框架 | FastAPI | Bot Webhook 回调、Web UI API、统一服务入口 |
| ORM | SQLModel | Pydantic + SQLAlchemy，FastAPI 同作者 |
| 数据库 | SQLite 默认 / PostgreSQL 可选 | DB_URL 配置切换 |
| 迁移 | Alembic | 行业标准，支持 SQLite batch 模式 |
| LLM 调用 | LiteLLM | 统一接口调用 100+ LLM，一行配置切换模型 |
| 定时任务 | APScheduler 4.x | 轻量 async，和 FastAPI 共存 |
| 配置 | Pydantic Settings + YAML | 类型安全，环境变量自动覆盖 |
| CLI | Typer | FastAPI 同作者，风格一致 |
| HTTP 客户端 | httpx | async，Webhook 推送和 API 调用 |
| 日志 | loguru | 简洁，支持 JSON 格式输出 |
| 包管理 | uv + pyproject.toml | 2026 主流 |
| 容器 | Docker + Docker Compose | 一键部署 |

### 后续版本可选技术（已调研，暂不引入）
| 技术 | 用途 | 引入时机 |
|------|------|---------|
| LiteLLM（多模型切换） | 配置切换 Claude/Gemini/本地模型 | v2 |
| Any-Agent（Mozilla AI） | Agent SDK 抽象层，切换不同 Agent 框架 | v4 |
| Pydantic AI / OpenAI Agents SDK | Agent 框架 | v4 |
| pgvector | 向量搜索 | 有语义搜索需求时 |

---

## 项目目录结构

```
order-guard/
├── src/
│   └── order_guard/
│       ├── __init__.py
│       ├── main.py              # FastAPI 应用入口
│       ├── cli.py               # Typer CLI 入口
│       ├── config/
│       │   ├── settings.py      # Pydantic Settings
│       │   └── constants.py
│       ├── models/
│       │   ├── db.py            # SQLModel 表定义
│       │   └── schemas.py       # API/LLM 输入输出 Schema
│       ├── connectors/
│       │   ├── base.py          # Adapter 抽象接口
│       │   └── mock.py          # Mock 数据源
│       ├── engine/
│       │   ├── metrics.py       # 指标计算
│       │   ├── rules.py         # 规则管理
│       │   └── analyzer.py      # LLM 分析调用
│       ├── alerts/
│       │   ├── base.py          # 推送抽象接口
│       │   └── webhook.py       # 通用 Webhook
│       ├── scheduler/
│       │   └── jobs.py          # APScheduler 任务定义
│       ├── storage/
│       │   └── database.py      # DB 初始化、Session 管理
│       └── api/                 # 预留，后续 Web UI / Bot 路由
│           └── __init__.py
├── specs/                       # 任务 Spec 文档
├── tests/
├── config.example.yaml
├── alembic/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 已确认事项

- [x] 规则管理：MVP 使用 Prompt 模板（自然语言规则），从 YAML 加载 + DB 持久化
- [x] 数据源：Adapter 插件机制，MVP 先用 Mock 数据源，后续按接口规范接入真实 ERP
- [x] 技术选型：Python + FastAPI + SQLModel + LiteLLM + APScheduler
- [x] 数据库：SQLModel 抽象，SQLite 默认可切 PostgreSQL
- [x] LLM 输出校验：MVP 做基础 JSON Schema 校验 + 重试，高级兜底后续版本

## 待定事项（后续版本讨论）

- [ ] 多租户支持（暂不做）
- [ ] 权限控制粒度（暂不做）
- [ ] 规则版本管理
- [ ] 告警静默期/去重机制
- [ ] Agent SDK 集成方案
