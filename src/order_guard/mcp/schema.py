"""Schema loader, filter, and context builder for anti-hallucination."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from order_guard.mcp.client import MCPConnection


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    """Column metadata."""

    name: str
    type: str = ""
    comment: str = ""


@dataclass
class IndexInfo:
    """Index metadata."""

    name: str
    columns: list[str] = field(default_factory=list)
    unique: bool = False


@dataclass
class ForeignKeyInfo:
    """Foreign key metadata."""

    column: str
    ref_table: str
    ref_column: str


@dataclass
class TableSchema:
    """Schema information for a single table."""

    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SchemaInfo:
    """Complete schema information for a database."""

    database: str = ""
    tables: dict[str, TableSchema] = field(default_factory=dict)

    @property
    def table_names(self) -> list[str]:
        return list(self.tables.keys())

    def get_columns(self, table_name: str) -> list[str]:
        """Get column names for a table."""
        t = self.tables.get(table_name)
        return [c.name for c in t.columns] if t else []


@dataclass
class SchemaFilterConfig:
    """Configuration for filtering sensitive tables/columns."""

    blocked_tables: list[str] = field(default_factory=list)
    blocked_columns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

class SchemaLoader:
    """Load schema information from an MCP connection (e.g. DBHub)."""

    def __init__(self, mcp_connection: MCPConnection, sample_rows: int = 3):
        self._mcp = mcp_connection
        self._sample_rows = sample_rows

    async def load(self) -> SchemaInfo:
        """Load full schema from MCP server by calling available tools."""
        schema = SchemaInfo(database=self._mcp.name)

        # Step 1: discover tables
        table_names = await self._discover_tables()
        logger.info("Schema loader: found {} tables from '{}'", len(table_names), self._mcp.name)

        # Step 2: for each table, get columns + sample data
        for table_name in table_names:
            table_schema = await self._load_table_schema(table_name)
            schema.tables[table_name] = table_schema

        return schema

    async def _discover_tables(self) -> list[str]:
        """Discover all table names via MCP tools."""
        tools = await self._mcp.list_tools()
        tool_names = {t.name for t in tools}

        # Try search_objects first (DBHub)
        if "search_objects" in tool_names:
            try:
                result = await self._mcp.call_tool("search_objects", {"query": ""})
                return self._parse_table_names(result)
            except Exception as e:
                logger.warning("search_objects failed: {}, trying execute_sql", e)

        # Fallback: use execute_sql to query information_schema or sqlite_master
        if "execute_sql" in tool_names:
            return await self._discover_tables_via_sql()

        # Fallback: list_tables (some MCP servers)
        if "list_tables" in tool_names:
            try:
                result = await self._mcp.call_tool("list_tables", {})
                return self._parse_table_names(result)
            except Exception as e:
                logger.warning("list_tables failed: {}", e)

        logger.warning("No table discovery tool available on '{}'", self._mcp.name)
        return []

    async def _discover_tables_via_sql(self) -> list[str]:
        """Discover tables via SQL (supports SQLite, MySQL, PostgreSQL)."""
        queries = [
            # SQLite
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            # MySQL / PostgreSQL information_schema
            "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'performance_schema', 'mysql', 'sys') ORDER BY table_name",
        ]
        for sql in queries:
            try:
                result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                names = self._parse_table_names_from_sql(result)
                if names:
                    return names
            except Exception:
                continue
        return []

    async def _load_table_schema(self, table_name: str) -> TableSchema:
        """Load column info and sample data for a table."""
        table = TableSchema(name=table_name)

        tools = await self._mcp.list_tools()
        tool_names = {t.name for t in tools}

        # Try describe_table (some MCP servers)
        if "describe_table" in tool_names:
            try:
                result = await self._mcp.call_tool("describe_table", {"table_name": table_name})
                table.columns = self._parse_columns(result)
            except Exception as e:
                logger.debug("describe_table failed for '{}': {}", table_name, e)

        # Fallback: PRAGMA or information_schema via SQL
        if not table.columns and "execute_sql" in tool_names:
            table.columns = await self._load_columns_via_sql(table_name)
            table.indexes = await self._load_indexes_via_sql(table_name)
            table.foreign_keys = await self._load_foreign_keys_via_sql(table_name)

        # Load sample data
        if self._sample_rows > 0 and "execute_sql" in tool_names:
            table.sample_rows = await self._load_sample_data(table_name)

        return table

    async def _load_columns_via_sql(self, table_name: str) -> list[ColumnInfo]:
        """Load columns via SQL."""
        queries = [
            # SQLite
            f"PRAGMA table_info('{table_name}')",
            # MySQL / PostgreSQL
            f"SELECT column_name, data_type, column_comment FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
        ]
        for sql in queries:
            try:
                result = await self._mcp.call_tool("execute_sql", {"sql": sql})
                columns = self._parse_columns_from_sql(result, table_name)
                if columns:
                    return columns
            except Exception:
                continue
        return []

    async def _load_indexes_via_sql(self, table_name: str) -> list[IndexInfo]:
        """Load index info via SQL."""
        try:
            # SQLite PRAGMA
            result = await self._mcp.call_tool("execute_sql", {"sql": f"PRAGMA index_list('{table_name}')"})
            return self._parse_indexes_from_pragma(result)
        except Exception:
            pass

        try:
            # MySQL / PostgreSQL
            sql = f"SELECT index_name, column_name, non_unique FROM information_schema.statistics WHERE table_name = '{table_name}' ORDER BY index_name, seq_in_index"
            result = await self._mcp.call_tool("execute_sql", {"sql": sql})
            return self._parse_indexes_from_info_schema(result)
        except Exception:
            pass

        return []

    async def _load_foreign_keys_via_sql(self, table_name: str) -> list[ForeignKeyInfo]:
        """Load foreign key info via SQL."""
        try:
            result = await self._mcp.call_tool("execute_sql", {"sql": f"PRAGMA foreign_key_list('{table_name}')"})
            return self._parse_fks_from_pragma(result)
        except Exception:
            return []

    async def _load_sample_data(self, table_name: str) -> list[dict[str, Any]]:
        """Load sample rows from a table."""
        try:
            sql = f"SELECT * FROM \"{table_name}\" LIMIT {self._sample_rows}"
            result = await self._mcp.call_tool("execute_sql", {"sql": sql})
            return self._parse_sample_rows(result)
        except Exception as e:
            logger.debug("Failed to load sample data for '{}': {}", table_name, e)
            return []

    # --- Parsers ---

    def _parse_table_names(self, result: str) -> list[str]:
        """Parse table names from search_objects or list_tables output."""
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
        # Try line-by-line
        return [line.strip() for line in result.strip().split("\n") if line.strip() and not line.startswith("---")]

    def _parse_table_names_from_sql(self, result: str) -> list[str]:
        """Parse table names from SQL query result."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                names = []
                for row in data:
                    if isinstance(row, dict):
                        val = row.get("name") or row.get("table_name") or row.get("Name") or row.get("TABLE_NAME")
                        if val:
                            names.append(val)
                    elif isinstance(row, (list, tuple)) and row:
                        names.append(str(row[0]))
                return names
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_columns(self, result: str) -> list[ColumnInfo]:
        """Parse columns from describe_table output."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                columns = []
                for item in data:
                    if isinstance(item, dict):
                        columns.append(ColumnInfo(
                            name=item.get("name") or item.get("column_name", ""),
                            type=item.get("type") or item.get("data_type", ""),
                            comment=item.get("comment") or item.get("column_comment", ""),
                        ))
                return columns
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_columns_from_sql(self, result: str, table_name: str) -> list[ColumnInfo]:
        """Parse columns from PRAGMA table_info or information_schema."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                columns = []
                for row in data:
                    if isinstance(row, dict):
                        # PRAGMA table_info format: {cid, name, type, notnull, dflt_value, pk}
                        name = row.get("name") or row.get("column_name") or row.get("COLUMN_NAME", "")
                        col_type = row.get("type") or row.get("data_type") or row.get("DATA_TYPE", "")
                        comment = row.get("comment") or row.get("column_comment") or row.get("COLUMN_COMMENT", "")
                        if name:
                            columns.append(ColumnInfo(name=name, type=col_type, comment=comment))
                return columns
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_indexes_from_pragma(self, result: str) -> list[IndexInfo]:
        """Parse indexes from SQLite PRAGMA index_list."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return [
                    IndexInfo(
                        name=row.get("name", ""),
                        unique=row.get("unique", 0) == 1,
                    )
                    for row in data
                    if isinstance(row, dict) and row.get("name")
                ]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_indexes_from_info_schema(self, result: str) -> list[IndexInfo]:
        """Parse indexes from information_schema.statistics."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                idx_map: dict[str, IndexInfo] = {}
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    name = row.get("index_name") or row.get("INDEX_NAME", "")
                    col = row.get("column_name") or row.get("COLUMN_NAME", "")
                    unique = not (row.get("non_unique", 1) or row.get("NON_UNIQUE", 1))
                    if name not in idx_map:
                        idx_map[name] = IndexInfo(name=name, unique=unique)
                    if col:
                        idx_map[name].columns.append(col)
                return list(idx_map.values())
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_fks_from_pragma(self, result: str) -> list[ForeignKeyInfo]:
        """Parse foreign keys from SQLite PRAGMA foreign_key_list."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return [
                    ForeignKeyInfo(
                        column=row.get("from", ""),
                        ref_table=row.get("table", ""),
                        ref_column=row.get("to", ""),
                    )
                    for row in data
                    if isinstance(row, dict) and row.get("from")
                ]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _parse_sample_rows(self, result: str) -> list[dict[str, Any]]:
        """Parse sample rows from SQL result."""
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)][:self._sample_rows]
        except (json.JSONDecodeError, TypeError):
            pass
        return []


# ---------------------------------------------------------------------------
# Schema filter
# ---------------------------------------------------------------------------

def filter_schema(schema: SchemaInfo, config: SchemaFilterConfig) -> SchemaInfo:
    """Filter out blocked tables and columns from schema."""
    blocked_tables = {t.lower() for t in config.blocked_tables}
    blocked_columns = {c.lower() for c in config.blocked_columns}

    filtered = SchemaInfo(database=schema.database)
    for table_name, table_schema in schema.tables.items():
        if table_name.lower() in blocked_tables:
            logger.debug("Schema filter: blocked table '{}'", table_name)
            continue

        # Filter columns
        filtered_columns = [
            c for c in table_schema.columns
            if c.name.lower() not in blocked_columns
        ]

        # Filter sample rows (remove blocked column keys)
        filtered_samples = []
        for row in table_schema.sample_rows:
            filtered_samples.append({
                k: v for k, v in row.items()
                if k.lower() not in blocked_columns
            })

        filtered.tables[table_name] = TableSchema(
            name=table_name,
            columns=filtered_columns,
            indexes=table_schema.indexes,
            foreign_keys=table_schema.foreign_keys,
            sample_rows=filtered_samples,
        )

    return filtered


# ---------------------------------------------------------------------------
# Schema context builder
# ---------------------------------------------------------------------------

def build_schema_context(schema: SchemaInfo, cold_tables: list[str] | None = None) -> str:
    """Build a markdown-formatted schema context string for Agent system prompt."""
    if not schema.tables:
        return ""

    cold_set = {t.lower() for t in (cold_tables or [])}

    lines: list[str] = []
    lines.append(f"## 可用数据库: {schema.database}")
    lines.append("")

    for table_name, table in schema.tables.items():
        if table_name.lower() in cold_set:
            lines.append(f"### 表: {table_name} ⚠️ [归档表 — 仅在明确需要历史数据时查询]")
        else:
            lines.append(f"### 表: {table_name}")

        # Columns table
        if table.columns:
            lines.append("| 字段 | 类型 | 说明 |")
            lines.append("|------|------|------|")
            for col in table.columns:
                comment = col.comment or ""
                lines.append(f"| {col.name} | {col.type} | {comment} |")
            lines.append("")

        # Foreign keys
        if table.foreign_keys:
            fk_strs = [f"{fk.column} → {fk.ref_table}.{fk.ref_column}" for fk in table.foreign_keys]
            lines.append(f"外键: {', '.join(fk_strs)}")
            lines.append("")

        # Indexes
        if table.indexes:
            idx_strs = []
            for idx in table.indexes:
                cols = f"({', '.join(idx.columns)})" if idx.columns else ""
                prefix = "UNIQUE " if idx.unique else ""
                idx_strs.append(f"{prefix}{idx.name}{cols}")
            lines.append(f"索引: {', '.join(idx_strs)}")
            lines.append("")

        # Sample data
        if table.sample_rows:
            lines.append(f"样例数据 ({len(table.sample_rows)}行):")
            for row in table.sample_rows:
                parts = [f"{k}={v}" for k, v in row.items()]
                lines.append(f"  {', '.join(parts)}")
            lines.append("")

    return "\n".join(lines)
