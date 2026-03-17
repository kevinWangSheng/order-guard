"""Unified data access layer — fixed tool set for Agent."""

from order_guard.data_access.base import BaseAdapter
from order_guard.data_access.layer import DataAccessLayer
from order_guard.data_access.models import DataSourceInfo, QueryResult, SchemaResult
from order_guard.data_access.sql_adapter import SQLAdapter
from order_guard.data_access.mcp_adapter import MCPAdapter

__all__ = [
    "BaseAdapter",
    "DataAccessLayer",
    "DataSourceInfo",
    "QueryResult",
    "SchemaResult",
    "SQLAdapter",
    "MCPAdapter",
]
