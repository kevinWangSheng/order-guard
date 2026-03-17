# T06: AI 层

## Context
封装 LLM 调用，接收"结构化数据摘要 + 业务规则 Prompt"，输出结构化的分析判断结果（JSON）。通过 LiteLLM 实现 LLM 可切换。

## Scope
### In Scope
- LiteLLM 封装（统一调用接口）
- Prompt 组装逻辑（系统提示 + 规则 + 数据摘要 + 输出格式要求）
- JSON Schema 输出约束（结构化返回）
- 基础格式校验（返回值是否符合 Schema）
- 重试机制（格式不对时重试一次）
- Token 用量记录

### Not In Scope
- 多 LLM 并行/竞争（后续版本）
- Agent SDK 集成（后续版本）
- 对话上下文管理（后续 Bot 接入时实现）
- 高级校验/兜底策略（后续版本）

## Design
### 调用流程
```
AnalyzerInput:
  - data_summary: str      # T05 生成的 Markdown 摘要
  - rule_prompt: str        # 业务规则 Prompt
  - output_schema: dict     # JSON Schema 定义

→ LLMClient.analyze(input)

→ AnalyzerOutput:
  - alerts: list[AlertItem]  # 告警列表
  - summary: str             # 整体总结
  - raw_response: str        # LLM 原始返回（调试用）
  - token_usage: dict        # token 用量
```

### 输出 JSON Schema
```json
{
  "type": "object",
  "properties": {
    "alerts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "sku": {"type": "string"},
          "severity": {"enum": ["critical", "warning", "info"]},
          "title": {"type": "string"},
          "reason": {"type": "string"},
          "suggestion": {"type": "string"}
        },
        "required": ["severity", "title", "reason"]
      }
    },
    "summary": {"type": "string"},
    "has_alerts": {"type": "boolean"}
  }
}
```

### Key Decisions
- 使用 LiteLLM 而非直接调用 OpenAI SDK，一行配置切换模型
- JSON Schema 通过 LLM 的 response_format 参数约束（OpenAI/Claude 都支持）
- 格式校验失败时重试一次，两次都失败则记录错误返回空结果
- 记录每次调用的 token 用量，便于成本控制

## Dependencies
- T2（配置管理）— LLM 配置（model, api_key, temperature）
- T5（指标计算）— 数据摘要作为输入

## Tasks
- [ ] T6.1: 实现 LLMClient — LiteLLM 封装（completion 调用 + 错误处理）
- [ ] T6.2: 实现 PromptBuilder — 组装系统提示 + 规则 + 数据摘要
- [ ] T6.3: 定义输出 JSON Schema + 实现 Pydantic 输出模型
- [ ] T6.4: 实现 Analyzer — 串联 Prompt 组装 → LLM 调用 → 输出解析校验
- [ ] T6.5: 实现格式校验 + 重试逻辑 + token 用量记录
