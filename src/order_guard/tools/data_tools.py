"""Data access tools — 3 tools migrated from DataAccessLayer for unified Agent.

These are thin wrappers that delegate to DataAccessLayer methods.
"""

from __future__ import annotations

from typing import Any

from order_guard.data_access.layer import FIXED_TOOLS, DataAccessLayer
from order_guard.mcp.models import ToolInfo

# Re-export tool definitions from DAL
TOOL_DEFINITIONS: list[ToolInfo] = list(FIXED_TOOLS)

# Module-level DAL reference (set via configure())
_UNSET = object()
_data_access_layer: DataAccessLayer | None = None


def configure(data_access_layer: Any = _UNSET) -> None:
    """Configure the DAL for data tools."""
    global _data_access_layer
    if data_access_layer is not _UNSET:
        _data_access_layer = data_access_layer


async def list_datasources(**kwargs: Any) -> str:
    """列出所有已连接的数据源。"""
    if _data_access_layer is None:
        return '{"error": "数据访问层未初始化。", "hint": "请检查数据源配置。"}'
    return await _data_access_layer.call_tool("list_datasources", kwargs)


async def get_schema(**kwargs: Any) -> str:
    """获取数据源的表结构。"""
    if _data_access_layer is None:
        return '{"error": "数据访问层未初始化。", "hint": "请检查数据源配置。"}'
    return await _data_access_layer.call_tool("get_schema", kwargs)


async def query(**kwargs: Any) -> str:
    """执行 SQL 查询。"""
    if _data_access_layer is None:
        return '{"error": "数据访问层未初始化。", "hint": "请检查数据源配置。"}'
    return await _data_access_layer.call_tool("query", kwargs)


TOOL_EXECUTORS: dict[str, Any] = {
    "list_datasources": list_datasources,
    "get_schema": get_schema,
    "query": query,
}
