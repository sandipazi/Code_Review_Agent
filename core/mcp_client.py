from models.mcp_protocol import MCPRequest, MCPResponse
from mcp_server.server import MCPServer
import uuid

class InternalMCPClient:
    def __init__(self):
        self.server = MCPServer()
        
    def get_tools(self):
        req = MCPRequest(method="tools/list", id=str(uuid.uuid4()))
        res = self.server.handle_request(req)
        if res.error:
            raise Exception(f"Failed to get tools: {res.error}")
        return res.result.get("tools", [])

    def call_tool(self, name: str, arguments: dict):
        req = MCPRequest(
            method="tools/call",
            params={"name": name, "arguments": arguments},
            id=str(uuid.uuid4())
        )
        res = self.server.handle_request(req)
        if res.error:
            return f"Error executing tool {name}: {res.error}"
        return res.result.get("content", "")
