# N6: 定时报告

## Context
当前系统只在规则触发异常时推送告警，缺少"没有异常也推送汇报"的能力。运营和管理层需要每日/每周经营摘要，了解整体经营状况，不只是异常。

## Scope

### In Scope
- 定时报告配置（YAML）
- 报告生成：Agent 自动查全局数据 → LLM 生成经营摘要
- 报告推送：飞书/企微 Webhook
- 内置默认报告模板（每日经营日报）
- 自然语言创建报告任务（复用 N4 的对话配规则能力）

### Not In Scope
- 报告可视化（图表、PDF 导出）
- 报告审批流
- 报告订阅管理（谁收谁不收）

## Design

### 配置
```yaml
reports:
  - name: "每日经营日报"
    schedule: "0 9 * * *"           # 每天 9:00
    mcp_server: "erp-mysql"          # 数据源
    focus: |
      请生成今日经营日报，包含：
      1. 昨日销售概况（总销量、总GMV、同比变化）
      2. TOP5 热销 SKU 和 TOP5 滞销 SKU
      3. 库存预警（可售天数 < 7 天的 SKU）
      4. 异常告警汇总（昨日触发的告警数量和类型）
      5. 需要关注的风险点
    channels: ["default"]            # 推送渠道（default = 全局配置的渠道）

  - name: "每周经营周报"
    schedule: "0 9 * * 1"           # 每周一 9:00
    mcp_server: "erp-mysql"
    focus: |
      请生成本周经营周报，包含：
      1. 本周 vs 上周核心指标对比
      2. SKU 表现排名变化
      3. 本周告警统计和处理情况
      4. 下周需要关注的事项
    channels: ["default"]
```

### 报告生成流程
```
定时触发
    ↓
加载报告配置（focus prompt）
    ↓
Agent 初始化（带 business_context + schema）
    ↓
Agent 自主查询相关数据（多轮 Tool Call）
    ↓
LLM 生成经营摘要（Markdown 格式）
    ↓
格式化为飞书卡片 / 企微 Markdown
    ↓
推送到配置的渠道
    ↓
记录到 DB（report_history）
```

### 报告与规则的区别
| | 规则（AlertRule） | 报告（Report） |
|---|---|---|
| 触发条件 | 异常时才告警 | 固定时间必定推送 |
| 输出 | 告警（severity + 建议） | 经营摘要（全局概览） |
| prompt | 聚焦单一问题 | 覆盖多个维度 |
| 存储 | alerts 表 | report_history 表 |

### DB 模型
```python
class ReportConfig(SQLModel, table=True):
    __tablename__ = "report_configs"

    id: str = Field(primary_key=True)
    name: str
    schedule: str                    # cron 表达式
    mcp_server: str
    focus: str                       # 报告内容要求 prompt
    channels: str = "default"        # JSON array
    enabled: bool = True
    created_at: datetime
    updated_at: datetime

class ReportHistory(SQLModel, table=True):
    __tablename__ = "report_history"

    id: str = Field(primary_key=True)
    report_id: str = Field(foreign_key="report_configs.id")
    content: str                     # LLM 生成的报告内容
    status: str                      # "success" | "failed"
    token_usage: int = 0
    duration_ms: int = 0
    created_at: datetime
```

### 报告推送格式
飞书卡片：
```
📊 每日经营日报 | 2026-03-09

━━━ 昨日销售概况 ━━━
总销量：1,234 件（↑ 5.2%）
总 GMV：$45,678（↓ 2.1%）
...

━━━ 库存预警 ━━━
⚠️ SKU-001: 可售 3 天
⚠️ SKU-005: 可售 5 天

━━━ 风险点 ━━━
1. SKU-001 需紧急补货
2. ...
```

### Key Decisions
- 报告使用与规则相同的 Agent 能力（Tool Call 查数据），但 prompt 不同
- 报告必定推送（不管有没有异常），规则只在异常时推送
- 报告配置独立于规则配置（不混用 AlertRule 表）
- focus 字段就是给 Agent 的 prompt，用户可以完全自定义内容
- 通过 N4 的对话能力也可以创建报告任务

## Dependencies
- N1（统一数据访问层）— Agent 通过统一工具查数据
- N8（业务知识注入）— 报告中融入业务背景

## File Changes
- `src/order_guard/models/tables.py` — ReportConfig + ReportHistory 模型
- `src/order_guard/engine/reporter.py` — 报告生成逻辑（新文件）
- `src/order_guard/scheduler/setup.py` — 注册报告定时任务
- `src/order_guard/scheduler/jobs.py` — 新增 run_report_job
- `src/order_guard/engine/rules.py` — 扩展 YAML 加载（reports 段）
- `src/order_guard/cli.py` — reports list / reports run 命令
- `config.example.yaml` — 添加 reports 配置示例
- `alembic/versions/` — DB 迁移
- `tests/test_reporter.py` — 单元测试

## Tasks
- [ ] N6.1: DB 模型（ReportConfig + ReportHistory）+ 迁移
- [ ] N6.2: YAML 配置加载（reports 段）
- [ ] N6.3: 报告生成核心逻辑（Agent 查数据 → LLM 生成摘要）
- [ ] N6.4: 报告推送（复用 AlertDispatcher，适配报告格式）
- [ ] N6.5: 调度集成（APScheduler 注册报告任务）
- [ ] N6.6: CLI reports 命令（list / run）
- [ ] N6.7: 编写单元测试
