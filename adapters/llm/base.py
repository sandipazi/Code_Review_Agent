from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class LLMMessage:
    def __init__(self, role: str, content: str, name: Optional[str] = None, tool_calls: Optional[List[Dict]] = None, tool_call_id: Optional[str] = None):
        self.role = role # "system", "user", "assistant", "tool"
        self.content = content
        self.name = name
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id

    def to_dict(self):
        d = {"role": self.role, "content": self.content}
        if self.name: d["name"] = self.name
        if self.tool_calls: d["tool_calls"] = self.tool_calls
        if self.tool_call_id: d["tool_call_id"] = self.tool_call_id
        return d

class BaseLLMAdapter(ABC):
    @abstractmethod
    def generate(self, messages: List[LLMMessage], tools: Optional[List[Dict[str, Any]]] = None) -> LLMMessage:
        """
        Generate a response from the LLM.
        tools should be provided in standard OpenAI function calling format or JSON Schema.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up any open connections."""
        pass
