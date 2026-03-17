# OrderGuard - Product Requirements Document

## Problem Statement
电商企业使用 ERP 系统（如 NetSuite、领星等）管理订单，但缺乏智能化的订单异常检测和告警能力。员工需要手动查看报表、人工判断异常，效率低且容易遗漏。本项目提供一个开源的 AI Agent 方案，连接 ERP 数据源，实现智能订单分析 + 自动告警推送，支持对话式查询和定时监控两种模式。

## Target Users
- 电商运营团队 — 需要实时了解订单状态、库存异常、退货趋势等
- 财务团队 — 需要监控异常交易、应收账款超期等
- 管理层 — 需要快速获取业务概览和异常汇报
- 开发者/集成商 — 需要为客户快速搭建 ERP AI 助手

## Architecture Overview

```
                    +------------------+
                    |   Web UI (配置)   |  ← Phase 3
                    +--------+---------+
                             |
+-------------+    +---------v---------+    +------------------+
| 对话入口     |    |   OrderGuard Core  |    | 定时任务调度      |
| - 企业微信Bot|───>|                   |<───| - Cron Schedule  |
| - Slack Bot  |    |  +-------------+  |    | - 异常检测触发    |
| - 飞书Bot    |    |  | LLM Agent   |  |    +------------------+
| - CLI        |    |  +------+------+  |
+-------------+    |         |          |    +------------------+
                   |  +------v------+  |    | 告警推送          |
                   |  | Data Layer  |  |───>| - 企业微信Webhook |
                   |  +------+------+  |    | - Slack Webhook   |
                   |         |          |    | - 飞书Webhook     |
                   +---------+---------+    | - 通用Webhook     |
                             |              +------------------+
                   +---------v---------+
                   | ERP 数据源         |
                   | - NetSuite MCP/API |
                   | - 领星 ERP API     |
                   | - 通用 REST API    |
                   +-------------------+
```

## Core Features

### F1: NetSuite 数据接入
**Description**: 通过 NetSuite MCP 或 REST API (SuiteTalk) 连接 NetSuite ERP，读取订单、库存、客户、财务等核心数据。
**User Story**: As a 运营人员, I want to 让系统能连接我们的 NetSuite ERP so that AI 可以读取和分析我们的业务数据。
**Acceptance Criteria**:
- [ ] Given NetSuite 凭证已配置, When 系统启动, Then 成功建立 API 连接并验证权限
- [ ] Given 连接已建立, When 查询订单数据, Then 返回正确的订单列表（支持分页、筛选、时间范围）
- [ ] Given 连接已建立, When 查询库存数据, Then 返回各 SKU 的当前库存量和仓库分布
- [ ] Given 连接已建立, When 查询客户数据, Then 返回客户信息和关联订单
- [ ] Given API 凭证无效, When 尝试连接, Then 返回清晰的错误提示和修复建议
- [ ] Given 网络异常, When API 调用失败, Then 自动重试（最多 3 次）并记录错误日志
**Priority**: High

### F2: 订单查询 Agent
**Description**: 基于 LLM 的 AI Agent，接受自然语言输入，自动调用 ERP 工具查询和分析订单数据，返回结构化的分析结果。
**User Story**: As a 运营人员, I want to 用自然语言问"最近 7 天退货率最高的 10 个 SKU" so that 不需要手写 SQL 或在 ERP 后台翻找。
**Acceptance Criteria**:
- [ ] Given 用户输入自然语言查询, When Agent 处理, Then 自动选择正确的工具和参数调用 ERP API
- [ ] Given 查询返回数据, When Agent 分析, Then 以结构化格式（表格/列表）展示结果
- [ ] Given 查询需要多步操作, When Agent 规划, Then 能链式调用多个工具完成复杂查询
- [ ] Given 用户追问, When Agent 响应, Then 保持上下文理解之前的对话内容
- [ ] Given 查询无结果, When Agent 响应, Then 提示可能的原因和替代查询建议
**Priority**: High

### F3: 异常检测引擎
**Description**: 基于规则 + LLM 的混合异常检测。支持预定义规则（阈值触发）和 LLM 智能判断（趋势分析、模式识别）。
**User Story**: As a 运营经理, I want to 系统自动检测订单异常（如退货率飙升、库存不足、异常大单）so that 我能及时处理问题。
**Acceptance Criteria**:
- [ ] Given 预定义规则（如退货率 > 10%）, When 检测到数据超过阈值, Then 生成异常告警
- [ ] Given 多条规则, When 同时触发, Then 按严重程度排序（critical/warning/info）
- [ ] Given 历史数据, When LLM 分析趋势, Then 能发现非显式规则的异常模式（如渐进式下降）
- [ ] Given 用户自定义规则, When 通过配置文件定义, Then 系统加载并执行自定义检测逻辑
- [ ] Given 已触发的告警, When 相同异常持续, Then 不重复推送（支持配置静默期）
**Priority**: High

### F4: 告警推送系统
**Description**: 将异常检测结果推送到多个渠道。支持企业微信 Webhook、Slack Webhook、飞书 Webhook、通用 Webhook。
**User Story**: As a 运营人员, I want to 在企业微信群里收到库存告警 so that 不需要时刻盯着 ERP 后台。
**Acceptance Criteria**:
- [ ] Given 企业微信 Webhook 已配置, When 触发告警, Then 消息成功推送到企业微信群
- [ ] Given Slack Webhook 已配置, When 触发告警, Then 消息成功推送到 Slack 频道
- [ ] Given 飞书 Webhook 已配置, When 触发告警, Then 消息成功推送到飞书群
- [ ] Given 通用 Webhook URL, When 触发告警, Then 以标准 JSON 格式 POST 到目标 URL
- [ ] Given 多渠道已配置, When 触发告警, Then 同时推送到所有已配置渠道
- [ ] Given 推送失败, When 网络异常, Then 重试并记录失败日志
- [ ] Given 告警消息, When 格式化, Then 包含告警级别、摘要、详情、时间、建议操作
**Priority**: High

### F5: 定时任务调度
**Description**: 支持 cron 表达式配置定时检测任务。定时拉取 ERP 数据 → 执行异常检测 → 触发告警推送。
**User Story**: As a 运营经理, I want to 每天早上 9 点自动检查昨天的订单异常 so that 上班就能看到汇报。
**Acceptance Criteria**:
- [ ] Given cron 表达式已配置, When 到达触发时间, Then 自动执行检测任务
- [ ] Given 任务执行中, When 检测完成, Then 根据结果决定是否触发告警
- [ ] Given 多个定时任务, When 配置文件定义, Then 各任务独立运行互不影响
- [ ] Given 任务执行失败, When 报错, Then 记录错误日志并推送任务失败通知
- [ ] Given 任务执行成功但无异常, When 完成, Then 可选配置"无异常也推送汇报"
**Priority**: High

### F6: 对话机器人接入
**Description**: 将 OrderGuard Agent 接入企业微信应用机器人、Slack Bot、飞书Bot，员工可以直接在聊天窗口与 Agent 对话查询。
**User Story**: As a 员工, I want to 在企业微信里 @机器人 问"今天有没有异常订单" so that 不需要登录额外系统。
**Acceptance Criteria**:
- [ ] Given 企业微信应用机器人已配置, When 员工发送消息, Then Agent 接收并回复分析结果
- [ ] Given Slack Bot 已配置, When 员工 mention Bot, Then Agent 接收并回复
- [ ] Given 飞书Bot 已配置, When 员工发送消息, Then Agent 接收并回复
- [ ] Given Bot 收到消息, When 处理, Then 调用 F2 的 Agent 能力完成查询
- [ ] Given 多轮对话, When 员工追问, Then Bot 保持上下文连续性
- [ ] Given 非授权用户, When 尝试查询, Then 拒绝并提示无权限
**Priority**: Medium

### F7: 配置管理
**Description**: 通过 YAML 配置文件管理所有设置：ERP 连接、告警规则、推送渠道、定时任务、Bot 接入等。支持环境变量覆盖敏感配置。
**User Story**: As a 开发者, I want to 通过一个配置文件管理所有设置 so that 部署和修改都很方便。
**Acceptance Criteria**:
- [ ] Given 配置文件模板, When 用户首次使用, Then 可以快速复制并修改配置
- [ ] Given YAML 配置, When 系统启动, Then 正确解析所有配置项
- [ ] Given 环境变量, When 与配置文件冲突, Then 环境变量优先
- [ ] Given 敏感信息（API Key）, When 配置, Then 支持环境变量引用而非明文
- [ ] Given 配置错误, When 系统启动, Then 给出明确的校验错误提示
- [ ] Given 配置变更, When 热重载（可选）, Then 无需重启即可生效
**Priority**: High

### F8: Web 管理界面
**Description**: 提供用户友好的 Web UI，用于可视化配置管理（数据源、告警规则、推送渠道、Bot 设置）、查看告警历史、手动触发检测、查看 Agent 对话日志。
**User Story**: As a 运营经理, I want to 在网页上配置告警规则和查看历史告警 so that 不需要手动编辑配置文件。
**Acceptance Criteria**:
- [ ] Given 用户访问 Web UI, When 首页加载, Then 展示 Dashboard（告警概览、最近异常、系统状态）
- [ ] Given 数据源配置页, When 用户填写 NetSuite 凭证, Then 可以测试连接并保存
- [ ] Given 告警规则页, When 用户添加/编辑/删除规则, Then 规则实时生效
- [ ] Given 推送渠道页, When 用户配置 Webhook/Bot, Then 可以发送测试消息验证
- [ ] Given 告警历史页, When 用户查看, Then 展示告警时间线、详情、处理状态
- [ ] Given 手动触发, When 用户点击"立即检测", Then 执行一次完整检测并展示结果
- [ ] Given 界面设计, When 用户使用, Then UI 风格现代、响应式、操作直观（参考 Grafana/Datadog 风格）
**Priority**: Medium

## Tech Stack

| Layer | Technology | Reason |
|-------|-----------|--------|
| Language | Python 3.11+ | 生态最好，LLM SDK 支持完善 |
| LLM | Claude API (默认) / OpenAI API (可选) | 可切换的 LLM 后端 |
| Agent Framework | 轻量自研或 Claude Agent SDK | 避免过度依赖第三方框架 |
| ERP 接入 | MCP Protocol + REST API Adapter | 标准化数据接入 |
| 配置 | YAML + 环境变量 | 简洁易维护 |
| 定时任务 | APScheduler | 轻量级 Python 调度器 |
| Web UI | FastAPI + React (或 Next.js) | 前后端分离，API 优先 |
| 部署 | Docker + Docker Compose | 一键部署 |
| 数据库 | SQLite (默认) / PostgreSQL (可选) | 存储告警历史、配置、对话日志 |

## Constraints
- 必须支持 Docker 一键部署和本地 CLI 两种运行方式
- 敏感信息（API Key、Token）不得明文存储在配置文件中，必须支持环境变量
- LLM 调用需要有成本控制机制（token 限制、缓存策略）
- 告警推送不得重复轰炸（必须有静默期/去重机制）
- 开源协议：MIT License

## Non-Functional Requirements
- 单次 Agent 查询响应时间 < 30 秒
- 定时任务执行可靠性 > 99%（失败自动重试 + 通知）
- 支持至少 3 个并发 Bot 会话
- 配置变更不需要重启服务（热重载或快速重启）
- 日志格式标准化，支持 JSON 输出便于日志收集

## Milestones

### Milestone 1: Core MVP
- **Target**: 能连 NetSuite、对话查询、基本告警
- **Features**: F1, F2, F4 (Webhook only), F7
- **Deliverable**: CLI 工具 + Docker 镜像，支持连接 NetSuite 查询订单，配置 Webhook 推送告警
- **Acceptance**: 用户可以通过 CLI 对话查询订单数据，手动触发检测并推送到企业微信 Webhook

### Milestone 2: 智能检测 + 定时任务
- **Target**: 自动化异常检测和定时推送
- **Features**: F3, F5
- **Deliverable**: 规则引擎 + LLM 混合检测，cron 定时任务
- **Acceptance**: 系统按配置的规则和时间表自动检测异常并推送告警

### Milestone 3: Bot 接入
- **Target**: 企业微信/Slack/飞书 Bot 双向对话
- **Features**: F6
- **Deliverable**: Bot 适配器，支持员工在聊天工具中直接与 Agent 对话
- **Acceptance**: 员工在企业微信中 @机器人 可以查询订单和接收告警

### Milestone 4: Web 管理界面
- **Target**: 用户友好的可视化配置和监控界面
- **Features**: F8
- **Deliverable**: Web UI Dashboard
- **Acceptance**: 用户可以通过网页配置所有设置、查看告警历史、手动触发检测
