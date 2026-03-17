# T10: CLI 入口 — 验收标准

## Given/When/Then

- Given 系统已配置, When 执行 `order-guard serve`, Then FastAPI 服务启动 + 定时任务注册
- Given 系统已配置且 Mock 数据源可用, When 执行 `order-guard run`, Then 完整执行一次检测并输出结果
- Given 执行 `order-guard run --dry-run`, When 检测完成, Then 结果输出到终端但不推送 Webhook
- Given 执行 `order-guard run --rule-id rule-inventory-risk`, When 执行, Then 只执行指定规则
- Given 数据库中有规则, When 执行 `order-guard rules list`, Then 输出规则列表
- Given 数据库中有告警历史, When 执行 `order-guard history --limit 5`, Then 输出最近 5 条告警
- Given 系统已配置, When 执行 `order-guard status`, Then 输出各数据源连接状态

## Checklist

- [ ] 所有命令有 --help 说明
- [ ] `serve` 正常启动服务
- [ ] `run` 端到端执行检测流程
- [ ] `--dry-run` 不推送只输出
- [ ] `rules list/show` 正常显示规则
- [ ] `history` 正常查询告警
- [ ] `status` 正常显示系统状态
- [ ] 命令执行出错时有友好的错误提示
