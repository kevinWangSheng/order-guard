"""MCP Adapter — access data through generic MCP Servers."""

from __future__ import annotations

import json
import time

from loguru import logger

from order_guard.data_access.base import BaseAdapter
from order_guard.data_access.models import (
    DataSourceInfo,
    QueryResult,
    SchemaResult,
    TableInfo,
)
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.models import MCPServerConfig


class MCPAdapter(BaseAdapter):
    """Access data through a generic MCP Server (non-SQL)."""

    def __init__(self, mcp_connection: MCPConnection, config: MCPServerConfig):
        self._mcp = mcp_connection
        self._config = config

    async def get_info(self) -> DataSourceInfo:
        tools = await self._mcp.list_tools()
        return DataSourceInfo(
            id=self._config.name,
            name=self._config.name,
            type="mcp",
            description=f"MCP Server with {len(tools)} tools: {', '.join(t.name for t in tools[:5])}",
            tables_count=0,
        )

    async def get_schema(self, table_name: str | None = None) -> SchemaResult:
        """For generic MCP, 'schema' means available tools."""
        ds_id = self._config.name
        try:
            tools = await self._mcp.list_tools()
            tables = [
                TableInfo(name=t.name, columns_count=len(t.input_schema.get("properties", {})))
                for t in tools
            ]
            return SchemaResult(datasource_id=ds_id, tables=tables)
        except Exception as e:
            return SchemaResult(datasource_id=ds_id, error=str(e))

    async def query(self, sql: str) -> QueryResult:
        """For generic MCP, route the 'sql' to the best matching tool.

        If execute_sql is available, use it. Otherwise try the first available tool.
        """
        ds_id = self._config.name
        t0 = time.monotonic()
        try:
            tools = await self._mcp.list_tools()
            tool_names = {t.name for t in tools}

            # Prefer execute_sql / read_query if available
            # Map tool name → parameter name for the SQL argument
            tool_param_map = {
                "execute_sql": "sql",
                "read_query": "query",  # SQLite MCP server uses 'query' param
                "query": "sql",
            }
            for tool_name in ("execute_sql", "read_query", "query"):
                if tool_name in tool_names:
                    param_name = tool_param_map.get(tool_name, "sql")
                    result = await self._mcp.call_tool(tool_name, {param_name: sql})
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    return QueryResult(
                        datasource_id=ds_id,
                        success=True,
                        data=result,
                        rows_count=self._count_rows(result),
                        duration_ms=duration_ms,
                    )

            duration_ms = int((time.monotonic() - t0) * 1000)
            return QueryResult(
                datasource_id=ds_id,
                success=False,
                error=f"数据源 '{ds_id}' 没有可用的查询工具。可用工具: {', '.join(tool_names)}",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            return QueryResult(datasource_id=ds_id, success=False, error=str(e), duration_ms=duration_ms)

    async def test_connection(self) -> bool:
        try:
            return self._mcp.is_connected()
        except Exception:
            return False

    @staticmethod
    def _count_rows(result: str) -> int:
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
