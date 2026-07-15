from typing import Dict, Any, Callable
from models.mcp_protocol import MCPRequest, MCPResponse
from .tools.file_reader import read_file
import inspect
import logging

logger = logging.getLogger(__name__)

class MCPServer:
    def __init__(self):
        # Register available tools
        self.tools: Dict[str, Callable] = {
            "read_file": read_file
        }
        self.tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": inspect.getdoc(read_file) or "Execute read_file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "The absolute or relative path to the file."
                            }
                        },
                        "required": ["file_path"]
                    }
                }
            }
        ]

    def register_github_tools(self, github_tools):
        self.tools["list_prs"] = github_tools.list_prs
        self.tools["trigger_review"] = github_tools.trigger_review
        
        self.tool_schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "list_prs",
                    "description": inspect.getdoc(github_tools.list_prs) or "List PRs",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo_name": {"type": "string", "description": "The full repository name, e.g. 'sandipazi/Code_Review_Agent'"},
                            "state": {"type": "string", "description": "State of PRs to list (open, closed, all)"}
                        },
                        "required": ["repo_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "trigger_review",
                    "description": inspect.getdoc(github_tools.trigger_review) or "Trigger PR review",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo_name": {"type": "string", "description": "The full repository name"},
                            "pr_number": {"type": "integer", "description": "The pull request number"}
                        },
                        "required": ["repo_name", "pr_number"]
                    }
                }
            }
        ])
        
    def get_tool_schemas(self) -> list[Dict[str, Any]]:
        """Return tool schemas in OpenAI function calling format."""
        return self.tool_schemas

    def handle_request(self, request: MCPRequest) -> MCPResponse:
        logger.info(f"MCP Server received request: {request.method}")
        
        if request.method == "tools/list":
            return MCPResponse(
                id=request.id,
                result={"tools": self.get_tool_schemas()}
            )
        
        elif request.method == "tools/call":
            params = request.params or {}
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            
            if tool_name not in self.tools:
                logger.error(f"MCP Tool not found: {tool_name}")
                return MCPResponse(
                    id=request.id,
                    error={"code": -32601, "message": f"Tool {tool_name} not found"}
                )
            
            func = self.tools[tool_name]
            try:
                # Call the tool function with provided arguments
                logger.info(f"Executing MCP Tool: {tool_name} with args {tool_args}")
                res = func(**tool_args)
                return MCPResponse(
                    id=request.id,
                    result={"content": res}
                )
            except Exception as e:
                logger.error(f"MCP Tool execution failed: {e}")
                return MCPResponse(
                    id=request.id,
                    error={"code": -32000, "message": str(e)}
                )
        
        return MCPResponse(
            id=request.id,
            error={"code": -32601, "message": "Method not found"}
        )
