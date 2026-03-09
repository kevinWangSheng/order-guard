"""Permission control for feishu bot — user_id → allowed MCP servers."""

from __future__ import annotations

from order_guard.config.settings import FeishuBotConfig


def get_allowed_servers(user_id: str, config: FeishuBotConfig) -> list[str]:
    """Get the list of MCP servers a user is allowed to query.

    Returns empty list if user has no permissions.
    """
    allowed: set[str] = set()

    for perm in config.permissions:
        if "*" in perm.user_ids or user_id in perm.user_ids:
            allowed.update(perm.mcp_servers)

    return list(allowed)


def route_to_server(text: str, allowed_servers: list[str]) -> str | None:
    """Simple server routing: check if the user mentioned a data source name.

    Falls back to first allowed server if no match.
    """
    if not allowed_servers:
        return None

    text_lower = text.lower()
    for server in allowed_servers:
        if server.lower() in text_lower:
            return server

    # Default to first allowed server
    return allowed_servers[0]
