"""Shared fixtures for scenario-based agent testing (Level 3).

This conftest provides:
1. OrderGuardAgent adapter — wraps the real Agent with all production tools
2. In-memory DB with seed data (both metadata AND business tables)
3. FakeMCPConnection — wraps in-memory SQLite for DAL query tools
4. Mock HTTP interception (for webhook/Feishu calls)
5. Seed data fixtures

L3 tests use REAL LLM calls and require an API key in .env.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from order_guard.config import get_settings


# ---------------------------------------------------------------------------
# In-memory DB for scenario tests (OrderGuard metadata: rules, alerts, etc.)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _scenario_engine():
    """Create a shared in-memory SQLite engine for all scenario tests."""
    import asyncio

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_init())
    yield engine
    loop.run_until_complete(engine.dispose())
    loop.close()


@pytest.fixture(autouse=True)
def _patch_scenario_db(_scenario_engine):
    """Patch storage.database to use the scenario in-memory engine."""
    import asyncio
    from order_guard.storage import database as _db

    original_engine = _db._engine
    _db._engine = _scenario_engine

    yield

    # Cleanup after each test
    async def _cleanup():
        async with AsyncSession(_scenario_engine, expire_on_commit=False) as session:
            for table in reversed(SQLModel.metadata.sorted_tables):
                try:
                    await session.execute(table.delete())
                except Exception:
                    pass  # Table may not exist in this engine
            await session.commit()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, _cleanup()).result()
        else:
            loop.run_until_complete(_cleanup())
    except RuntimeError:
        asyncio.run(_cleanup())

    _db._engine = original_engine


# ---------------------------------------------------------------------------
# FakeMCPConnection — wraps in-memory SQLite for business data queries
# ---------------------------------------------------------------------------

class FakeMCPConnection:
    """Fake MCP connection backed by an in-memory SQLite database.

    Supports the same interface as MCPConnection but executes SQL directly
    against a local SQLite database with seeded business data (inventory,
    orders, etc.).
    """

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def is_connected(self) -> bool:
        return True

    async def list_tools(self):
        """Return fake tool list matching what SQLAdapter expects."""
        from order_guard.mcp.models import ToolInfo
        return [
            ToolInfo(
                name="execute_sql",
                description="Execute SQL",
                input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
                server_name="test-db",
            ),
        ]

    async def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """Execute SQL against the in-memory SQLite."""
        if name != "execute_sql":
            return json.dumps({"error": f"Unknown tool: {name}"})

        sql = (arguments or {}).get("sql", "")
        try:
            cursor = self._db.execute(sql)
            if cursor.description:
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return json.dumps(rows, ensure_ascii=False)
            return json.dumps([])
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


def _create_business_db() -> sqlite3.Connection:
    """Create an in-memory SQLite with business data tables and seed rows."""
    db = sqlite3.connect(":memory:")

    # inventory table
    db.execute("""
        CREATE TABLE inventory (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            safety_stock INTEGER NOT NULL DEFAULT 10,
            warehouse TEXT DEFAULT '主仓',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.executemany(
        "INSERT INTO inventory (sku, name, quantity, safety_stock, warehouse) VALUES (?, ?, ?, ?, ?)",
        [
            ("SKU-001", "无线蓝牙耳机", 0, 50, "主仓"),       # 缺货！
            ("SKU-002", "手机保护壳", 200, 30, "主仓"),        # 正常
            ("SKU-003", "USB-C 数据线", 5, 20, "主仓"),        # 低于安全库存
            ("SKU-004", "笔记本电脑支架", 100, 15, "华东仓"),  # 正常
            ("SKU-005", "便携充电宝", 3, 25, "华南仓"),        # 低于安全库存
        ],
    )

    # orders table
    db.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            sku TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sku) REFERENCES inventory(sku)
        )
    """)
    db.executemany(
        "INSERT INTO orders (id, sku, quantity, amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("ORD-001", "SKU-001", 10, 299.0, "pending", "2026-03-14 10:00:00"),
            ("ORD-002", "SKU-001", 5, 149.5, "pending", "2026-03-14 11:00:00"),
            ("ORD-003", "SKU-002", 3, 59.7, "shipped", "2026-03-13 09:00:00"),
            ("ORD-004", "SKU-003", 20, 180.0, "pending", "2026-03-14 14:00:00"),
            ("ORD-005", "SKU-005", 8, 320.0, "pending", "2026-03-14 15:00:00"),
        ],
    )

    db.commit()
    return db


# ---------------------------------------------------------------------------
# DAL wiring fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def business_db():
    """Create business data SQLite and wire DAL so data tools work."""
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.data_access.sql_adapter import SQLAdapter
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.mcp.manager import MCPManager
    from order_guard.tools import data_tools

    db = _create_business_db()
    fake_conn = FakeMCPConnection(db)

    config = MCPServerConfig(
        name="test-db",
        type="dbhub",
        transport="stdio",
        command="fake",
        enabled=True,
    )

    # Build MCPManager with no real connections
    mgr = MCPManager()

    # Create DAL and manually register adapter
    dal = DataAccessLayer(mcp_manager=mgr, configs=[config])
    adapter = SQLAdapter(fake_conn, config)
    adapter._is_sqlite = True
    dal._adapters["test-db"] = adapter

    # Wire into data_tools module
    original_dal = data_tools._data_access_layer
    data_tools.configure(dal)

    yield db

    # Restore
    data_tools.configure(original_dal)
    db.close()


# ---------------------------------------------------------------------------
# Seed data fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seeded_data(business_db):
    """Seed the DB with metadata + business data. Returns dict of seeded objects.

    Depends on business_db to ensure DAL is wired before tests run.
    """
    from tests.scenarios.seed_data import seed_all
    result = await seed_all()
    result["business_db"] = business_db
    return result


# ---------------------------------------------------------------------------
# Mock HTTP fixture (intercepts httpx calls)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_http():
    """Mock httpx.AsyncClient to intercept webhook/Feishu HTTP calls.

    Returns a dict with recorded calls for assertion.
    """
    recorded = {"calls": []}

    class FakeResponse:
        status_code = 200
        text = '{"code": 0}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"code": 0}

        def raise_for_status(self):
            pass

    async def fake_post(url, **kwargs):
        recorded["calls"].append({"url": url, "kwargs": kwargs})
        return FakeResponse()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        yield recorded


# ---------------------------------------------------------------------------
# Tool assembly — mirrors production logic in feishu.py / cli.py
# ---------------------------------------------------------------------------

def assemble_all_tools():
    """Assemble all tool definitions + executors exactly as production does.

    This is the SINGLE SOURCE OF TRUTH for what tools the Agent should have.
    If a tool module is not imported here, scenario tests will fail.
    """
    from order_guard.tools import (
        data_tools,
        rule_tools,
        context_tools,
        alert_tools,
        health_tools,
        report_tools,
        usage_tools,
    )

    all_tools = (
        data_tools.TOOL_DEFINITIONS
        + rule_tools.TOOL_DEFINITIONS
        + context_tools.TOOL_DEFINITIONS
        + alert_tools.TOOL_DEFINITIONS
        + health_tools.TOOL_DEFINITIONS
        + report_tools.TOOL_DEFINITIONS
        + usage_tools.TOOL_DEFINITIONS
    )

    all_executors = {}
    all_executors.update(data_tools.TOOL_EXECUTORS)
    all_executors.update(rule_tools.TOOL_EXECUTORS)
    all_executors.update(context_tools.TOOL_EXECUTORS)
    all_executors.update(alert_tools.TOOL_EXECUTORS)
    all_executors.update(health_tools.TOOL_EXECUTORS)
    all_executors.update(report_tools.TOOL_EXECUTORS)
    all_executors.update(usage_tools.TOOL_EXECUTORS)

    return all_tools, all_executors


# ---------------------------------------------------------------------------
# Manifest: expected tool names (human-authored, not auto-discovered)
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    # data_tools (3)
    "list_datasources",
    "get_schema",
    "query",
    # rule_tools (6)
    "list_rules",
    "create_rule",
    "update_rule",
    "delete_rule",
    "test_rule",
    "get_rule_stats",
    # context_tools (3)
    "list_context",
    "add_context",
    "delete_context",
    # alert_tools (3)
    "list_alerts",
    "handle_alert",
    "get_alert_stats",
    # usage_tools (1)
    "get_usage_stats",
    # health_tools (1)
    "check_health",
    # report_tools (2)
    "manage_report",
    "preview_report",
}


# ---------------------------------------------------------------------------
# OrderGuard Agent Adapter for Scenario tests
# ---------------------------------------------------------------------------

try:
    import scenario

    class OrderGuardAgent(scenario.AgentAdapter):
        """Wraps the real OrderGuard Agent for scenario testing.

        Uses the same tool assembly as production with a real LLM client
        loaded from settings (.env).
        """

        def __init__(self, llm_client=None, db_session=None):
            self._llm_client = llm_client
            self._db_session = db_session
            self._agent = None

        def _build_agent(self):
            from order_guard.engine.agent import Agent, AgentConfig
            from order_guard.engine.llm_client import LLMClient

            all_tools, all_executors = assemble_all_tools()

            # Use real LLM client from settings if none provided
            llm = self._llm_client or LLMClient()

            self._agent = Agent(
                llm_client=llm,
                config=AgentConfig(
                    inject_schema=False,
                    inject_business_context=False,
                ),
                tools=all_tools,
                tool_executors=all_executors,
            )
            return self._agent

        @staticmethod
        def user_simulator() -> scenario.UserSimulatorAgent:
            """Build a UserSimulatorAgent using the same LLM as the project."""
            settings = get_settings()
            return scenario.UserSimulatorAgent(
                model=settings.llm.model,
                api_key=settings.llm.api_key.get_secret_value(),
                api_base=settings.llm.api_base or None,
            )

        async def call(self, input: scenario.AgentInput) -> scenario.AgentReturnTypes:
            if self._agent is None:
                self._build_agent()

            user_msg = ""
            history = []
            for msg in input.messages:
                if msg["role"] == "user":
                    user_msg = msg.get("content", "")
                history.append(msg)

            # Use the last user message as the query
            if history:
                history = history[:-1]  # Remove last (it's the user_message)

            result = await self._agent.run_unified(
                user_message=user_msg,
                context_messages=history if history else None,
            )

            # Build OpenAI-compatible tool_calls so scenario's has_tool_call works
            tool_calls = []
            for i, tc in enumerate(result.tool_calls_log):
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc["tool"],
                        "arguments": json.dumps(tc.get("args", {})),
                    },
                })

            msg: dict = {"role": "assistant", "content": result.response}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return msg

except ImportError:
    # scenario package not installed — L3 tests will be skipped
    pass


# ---------------------------------------------------------------------------
# Helper: build Agent for direct use (without scenario framework)
# ---------------------------------------------------------------------------

def build_e2e_agent(llm_client=None):
    """Build a fully-wired Agent for E2E testing.

    Uses real LLM from settings if llm_client not provided.
    """
    from order_guard.engine.agent import Agent, AgentConfig
    from order_guard.engine.llm_client import LLMClient

    all_tools, all_executors = assemble_all_tools()

    llm = llm_client or LLMClient()
    agent = Agent(
        llm_client=llm,
        config=AgentConfig(
            inject_schema=False,
            inject_business_context=False,
        ),
        tools=all_tools,
        tool_executors=all_executors,
    )
    return agent
