"""LLM client wrapper using LiteLLM."""

from __future__ import annotations

import json
from typing import Any

import litellm
from loguru import logger
from pydantic import BaseModel, Field

from order_guard.config import get_settings

# Suppress LiteLLM verbose logging
litellm.suppress_debug_info = True


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str = ""
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMClient:
    """Unified LLM client via LiteLLM."""

    def __init__(self):
        settings = get_settings()
        self._model = settings.llm.model
        self._api_key = settings.llm.api_key.get_secret_value()
        self._api_base = settings.llm.api_base or None
        self._max_tokens = settings.llm.max_tokens
        self._temperature = settings.llm.temperature

    async def completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call LLM and return structured response."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens or self._max_tokens,
            "api_key": self._api_key,
        }
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.AuthenticationError as e:
            raise RuntimeError("LLM API authentication failed. Please check your API key.") from e
        except litellm.APIConnectionError as e:
            raise RuntimeError(f"LLM API connection error: {e}") from e

        content = response.choices[0].message.content or ""
        usage = response.usage
        token_usage = TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )

        logger.info(
            "LLM call: model={} tokens={}",
            response.model,
            token_usage.total_tokens,
        )

        return LLMResponse(
            content=content,
            token_usage=token_usage,
            model=response.model or self._model,
        )
