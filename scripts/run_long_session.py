#!/usr/bin/env python3
"""Standalone long-running agent session test.

Usage:
    uv run python scripts/run_long_session.py              # full 35 turns
    uv run python scripts/run_long_session.py --short      # 15 turns
    uv run python scripts/run_long_session.py --turns 5    # custom turn count
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import os

# Ensure unbuffered output
sys.stdout.reconfigure(line_buffering=True)

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main(max_turns: int | None = None, short: bool = False):
    from order_guard.config import get_settings
    from order_guard.mcp import MCPManager
    from order_guard.mcp.models import MCPServerConfig
    from order_guard.data_access.layer import DataAccessLayer
    from order_guard.tools import data_tools, rule_tools, health_tools, report_tools
    from order_guard.storage.database import init_db
    from tests.scenarios.long_session_runner import (
        run_long_session, print_session_summary, save_long_session_report,
        LONG_SESSION_SCRIPT, SHORT_SESSION_SCRIPT,
    )

    print("Initializing...", flush=True)
    await init_db()

    settings = get_settings()
    print(f"Model: {settings.llm.model}", flush=True)

    mcp_configs = [
        MCPServerConfig(**c.model_dump())
        for c in settings.mcp_servers if c.enabled
    ]
    print(f"MCP servers: {len(mcp_configs)}", flush=True)

    mgr = MCPManager(mcp_configs)
    if mcp_configs:
        await mgr.connect_all()

    dal = DataAccessLayer(mcp_manager=mgr, configs=mcp_configs)
    await dal.initialize()

    data_tools.configure(data_access_layer=dal)
    rule_tools.configure(data_access_layer=dal, mcp_manager=mgr)
    health_tools.configure(mcp_manager=mgr)
    report_tools.configure(data_access_layer=dal, mcp_manager=mgr)

    schema_context = ""
    try:
        schema_context = await dal.get_or_warm_schema_context()
        print(f"Schema cached: {len(schema_context)} chars", flush=True)
    except Exception as e:
        print(f"Schema cache failed: {e}", flush=True)

    infra = {"dal": dal, "mcp_manager": mgr, "schema_context": schema_context}

    # Select script
    if short:
        script = SHORT_SESSION_SCRIPT
    else:
        script = LONG_SESSION_SCRIPT

    if max_turns and max_turns < len(script):
        script = script[:max_turns]

    print(f"\nStarting {len(script)}-turn session...\n", flush=True)

    try:
        report = await run_long_session(
            infra=infra,
            script=script,
            persona_name="李姐（运营主管）",
        )

        print_session_summary(report)
        report_path = save_long_session_report(report)
        print(f"\n📄 Report saved: {report_path}", flush=True)

    except KeyboardInterrupt:
        print("\n⚠️ Session interrupted by user", flush=True)
    finally:
        print("\nCleaning up MCP connections...", flush=True)
        try:
            await asyncio.wait_for(mgr.disconnect_all(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            print("MCP disconnect timed out, forcing exit", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Long-running agent session test")
    parser.add_argument("--short", action="store_true", help="Run short 15-turn version")
    parser.add_argument("--turns", type=int, help="Custom number of turns")
    args = parser.parse_args()

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    asyncio.run(main(max_turns=args.turns, short=args.short))
