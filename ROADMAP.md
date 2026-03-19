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

## v4: AI 经营助手改版（产品重新定位）

> 产品定位从"数据监控中台"升级为"AI 经营助手"
> 详细方案见 `.local/product-plan-v4.md`

### 架构基础（第一批）
- [x] **N1: 统一数据访问层** — 固定工具集（list_datasources / get_schema / query），内部按数据源类型路由（SQL / MCP / API Adapter），工具数量不随数据源增长
- [x] **N8: 业务知识注入** — business_context 配置 + system prompt 注入 + 对话更新
- [x] **N5: 会话管理** — /new /list /switch /delete，会话持久化（DB），最近 N 轮 context 截断

### 产品差异化（第二批）
- [x] **N4: 自然语言配规则** — 用户对话描述监控需求 → LLM 调用 get_schema 理解数据 → 生成结构化规则 → 用户确认生效

### 增值功能（第三批）
- [x] **N6: 定时报告** — 日报/周报自动生成推送，定时收集全局数据 + LLM 生成摘要
- [x] **N7: CSV 辅助输入** — 上传 SKU 列表等作为查询筛选条件，结合已接入数据源分析（非独立数据源）

### 任务依赖关系
```
N1(统一数据访问层) → N4(自然语言配规则)
N1 → N6(定时报告)
N1 → N7(CSV 辅助输入)
N5(会话管理) → N4
N8(业务知识注入) 无依赖，可并行
```

### 统一 Agent 改造（第四批）
- [x] **N9: 规则工具** — 5 个规则管理工具（list/create/update/delete/test_rule），cron 校验，动态调度注册
- [x] **N10: 上下文工具** — 3 个业务知识工具（list/add/delete_context），分类 + 过期机制，system prompt 注入
- [x] **N11: 告警历史工具** — 1 个工具（list_alerts），按规则/时间/限制过滤，severity 分布
- [x] **N12: 统一 Agent** — 12 工具 + 写拦截 + AgentResult + run_unified()，保留 run() 向后兼容
- [x] **N13: 飞书重构** — 删除意图分类，简化为：消息→检查 pending→调 Agent→回复，删除 rule_agent.py
- [x] **N14: 会话超时** — 30 分钟不活跃自动新建会话，session_timeout_minutes 可配置，0=禁用

### 任务依赖关系
```
N9(规则工具) + N10(上下文工具) + N11(告警历史) → N12(统一Agent)
N12 → N13(飞书重构)
N5(会话管理) + N13 → N14(会话超时)
```

## v5: 产品完整度（对话闭环 + 可观测性）

> 产品定位：对话即配置。所有管理操作通过 Bot 对话完成，不依赖 Web UI。
> 本版本聚焦：让已有功能形成闭环，补齐可观测性短板。

### 第一批（核心闭环 + 可观测性）
- [x] **P1: 告警闭环** — Alert 新增 resolution 状态（handled/ignored/false_positive），2 个工具（handle_alert/get_alert_stats），对话标记处理 + 统计
- [x] **P2: LLM 用量追踪** — LLMUsageLog 表自动记录每次 Agent 调用，1 个工具（get_usage_stats），按规则/触发类型/模型/天分组统计 + 成本估算
- [x] **P4: 数据源健康监控** — 定时探活（APScheduler）+ 连续失败自动推送告警 + 恢复通知，1 个工具（check_health），24h 可用率

### 第二批（体验优化）
- [x] **P3: 规则效果评估** — 增强 list_rules 返回值（触发次数/误报率/执行成功率），1 个工具（get_rule_stats），智能 hint 引导规则调优
- [x] **P6: 报告模板定制** — ReportConfig 新增 sections/kpis，2 个工具（manage_report/preview_report），对话定制报告章节和指标

### 任务依赖关系
```
P1(告警闭环) → P3(规则效果评估)
P2(LLM用量), P4(数据源健康) 无依赖，可并行
P6(报告定制) 无依赖，可独立
```

### 工具变化
```
v4 现有 12 工具 → v5 新增 7 工具 = 共 19 工具

新增：
  handle_alert      — 标记告警处理状态
  get_alert_stats   — 告警统计面板
  get_usage_stats   — LLM 用量和成本统计
  check_health      — 数据源健康检查
  get_rule_stats    — 单条规则效果详情
  manage_report     — 报告配置管理
  preview_report    — 报告预览
```

## v6: 生产级 Agent 测试（E5-E8）

> 目标：测试 Agent 长时间运行的正确性和稳定性，通过 LangWatch trace 驱动持续优化。
> 详细方案见 `specs/agent-testing-v2/plan.md`

### Investigation 场景层（E5-E6）
- [ ] **E5: Investigation 场景定义** — 5个业务调查场景（goal+persona+guidelines格式），4个真实人设，受控 ground_truth 数据库，替换旧的预设消息方案
- [ ] **E6: Session Scorer** — 5维度 LLM-as-Judge（goal_achieved / data_accuracy / actionable / no_hallucination / conversation_quality），会话级评分，推送 LangWatch

### 长时间稳定性层（E7-E8）
- [ ] **E7: Soak Test 框架** — 同场景跑 N 次，统计 pass_rate / token / latency / 失败维度分布，pass_rate < 85% 告警
- [ ] **E8: 时序一致性测试** — 30轮对话 + 时序探针，检测 Agent 在长对话中是否自相矛盾

### 任务依赖关系
```
E5(场景定义) → E6(Session Scorer) → E7(Soak Test)
                                  → E8(时序一致性)
```

## v7: 外部知识源集成（待定）
- [ ] MCP 连接飞书文档 — 自动同步运营维护的飞书表格/文档中的业务上下文
- [ ] MCP 连接 Google Sheet — 自动读取运营维护的促销日历、备货计划等表格
- [ ] 业务上下文自动同步 — 定时从外部文档源拉取，与本地 business_context 合并

## 后续版本（待定）
- [ ] 企业微信 Bot 双向对话 — 复用统一 Agent，企微应用消息回调
- [ ] Web 管理界面 — Dashboard、规则可视化、告警历史
- [ ] 自动执行（调价/补货/广告调整）— 需完善审批流和回滚机制
- [ ] 竞品监控 — 需确定数据来源
- [ ] 角色权限 / 审批流
- [ ] 第三方 MCP Server 安全规范检测
- [ ] 多 Agent 协作（跨数据源联合分析）

---
Legend: [ ] Todo | [-] In Progress | [x] Completed
