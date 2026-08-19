# MCP Server

This document covers the repo's own MCP ("Model Context Protocol") server — the **`pr-review-agent`** server defined in [mcp_server/](mcp_server/) and [mcp_runner.py](mcp_runner.py). It is a plain, hand-rolled tool registry (no FastMCP or other MCP framework) that exposes a small set of GitHub/RAG tools over two transports, and is also used **in-process** by the chat/review agents themselves.

> Note: this is unrelated to the globally-installed `code-review-graph` MCP tool you may see attached in some Claude sessions on this machine — that's a separate, external package, not part of this repository.

## Why it exists

Both `ChatAgent` (`core/chat_agent.py`) and `PRReviewAgent` (`core/agent.py`) need to call the same small set of tools (`list_prs`, `trigger_review`, `search_knowledge_base`, `read_file`) during their ReAct loops. Rather than hardcoding tool-calling logic into each agent, the project defines those tools once, behind the MCP protocol, and exposes them three ways:

1. **In-process**, for the agents themselves (`core/mcp_client.py`).
2. **stdio**, for local IDEs/desktop apps (Cursor, VS Code, Claude Desktop).
3. **HTTP JSON-RPC**, for remote/web clients.

## Folder structure

```text
mcp_server/
├── server.py            # MCPServer — tool registry + JSON-RPC-style request handler
├── tool_factory.py       # build_mcp_server() — wires a GitHub token into a fresh MCPServer
├── stdio_transport.py    # wraps MCPServer with the official `mcp` SDK Server, for stdio
├── http_transport.py     # FastAPI router — raw JSON-RPC over HTTP, no MCP SDK needed
└── tools/
    ├── file_reader.py    # read_file
    ├── github_tools.py   # list_prs, trigger_review
    ├── rag_tools.py       # search_knowledge_base
    └── linter.py          # empty — no tool implemented yet (see IMPROVEMENTS.md)

mcp_runner.py              # stdio entry point (repo root)
core/mcp_client.py         # InternalMCPClient — calls MCPServer in-process (no transport)
models/mcp_protocol.py     # MCPRequest / MCPResponse pydantic schemas
```

## The `MCPServer` core (`mcp_server/server.py`)

`MCPServer` is a plain class, not a framework:

- `self.tools: Dict[str, Callable]` — name → function.
- `self.tool_schemas: list[dict]` — OpenAI function-calling-style schemas, built from each tool's docstring (`inspect.getdoc`) plus a hand-written JSON-schema `parameters` block.
- `handle_request(request: MCPRequest) -> MCPResponse` handles two methods:
  - `tools/list` → returns `{"tools": self.tool_schemas}`
  - `tools/call` → looks up `params["name"]` in `self.tools`, calls it with `params["arguments"]`, wraps the return value as `{"content": result}`, or returns a JSON-RPC-style error (`-32601` unknown tool, `-32000` execution error).

Tools are added via two registration methods, both called by whoever builds the server:

- `register_github_tools(github_tools)` — always called, adds `list_prs` + `trigger_review`.
- `register_rag_tools(rag_tools)` — only called when RAG is enabled and configured (see below), adds `search_knowledge_base`.

`read_file` is registered unconditionally in `__init__`.

## Tools

| Tool | File | Description |
|---|---|---|
| `read_file` | [mcp_server/tools/file_reader.py:3](mcp_server/tools/file_reader.py#L3) | Reads a local file's contents; returns an `"Error: ..."` string if missing/unreadable. |
| `list_prs` | [mcp_server/tools/github_tools.py:13](mcp_server/tools/github_tools.py#L13) | Lists PRs for `repo_name` (default `state="open"`) via the VCS adapter; returns a JSON string of `{number, title, state}`. |
| `trigger_review` | [mcp_server/tools/github_tools.py:25](mcp_server/tools/github_tools.py#L25) | Invokes the caller-supplied `review_callback(repo_name, pr_number)`. In the FastAPI `/chat` flow this starts a durable Temporal workflow (see [HOW_IT_WORKS.md](HOW_IT_WORKS.md)); in a bare `mcp_runner.py` stdio session it falls back to a logging no-op (`tool_factory.py`'s `_noop_review_callback`), since there's no background-task runner to call into. |
| `search_knowledge_base` | [mcp_server/tools/rag_tools.py:21](mcp_server/tools/rag_tools.py#L21) | Only registered when a `RAGRetriever` is available. Runs `RAGRetriever.retrieve_for_query(query, repo_name)` — lets the LLM proactively pull knowledge-base context mid-ReAct-loop, on top of the context auto-injected at the start of a review. See [RAG.md](RAG.md). |
| `linter` (tools) | [mcp_server/tools/linter.py](mcp_server/tools/linter.py) | File exists but is empty — no tool is implemented or registered. |

## Transports

### 1. In-process (`core/mcp_client.py`)
`InternalMCPClient` wraps an `MCPServer` instance and calls `handle_request()` directly — no network, no serialization boundary. This is how `ChatAgent` and `PRReviewAgent` call tools during their ReAct loops.

### 2. stdio (`mcp_runner.py` + `mcp_server/stdio_transport.py`)
Entry point:
```bash
python mcp_runner.py --github-token ghp_YOUR_TOKEN
```
`mcp_runner.py` calls `build_mcp_server(github_token=...)` once, then `run_stdio()` wraps it with the official `mcp` Python SDK's `Server` class, which handles protocol framing/handshake and delegates `list_tools()`/`call_tool()` back to the same `MCPServer.tools` dict. Logging is set to `WARNING`+ on stderr so stdout stays clean for JSON-RPC traffic.

Registered as `"pr-review-agent"` in [.vscode/mcp.json](.vscode/mcp.json) (using `${env:GITHUB_TOKEN}`) and documented for Claude Desktop / Cursor in [docs/client_configs/](docs/client_configs/) (paste-in JSON snippets referencing an absolute path to `mcp_runner.py`).

### 3. HTTP JSON-RPC (`mcp_server/http_transport.py`)
Mounted at `/mcp` in `main.py:36` (`app.include_router(mcp_router, prefix="/mcp")`). No MCP SDK dependency — implements the JSON-RPC envelope directly for `initialize`, `tools/list`, `tools/call`.

```
POST /mcp
X-GitHub-Token: ghp_YOUR_TOKEN
Content-Type: application/json

{"jsonrpc": "2.0", "method": "tools/list", "id": "1"}
```

Every request (except `initialize`, which needs no token yet) builds a **fresh `MCPServer`** via `build_mcp_server(github_token=...)` — see "Statelessness" below. Only GitHub tools are registered over HTTP; RAG tools are not wired into this transport today (see [IMPROVEMENTS.md](IMPROVEMENTS.md)).

## `build_mcp_server()` (`mcp_server/tool_factory.py:23`)

```python
def build_mcp_server(github_token: str, review_callback=None) -> MCPServer:
```
Builds a `GitHubAdapter` from the token, wraps it (plus `review_callback`, defaulting to the logging no-op) in `GitHubTools`, constructs a new `MCPServer`, and calls `register_github_tools`. Called once per HTTP request and once per stdio session — **no credential state is stored globally**.

## Auth & statelessness

- **stdio**: token supplied via `--github-token` CLI arg (or `${env:GITHUB_TOKEN}` in `.vscode/mcp.json`); one token per session, held only in that process's memory.
- **HTTP**: token supplied per-request via the `X-GitHub-Token` header; a new `MCPServer` (and new `GitHubAdapter`) is built for every single call — there is no server-side session or token cache.
- **In-process** (`/chat`, review activities): the token comes from the request header or `settings.GITHUB_TOKEN`, and `main.py`/`core/temporal_workflows.py` build their own `MCPServer` + tools directly (not via `build_mcp_server`, since they also need to conditionally attach RAG tools and a real `trigger_review` callback).

## Protocol schemas (`models/mcp_protocol.py`)

```python
class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[str] = None

class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    id: Optional[str] = None
```
Used by the in-process client and `MCPServer.handle_request`; the HTTP transport builds its own equivalent dicts directly (`_mcp_response()` in `http_transport.py`) rather than importing these models.

## Running it

```bash
# stdio (IDEs / desktop clients)
python mcp_runner.py --github-token <PAT>

# HTTP (mounted inside the main FastAPI app)
uvicorn main:app --reload
# then: POST http://localhost:8000/mcp  with header X-GitHub-Token: <PAT>
```
