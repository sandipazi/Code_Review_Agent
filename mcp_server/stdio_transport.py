"""
stdio transport bridge for the PR Review Agent MCP server.

Wraps the existing MCPServer using the official MCP Python SDK,
enabling any stdio-capable MCP client (VS Code, Claude Desktop, Cursor)
to discover and invoke tools via the standard MCP protocol.
"""
import asyncio
import logging
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from mcp_server.server import MCPServer

logger = logging.getLogger(__name__)


def _build_sdk_server(mcp_server: MCPServer) -> Server:
    """
    Create an official MCP SDK Server that delegates all calls to MCPServer.

    The SDK handles: protocol framing, initialization handshake, capability
    negotiation, and JSON-RPC routing. We just register our tool handlers.
    """
    sdk_server = Server("pr-review-agent")

    @sdk_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """Return all registered tools in MCP Tool format."""
        schemas = mcp_server.get_tool_schemas()
        return [
            types.Tool(
                name=s["function"]["name"],
                description=s["function"].get("description", ""),
                inputSchema=s["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            )
            for s in schemas
        ]

    @sdk_server.call_tool()
    async def call_tool(
        name: str, arguments: Optional[dict]
    ) -> list[types.TextContent]:
        """Execute the named tool and return its output as TextContent."""
        args = arguments or {}

        if name not in mcp_server.tools:
            return [types.TextContent(type="text", text=f"Error: Tool '{name}' not found.")]

        try:
            result = mcp_server.tools[name](**args)
            return [types.TextContent(type="text", text=str(result))]
        except Exception as e:
            logger.error(f"Tool '{name}' execution error: {e}")
            return [types.TextContent(type="text", text=f"Error executing '{name}': {e}")]

    return sdk_server


async def run_stdio(mcp_server: MCPServer) -> None:
    """
    Run the MCP server over the stdio transport.

    Reads JSON-RPC messages from stdin and writes responses to stdout.
    Compatible with: VS Code MCP extension, Claude Desktop, Cursor IDE,
    and any MCP client that supports the stdio transport.

    Args:
        mcp_server: Pre-built MCPServer from tool_factory.build_mcp_server().
    """
    sdk_server = _build_sdk_server(mcp_server)
    logger.info("MCP server running on stdio transport — waiting for client...")

    async with stdio_server() as (read_stream, write_stream):
        await sdk_server.run(
            read_stream,
            write_stream,
            sdk_server.create_initialization_options(),
        )
