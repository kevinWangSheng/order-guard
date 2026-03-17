# T18: MCP Connector + Pipeline 适配

## Context
T16 提供了 MCP Client 连接能力，T17 提供了 AI Agent 工具调用能力。本任务将它们集成到现有的调度 pipeline 中，让规则可以绑定 MCP Server，执行时走 Agent 流程。

## Scope

### In Scope
- MCPConnector 类型注册（ConnectorRegistry 中新增 "mcp" 类型）
- AlertRule 模型扩展（支持 connector_type 和 mcp_server 字段）
- scheduler pipeline 分支（传统 connector vs MCP agent）
- 规则 YAML 配置支持 MCP 绑定
- CLI `run` 命令支持 MCP 规则

### Not In Scope
- 多数据源联合查询（v3 多 Agent）
- 动态添加 MCP Server（通过 API，v5 Web UI）

## Design

### 规则配置扩展
```yaml
rules:
  # 传统模式 — 使用固定 Connector（兼容 v1）
  - id: "rule-inventory-risk"
    name: "库存风险检查"
    connector: "mock"
    data_type: "inventory"
    prompt: |
      检查 SKU 库存风险...
    enabled: true

  # MCP 模式 — AI Agent 自动探索+取数+分析（v2 新增）
  - id: "rule-warehouse-check"
    name: "仓库数据检查"
    connector_type: "mcp"
    mcp_server: "warehouse-db"       # 对应 mcp_servers 配置中的 name
    prompt: |
      你需要检查仓库数据库中的库存情况：
      1. 找出所有库存天数低于 7 天的 SKU
      2. 找出库存积压（库存天数 > 90 天）的 SKU
      3. 对每个异常 SKU 给出补货或清仓建议
    enabled: true
```

### Pipeline 分支逻辑
```python
async def run_detection_job(job_config: JobConfig):
    task_run = create_task_run(...)
    rule = get_rule(job_config.rule_id)

    if rule.connector_type == "mcp":
        # ---- MCP Agent 流程 ----
        mcp_conn = mcp_manager.get_connection(rule.mcp_server)
        agent = Agent(llm_client, mcp_conn)
        result = await agent.run(rule.prompt_template)
    else:
        # ---- 传统 Connector 流程（兼容 v1）----
        connector = registry.get(rule.connector_id)
        raw_data = await connector.query(rule.data_type)
        metrics = MetricsEngine.compute(raw_data)
        summary = SummaryBuilder.build(metrics)
        result = await analyzer.analyze(summary, rule.prompt_template)

    # 告警推送（两种流程输出格式一致，统一处理）
    if result.has_alerts:
        await dispatcher.dispatch(result.alerts, rule)

    update_task_run(task_run.id, status="success", result_summary=result)
```

### AlertRule 模型扩展
```python
class AlertRule(SQLModel, table=True):
    # ... 现有字段 ...
    connector_id: str = ""           # 传统 connector 名称
    connector_type: str = "legacy"   # "legacy" | "mcp"
    mcp_server: str = ""             # MCP Server 名称（connector_type=mcp 时使用）
    data_type: str = ""              # 传统模式的数据类型
```

### Key Decisions
- 传统 Connector 流程完全保留，不做 breaking change
- 通过 connector_type 字段区分走哪个流程
- 两种流程的输出格式统一（AnalyzerOutput），告警推送逻辑不用改
- MCP 规则的 prompt 更偏向"任务描述"风格（告诉 Agent 要做什么），而不是"分析以下数据"风格
- DB 迁移：AlertRule 表增加 connector_type 和 mcp_server 字段

## Dependencies
- T09（调度层）— 修改 pipeline 编排
- T16（MCP Client）— MCPManager
- T17（AI Agent）— Agent

## File Changes
- `src/order_guard/models/tables.py` — AlertRule 增加字段
- `src/order_guard/engine/rules.py` — 规则加载支持新字段
- `src/order_guard/scheduler/jobs.py` — pipeline 分支逻辑
- `src/order_guard/cli.py` — run 命令支持 MCP 规则
- `alembic/versions/` — 新迁移脚本
- `config.example.yaml` — MCP 规则示例
- `tests/test_mcp_pipeline.py` — 集成测试

## Tasks
- [ ] T18.1: AlertRule 模型增加 connector_type / mcp_server 字段 + DB 迁移
- [ ] T18.2: 规则 YAML 加载支持 MCP 类型规则
- [ ] T18.3: 修改 run_detection_job() 增加 MCP Agent 分支
- [ ] T18.4: CLI run 命令适配 MCP 规则
- [ ] T18.5: 更新 config.example.yaml 增加 MCP 规则示例
- [ ] T18.6: 编写集成测试
