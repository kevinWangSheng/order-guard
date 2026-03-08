"""Minimal SQLite MCP Server for testing.

Provides tools: list_tables, describe_table, read_query
Usage: python scripts/sqlite_mcp_server.py <database_path>
"""

from __future__ import annotations

import json
import sqlite3
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


def create_server(db_path: str) -> Server:
    server = Server("sqlite-mcp-server")

    def _get_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_tables",
                description="List all tables in the SQLite database",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="describe_table",
                description="Get the schema (columns, types) of a specific table",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Name of the table to describe",
                        }
                    },
                    "required": ["table_name"],
                },
            ),
            Tool(
                name="read_query",
                description="Execute a SELECT SQL query and return the results",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The SQL SELECT query to execute",
                        }
                    },
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        conn = _get_conn()
        try:
            if name == "list_tables":
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row["name"] for row in cursor]
                return [TextContent(type="text", text=json.dumps(tables, indent=2))]

            elif name == "describe_table":
                table_name = arguments["table_name"]
                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [
                    {"name": row["name"], "type": row["type"], "notnull": bool(row["notnull"]), "pk": bool(row["pk"])}
                    for row in cursor
                ]
                return [TextContent(type="text", text=json.dumps(columns, indent=2))]

            elif name == "read_query":
                query = arguments["query"]
                # Safety: only allow SELECT queries
                if not query.strip().upper().startswith("SELECT"):
                    return [TextContent(type="text", text="Error: Only SELECT queries are allowed")]
                cursor = conn.execute(query)
                rows = [dict(row) for row in cursor]
                return [TextContent(type="text", text=json.dumps(rows, indent=2))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
        finally:
            conn.close()

    return server


async def main():
    if len(sys.argv) < 2:
        print("Usage: python sqlite_mcp_server.py <database_path>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    server = create_server(db_path)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
