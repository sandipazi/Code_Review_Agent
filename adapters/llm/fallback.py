# pyrefly: ignore [missing-import]
import httpx
from typing import List, Dict, Any, Optional
from .base import BaseLLMAdapter, LLMMessage
import logging

logger = logging.getLogger(__name__)

class FallbackLLMAdapter(BaseLLMAdapter):
    """Wraps an ordered list of LLM adapters, trying each in turn until one succeeds.

    Falls through to the next adapter on any HTTP-status error (e.g. 429 rate limit,
    413 payload too large) or connection error from the current one.
    """

    def __init__(self, adapters: List[BaseLLMAdapter]):
        if not adapters:
            raise ValueError("FallbackLLMAdapter requires at least one adapter.")
        self.adapters = adapters

    def generate(self, messages: List[LLMMessage], tools: Optional[List[Dict[str, Any]]] = None) -> LLMMessage:
        last_exc: Optional[Exception] = None
        for i, adapter in enumerate(self.adapters):
            try:
                return adapter.generate(messages, tools=tools)
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                if i + 1 < len(self.adapters):
                    logger.warning(f"{type(adapter).__name__} failed ({e}); trying next provider")
                else:
                    logger.warning(f"{type(adapter).__name__} failed ({e}); no more fallback providers")
        raise last_exc

    def close(self):
        for adapter in self.adapters:
            try:
                adapter.close()
            except Exception:
                pass
