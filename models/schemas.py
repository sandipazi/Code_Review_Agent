# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class PullRequestEvent(BaseModel):
    action: str
    number: int
    repository: Dict[str, Any]
    pull_request: Dict[str, Any]

class ChatMessage(BaseModel):
    role: str
    content: str = ""
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class ChatResponse(BaseModel):
    messages: List[Dict[str, Any]]
