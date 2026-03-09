"""SQL query validator using SQLGlot — validates table/column references against schema."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from order_guard.mcp.schema import SchemaInfo

try:
    import sqlglot
    from sqlglot import exp

    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False


@dataclass
class ValidationResult:
    """Result of SQL validation."""

    valid: bool
    error: str = ""
    warnings: list[str] | None = None


def validate_query(sql: str, schema: SchemaInfo, *, dialect: str | None = None) -> ValidationResult:
    """Validate that a SQL query only references existing tables and columns.

    Args:
        sql: The SQL query to validate.
        schema: The known database schema.
        dialect: SQL dialect for parsing (e.g. "sqlite", "mysql", "postgres"). None = auto.

    Returns:
        ValidationResult with valid=True if OK, or error message if not.
    """
    if not SQLGLOT_AVAILABLE:
        logger.warning("sqlglot not installed, skipping SQL validation")
        return ValidationResult(valid=True)

    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except sqlglot.errors.ParseError as e:
        return ValidationResult(valid=False, error=f"SQL 语法错误: {e}")

    warnings: list[str] = []

    # Check table references
    for table_node in parsed.find_all(exp.Table):
        table_name = table_node.name
        if not table_name:
            continue
        # Skip subquery aliases and CTEs
        if table_name.lower() in ("dual",):
            continue
        if table_name not in schema.tables:
            available = ", ".join(schema.table_names[:20])
            return ValidationResult(
                valid=False,
                error=f"表 '{table_name}' 不存在。可用表: {available}",
            )

    # Check column references (only when table is explicitly specified)
    for col_node in parsed.find_all(exp.Column):
        col_table = col_node.table
        col_name = col_node.name
        if not col_table or not col_name:
            continue
        # Resolve table name
        if col_table in schema.tables:
            table_columns = schema.get_columns(col_table)
            if table_columns and col_name not in table_columns:
                return ValidationResult(
                    valid=False,
                    error=f"字段 '{col_table}.{col_name}' 不存在。可用字段: {', '.join(table_columns[:20])}",
                )

    return ValidationResult(valid=True, warnings=warnings if warnings else None)
