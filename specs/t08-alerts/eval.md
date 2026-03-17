# T08: 告警推送 — 验收标准

## Given/When/Then

- Given Webhook URL 已配置, When 调用 WebhookChannel.send(alert), Then 成功 POST JSON 到目标 URL 并返回 200
- Given Webhook URL 不可达, When 第一次推送失败, Then 自动重试最多 3 次（指数退避）
- Given 3 次重试都失败, When 最终失败, Then 记录错误日志 + alerts 表 status 更新为 "failed"
- Given 推送成功, When 记录结果, Then alerts 表 status 更新为 "sent" + sent_at 有值
- Given AI 输出包含 3 条告警, When AlertDispatcher 处理, Then 生成 3 条 AlertMessage 并推送
- Given 配置了 2 个 Webhook 渠道, When 触发告警, Then 两个渠道都收到推送

## Checklist

- [ ] BaseAlertChannel 接口清晰，新渠道只需实现 send()
- [ ] WebhookChannel POST 请求体符合定义的 JSON 格式
- [ ] 重试逻辑工作正常（指数退避）
- [ ] 推送结果正确写入数据库
- [ ] 有单元测试（mock HTTP 请求）
