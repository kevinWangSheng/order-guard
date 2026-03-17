"""Data models for the unified data access layer."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DataSourceInfo(BaseModel):
    """Metadata about a connected data source."""

    id: str
    name: str
    type: str  # "sql" | "mcp"
    description: str = ""
    tables_count: int = 0


class SchemaResult(BaseModel):
    """Schema information returned by get_schema tool."""

    datasource_id: str
    tables: list[TableInfo] | None = None  # When table_name is not specified
    table_detail: TableDetail | None = None  # When table_name is specified
    error: str = ""
    hint: str = ""


class TableInfo(BaseModel):
    """Brief table info for listing."""

    name: str
    columns_count: int = 0


class ColumnDetail(BaseModel):
    """Column metadata."""

    name: str
    type: str = ""
    comment: str = ""


class TableDetail(BaseModel):
    """Detailed table schema."""

    name: str
    columns: list[ColumnDetail] = Field(default_factory=list)
    foreign_keys: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    sample_rows: list[dict] = Field(default_factory=list)


class QueryResult(BaseModel):
    """Result from a data query."""

    datasource_id: str
    success: bool = True
    data: str = ""  # JSON string of results
    rows_count: int = 0
    error: str = ""
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    hint: str = ""
    truncated: bool = False
