"""Shared seed data factory for L3 scenario tests.

Seeds an in-memory SQLite database with realistic business data that
the LLM Agent can query via DAL tools.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from order_guard.models import (
    Alert,
    AlertRule,
    BusinessContext,
    ReportConfig,
    TaskRun,
)
from order_guard.storage.database import get_session
from order_guard.storage.crud import create


async def seed_alert_rules() -> list[AlertRule]:
    """Seed 3 alert rules for testing."""
    rules_data = [
        {
            "id": "rule-stockout",
            "name": "缺货检测",
            "mcp_server": "test-db",
            "prompt_template": "检查库存表中库存量低于安全库存的 SKU，列出缺货风险。",
            "schedule": "0 9 * * *",
            "data_window": "7d",
            "enabled": True,
        },
        {
            "id": "rule-returns",
            "name": "退货率异常",
            "mcp_server": "test-db",
            "prompt_template": "分析退货数据，检测退货率异常偏高的 SKU。",
            "schedule": "0 10 * * *",
            "data_window": "30d",
            "enabled": True,
        },
        {
            "id": "rule-disabled",
            "name": "已禁用规则",
            "mcp_server": "test-db",
            "prompt_template": "这条规则已禁用",
            "schedule": "0 0 * * *",
            "enabled": False,
        },
    ]
    rules = []
    for data in rules_data:
        async with get_session() as session:
            rule = AlertRule(**data)
            rule = await create(session, rule)
            rules.append(rule)
    return rules


async def seed_alerts() -> list[Alert]:
    """Seed alerts for testing — some resolved, some unresolved."""
    now = datetime.now(timezone.utc)
    alerts_data = [
        {
            "rule_id": "rule-stockout",
            "severity": "critical",
            "title": "SKU-001 缺货",
            "summary": "SKU-001 库存为0，建议立即补货",
            "status": "sent",
            "created_at": now - timedelta(hours=2),
        },
        {
            "rule_id": "rule-stockout",
            "severity": "warning",
            "title": "SKU-003 库存偏低",
            "summary": "SKU-003 库存低于安全线",
            "status": "sent",
            "created_at": now - timedelta(hours=1),
        },
        {
            "rule_id": "rule-returns",
            "severity": "warning",
            "title": "SKU-004 退货率偏高",
            "summary": "SKU-004 退货率 15%",
            "status": "sent",
            "resolution": "handled",
            "resolved_at": now - timedelta(minutes=30),
            "note": "已联系供应商",
            "created_at": now - timedelta(hours=3),
        },
    ]
    alerts = []
    for data in alerts_data:
        async with get_session() as session:
            alert = Alert(**data)
            alert = await create(session, alert)
            alerts.append(alert)
    return alerts


async def seed_business_context() -> list[BusinessContext]:
    """Seed business knowledge entries."""
    entries = [
        {"content": "主要供应商是义乌XX工厂", "category": "supplier", "source": "config"},
        {"content": "3月全线提价5%", "category": "promotion", "source": "chat"},
    ]
    result = []
    for data in entries:
        async with get_session() as session:
            ctx = BusinessContext(**data)
            ctx = await create(session, ctx)
            result.append(ctx)
    return result


async def seed_report_configs() -> list[ReportConfig]:
    """Seed report configurations."""
    configs = [
        {
            "id": "daily-report",
            "name": "每日经营报告",
            "schedule": "0 9 * * *",
            "mcp_server": "test-db",
            "focus": "统计今日销售额、订单量、库存异常",
            "enabled": True,
        },
    ]
    result = []
    for data in configs:
        async with get_session() as session:
            config = ReportConfig(**data)
            config = await create(session, config)
            result.append(config)
    return result


async def seed_all():
    """Seed all test data. Call in conftest fixture."""
    rules = await seed_alert_rules()
    alerts = await seed_alerts()
    contexts = await seed_business_context()
    reports = await seed_report_configs()
    return {
        "rules": rules,
        "alerts": alerts,
        "contexts": contexts,
        "reports": reports,
    }
