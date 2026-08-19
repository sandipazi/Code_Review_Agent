# pyrefly: ignore [missing-import]
from pydantic import BaseModel, field_validator
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

    @field_validator("content", mode="before")
    @classmethod
    def coerce_none_content(cls, v):
        # Assistant messages that are pure tool-calls come back from OpenAI-compatible
        # APIs with content explicitly set to null — treat that the same as "no text".
        return v if v is not None else ""

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class ChatResponse(BaseModel):
    messages: List[Dict[str, Any]]

class CancelReviewRequest(BaseModel):
    repo_name: str
    pr_number: int
