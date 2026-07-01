# pyrefly: ignore [missing-import]
import httpx
from typing import List, Dict, Any, Optional
from .base import BaseLLMAdapter, LLMMessage
import logging

logger = logging.getLogger(__name__)

class GitHubModelsAdapter(BaseLLMAdapter):
    def __init__(self, token: str, model: str = "gpt-4o"):
        self.token = token
        self.model = model
        self.base_url = "https://models.inference.ai.azure.com/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        self.client = httpx.Client(headers=self.headers)

    def generate(self, messages: List[LLMMessage], tools: Optional[List[Dict[str, Any]]] = None) -> LLMMessage:
        payload = {
            "model": self.model,
            "messages": [msg.to_dict() for msg in messages],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = self.client.post(self.base_url, json=payload, timeout=60.0)
        
        if response.status_code != 200:
            logger.error(f"GitHub Models API error: {response.text}")
            response.raise_for_status()
            
        data = response.json()
        choice = data["choices"][0]["message"]
        
        return LLMMessage(
            role=choice.get("role", "assistant"),
            content=choice.get("content", ""),
            tool_calls=choice.get("tool_calls")
        )

    def close(self):
        self.client.close()
