# T09: 调度层 — 验收标准

## Given/When/Then

- Given scheduler 配置了 cron="0 9 * * *", When 系统启动, Then APScheduler 注册该定时任务
- Given 定时任务触发, When 执行 run_detection_job(), Then 完整走通：拉数据 → 计算 → LLM 分析 → 推送
- Given 任务执行成功, When 查看 task_runs 表, Then 有一条 status="success" 的记录，含 duration_ms
- Given 任务执行中 Connector 报错, When 捕获异常, Then task_runs 记录 status="failed" + error 字段有错误信息
- Given 任务执行失败, When 配置了失败通知, Then 通过 Webhook 推送失败通知
- Given 配置了多个 job, When 系统运行, Then 各任务独立执行互不影响

## Checklist

- [ ] APScheduler 和 FastAPI 共存正常（同一 event loop）
- [ ] Cron 表达式正确解析并按时触发
- [ ] 完整的检测流程端到端走通
- [ ] task_runs 表正确记录每次执行
- [ ] 任务失败不影响其他任务
- [ ] 有集成测试覆盖完整流程（可 mock LLM）
