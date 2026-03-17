"""Rule management — load, sync, CRUD for alert rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import yaml
from loguru import logger

from order_guard.models import AlertRule
from order_guard.storage.database import get_session
from order_guard.storage.crud import create, get_by_id, list_all, update


class RuleManager:
    """Manage alert rules — YAML loading and DB operations."""

    def __init__(self, rules_file: str | None = None):
        if rules_file is None:
            try:
                from order_guard.config import get_settings
                rules_file = get_settings().rules_file
            except Exception:
                rules_file = "rules.yaml"
        self._rules_file = rules_file

    # ------------------------------------------------------------------
    # YAML loading + DB sync
    # ------------------------------------------------------------------

    def load_rules_from_yaml(self) -> list[dict[str, Any]]:
        """Load rules from YAML file."""
        path = Path(self._rules_file)
        if not path.exists():
            logger.warning("Rules file not found: {}", self._rules_file)
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("rules", [])

    async def sync_rules_to_db(self) -> int:
        """Sync YAML rules to database. Updates existing, creates new."""
        yaml_rules = self.load_rules_from_yaml()
        synced = 0

        for rule_def in yaml_rules:
            rule_id = rule_def.get("id", "")
            if not rule_id:
                continue

            async with get_session() as session:
                existing = await get_by_id(session, AlertRule, rule_id)

                if existing:
                    # Update existing rule from YAML
                    await update(
                        session,
                        existing,
                        name=rule_def.get("name", existing.name),
                        description=rule_def.get("description", existing.description),
                        prompt_template=rule_def.get("prompt", existing.prompt_template),
                        mcp_server=rule_def.get("mcp_server", existing.mcp_server),
                        data_window=rule_def.get("data_window", existing.data_window),
                        enabled=rule_def.get("enabled", existing.enabled),
                    )
                    logger.info("Rule synced (updated): {}", rule_id)
                else:
                    # Create new rule
                    new_rule = AlertRule(
                        id=rule_id,
                        name=rule_def.get("name", ""),
                        description=rule_def.get("description", ""),
                        prompt_template=rule_def.get("prompt", ""),
                        mcp_server=rule_def.get("mcp_server", ""),
                        data_window=rule_def.get("data_window", ""),
                        enabled=rule_def.get("enabled", True),
                    )
                    await create(session, new_rule)
                    logger.info("Rule synced (created): {}", rule_id)
                synced += 1

        return synced

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def get_rule(self, rule_id: str) -> AlertRule | None:
        async with get_session() as session:
            return await get_by_id(session, AlertRule, rule_id)

    async def list_rules(self, *, enabled_only: bool = False) -> Sequence[AlertRule]:
        async with get_session() as session:
            filters = {"enabled": True} if enabled_only else None
            return await list_all(session, AlertRule, order_by="created_at", filters=filters)

    async def create_rule(self, **kwargs: Any) -> AlertRule:
        async with get_session() as session:
            rule = AlertRule(**kwargs)
            return await create(session, rule)

    async def update_rule(self, rule_id: str, **kwargs: Any) -> AlertRule | None:
        async with get_session() as session:
            rule = await get_by_id(session, AlertRule, rule_id)
            if rule is None:
                return None
            return await update(session, rule, **kwargs)

    async def toggle_rule(self, rule_id: str, enabled: bool) -> AlertRule | None:
        return await self.update_rule(rule_id, enabled=enabled)

    async def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule from the database."""
        async with get_session() as session:
            rule = await get_by_id(session, AlertRule, rule_id)
            if rule is None:
                return False
            await session.delete(rule)
            await session.flush()
            return True
