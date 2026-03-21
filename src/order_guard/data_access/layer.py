"""DataAccessLayer — unified data access with fixed tool set for Agent."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from order_guard.data_access.base import BaseAdapter
from order_guard.data_access.models import DataSourceInfo, QueryResult
from order_guard.data_access.mcp_adapter import MCPAdapter
from order_guard.data_access.sql_adapter import SQLAdapter
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.manager import MCPManager
from order_guard.mcp.models import MCPServerConfig, ToolInfo

# Max rows auto-appended when LIMIT is missing
_DEFAULT_LIMIT = 1000

# SQL keywords that indicate write operations
_WRITE_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "REPLACE", "MERGE",
})

# ---------------------------------------------------------------------------
# Tool definitions — Agent always sees exactly these 3 tools
# ---------------------------------------------------------------------------

_TOOL_LIST_DATASOURCES = ToolInfo(
    name="list_datasources",
    description=(
        "列出所有已连接的数据源。\n"
        "\n"
        "返回格式: JSON 对象，包含 datasources 数组，每个元素有 id, name, type, description, tables_count。\n"
        "使用场景: 作为第一步调用，了解有哪些数据源可用。\n"
        "下一步: 拿到 datasource_id 后，调用 get_schema 查看表结构。"
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    server_name="data_access_layer",
)

_TOOL_GET_SCHEMA = ToolInfo(
    name="get_schema",
    description=(
        "获取指定数据源的表结构信息。\n"
        "\n"
        "- 不传 table_name: 返回所有表的列表（名称 + 字段数量）\n"
        "- 传 table_name: 返回该表的字段详情（名称、类型、说明、外键、索引、样例数据）\n"
        "\n"
        "使用场景: 在写 SQL 查询之前，先了解表结构，避免猜测字段名。\n"
        "重要: 不要跳过此步直接写 SQL，否则容易引用不存在的表或字段。\n"
        "返回格式: JSON 对象，包含 datasource_id + tables 列表或 table_detail。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "datasource_id": {
                "type": "string",
                "description": "数据源 ID（从 list_datasources 获取）",
                "examples": ["mysql-erp", "pg-analytics"],
            },
            "table_name": {
                "type": "string",
                "description": (
                    "表名（可选）。不传则返回所有表列表。"
                    "建议先不传获取表名，再传具体表名查看字段。"
                ),
                "examples": ["orders", "products", "inventory"],
            },
        },
        "required": ["datasource_id"],
    },
    server_name="data_access_layer",
)

_TOOL_QUERY = ToolInfo(
    name="query",
    description=(
        "对指定数据源执行只读 SQL 查询。\n"
        "\n"
        "限制:\n"
        "- 仅支持 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP 等写操作\n"
        "- 如果未指定 LIMIT，系统将自动添加 LIMIT 1000\n"
        "- 建议指定具体字段而非 SELECT *\n"
        "- 大表查询务必添加 WHERE 条件，避免全表扫描\n"
        "\n"
        "返回格式: JSON 对象，包含 data (查询结果数组), row_count, duration_ms, warnings。\n"
        "常见错误:\n"
        "- 表名不存在 → 先调用 get_schema 确认可用表\n"
        "- SELECT * 拉取大量数据 → 指定需要的字段\n"
        "- 缺少 WHERE 导致全表扫描 → 添加过滤条件"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "datasource_id": {
                "type": "string",
                "description": "数据源 ID（从 list_datasources 获取）",
                "examples": ["mysql-erp"],
            },
            "sql": {
                "type": "string",
                "description": (
                    "SQL 查询语句（仅 SELECT）。"
                    "建议包含 WHERE 条件和 LIMIT，指定具体字段名。"
                ),
                "examples": [
                    "SELECT id, name, quantity FROM inventory WHERE quantity < 10 LIMIT 100",
                ],
            },
        },
        "required": ["datasource_id", "sql"],
    },
    server_name="data_access_layer",
)

FIXED_TOOLS = [_TOOL_LIST_DATASOURCES, _TOOL_GET_SCHEMA, _TOOL_QUERY]


class DataAccessLayer:
    """Unified data access layer — provides fixed 3-tool interface for Agent.

    Manages adapters for all data sources (SQL via DBHub, generic MCP, etc.).
    Agent always sees exactly 3 tools: list_datasources, get_schema, query.
    """

    def __init__(
        self,
        mcp_manager: MCPManager,
        configs: list[MCPServerConfig] | None = None,
    ):
        self._mcp_manager = mcp_manager
        self._configs = configs or []
        self._adapters: dict[str, BaseAdapter] = {}
        # Schema cache: {datasource_id: {table_name: TableDetail}}
        self._schema_cache: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Initialize adapters for all connected MCP servers."""
        for config in self._configs:
            if not config.enabled:
                continue
            name = config.name
            try:
                conn = self._mcp_manager.get_connection(name)
                if not conn.is_connected():
                    logger.debug("DataAccessLayer: skipping '{}' (not connected)", name)
                    continue

                if config.type == "dbhub":
                    self._adapters[name] = SQLAdapter(conn, config)
                else:
                    self._adapters[name] = MCPAdapter(conn, config)

                logger.info("DataAccessLayer: registered adapter '{}' (type={})", name, config.type)
            except Exception as e:
                logger.warning("DataAccessLayer: failed to init adapter '{}': {}", name, e)

    # ------------------------------------------------------------------
    # Schema cache
    # ------------------------------------------------------------------

    async def warm_schema_cache(self) -> None:
        """Pre-load all table schemas into memory. Called once at startup."""
        for ds_id, adapter in self._adapters.items():
            try:
                # Get table list
                result = await adapter.get_schema(None)
                if result.error or not result.tables:
                    continue

                ds_cache: dict[str, Any] = {}
                for table_info in result.tables:
                    try:
                        detail_result = await adapter.get_schema(table_info.name)
                        if detail_result.table_detail:
                            ds_cache[table_info.name] = detail_result.table_detail
                    except Exception as e:
                        logger.debug("Schema cache: skip {}.{}: {}", ds_id, table_info.name, e)

                self._schema_cache[ds_id] = ds_cache
                logger.info(
                    "Schema cache warmed: {} — {} tables",
                    ds_id, len(ds_cache),
                )
            except Exception as e:
                logger.warning("Schema cache failed for '{}': {}", ds_id, e)

    def get_schema_context(self, datasource_id: str | None = None) -> str:
        """Build a compact schema context string from cache.

        If datasource_id is None, returns schema for all datasources.
        Returns DDL-like text suitable for injecting into an LLM prompt.
        """
        parts: list[str] = []
        sources = (
            {datasource_id: self._schema_cache.get(datasource_id, {})}
            if datasource_id
            else self._schema_cache
        )

        for ds_id, tables in sources.items():
            if not tables:
                continue
            parts.append(f"## 数据源: {ds_id}")
            for table_name, detail in tables.items():
                cols = ", ".join(
                    f"{c.name} {c.type}" + (f" -- {c.comment}" if c.comment else "")
                    for c in detail.columns
                )
                parts.append(f"  {table_name}({cols})")
                if detail.foreign_keys:
                    parts.append(f"    FK: {'; '.join(detail.foreign_keys)}")
            parts.append("")

        return "\n".join(parts)

    async def get_or_warm_schema_context(self) -> str:
        """Warm schema cache if empty, then return formatted schema text.

        Uses a fast bulk query (single SQL per datasource, no sample rows).
        Safe to call multiple times — only fetches once per session.
        Returns empty string if no adapters are configured.
        """
        if not self._adapters:
            return ""
        if not self._schema_cache:
            await self._warm_schema_cache_lite()
        return self.get_schema_context()

    async def _warm_schema_cache_lite(self) -> None:
        """Fast schema warm: one bulk SQL query per datasource, no sample rows.

        Populates _schema_cache with {datasource_id: {table_name: TableDetail}}.
        Skips adapters that don't support the bulk query.
        """
        from order_guard.data_access.models import TableDetail
        from order_guard.data_access.sql_adapter import SQLAdapter

        for ds_id, adapter in self._adapters.items():
            try:
                if isinstance(adapter, SQLAdapter):
                    bulk = await adapter.get_all_schema_bulk()
                    if bulk:
                        self._schema_cache[ds_id] = {
                            tname: TableDetail(name=tname, columns=cols)
                            for tname, cols in bulk.items()
                        }
                        logger.info(
                            "Schema cache (lite) warmed: {} — {} tables",
                            ds_id, len(bulk),
                        )
                    else:
                        logger.warning("Schema cache (lite): no tables found for '{}'", ds_id)
                else:
                    # Non-SQL adapters: fall back to full warm
                    result = await adapter.get_schema(None)
                    if not result.error and result.tables:
                        ds_cache: dict[str, Any] = {}
                        for table_info in result.tables:
                            try:
                                detail_result = await adapter.get_schema(table_info.name)
                                if detail_result.table_detail:
                                    ds_cache[table_info.name] = detail_result.table_detail
                            except Exception as e:
                                logger.debug("Schema cache: skip {}.{}: {}", ds_id, table_info.name, e)
                        self._schema_cache[ds_id] = ds_cache
                        logger.info("Schema cache warmed: {} — {} tables", ds_id, len(ds_cache))
            except Exception as e:
                logger.warning("Schema cache lite failed for '{}': {}", ds_id, e)

    @property
    def schema_cache(self) -> dict[str, dict[str, Any]]:
        return self._schema_cache

    def get_tools(self) -> list[ToolInfo]:
        """Return the fixed set of 3 data access tools."""
        return list(FIXED_TOOLS)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Route a tool call to the appropriate adapter."""
        if tool_name == "list_datasources":
            return await self._handle_list_datasources()
        elif tool_name == "get_schema":
            return await self._handle_get_schema(arguments)
        elif tool_name == "query":
            return await self._handle_query(arguments)
        else:
            return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_list_datasources(self) -> str:
        infos: list[dict] = []
        for name, adapter in self._adapters.items():
            try:
                info = await adapter.get_info()
                infos.append(info.model_dump())
            except Exception as e:
                logger.warning("Failed to get info for '{}': {}", name, e)
                infos.append({"id": name, "name": name, "type": "unknown", "error": str(e)})

        return json.dumps(
            {
                "datasources": infos,
                "count": len(infos),
                "hint": (
                    "选择一个 datasource_id，调用 get_schema 查看其表结构。"
                    if infos
                    else "没有可用数据源。请检查配置。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _handle_get_schema(self, args: dict) -> str:
        ds_id = args.get("datasource_id", "")
        table_name = args.get("table_name")

        adapter = self._adapters.get(ds_id)
        if adapter is None:
            available = ", ".join(self._adapters.keys()) or "(无)"
            return json.dumps(
                {
                    "error": f"数据源 '{ds_id}' 不存在。可用数据源: {available}",
                    "hint": "请先调用 list_datasources 获取可用数据源列表。",
                },
                ensure_ascii=False,
            )

        result = await adapter.get_schema(table_name)

        if result.error:
            hint = "请检查表名是否正确。"
            if table_name and hasattr(adapter, "_tables_cache") and adapter._tables_cache:
                available = ", ".join(sorted(adapter._tables_cache)[:20])
                hint = f"可用表: {available}。请选择正确的表名。"
            response = result.model_dump(exclude_none=True)
            response["hint"] = hint
            return json.dumps(response, ensure_ascii=False, indent=2)

        response = result.model_dump(exclude_none=True)
        if table_name is None:
            response["hint"] = "选择一个表名，调用 get_schema(datasource_id, table_name) 查看字段详情。"
        else:
            response["hint"] = "了解字段后，使用 query 工具编写 SELECT 查询。建议指定具体字段和 WHERE 条件。"
        return json.dumps(response, ensure_ascii=False, indent=2)

    async def _handle_query(self, args: dict) -> str:
        ds_id = args.get("datasource_id", "")
        sql = args.get("sql", "")

        # Parameter validation
        if not sql.strip():
            return json.dumps(
                {
                    "error": "sql 参数不能为空。",
                    "hint": "请提供一条 SELECT 查询语句。如需了解表结构，请先调用 get_schema。",
                },
                ensure_ascii=False,
            )

        adapter = self._adapters.get(ds_id)
        if adapter is None:
            available = ", ".join(self._adapters.keys()) or "(无)"
            return json.dumps(
                {
                    "error": f"数据源 '{ds_id}' 不存在。可用数据源: {available}",
                    "hint": "请先调用 list_datasources 获取可用数据源列表。",
                },
                ensure_ascii=False,
            )

        # SQL safety validation
        try:
            sql, warnings = self._validate_and_normalize_sql(sql, ds_id)
        except ValueError as e:
            return json.dumps(
                {
                    "error": str(e),
                    "hint": "请修正 SQL 后重试。如需了解表结构，请调用 get_schema。",
                },
                ensure_ascii=False,
            )

        # Execute query
        result = await adapter.query(sql)

        if result.success:
            # Parse data for structured response
            try:
                data = json.loads(result.data) if result.data else []
            except (json.JSONDecodeError, TypeError):
                data = result.data

            return json.dumps(
                {
                    "data": data,
                    "row_count": result.rows_count,
                    "duration_ms": result.duration_ms,
                    "warnings": warnings,
                    "hint": self._build_query_hint(result),
                },
                ensure_ascii=False,
                indent=2,
            )
        else:
            error_msg, hint = self._build_error_response(result.error, ds_id)
            return json.dumps(
                {"error": error_msg, "hint": hint},
                ensure_ascii=False,
            )

    # ------------------------------------------------------------------
    # SQL safety guard
    # ------------------------------------------------------------------

    def _validate_and_normalize_sql(self, sql: str, ds_id: str) -> tuple[str, list[str]]:
        """Validate SQL safety and normalize.

        Returns (normalized_sql, warnings).
        Raises ValueError on hard rejection (write ops, multi-statement).
        """
        warnings: list[str] = []
        sql_stripped = sql.strip().rstrip(";")
        sql_upper = sql_stripped.upper()

        # 1. Reject non-SELECT (write operations)
        tokens = sql_upper.split()
        first_keyword = tokens[0] if tokens else ""
        if first_keyword in _WRITE_KEYWORDS:
            raise ValueError(
                f"禁止执行写操作 ({first_keyword})。本系统仅支持 SELECT 查询。"
            )

        # 2. Reject multi-statement
        if ";" in sql_stripped:
            raise ValueError("禁止执行多条 SQL 语句。请一次只提交一条 SELECT 查询。")

        # 3. Warn on SELECT *
        if re.search(r"\bSELECT\s+\*", sql_upper):
            warnings.append(
                "建议指定具体字段而非 SELECT *，以减少数据传输量和提高可读性。"
            )

        # 4. Warn if no WHERE clause
        has_where = "WHERE" in sql_upper
        has_limit = "LIMIT" in sql_upper
        if not has_where and not has_limit:
            warnings.append(
                "建议添加 WHERE 条件缩小查询范围，避免全表扫描。"
            )
        elif not has_where:
            warnings.append(
                "查询没有 WHERE 条件，可能返回大量数据。建议添加过滤条件。"
            )

        # 5. Auto-append LIMIT if missing
        if not has_limit:
            sql_stripped = f"{sql_stripped} LIMIT {_DEFAULT_LIMIT}"
            warnings.append(
                f"已自动添加 LIMIT {_DEFAULT_LIMIT}。如需更多数据，请显式指定 LIMIT。"
            )

        # 6. Table existence check (best-effort, uses adapter cache if available)
        adapter = self._adapters.get(ds_id)
        if adapter and hasattr(adapter, "_tables_cache") and adapter._tables_cache:
            known_tables = {t.lower() for t in adapter._tables_cache}
            table_refs = re.findall(
                r"\b(?:FROM|JOIN)\s+[`\"]?(\w+)[`\"]?", sql_stripped, re.IGNORECASE
            )
            for tbl in table_refs:
                if tbl.lower() == "information_schema" or tbl.lower() == "dual":
                    continue
                if tbl.lower() not in known_tables:
                    available = ", ".join(sorted(adapter._tables_cache)[:20])
                    raise ValueError(
                        f"表 '{tbl}' 不存在。可用表: {available}。"
                        "请使用 get_schema 查看表结构。"
                    )

        return sql_stripped, warnings

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query_hint(result: QueryResult) -> str:
        if result.rows_count >= _DEFAULT_LIMIT:
            return (
                f"结果恰好 {_DEFAULT_LIMIT} 行，可能被 LIMIT 截断。"
                "如需完整数据，请增加 LIMIT 或添加 WHERE 条件精确过滤。"
            )
        if result.rows_count == 0:
            return (
                "查询无结果。请检查 WHERE 条件是否正确，"
                "或使用 get_schema 确认表名和字段名。"
            )
        if result.duration_ms > 5000:
            return (
                f"查询耗时 {result.duration_ms}ms，较慢。"
                "建议添加 WHERE 条件缩小范围或利用索引字段过滤。"
            )
        return ""

    def _build_error_response(self, error: str, ds_id: str) -> tuple[str, str]:
        error_lower = error.lower()
        if any(kw in error_lower for kw in ("doesn't exist", "not found", "no such table", "不存在")):
            adapter = self._adapters.get(ds_id)
            if adapter and hasattr(adapter, "_tables_cache") and adapter._tables_cache:
                available = ", ".join(sorted(adapter._tables_cache)[:20])
                return error, f"可用表: {available}。请使用 get_schema 查看表结构。"
            return error, "请先调用 get_schema 查看可用表列表。"
        if any(kw in error_lower for kw in ("unknown column", "no such column")):
            return error, "请调用 get_schema(datasource_id, table_name) 查看该表的字段列表。"
        if "timeout" in error_lower:
            return error, "查询超时。建议添加 WHERE 条件减少数据范围，或添加 LIMIT 限制返回行数。"
        return error, "请检查 SQL 语法和表/字段名称是否正确。"

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_adapter(self, datasource_id: str) -> BaseAdapter | None:
        """Get adapter by datasource ID."""
        return self._adapters.get(datasource_id)

    def list_datasource_ids(self) -> list[str]:
        """List all registered datasource IDs."""
        return list(self._adapters.keys())

    def get_mcp_connection(self, datasource_id: str) -> MCPConnection | None:
        """Get underlying MCP connection for a datasource (for schema loading etc.)."""
        try:
            return self._mcp_manager.get_connection(datasource_id)
        except KeyError:
            return None
