# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional, Dict, Any

class PullRequestEvent(BaseModel):
    action: str
    number: int
    repository: Dict[str, Any]
    pull_request: Dict[str, Any]
