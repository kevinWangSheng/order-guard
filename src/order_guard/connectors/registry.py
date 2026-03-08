"""Connector registry — discover and manage data source connectors."""

from __future__ import annotations

from typing import Any

from order_guard.connectors.base import BaseConnector
from order_guard.connectors.mock import MockConnector

# Map of type -> connector class
_CONNECTOR_TYPES: dict[str, type[BaseConnector]] = {
    "mock": MockConnector,
}


class ConnectorRegistry:
    """Registry that creates and manages connector instances from config."""

    def __init__(self):
        self._instances: dict[str, BaseConnector] = {}

    def register_from_config(self, connectors_config: list[dict[str, Any]]) -> None:
        """Register connectors from configuration list."""
        for cfg in connectors_config:
            name = cfg.get("name", "")
            ctype = cfg.get("type", "")
            enabled = cfg.get("enabled", True)

            if not enabled:
                continue

            cls = _CONNECTOR_TYPES.get(ctype)
            if cls is None:
                raise ValueError(
                    f"Unknown connector type '{ctype}' for connector '{name}'. "
                    f"Available types: {list(_CONNECTOR_TYPES.keys())}"
                )

            extra_config = cfg.get("config", {})
            self._instances[name] = cls(config=extra_config)

    def get(self, name: str) -> BaseConnector:
        """Get a connector instance by name."""
        if name not in self._instances:
            raise KeyError(
                f"Connector '{name}' not found. "
                f"Available: {list(self._instances.keys())}"
            )
        return self._instances[name]

    def list_names(self) -> list[str]:
        """List all registered connector names."""
        return list(self._instances.keys())

    def register(self, name: str, connector: BaseConnector) -> None:
        """Manually register a connector instance."""
        self._instances[name] = connector
