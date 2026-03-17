# T06: AI 层 — 验收标准

## Given/When/Then

- Given 有效的 LLM API Key 已配置, When 调用 LLMClient.completion(), Then 返回 LLM 响应且无报错
- Given 一段数据摘要 + 一条规则 Prompt, When 调用 Analyzer.analyze(), Then 返回符合 JSON Schema 的结构化结果
- Given LLM 返回格式不对（非 JSON）, When Analyzer 校验失败, Then 自动重试一次
- Given 两次重试都失败, When Analyzer 处理, Then 返回空结果 + 记录错误日志
- Given 配置 model="gpt-4o", When 切换为 model="anthropic/claude-sonnet", Then 调用流程不变，正常返回结果
- Given 一次成功的 LLM 调用, When 查看返回值, Then 包含 token_usage 字段

## Checklist

- [ ] LiteLLM 封装可正常调用 OpenAI API
- [ ] Prompt 组装包含系统提示、规则、数据摘要、输出格式要求
- [ ] 输出符合定义的 JSON Schema
- [ ] 格式校验 + 重试逻辑工作正常
- [ ] token 用量被记录
- [ ] API Key 无效时抛出清晰错误（不暴露 Key）
- [ ] 有单元测试（可 mock LLM 调用）
