"""Base adapter interface for data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from order_guard.data_access.models import DataSourceInfo, SchemaResult, QueryResult


class BaseAdapter(ABC):
    """Abstract base class for data source adapters."""

    @abstractmethod
    async def get_info(self) -> DataSourceInfo:
        """Return metadata about this data source."""
        ...

    @abstractmethod
    async def get_schema(self, table_name: str | None = None) -> SchemaResult:
        """Get schema information.

        - table_name=None: return list of all tables
        - table_name=<name>: return column details for that table
        """
        ...

    @abstractmethod
    async def query(self, sql: str) -> QueryResult:
        """Execute a read query against this data source."""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if the data source is reachable."""
        ...
