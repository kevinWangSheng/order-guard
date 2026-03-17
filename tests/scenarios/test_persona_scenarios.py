"""L3 — Persona-based scenario tests.

Each (role, persona, task) combination from personas.yaml becomes a test case.
AI simulates the user, JudgeAgent evaluates against criteria.

Requires LLM API Key in .env.
"""
from __future__ import annotations

import pytest

from tests.scenarios.persona_runner import (
    PERSONAS_YAML,
    load_personas,
    run_single_scenario,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

ALL_TASKS, _ = load_personas(PERSONAS_YAML)


@pytest.mark.parametrize(
    "task",
    ALL_TASKS,
    ids=[f"{t.role_id}/{t.persona_id}/{t.task_id}" for t in ALL_TASKS],
)
async def test_persona_scenario(seeded_data, task):
    """Run a single persona scenario and assert all criteria pass."""
    result = await run_single_scenario(task, verbose=False)

    if result.error:
        pytest.fail(f"Scenario error: {result.error}")

    assert result.success, (
        f"Failed criteria: {result.failed_criteria}\n"
        f"Reasoning: {result.reasoning}\n"
        f"Tools used: {result.tools_used}\n"
        f"Turns: {result.turns}"
    )
