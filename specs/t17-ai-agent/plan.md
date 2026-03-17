# T17: AI Agent 工具调用

## Context
有了 MCP Client（T16）提供的工具，需要让 LLM 通过 function calling 自主调用这些工具，形成 Agent 循环：LLM 思考 → 调用工具 → 获取结果 → 继续思考 → ... → 输出最终分析。

## Scope

### In Scope
- LLM function calling / tool use 支持（扩展现有 LLMClient）
- Agent 循环引擎（思考 → 工具调用 → 结果 → 思考 → ... → 最终输出）
- MCP 工具转 LLM function 定义的适配
- 循环次数限制（防止无限循环）
- Token 用量累计追踪
- 结构化输出（复用现有 AnalyzerOutput 格式）

### Not In Scope
- 多 Agent 协作（v3）
- 跨数据源分析（v3）
- Agent 记忆/历史（v3）

## Design

### Agent 循环流程
```
输入: system_prompt + rule_prompt + tools（来自 MCP Server）

循环:
  1. 发送 messages + tools 给 LLM
  2. LLM 返回:
     a. tool_call → 执行 MCP 工具 → 结果加入 messages → 回到 1
     b. text（最终回答）→ 解析为 AnalyzerOutput → 结束
  3. 如果循环超过 max_iterations → 强制结束，返回已有信息

输出: AnalyzerOutput（alerts, summary, has_alerts, token_usage）
```

### 核心类设计
```python
class AgentConfig(BaseModel):
    max_iterations: int = 15        # 最大循环次数
    max_tokens_per_call: int = 4096 # 单次 LLM 调用最大 token
    temperature: float = 0.1

class Agent:
    def __init__(
        self,
        llm_client: LLMClient,
        mcp_connection: MCPConnection,
        config: AgentConfig | None = None,
    ): ...

    async def run(
        self,
        rule_prompt: str,
        system_prompt: str | None = None,
    ) -> AnalyzerOutput:
        """
        执行 Agent 循环:
        1. 从 MCP 获取可用工具列表
        2. 构建初始 messages（system + rule prompt）
        3. 循环调用 LLM，处理 tool calls
        4. 返回结构化分析结果
        """
```

### System Prompt 设计
```
你是一个企业数据分析 Agent。你可以使用提供的工具来探索和查询数据源。

工作流程：
1. 先了解数据源的结构（如查看有哪些表、表的字段）
2. 根据分析需求查询相关数据
3. 对数据进行分析，判断是否有异常
4. 输出结构化的分析结果

注意事项：
- 先探索再查询，不要盲目猜测表名或字段名
- SQL 查询只用 SELECT，不要修改数据
- 数据量大时先 LIMIT 采样了解数据特征，再做完整查询
```

### LLM Tool Use 消息格式（以 OpenAI 兼容格式为例）
```python
# 请求
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": rule_prompt},
]
tools = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List all tables in the database",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    ...
]

# LLM 返回 tool_call
response.choices[0].message.tool_calls = [
    {"id": "call_1", "function": {"name": "list_tables", "arguments": "{}"}}
]

# 执行工具后，将结果加入 messages
messages.append({"role": "assistant", "content": None, "tool_calls": [...]})
messages.append({"role": "tool", "tool_call_id": "call_1", "content": "表: inventory, orders, sales"})

# 继续调用 LLM...
```

### Key Decisions
- 基于 LiteLLM 的 tool use 支持，兼容 OpenAI / Claude / Qwen 等模型
- Agent 循环有最大次数限制（默认 15 次），防止无限循环和 token 浪费
- 最终输出格式复用现有 AnalyzerOutput，保证告警 pipeline 不用改
- 每次工具调用结果追加到 messages，LLM 能看到完整对话历史
- Token 用量跨多次 LLM 调用累计计算

## Dependencies
- T06（AI 层）— 复用 LLMClient 和 AnalyzerOutput
- T16（MCP Client）— MCPConnection 提供工具

## File Changes
- `src/order_guard/engine/agent.py` — Agent 实现
- `src/order_guard/engine/llm_client.py` — 扩展支持 tool use
- `src/order_guard/engine/prompt.py` — Agent system prompt
- `tests/test_agent.py` — 单元测试（mock LLM + mock MCP）

## Tasks
- [ ] T17.1: 扩展 LLMClient 支持 tool use（tools 参数 + tool_calls 解析）
- [ ] T17.2: 实现 MCP 工具 → LLM function 定义的转换
- [ ] T17.3: 实现 Agent 循环引擎（思考 → 工具调用 → 结果 → 继续）
- [ ] T17.4: 实现循环终止条件（max_iterations + 最终输出检测）
- [ ] T17.5: 实现 token 用量累计追踪
- [ ] T17.6: 编写 Agent system prompt
- [ ] T17.7: 编写单元测试（mock LLM 返回 tool_calls → mock MCP 执行）
