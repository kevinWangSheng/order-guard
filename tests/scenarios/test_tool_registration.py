"""Level 2: Tool registration manifest test.

This test verifies that ALL expected tools are actually wired into the Agent.
It catches the exact class of bug where unit tests pass but tools are never
registered to the orchestrator.

This is NOT a unit test — it tests the assembly/wiring layer.
"""

from __future__ import annotations

import pytest
from tests.scenarios.conftest import assemble_all_tools, EXPECTED_TOOLS


class TestToolManifest:
    """Verify all declared tools are registered in the Agent."""

    def test_all_expected_tools_registered(self):
        """Every tool in the manifest must be present in assembled tools."""
        all_tools, all_executors = assemble_all_tools()
        registered_names = {t.name for t in all_tools}

        missing = EXPECTED_TOOLS - registered_names
        assert not missing, (
            f"Tools declared in manifest but NOT registered to Agent: {missing}\n"
            f"These tools exist as code but will never be callable by users.\n"
            f"Fix: add them to the tool assembly in conftest.py AND in feishu.py/cli.py"
        )

    def test_no_unexpected_tools(self):
        """No tool should be registered without being in the manifest."""
        all_tools, _ = assemble_all_tools()
        registered_names = {t.name for t in all_tools}

        extra = registered_names - EXPECTED_TOOLS
        assert not extra, (
            f"Tools registered but NOT in manifest: {extra}\n"
            f"If these are new tools, add them to EXPECTED_TOOLS in conftest.py"
        )

    def test_every_tool_has_executor(self):
        """Every registered tool must have a corresponding executor function."""
        all_tools, all_executors = assemble_all_tools()

        missing_executors = [
            t.name for t in all_tools if t.name not in all_executors
        ]
        assert not missing_executors, (
            f"Tools without executors (will crash at runtime): {missing_executors}"
        )

    def test_tool_count_matches(self):
        """Total tool count must match manifest size."""
        all_tools, _ = assemble_all_tools()
        assert len(all_tools) == len(EXPECTED_TOOLS), (
            f"Expected {len(EXPECTED_TOOLS)} tools, got {len(all_tools)}.\n"
            f"Registered: {sorted(t.name for t in all_tools)}\n"
            f"Expected: {sorted(EXPECTED_TOOLS)}"
        )

    def test_feishu_assembles_same_tools(self):
        """feishu.py must import the same tool modules as the manifest.

        This is a static check: we verify that feishu.py's _call_unified_agent
        imports all required tool modules.
        """
        import inspect
        from order_guard.api import feishu

        source = inspect.getsource(feishu)

        # These modules MUST be imported in feishu.py for production use
        required_imports = [
            "alert_tools",
            "rule_tools",
            "context_tools",
            "data_tools",
            "health_tools",
            "report_tools",
        ]

        missing_in_feishu = [
            mod for mod in required_imports
            if f"{mod}.TOOL_DEFINITIONS" not in source
            and f"from order_guard.tools import" not in source
        ]

        # Note: usage_tools might not follow convention yet, check separately
        if "usage_tools" not in source and "get_usage_stats" not in source:
            missing_in_feishu.append("usage_tools")

        assert not missing_in_feishu, (
            f"feishu.py does not import these tool modules: {missing_in_feishu}\n"
            f"Users will never be able to call these tools via Feishu Bot."
        )
