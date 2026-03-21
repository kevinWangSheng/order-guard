"""SQL Adapter — access SQL databases via DBHub MCP Server."""

from __future__ import annotations

import json
import time

from loguru import logger

from order_guard.data_access.base import BaseAdapter
from order_guard.data_access.models import (
    ColumnDetail,
    DataSourceInfo,
    QueryResult,
    SchemaResult,
    TableDetail,
    TableInfo,
)
from order_guard.mcp.client import MCPConnection
from order_guard.mcp.models import MCPServerConfig


class SQLAdapter(BaseAdapter):
    """Access SQL databases through DBHub MCP Server."""

    def __init__(self, mcp_connection: MCPConnection, config: MCPServerConfig):
        self._mcp = mcp_connection
        self._config = config
        self._tables_cache: list[str] | None = None
        self._is_sqlite: bool = False  # Set during table discovery

    async def get_info(self) -> DataSourceInfo:
        tables = await self._get_table_names()
        desc_parts = []
        if self._config.databases:
            desc_parts.append(f"databases: {', '.join(d.alias for d in self._config.databases)}")
        desc_parts.append(f"type: {self._config.type}")
        return DataSourceInfo(
            id=self._config.name,
            name=self._config.name,
            type="sql",
            description="; ".join(desc_parts),
            tables_count=len(tables),
        )

    async def get_schema(self, table_name: str | None = None) -> SchemaResult:
        ds_id = self._config.name
        try:
            if table_name is None:
                tables = await self._get_table_names()
                table_infos = []
                for t in tables:
                    cols = await self._get_columns(t)
                    table_infos.append(TableInfo(name=t, columns_count=len(cols)))
                return SchemaResult(datasource_id=ds_id, tables=table_infos)
            else:
                cols = await self._get_columns(table_name)
                if not cols:
                    return SchemaResult(
                        datasource_id=ds_id,
                        error=f"表 '{table_name}' 不存在或无法获取字段信息",
                    )
                fks = await self._get_foreign_keys(table_name)
                indexes = await self._get_indexes(table_name)
                samples = await self._get_sample_rows(table_name)
                detail = TableDetail(
                    name=table_name,
                    columns=cols,
                    foreign_keys=fks,
                    indexes=indexes,
                    sample_rows=samples,
                )
                return SchemaResult(datasource_id=ds_id, table_detail=detail)
        except Exception as e:
            logger.warning("SQLAdapter get_schema error: {}", e)
            return SchemaResult(datasource_id=ds_id, error=str(e))

    async def query(self, sql: str) -> QueryResult:
        ds_id = self._config.name
        start = time.monotonic()
        try:
            result = await self._mcp.call_tool("execute_sql", {"sql": sql})
            duration_ms = int((time.monotonic() - start) * 1000)
            rows_count = self._count_rows(result)
            return QueryResult(
                datasource_id=ds_id,
                success=True,
                data=result,
                rows_count=rows_count,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return QueryResult(
                datasource_id=ds_id,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    async def test_connection(self) -> bool:
        try:
            return self._mcp.is_connected()
        except Exception:
            return False

    async def get_all_schema_bulk(self) -> dict[str, list[ColumnDetail]]:
        """Fetch all tables and their columns in a single SQL query (no sample rows).

        Returns {table_name: [ColumnDetail, ...]} for all tables in the current database.
        Much faster than calling get_schema() per table — used for schema injection.
        """
        tools = await self._mcp.list_tools()
        tool_names = {t.name for t in tools}

        if "execute_sql" not in tool_names:
            return {}

        # MySQL / MariaDB
        mysql_sql = (
            "SELECT table_name, column_name, data_type, COALESCE(column_comment, '') as column_comment "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "ORDER BY table_name, ordinal_position"
        )
        # PostgreSQL
        pg_sql = (
            "SELECT table_name, column_name, data_type, '' as column_comment "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name, ordinal_position"
        )
        # SQLite
        sqlite_tables_sql = (
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )

        for sql in (mysql_sql, pg_sql):
            try:
                result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                rows = self._extract_rows(result)
                if not rows:
                    continue
                tables: dict[str, list[ColumnDetail]] = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    tname = row.get("table_name") or row.get("TABLE_NAME") or ""
                    col = row.get("column_name") or row.get("COLUMN_NAME") or ""
                    dtype = row.get("data_type") or row.get("DATA_TYPE") or ""
                    comment = row.get("column_comment") or row.get("COLUMN_COMMENT") or ""
                    if tname and col:
                        tables.setdefault(tname, []).append(
                            ColumnDetail(name=col, type=dtype, comment=comment)
                        )
                if tables:
                    return tables
            except Exception:
                continue

        # SQLite fallback: get table list then PRAGMA per table
        try:
            result = await self._mcp.call_tool("execute_sql", {"sql": sqlite_tables_sql})
            names = self._parse_names_from_sql(result)
            if names:
                self._is_sqlite = True
                tables = {}
                for tname in names:
                    cols = await self._get_columns(tname)
                    if cols:
                        tables[tname] = cols
                return tables
        except Exception:
            pass

        return {}

    # --- Internal helpers ---

    async def _get_table_names(self) -> list[str]:
        if self._tables_cache is not None:
            return self._tables_cache

        tools = await self._mcp.list_tools()
        tool_names = {t.name for t in tools}

        # Try search_objects (DBHub)
        if "search_objects" in tool_names:
            try:
                result = await self._mcp.call_tool("search_objects", {"query": ""})
                names = self._parse_names(result)
                if names:
                    self._tables_cache = names
                    return names
            except Exception:
                pass

        # Try SQL discovery — filter by current database to avoid phantom tables
        if "execute_sql" in tool_names:
            for sql in (
                # MySQL: filter by current schema
                "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name",
                # PostgreSQL: public schema only
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name",
                # Old fallback (may return cross-db tables on some configs)
                "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'performance_schema', 'mysql', 'sys') ORDER BY table_name",
            ):
                try:
                    result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                    names = self._parse_names_from_sql(result)
                    if names:
                        self._tables_cache = names
                        return names
                except Exception:
                    continue

            try:
                result = await self._mcp.call_tool("execute_sql", {
                    "sql": "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
                })
                names = self._parse_names_from_sql(result)
                if names:
                    self._is_sqlite = True
                    self._tables_cache = names
                    return names
            except Exception:
                pass

        # Try list_tables
        if "list_tables" in tool_names:
            try:
                result = await self._mcp.call_tool("list_tables", {})
                names = self._parse_names(result)
                if names:
                    self._tables_cache = names
                    return names
            except Exception:
                pass

        self._tables_cache = []
        return []

    async def _get_columns(self, table_name: str) -> list[ColumnDetail]:
        queries = [
            f"SELECT column_name, data_type, column_comment FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
            f"SELECT column_name, data_type, '' as column_comment FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
            f"PRAGMA table_info('{table_name}')",
        ]
        for sql in queries:
            try:
                result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                cols = self._parse_columns(result)
                if cols:
                    return cols
            except Exception:
                continue
        return []

    async def _get_foreign_keys(self, table_name: str) -> list[str]:
        if not self._is_sqlite:
            return []  # FK discovery via PRAGMA only works on SQLite
        try:
            result = await self._mcp.call_tool(
                "execute_sql", {"sql": f"PRAGMA foreign_key_list('{table_name}')"}
            )
            rows = self._extract_rows(result)
            return [
                f"{r.get('from', '')} → {r.get('table', '')}.{r.get('to', '')}"
                for r in rows if isinstance(r, dict) and r.get("from")
            ]
        except Exception:
            return []

    async def _get_indexes(self, table_name: str) -> list[str]:
        queries = [
            f"SELECT index_name, column_name FROM information_schema.statistics WHERE table_name = '{table_name}' ORDER BY index_name, seq_in_index",
            f"SELECT indexname FROM pg_indexes WHERE tablename = '{table_name}'",
        ]
        for sql in queries:
            try:
                result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                rows = self._extract_rows(result)
                return list({
                    r.get("index_name") or r.get("indexname", "")
                    for r in rows if isinstance(r, dict)
                } - {""})
            except Exception:
                continue

        if self._is_sqlite:
            try:
                result = await self._mcp.call_tool(
                    "execute_sql", {"sql": f"PRAGMA index_list('{table_name}')"}
                )
                rows = self._extract_rows(result)
                return [r.get("name", "") for r in rows if isinstance(r, dict) and r.get("name")]
            except Exception:
                pass
        return []

    async def _get_sample_rows(self, table_name: str, limit: int = 3) -> list[dict]:
        for quote in ("`", '"'):
            try:
                result = await self._mcp.call_tool(
                    "execute_sql",
                    {"sql": f"SELECT * FROM {quote}{table_name}{quote} LIMIT {limit}"},
                )
                rows = self._extract_rows(result)
                return [r for r in rows if isinstance(r, dict)][:limit]
            except Exception:
                continue
        return []

    # --- Parsers ---

    @staticmethod
    def _extract_rows(result: str) -> list:
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict):
                rows = inner.get("rows")
                if isinstance(rows, list):
                    return rows
            rows = data.get("rows")
            if isinstance(rows, list):
                return rows
        return []

    @staticmethod
    def _parse_names(result: str) -> list[str]:
        try:
            data = json.loads(result)
            if isinstance(data, list):
                names = []
                for item in data:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("table_name") or item.get("table")
                        if name:
                            names.append(name)
                return names
        except (json.JSONDecodeError, TypeError):
            pass
        return [l.strip() for l in result.strip().split("\n") if l.strip() and not l.startswith("---")]

    @staticmethod
    def _parse_names_from_sql(result: str) -> list[str]:
        try:
            data = json.loads(result)
            rows = SQLAdapter._extract_rows_static(data)
            names = []
            for row in rows:
                if isinstance(row, dict):
                    val = row.get("name") or row.get("table_name") or row.get("TABLE_NAME")
                    if not val:
                        vals = list(row.values())
                        val = vals[0] if vals else None
                    if val:
                        names.append(str(val))
            return names
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _extract_rows_static(data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict):
                rows = inner.get("rows")
                if isinstance(rows, list):
                    return rows
            rows = data.get("rows")
            if isinstance(rows, list):
                return rows
        return []

    @staticmethod
    def _parse_columns(result: str) -> list[ColumnDetail]:
        try:
            data = json.loads(result)
            rows = SQLAdapter._extract_rows_static(data)
            cols = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("name") or row.get("column_name") or row.get("COLUMN_NAME", "")
                col_type = row.get("type") or row.get("data_type") or row.get("DATA_TYPE", "")
                comment = row.get("comment") or row.get("column_comment") or row.get("COLUMN_COMMENT", "")
                if name:
                    cols.append(ColumnDetail(name=name, type=col_type, comment=comment))
            return cols
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _count_rows(result: str) -> int:
        try:
            data = json.loads(result)
            rows = SQLAdapter._extract_rows_static(data)
            if rows:
                return len(rows)
            if isinstance(data, list):
                return len(data)
        except (json.JSONDecodeError, TypeError):
            pass
        return 0
