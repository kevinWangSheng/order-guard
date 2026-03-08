"""MCP Connection — manage a single MCP server connection."""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from loguru import logger
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client

from order_guard.mcp.models import MCPServerConfig, ToolInfo


class MCPConnection:
    """Manage connection to a single MCP server."""

    def __init__(self, config: MCPServerConfig):
        self._config = config
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def name(self) -> str:
        return self._config.name

    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """Establish connection to the MCP server."""
        if self._session is not None:
            logger.warning("MCP server '{}' already connected", self.name)
            return

        self._exit_stack = AsyncExitStack()
        try:
            if self._config.transport == "stdio":
                session = await self._connect_stdio()
            else:
                session = await self._connect_sse()

            await session.initialize()
            self._session = session
            logger.info("MCP server '{}' connected ({})", self.name, self._config.transport)

        except Exception as e:
            logger.error("Failed to connect MCP server '{}': {}", self.name, e)
            await self._cleanup()
            raise

    async def _connect_stdio(self) -> ClientSession:
        """Connect via stdio transport."""
        assert self._exit_stack is not None
        assert self._config.command is not None, f"MCP server '{self.name}': stdio requires 'command'"

        # Merge environment variables
        env = {**os.environ, **self._config.env} if self._config.env else None

        server_params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env=env,
        )

        # stdio_client is an async context manager yielding (read_stream, write_stream)
        # Use sys.stderr as errlog — must be a real file with fileno()
        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params, errlog=sys.stderr)
        )
        read_stream, write_stream = stdio_transport

        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        return session

    async def _connect_sse(self) -> ClientSession:
        """Connect via SSE transport."""
        assert self._exit_stack is not None
        assert self._config.url is not None, f"MCP server '{self.name}': sse requires 'url'"

        headers = self._config.headers or None
        sse_transport = await self._exit_stack.enter_async_context(
            sse_client(self._config.url, headers=headers)
        )
        read_stream, write_stream = sse_transport

        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        return session

    async def disconnect(self) -> None:
        """Close the connection."""
        await self._cleanup()
        logger.info("MCP server '{}' disconnected", self.name)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._session = None
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                logger.warning("Error closing MCP connection '{}': {}", self.name, e)
            self._exit_stack = None

    async def list_tools(self) -> list[ToolInfo]:
        """List all tools available on this MCP server."""
        if self._session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")

        result = await self._session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append(ToolInfo(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
                server_name=self.name,
            ))

        logger.debug("MCP server '{}': {} tools available", self.name, len(tools))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a tool on this MCP server and return the text result."""
        if self._session is None:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")

        logger.info("MCP tool call: server='{}', tool='{}', args={}", self.name, name, arguments)
        result = await self._session.call_tool(name, arguments or {})

        # Extract text content from result
        text_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                text_parts.append(content.text)
            elif hasattr(content, "data"):
                text_parts.append(f"[binary data: {content.mimeType}]")

        output = "\n".join(text_parts)

        if result.isError:
            logger.warning("MCP tool '{}' returned error: {}", name, output[:200])

        return output
