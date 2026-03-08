"""Base connector interface for data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    """Abstract base for all data source connectors."""

    name: str
    type: str

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the data source is reachable."""
        ...

    @abstractmethod
    async def get_orders(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch order data."""
        ...

    @abstractmethod
    async def get_inventory(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch inventory/stock data."""
        ...

    @abstractmethod
    async def get_sales(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch sales data."""
        ...

    async def query(self, query_type: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Generic query dispatcher."""
        method_map = {
            "orders": self.get_orders,
            "inventory": self.get_inventory,
            "sales": self.get_sales,
        }
        method = method_map.get(query_type)
        if method is None:
            raise ValueError(f"Unknown query_type: {query_type}")
        return await method(params or {})
