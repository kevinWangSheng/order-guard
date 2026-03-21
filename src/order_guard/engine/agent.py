"""AI Agent — LLM + tool use loop (supports unified tools and direct MCP).

Supports three modes:
1. Unified mode (v4+): all tools from tools/ package
2. Detection mode: data-only tools for scheduled rule detection
3. Legacy MCP mode: direct MCP tools (backward compatible)
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from loguru import logger
from pydantic import BaseModel

from order_guard.engine.analyzer import AlertItem, AnalyzerOutput
from order_guard.engine.llm_client import LLMClient, TokenUsage
from order_guard.mcp.models import SchemaFilterConfig, ToolInfo
from order_guard.mcp.schema import (
    SchemaFilterConfig as SchemaFilterCfg,
    SchemaInfo,
    SchemaLoader,
    build_schema_context,
    filter_schema,
)
from order_guard.mcp.validator import validate_query


# ---------------------------------------------------------------------------
# LangWatch integration helpers
# ---------------------------------------------------------------------------

_langwatch_initialized = False


def langwatch_init():
    """Mark LangWatch as explicitly initialized (called from main.py)."""
    global _langwatch_initialized
    _langwatch_initialized = True


def _langwatch_available() -> bool:
    """Check if LangWatch was explicitly initialized via langwatch_init()."""
    return _langwatch_initialized


@contextmanager
def _lw_trace(name: str, metadata: dict[str, Any]):
    """Open a LangWatch trace if available, otherwise yield None.

    Uses the v0.16+ API: langwatch.trace() as context manager, then
    get_current_trace().autotrack_litellm_calls() to wire LiteLLM capture.
    """
    if not _langwatch_available():
        yield None
        return
    try:
        import langwatch
        import litellm as _litellm_mod
        with langwatch.trace(name=name, metadata=metadata) as t:
            # v0.16+: get_current_trace() returns the active trace in context
            current = langwatch.get_current_trace()
            if current is not None:
                current.autotrack_litellm_calls(_litellm_mod)
            else:
                t.autotrack_litellm_calls(_litellm_mod)
            yield t
    except Exception as e:
        logger.debug("LangWatch trace error: {}", e)
        yield None


def _lw_tool_span(trace: Any, tool_name: str, args: dict[str, Any]):
    """Open a LangWatch span for a tool call if trace is active."""
    if trace is None:
        return _noop_context()
    try:
        import langwatch
        return langwatch.span(
            name=tool_name,
            type="tool",
            input={"type": "json", "value": args},
        )
    except Exception:
        return _noop_context()


@contextmanager
def _noop_context():
    yield None

# Keep for backward compatibility
AGENT_SYSTEM_PROMPT = """你是企业数据分析 Agent。根据分析需求，使用工具查询数据，判断异常并输出结果。

工作流程：
1. 调用 list_datasources 了解可用数据源
2. 调用 get_schema 了解表结构和字段
3. 调用 query 查询数据进行分析
4. 分析完成后，直接输出下方 JSON 格式的结果（不再调用工具）

输出格式（严格 JSON）：
```json
{
  "alerts": [
    {
      "sku": "SKU 编号",
      "severity": "critical/warning/info",
      "title": "告警标题",
      "reason": "告警原因（含具体数字）",
      "suggestion": "建议措施"
    }
  ],
  "summary": "整体分析总结（中文）",
  "has_alerts": true/false
}
```"""


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


class ToolBackend(Protocol):
    """Protocol for tool backends (DataAccessLayer or MCPConnection)."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


@dataclass
class AgentResult:
    """Result from a unified Agent run."""

    response: str                               # LLM's final text reply
    tool_calls_log: list[dict[str, Any]] = field(default_factory=list)
    token_usage: TokenUsage | None = None       # Aggregate token usage for this call
    iterations: int = 0                         # Number of agent loop iterations


class AgentConfig(BaseModel):
    """Agent configuration."""

    max_iterations: int = 15
    max_tokens_per_call: int = 4096
    temperature: float = 0.1
    inject_schema: bool = True             # Auto-inject schema context
    validate_sql: bool = True              # Validate SQL before execution
    inject_business_context: bool = True   # Auto-inject business context


class Agent:
    """AI Agent that uses tools to explore data and produce analysis.

    Supports three modes:
    - Unified (v4+): all tools + tool_executors
    - DataAccessLayer: fixed 3 data tools
    - MCPConnection (legacy): direct MCP tools
    """

    def __init__(
        self,
        llm_client: LLMClient,
        mcp_connection: Any | None = None,
        config: AgentConfig | None = None,
        schema_filter: SchemaFilterConfig | None = None,
        schema_sample_rows: int = 3,
        data_window: str = "",
        rule_id: str = "",
        data_access_layer: Any | None = None,
        # Unified mode: explicit tools + executors
        tools: list[ToolInfo] | None = None,
        tool_executors: dict[str, Callable] | None = None,
    ):
        self._llm = llm_client
        self._mcp = mcp_connection
        self._dal = data_access_layer
        self._config = config or AgentConfig()
        self._schema_filter = schema_filter
        self._schema_sample_rows = schema_sample_rows
        self._data_window = data_window
        self._rule_id = rule_id
        self._schema: SchemaInfo | None = None

        # Unified mode tools
        self._explicit_tools = tools
        self._tool_executors = tool_executors or {}

        self._tool_calls_log: list[dict[str, Any]] = []
        # Step 2: Session-level tool result cache {cache_key → result_str}
        self._tool_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Unified mode: run with AgentResult (for interactive use)
    # ------------------------------------------------------------------

    async def run_unified(
        self,
        user_message: str,
        system_prompt: str = "",
        context_messages: list[dict[str, Any]] | None = None,
        *,
        trigger_type: str = "chat",
        user_id: str = "",
        session_id: str = "",
        on_tool_result: Callable[[str, dict[str, Any], str], Any] | None = None,
    ) -> AgentResult:
        """Run the Agent in unified mode with all tools.

        Args:
            on_tool_result: Optional async callback(tool_name, args, result_str)
                called after each tool execution. Used for progressive output.

        Returns AgentResult with response text.
        """
        with _lw_trace("agent.run_unified", metadata={
            "trigger_type": trigger_type,
            "user_id": user_id,
            "session_id": session_id,
            "rule_id": self._rule_id,
        }) as lw_trace:
            return await self._run_unified_impl(
                user_message, system_prompt, context_messages,
                trigger_type=trigger_type, user_id=user_id,
                session_id=session_id, on_tool_result=on_tool_result,
                lw_trace=lw_trace,
            )

    async def _run_unified_impl(
        self,
        user_message: str,
        system_prompt: str,
        context_messages: list[dict[str, Any]] | None,
        *,
        trigger_type: str,
        user_id: str,
        session_id: str,
        on_tool_result: Callable[[str, dict[str, Any], str], Any] | None,
        lw_trace: Any = None,
    ) -> AgentResult:
        self._tool_calls_log = []
        start_time = time.monotonic()
        tool_calls_total = 0
        iterations_total = 0

        # Determine tools
        tools, backend = self._resolve_tools_and_backend()
        llm_tools = [_tool_info_to_llm_function(t) for t in tools]

        logger.info(
            "Agent (unified) starting: {} tools, max_iterations={}",
            len(llm_tools), self._config.max_iterations,
        )

        # Build messages
        # Step 1 (Prompt Caching): use content-array format for system message so
        # Anthropic models can cache it. LiteLLM transparently handles this for
        # non-Anthropic models by joining the text parts.
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        if context_messages:
            messages.extend(context_messages)
        messages.append({"role": "user", "content": user_message})

        total_usage = TokenUsage()

        # Agent loop
        for iteration in range(self._config.max_iterations):
            logger.info("Agent iteration {}/{}", iteration + 1, self._config.max_iterations)

            response = await self._llm.completion(
                messages,
                tools=llm_tools if llm_tools else None,
                max_tokens=self._config.max_tokens_per_call,
                temperature=self._config.temperature,
            )
            _accumulate_usage(total_usage, response.token_usage)
            iterations_total = iteration + 1

            if response.tool_calls:
                tool_calls_total += len(response.tool_calls)
                messages.append(_build_assistant_msg(response))

                for tc in response.tool_calls:
                    with _lw_tool_span(lw_trace, tc.name, tc.arguments) as span:
                        result = await self._execute_tool_call_unified(
                            backend, tc, iteration + 1,
                        )
                        if span is not None:
                            try:
                                span.update(output={"type": "json", "value": result[:2000]})
                            except Exception:
                                pass
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                    # Progressive output callback
                    if on_tool_result is not None:
                        try:
                            ret = on_tool_result(tc.name, tc.arguments, result)
                            if hasattr(ret, "__await__"):
                                await ret
                        except Exception as e:
                            logger.warning("on_tool_result callback error: {}", e)

                continue

            # Final text
            if response.content:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await self._log_usage(
                    token_usage=total_usage,
                    model=getattr(self._llm, "_model", ""),
                    trigger_type=trigger_type,
                    rule_id=self._rule_id,
                    user_id=user_id,
                    session_id=session_id,
                    duration_ms=duration_ms,
                    tool_calls_count=tool_calls_total,
                    iterations=iterations_total,
                )
                return AgentResult(
                    response=response.content,
                    tool_calls_log=self._tool_calls_log,
                    token_usage=total_usage,
                    iterations=iterations_total,
                )

            logger.warning("Agent received empty response at iteration {}", iteration + 1)
            break

        duration_ms = int((time.monotonic() - start_time) * 1000)
        await self._log_usage(
            token_usage=total_usage,
            model=getattr(self._llm, "_model", ""),
            trigger_type=trigger_type,
            rule_id=self._rule_id,
            user_id=user_id,
            session_id=session_id,
            duration_ms=duration_ms,
            tool_calls_count=tool_calls_total,
            iterations=iterations_total,
        )
        return AgentResult(
            response="抱歉，分析超时。请简化您的问题后重试。",
            tool_calls_log=self._tool_calls_log,
            token_usage=total_usage,
            iterations=iterations_total,
        )

    # ------------------------------------------------------------------
    # Detection mode: run with AnalyzerOutput (for scheduled rules)
    # ------------------------------------------------------------------

    async def run(
        self,
        rule_prompt: str,
        system_prompt: str | None = None,
        *,
        trigger_type: str = "",
        user_id: str = "",
        session_id: str = "",
    ) -> AnalyzerOutput:
        """Execute the Agent loop for rule detection. Returns AnalyzerOutput."""
        with _lw_trace("agent.run", metadata={
            "trigger_type": trigger_type or "detection",
            "rule_id": self._rule_id,
            "user_id": user_id,
            "session_id": session_id,
        }) as lw_trace:
            return await self._run_detection_impl(
                rule_prompt, system_prompt,
                trigger_type=trigger_type, user_id=user_id,
                session_id=session_id, lw_trace=lw_trace,
            )

    async def _run_detection_impl(
        self,
        rule_prompt: str,
        system_prompt: str | None,
        *,
        trigger_type: str,
        user_id: str,
        session_id: str,
        lw_trace: Any = None,
    ) -> AnalyzerOutput:
        total_usage = TokenUsage()
        start_time = time.monotonic()
        tool_calls_total = 0
        iterations_total = 0

        # Determine backend and tools
        tools, backend = self._resolve_tools_and_backend()
        llm_tools = [_tool_info_to_llm_function(t) for t in tools]
        logger.info(
            "Agent starting: {} tools, max_iterations={}",
            len(llm_tools), self._config.max_iterations,
        )

        # Inject business context + schema context + time constraint
        effective_system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        if self._config.inject_business_context:
            try:
                from order_guard.engine.business_context import get_business_context, build_business_context_prompt
                biz_ctx = await get_business_context()
                if biz_ctx:
                    biz_prompt = build_business_context_prompt(biz_ctx)
                    effective_system_prompt = effective_system_prompt + "\n\n" + biz_prompt
                    logger.info("Agent: business context injected ({} chars)", len(biz_ctx))
            except Exception as e:
                logger.debug("Failed to inject business context: {}", e)

        # Schema pre-injection: only for direct MCP path (no DAL).
        if self._config.inject_schema and self._mcp is not None and self._dal is None:
            schema_ctx = await self._load_schema_context()
            if schema_ctx:
                effective_system_prompt = effective_system_prompt + "\n\n" + schema_ctx
                logger.info("Agent: schema context injected ({} chars)", len(schema_ctx))

        if self._data_window:
            time_ctx = build_time_constraint(self._data_window)
            effective_system_prompt = effective_system_prompt + "\n" + time_ctx
            logger.info("Agent: time constraint injected (data_window={})", self._data_window)

        # Build initial messages
        # Use content-array format for system message so Anthropic models can cache it.
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": effective_system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
            },
            {"role": "user", "content": rule_prompt},
        ]

        # Agent loop
        for iteration in range(self._config.max_iterations):
            logger.info("Agent iteration {}/{}", iteration + 1, self._config.max_iterations)

            response = await self._llm.completion(
                messages,
                tools=llm_tools if llm_tools else None,
                max_tokens=self._config.max_tokens_per_call,
                temperature=self._config.temperature,
            )
            _accumulate_usage(total_usage, response.token_usage)

            iterations_total = iteration + 1

            if response.tool_calls:
                tool_calls_total += len(response.tool_calls)
                messages.append(_build_assistant_msg(response))

                for tc in response.tool_calls:
                    with _lw_tool_span(lw_trace, tc.name, tc.arguments) as span:
                        result = await self._execute_tool_call(
                            backend, tc, iteration + 1,
                        )
                        if span is not None:
                            try:
                                span.update(output={"type": "json", "value": result[:2000]})
                            except Exception:
                                pass
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue

            if response.content:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await self._log_usage(
                    token_usage=total_usage,
                    model=getattr(self._llm, "_model", ""),
                    trigger_type=trigger_type,
                    rule_id=self._rule_id,
                    user_id=user_id,
                    session_id=session_id,
                    duration_ms=duration_ms,
                    tool_calls_count=tool_calls_total,
                    iterations=iterations_total,
                )
                return self._parse_final_output(response.content, total_usage)

            logger.warning("Agent received empty response at iteration {}", iteration + 1)
            break

        logger.warning(
            "Agent reached max iterations ({}), forcing output",
            self._config.max_iterations,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)
        await self._log_usage(
            token_usage=total_usage,
            model=getattr(self._llm, "_model", ""),
            trigger_type=trigger_type,
            rule_id=self._rule_id,
            user_id=user_id,
            session_id=session_id,
            duration_ms=duration_ms,
            tool_calls_count=tool_calls_total,
            iterations=iterations_total,
        )
        return AnalyzerOutput(
            summary=f"Agent 达到最大迭代次数 ({self._config.max_iterations})，分析可能不完整。",
            token_usage=total_usage,
        )

    # ------------------------------------------------------------------
    # Tool resolution
    # ------------------------------------------------------------------

    def _resolve_tools_and_backend(self) -> tuple[list[ToolInfo], Any]:
        """Resolve tools and backend based on init params."""
        # Explicit tools provided (unified mode)
        if self._explicit_tools is not None:
            return self._explicit_tools, self  # self as backend, routes to executors
        # DAL mode
        if self._dal is not None:
            return self._dal.get_tools(), self._dal
        # MCP mode
        if self._mcp is not None:
            # Note: MCP tools need async list_tools, but we call from sync context
            # The caller should have already listed tools; fallback to empty
            return [], self._mcp
        raise ValueError("Agent requires tools, data_access_layer, or mcp_connection")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Route tool calls to executors (when Agent is the backend)."""
        executor = self._tool_executors.get(name)
        if executor is None:
            # Fallback to DAL if available
            if self._dal is not None:
                return await self._dal.call_tool(name, arguments)
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        result = await executor(**arguments)
        # Tool executors return dict, convert to string for LLM
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, indent=2)
        return str(result)

    # ------------------------------------------------------------------
    # Unified mode tool execution
    # ------------------------------------------------------------------

    # Tools whose results can be safely cached within a session
    _CACHEABLE_TOOLS = frozenset({"list_datasources", "get_schema", "query"})
    # Tools whose results can be masked (replaced with summary) after first use
    _MASKABLE_TOOLS = frozenset({"list_datasources", "get_schema"})
    # Max chars for tool result injected into message history (Step 3)
    _MAX_RESULT_CHARS = 4000

    async def _execute_tool_call_unified(
        self,
        backend: Any,
        tc: Any,
        iteration: int,
    ) -> str:
        """Execute tool call with session caching (Step 2) and result size control."""
        log_entry = {
            "iteration": iteration,
            "tool": tc.name,
            "args": tc.arguments,
        }
        self._tool_calls_log.append(log_entry)

        # Step 2: Session-level tool result cache
        cache_key: str | None = None
        if tc.name in self._CACHEABLE_TOOLS:
            cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
            if cache_key in self._tool_cache:
                logger.debug("Tool cache hit: {} {}", tc.name, cache_key[:60])
                cached = self._tool_cache[cache_key]
                log_entry["result"] = cached[:2000]
                log_entry["cache_hit"] = True
                return cached

        result = await self._execute_tool_call(backend, tc, iteration)

        if cache_key is not None:
            self._tool_cache[cache_key] = result

        # Store truncated result for evaluation (judge needs to verify data accuracy)
        log_entry["result"] = result[:2000] if result else ""

        # Step 3: Observation Masking — truncate very long results to keep context small
        if len(result) > self._MAX_RESULT_CHARS:
            summary = result[: self._MAX_RESULT_CHARS]
            result = summary + f"\n...[truncated, total {len(result)} chars]"
            logger.debug(
                "Tool result truncated: {} → {} chars", tc.name, self._MAX_RESULT_CHARS
            )

        return result

    # ------------------------------------------------------------------
    # Standard tool execution (with SQL audit)
    # ------------------------------------------------------------------

    async def _execute_tool_call(
        self,
        backend: Any,
        tc: Any,
        iteration: int,
    ) -> str:
        """Execute a single tool call with SQL validation and audit logging."""
        is_sql = tc.name in ("execute_sql", "query")
        sql_text = ""
        if is_sql:
            sql_text = tc.arguments.get("sql", "")

        # SQL validation (only when we have schema loaded)
        if self._config.validate_sql and self._schema and is_sql and sql_text:
            vr = validate_query(sql_text, self._schema)
            if not vr.valid:
                result = f"SQL 校验失败: {vr.error}"
                logger.warning("SQL validation failed: {}", vr.error)
                await self._log_query(
                    sql=sql_text, status="rejected", error=vr.error,
                    duration_ms=0, rows_returned=0,
                    agent_iteration=iteration,
                )
                return result

        start_t = time.monotonic() if is_sql else 0

        try:
            result = await backend.call_tool(tc.name, tc.arguments)
            if is_sql:
                dur = int((time.monotonic() - start_t) * 1000)
                rows = self._count_result_rows(result)
                await self._log_query(
                    sql=sql_text, status="success",
                    duration_ms=dur, rows_returned=rows,
                    agent_iteration=iteration,
                )
        except Exception as e:
            if is_sql:
                dur = int((time.monotonic() - start_t) * 1000)
                status = "timeout" if "timeout" in str(e).lower() else "error"
                await self._log_query(
                    sql=sql_text, status=status, error=str(e),
                    duration_ms=dur, rows_returned=0,
                    agent_iteration=iteration,
                )
            result = f"Error calling tool '{tc.name}': {e}"
            logger.warning("Tool call failed: {} - {}", tc.name, e)

        return result

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

            mcp_server = ""
            if self._mcp is not None:
                mcp_server = self._mcp.name

            async with get_session() as session:
                log = QueryLog(
                    rule_id=self._rule_id,
                    mcp_server=mcp_server,
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

    async def _log_usage(
        self,
        token_usage: TokenUsage,
        model: str,
        trigger_type: str = "",
        rule_id: str = "",
        user_id: str = "",
        session_id: str = "",
        duration_ms: int = 0,
        tool_calls_count: int = 0,
        iterations: int = 0,
    ) -> None:
        """Record LLM usage log entry to the database."""
        try:
            from order_guard.models import LLMUsageLog
            from order_guard.storage.database import get_session
            from order_guard.storage.crud import create
            from order_guard.tools.usage_tools import estimate_cost
            from order_guard.config import get_settings

            settings = get_settings()
            custom_pricing = settings.llm.custom_pricing or {}

            cost = estimate_cost(
                model,
                token_usage.prompt_tokens,
                token_usage.completion_tokens,
                custom_pricing=custom_pricing if custom_pricing else None,
            )

            async with get_session() as session:
                log = LLMUsageLog(
                    model=model,
                    prompt_tokens=token_usage.prompt_tokens,
                    completion_tokens=token_usage.completion_tokens,
                    total_tokens=token_usage.total_tokens,
                    cost_estimate_usd=cost,
                    trigger_type=trigger_type,
                    rule_id=rule_id,
                    user_id=user_id,
                    session_id=session_id,
                    duration_ms=duration_ms,
                    tool_calls_count=tool_calls_count,
                    iterations=iterations,
                )
                await create(session, log)
        except Exception as e:
            logger.debug("Failed to log LLM usage: {}", e)

    @staticmethod
    def _count_result_rows(result: str) -> int:
        """Count rows in a JSON result string (handles DBHub envelope)."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                inner = data.get("data")
                if isinstance(inner, dict):
                    rows = inner.get("rows")
                    if isinstance(rows, list):
                        return len(rows)
                rows = data.get("rows")
                if isinstance(rows, list):
                    return len(rows)
        except (json.JSONDecodeError, TypeError):
            pass
        return 0

    async def _load_schema_context(self) -> str:
        """Load schema, apply filter, and return formatted context string."""
        if self._mcp is None:
            return ""
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
            return AnalyzerOutput(
                summary=content[:500],
                raw_response=content,
                token_usage=token_usage,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_assistant_msg(response: Any) -> dict[str, Any]:
    """Build assistant message with tool calls for message history."""
    return {
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


def _accumulate_usage(total: TokenUsage, incoming: TokenUsage) -> None:
    """Accumulate token usage counts."""
    total.prompt_tokens += incoming.prompt_tokens
    total.completion_tokens += incoming.completion_tokens
    total.total_tokens += incoming.total_tokens


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
