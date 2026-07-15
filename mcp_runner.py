#!/usr/bin/env python3
"""
PR Review Agent — MCP Server stdio entry point.

Launches the MCP server over stdin/stdout, making it usable from any
MCP client that supports the stdio transport.

Usage:
    python mcp_runner.py --github-token ghp_YOUR_TOKEN

VS Code (.vscode/mcp.json) picks this up automatically.
Claude Desktop / Cursor: see docs/client_configs/ for config snippets.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on the Python path when invoked directly
sys.path.insert(0, str(Path(__file__).parent))

from mcp_server.tool_factory import build_mcp_server
from mcp_server.stdio_transport import run_stdio

# Log only WARNING+ to stderr so stdout stays clean for MCP JSON-RPC traffic
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PR Review Agent MCP Server (stdio transport)"
    )
    parser.add_argument(
        "--github-token",
        required=True,
        help="GitHub personal access token (PAT) used to authenticate tool calls.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Build a stateless MCPServer with the caller-supplied token
    mcp_server = build_mcp_server(github_token=args.github_token)

    # Hand off to the stdio transport (blocks until the client disconnects)
    asyncio.run(run_stdio(mcp_server))


if __name__ == "__main__":
    main()
