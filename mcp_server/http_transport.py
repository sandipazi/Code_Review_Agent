"""
HTTP MCP transport router — exposes MCP tools over a stateless JSON-RPC HTTP endpoint.

Clients (web frontends, remote AI agents, LangChain, AutoGen, etc.) send:
  POST /mcp
  X-GitHub-Token: ghp_YOUR_TOKEN
  Content-Type: application/json
  Body: { "jsonrpc": "2.0", "method": "tools/list" | "tools/call", ... }

No MCP SDK is required here — the MCP HTTP protocol is simple JSON-RPC
and is implemented directly for clarity and zero extra dependencies.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mcp_server.tool_factory import build_mcp_server

logger = logging.getLogger(__name__)

router = APIRouter()


def _mcp_response(result: Any = None, error: Optional[Dict] = None, req_id: Any = None) -> Dict:
    """Build a JSON-RPC 2.0 response envelope."""
    resp: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


@router.post("", summary="MCP JSON-RPC endpoint")
async def handle_mcp_request(request: Request) -> JSONResponse:
    """
    Stateless MCP JSON-RPC endpoint.

    Supported methods:
      - initialize         — MCP handshake (capability negotiation)
      - tools/list         — Discover available tools
      - tools/call         — Execute a tool

    Required header: X-GitHub-Token
    Each request creates an isolated MCPServer instance (stateless).
    """
    # --- Auth: require GitHub token from the client ---
    github_token = request.headers.get("X-GitHub-Token")
    if not github_token:
        raise HTTPException(
            status_code=401,
            detail="Missing required header: X-GitHub-Token",
        )

    # --- Parse request body ---
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    method = body.get("method", "")
    params: Dict = body.get("params") or {}
    req_id = body.get("id", str(uuid.uuid4()))

    logger.info(f"MCP HTTP request: method={method}")

    # --- Initialize handshake (required by some clients before tools/list) ---
    if method == "initialize":
        return JSONResponse(
            _mcp_response(
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "pr-review-agent", "version": "1.0.0"},
                },
                req_id=req_id,
            )
        )

    # --- Build a fresh, stateless MCPServer per request ---
    mcp_server = build_mcp_server(github_token=github_token)

    # --- tools/list: discover available tools ---
    if method == "tools/list":
        schemas = mcp_server.get_tool_schemas()
        tools = [
            {
                "name": s["function"]["name"],
                "description": s["function"].get("description", ""),
                "inputSchema": s["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for s in schemas
        ]
        return JSONResponse(_mcp_response(result={"tools": tools}, req_id=req_id))

    # --- tools/call: execute a tool ---
    if method == "tools/call":
        tool_name: Optional[str] = params.get("name")
        tool_args: Dict = params.get("arguments") or {}

        if not tool_name:
            return JSONResponse(
                _mcp_response(
                    error={"code": -32600, "message": "Missing 'name' in params"},
                    req_id=req_id,
                )
            )

        if tool_name not in mcp_server.tools:
            return JSONResponse(
                _mcp_response(
                    error={"code": -32601, "message": f"Tool '{tool_name}' not found"},
                    req_id=req_id,
                )
            )

        try:
            result = mcp_server.tools[tool_name](**tool_args)
            return JSONResponse(
                _mcp_response(
                    result={"content": [{"type": "text", "text": str(result)}]},
                    req_id=req_id,
                )
            )
        except Exception as e:
            logger.error(f"Tool '{tool_name}' execution failed: {e}")
            return JSONResponse(
                _mcp_response(
                    error={"code": -32000, "message": str(e)},
                    req_id=req_id,
                )
            )

    # --- Unknown method ---
    return JSONResponse(
        _mcp_response(
            error={"code": -32601, "message": f"Method '{method}' not supported"},
            req_id=req_id,
        )
    )
