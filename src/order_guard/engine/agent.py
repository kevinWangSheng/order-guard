"""AI Agent — LLM + MCP tool use loop."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMClient, TokenUsage
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.models import ToolInfo


AGENT_SYSTEM_PROMPT = """你是一个企业数据分析 Agent。你可以使用提供的工具来探索和查询数据源。

工作流程：
1. 先了解数据源的结构（如查看有哪些表、表的字段）
2. 根据分析需求查询相关数据
3. 对数据进行分析，判断是否有异常
4. 输出结构化的分析结果

注意事项：
- 先探索再查询，不要盲目猜测表名或字段名
- SQL 查询只用 SELECT，不要修改数据
- 数据量大时先 LIMIT 采样了解数据特征，再做完整查询

最终输出格式要求（严格 JSON）：
```json
{
  "alerts": [
    {
      "sku": "SKU 编号",
      "severity": "critical/warning/info",
      "title": "告警标题",
      "reason": "告警原因",
      "suggestion": "建议措施"
    }
  ],
  "summary": "整体分析总结（中文）",
  "has_alerts": true/false
}
```

在你完成数据探索和分析后，请直接输出上述 JSON 格式的最终结果，不要调用任何工具。"""


class AgentConfig(BaseModel):
    """Agent configuration."""

    max_iterations: int = 15
    max_tokens_per_call: int = 4096
    temperature: float = 0.1


class Agent:
    """AI Agent that uses MCP tools to explore data and produce analysis."""

    def __init__(
        self,
        llm_client: LLMClient,
        mcp_connection: MCPConnection,
        config: AgentConfig | None = None,
    ):
        self._llm = llm_client
        self._mcp = mcp_connection
        self._config = config or AgentConfig()

    async def run(
        self,
        rule_prompt: str,
        system_prompt: str | None = None,
    ) -> AnalyzerOutput:
        """Execute the Agent loop: LLM → tool calls → MCP → ... → final output."""
        total_usage = TokenUsage()

        # 1. Get available tools from MCP
        tools = await self._mcp.list_tools()
        llm_tools = [_tool_info_to_llm_function(t) for t in tools]
        logger.info(
            "Agent starting: {} tools from '{}', max_iterations={}",
            len(llm_tools), self._mcp.name, self._config.max_iterations,
        )

        # 2. Build initial messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": rule_prompt},
        ]

        # 3. Agent loop
        for iteration in range(self._config.max_iterations):
            logger.info("Agent iteration {}/{}", iteration + 1, self._config.max_iterations)

            response = await self._llm.completion(
                messages,
                tools=llm_tools if llm_tools else None,
                max_tokens=self._config.max_tokens_per_call,
                temperature=self._config.temperature,
            )

            # Accumulate token usage
            total_usage.prompt_tokens += response.token_usage.prompt_tokens
            total_usage.completion_tokens += response.token_usage.completion_tokens
            total_usage.total_tokens += response.token_usage.total_tokens

            # Check if LLM wants to call tools
            if response.tool_calls:
                # Append assistant message with tool calls
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call via MCP
                for tc in response.tool_calls:
                    try:
                        result = await self._mcp.call_tool(tc.name, tc.arguments)
                    except Exception as e:
                        result = f"Error calling tool '{tc.name}': {e}"
                        logger.warning("Tool call failed: {} - {}", tc.name, e)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                continue  # Next iteration with tool results

            # No tool calls — LLM returned final text
            if response.content:
                return self._parse_final_output(response.content, total_usage)

            # Empty response — shouldn't happen, but handle gracefully
            logger.warning("Agent received empty response at iteration {}", iteration + 1)
            break

        # Max iterations exceeded
        logger.warning(
            "Agent reached max iterations ({}), forcing output",
            self._config.max_iterations,
        )
        return AnalyzerOutput(
            summary=f"Agent 达到最大迭代次数 ({self._config.max_iterations})，分析可能不完整。",
            token_usage=total_usage,
        )

    def _parse_final_output(self, content: str, token_usage: TokenUsage) -> AnalyzerOutput:
        """Parse LLM's final text response into AnalyzerOutput."""
        try:
            # Extract JSON from possible markdown code blocks
            cleaned = content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines[1:] if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)

            data = json.loads(cleaned)

            alerts = []
            for a in data.get("alerts", []):
                alerts.append(AlertItem(
                    sku=a.get("sku", ""),
                    severity=a.get("severity", "info"),
                    title=a.get("title", ""),
                    reason=a.get("reason", ""),
                    suggestion=a.get("suggestion", ""),
                ))

            return AnalyzerOutput(
                alerts=alerts,
                summary=data.get("summary", ""),
                has_alerts=data.get("has_alerts", len(alerts) > 0),
                raw_response=content,
                token_usage=token_usage,
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse Agent output as JSON: {}", e)
            # Return raw text as summary
            return AnalyzerOutput(
                summary=content[:500],
                raw_response=content,
                token_usage=token_usage,
            )


def _tool_info_to_llm_function(tool: ToolInfo) -> dict[str, Any]:
    """Convert MCP ToolInfo to LLM function calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }
