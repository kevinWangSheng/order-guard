# T09: 调度层

## Context
编排完整的巡检流程：定时触发 → 加载规则 → 拉数据 → 计算指标 → LLM 分析 → 告警推送。这是整个系统的"主循环"。

## Scope
### In Scope
- APScheduler 集成（async，和 FastAPI 共存）
- 从配置文件解析 Cron 表达式定义定时任务
- 任务执行编排（串联 T4-T8 所有模块）
- 任务执行日志记录（task_runs 表）
- 任务失败时记录错误 + 推送失败通知
- FastAPI 启动时自动注册定时任务

### Not In Scope
- 任务手动暂停/恢复（后续版本）
- 任务并发控制（后续版本）
- 事件触发模式（Bot 消息触发，后续版本）

## Design
### 任务编排流程
```python
async def run_detection_job(job_config: JobConfig):
    # 1. 记录任务开始
    task_run = create_task_run(job_name=job_config.name, status="running")

    # 2. 加载规则
    rule = get_rule(job_config.rule_id)

    # 3. 拉取数据
    connector = registry.get(rule.connector)
    raw_data = await connector.query(rule.data_type)

    # 4. 指标计算
    metrics = MetricsEngine.compute(raw_data)
    summary = SummaryBuilder.build(metrics)

    # 5. LLM 分析
    result = await analyzer.analyze(summary, rule.prompt_template)

    # 6. 告警推送（如果有异常）
    if result.has_alerts:
        await dispatcher.dispatch(result.alerts, rule)

    # 7. 记录任务完成
    update_task_run(task_run.id, status="success", result_summary=result)
```

### Cron 配置示例
```yaml
scheduler:
  jobs:
    - name: "daily-inventory-check"
      cron: "0 9 * * *"           # 每天早上9点
      rule_ids: ["rule-inventory-risk"]

    - name: "hourly-return-check"
      cron: "0 * * * *"           # 每小时
      rule_ids: ["rule-return-rate"]
```

### Key Decisions
- APScheduler 4.x 的 AsyncScheduler 和 FastAPI 共用同一个 event loop
- 每个 job 可以关联多个 rule_ids，依次执行
- 任务失败不影响其他任务
- 所有执行记录写入 task_runs 表

## Dependencies
- T2（配置管理）— scheduler 配置段
- T3（存储层）— task_runs 表
- T4（数据对接）— Connector
- T5（指标计算）— MetricsEngine
- T6（AI 层）— Analyzer
- T7（规则管理）— RuleManager
- T8（告警推送）— AlertDispatcher

## Tasks
- [ ] T9.1: 集成 APScheduler AsyncScheduler + FastAPI lifespan
- [ ] T9.2: 实现 Cron 配置解析 → 注册定时任务
- [ ] T9.3: 实现 run_detection_job() 完整编排流程
- [ ] T9.4: 实现任务执行日志记录（task_runs CRUD）
- [ ] T9.5: 实现任务失败处理（错误记录 + 失败通知推送）
