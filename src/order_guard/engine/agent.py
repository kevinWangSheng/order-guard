"""AI Agent — LLM + MCP tool use loop."""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMClient, TokenUsage
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.models import SchemaFilterConfig, ToolInfo
from order_guard.mcp.schema import (
    SchemaFilterConfig as SchemaFilterCfg,
    SchemaInfo,
    SchemaLoader,
    build_schema_context,
    filter_schema,
)
from order_guard.mcp.validator import validate_query


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

在你完成数据探索和分析后，请直接输出上述 JSON 格式的最终结果，不要调用任何工具。

查询策略：
1. 对任何表，先执行 SELECT COUNT(*) 了解数据量
2. 如果数据量 > 10000 行：
   - 先 LIMIT 100 采样了解数据特征
   - 使用 WHERE 条件精确过滤，避免全表扫描
   - 使用 GROUP BY 聚合而非拉明细
3. 如果数据量 < 10000 行：可以直接查询
4. 永远不要执行不带 WHERE 和 LIMIT 的 SELECT *"""


def build_time_constraint(data_window: str) -> str:
    """Generate time constraint prompt based on data_window config."""
    if not data_window:
        return ""
    return f"""
重要时间约束：
- 你只需要分析最近 {data_window} 的数据
- 所有 SQL 查询的 WHERE 条件必须包含时间过滤
- 时间字段常见名称：created_at, order_date, sale_date, updated_at
- 不要查询超出此时间范围的数据
"""


class AgentConfig(BaseModel):
    """Agent configuration."""

    max_iterations: int = 15
    max_tokens_per_call: int = 4096
    temperature: float = 0.1
    inject_schema: bool = True       # Auto-inject schema context
    validate_sql: bool = True        # Validate SQL before execution


class Agent:
    """AI Agent that uses MCP tools to explore data and produce analysis."""

    def __init__(
        self,
        llm_client: LLMClient,
        mcp_connection: MCPConnection,
        config: AgentConfig | None = None,
        schema_filter: SchemaFilterConfig | None = None,
        schema_sample_rows: int = 3,
        data_window: str = "",
        rule_id: str = "",
    ):
        self._llm = llm_client
        self._mcp = mcp_connection
        self._config = config or AgentConfig()
        self._schema_filter = schema_filter
        self._schema_sample_rows = schema_sample_rows
        self._data_window = data_window
        self._rule_id = rule_id
        self._schema: SchemaInfo | None = None

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

        # 1.5. Load and inject schema context + time constraint
        effective_system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        if self._config.inject_schema:
            schema_ctx = await self._load_schema_context()
            if schema_ctx:
                effective_system_prompt = effective_system_prompt + "\n\n" + schema_ctx
                logger.info("Agent: schema context injected ({} chars)", len(schema_ctx))

        if self._data_window:
            time_ctx = build_time_constraint(self._data_window)
            effective_system_prompt = effective_system_prompt + "\n" + time_ctx
            logger.info("Agent: time constraint injected (data_window={})", self._data_window)

        # 2. Build initial messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": effective_system_prompt},
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
                    # SQL validation before execute_sql
                    if self._config.validate_sql and self._schema and tc.name == "execute_sql":
                        sql = tc.arguments.get("sql", "")
                        if sql:
                            vr = validate_query(sql, self._schema)
                            if not vr.valid:
                                result = f"SQL 校验失败: {vr.error}"
                                logger.warning("SQL validation failed: {}", vr.error)
                                await self._log_query(
                                    sql=sql, status="rejected", error=vr.error,
                                    duration_ms=0, rows_returned=0,
                                    agent_iteration=iteration + 1,
                                )
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": result,
                                })
                                continue

                    is_sql = tc.name == "execute_sql"
                    sql_text = tc.arguments.get("sql", "") if is_sql else ""
                    start_t = time.monotonic() if is_sql else 0

                    try:
                        result = await self._mcp.call_tool(tc.name, tc.arguments)
                        if is_sql:
                            dur = int((time.monotonic() - start_t) * 1000)
                            rows = self._count_result_rows(result)
                            await self._log_query(
                                sql=sql_text, status="success",
                                duration_ms=dur, rows_returned=rows,
                                agent_iteration=iteration + 1,
                            )
                    except Exception as e:
                        if is_sql:
                            dur = int((time.monotonic() - start_t) * 1000)
                            status = "timeout" if "timeout" in str(e).lower() else "error"
                            await self._log_query(
                                sql=sql_text, status=status, error=str(e),
                                duration_ms=dur, rows_returned=0,
                                agent_iteration=iteration + 1,
                            )
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

    async def _log_query(
        self, *, sql: str, status: str, duration_ms: int = 0,
        rows_returned: int = 0, error: str | None = None,
        agent_iteration: int = 0,
    ) -> None:
        """Record a query log entry to the database."""
        try:
            from order_guard.models import QueryLog
            from order_guard.storage.database import get_session
            from order_guard.storage.crud import create

            async with get_session() as session:
                log = QueryLog(
                    rule_id=self._rule_id,
                    mcp_server=self._mcp.name,
                    sql=sql,
                    status=status,
                    rows_returned=rows_returned,
                    duration_ms=duration_ms,
                    error=error,
                    agent_iteration=agent_iteration,
                )
                await create(session, log)
        except Exception as e:
            logger.debug("Failed to log query: {}", e)

    @staticmethod
    def _count_result_rows(result: str) -> int:
        """Count rows in a JSON result string."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return len(data)
        except (json.JSONDecodeError, TypeError):
            pass
        return 0

    async def _load_schema_context(self) -> str:
        """Load schema, apply filter, and return formatted context string."""
        try:
            loader = SchemaLoader(self._mcp, sample_rows=self._schema_sample_rows)
            schema = await loader.load()

            cold_tables: list[str] = []
            if self._schema_filter:
                if self._schema_filter.blocked_tables or self._schema_filter.blocked_columns:
                    filter_cfg = SchemaFilterCfg(
                        blocked_tables=self._schema_filter.blocked_tables,
                        blocked_columns=self._schema_filter.blocked_columns,
                    )
                    schema = filter_schema(schema, filter_cfg)
                cold_tables = self._schema_filter.cold_tables

            self._schema = schema  # Store for SQL validation
            return build_schema_context(schema, cold_tables=cold_tables)
        except Exception as e:
            logger.warning("Failed to load schema context: {}", e)
            return ""

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
