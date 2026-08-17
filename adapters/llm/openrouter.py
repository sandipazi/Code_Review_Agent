# pyrefly: ignore [missing-import]
import httpx
from typing import List, Dict, Any, Optional
from .base import BaseLLMAdapter, LLMMessage
import logging

logger = logging.getLogger(__name__)

class OpenRouterAdapter(BaseLLMAdapter):
    def __init__(
        self,
        api_key: str,
        model: str = "meta-llama/llama-3.3-70b-instruct",
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        if site_url:
            self.headers["HTTP-Referer"] = site_url
        if app_name:
            self.headers["X-Title"] = app_name
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
            logger.error(f"OpenRouter API error: {response.text}")
            response.raise_for_status()

        data = response.json()
        choice = data["choices"][0]["message"]

        return LLMMessage(
            role=choice.get("role", "assistant"),
            content=choice.get("content") or "",
            tool_calls=choice.get("tool_calls")
        )

    def close(self):
        self.client.close()
