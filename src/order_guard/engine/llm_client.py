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


class ToolCall(BaseModel):
    """Represents a tool call from the LLM."""

    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
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
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Call LLM and return structured response.

        Args:
            messages: Chat messages (system, user, assistant, tool).
            tools: Optional list of tool definitions for function calling.
        """
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
        if tools:
            kwargs["tools"] = tools

        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.AuthenticationError as e:
            raise RuntimeError("LLM API authentication failed. Please check your API key.") from e
        except litellm.APIConnectionError as e:
            raise RuntimeError(f"LLM API connection error: {e}") from e

        message = response.choices[0].message
        content = message.content or ""
        usage = response.usage
        token_usage = TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )

        # Parse tool calls if present
        tool_calls: list[ToolCall] = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                arguments = tc.function.arguments
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                tool_calls.append(ToolCall(
                    id=tc.id or "",
                    name=tc.function.name or "",
                    arguments=arguments,
                ))

        logger.info(
            "LLM call: model={} tokens={} tool_calls={}",
            response.model,
            token_usage.total_tokens,
            len(tool_calls),
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            token_usage=token_usage,
            model=response.model or self._model,
        )
