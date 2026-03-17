# T17: AI Agent 工具调用 — 验收标准

## 验收步骤

### 1. LLM Tool Use 支持
- [ ] LLMClient.completion() 支持传入 tools 参数
- [ ] 正确解析 LLM 返回的 tool_calls
- [ ] 兼容 LiteLLM 支持的模型（OpenAI / Claude / Qwen 等）

### 2. MCP 工具转换
- [ ] MCP ToolInfo 正确转换为 LLM function 定义格式
- [ ] 工具名称、描述、参数 schema 完整保留

### 3. Agent 循环
- [ ] Agent 能发起第一次 LLM 调用并传入工具列表
- [ ] LLM 返回 tool_call 时，Agent 正确调用 MCP 工具
- [ ] 工具结果正确回传给 LLM 继续对话
- [ ] LLM 返回最终文本时，Agent 正确解析为 AnalyzerOutput
- [ ] 多轮工具调用场景正常工作（如先 list_tables → describe_table → read_query）

### 4. 安全与限制
- [ ] 超过 max_iterations 时强制停止并返回已有信息
- [ ] 工具调用失败时错误信息回传给 LLM（不中断循环）
- [ ] Token 用量跨多次调用正确累计

### 5. 输出格式
- [ ] 输出为标准 AnalyzerOutput 格式（alerts, summary, has_alerts, token_usage）
- [ ] 兼容现有告警 pipeline（AlertDispatcher 能直接使用）

### 6. 单元测试
```bash
uv run pytest tests/test_agent.py -v
```
- [ ] Mock LLM + Mock MCP 场景测试通过
- [ ] 循环终止测试通过
- [ ] 工具调用失败处理测试通过
