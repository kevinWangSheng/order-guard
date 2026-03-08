"""Analyzer — orchestrate prompt → LLM → parse → validate."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from order_guard.engine.llm_client import LLMClient, TokenUsage
from order_guard.engine.prompt import OUTPUT_SCHEMA, PromptBuilder


class AlertItem(BaseModel):
    sku: str = ""
    severity: str = "info"  # critical / warning / info
    title: str = ""
    reason: str = ""
    suggestion: str = ""


class AnalyzerOutput(BaseModel):
    alerts: list[AlertItem] = Field(default_factory=list)
    summary: str = ""
    has_alerts: bool = False
    raw_response: str = ""
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


class Analyzer:
    """Run LLM analysis on data summaries with business rules."""

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()
        self._prompt_builder = PromptBuilder()

    async def analyze(
        self,
        data_summary: str,
        rule_prompt: str,
        max_retries: int = 1,
    ) -> AnalyzerOutput:
        """Analyze data with LLM. Retries on parse failure."""
        messages = self._prompt_builder.build_messages(data_summary, rule_prompt)
        total_usage = TokenUsage()
        last_error = ""

        for attempt in range(1 + max_retries):
            try:
                response = await self._llm.completion(
                    messages,
                    response_format=OUTPUT_SCHEMA,
                )
                # Accumulate token usage across retries
                total_usage.prompt_tokens += response.token_usage.prompt_tokens
                total_usage.completion_tokens += response.token_usage.completion_tokens
                total_usage.total_tokens += response.token_usage.total_tokens

                parsed = self._parse_response(response.content)
                parsed["raw_response"] = response.content
                parsed["token_usage"] = total_usage.model_dump()

                return AnalyzerOutput(**parsed)

            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                logger.warning("LLM response parse failed (attempt {}): {}", attempt + 1, last_error)
            except Exception as e:
                last_error = str(e)
                logger.error("Analyzer error (attempt {}): {}", attempt + 1, last_error)
                if attempt >= max_retries:
                    break

        # All retries exhausted
        logger.error("Analyzer failed after {} attempts: {}", max_retries + 1, last_error)
        return AnalyzerOutput(
            summary=f"分析失败: {last_error}",
            token_usage=total_usage,
        )

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse LLM response content as JSON."""
        # Try to extract JSON from markdown code blocks
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        data = json.loads(cleaned)

        # Validate minimum structure
        if not isinstance(data, dict):
            raise json.JSONDecodeError("Expected JSON object", content, 0)
        if "alerts" not in data:
            data["alerts"] = []
        if "summary" not in data:
            data["summary"] = ""
        if "has_alerts" not in data:
            data["has_alerts"] = len(data["alerts"]) > 0

        return data
