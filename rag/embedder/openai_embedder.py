"""
OpenAIEmbedder — uses OpenAI text-embedding-3-small (1536 dims).

Paid but higher quality. Set RAG_EMBEDDER=openai in .env to use.
Re-uses the project's existing httpx client pattern (no openai SDK dependency).
"""
import json
import logging
import httpx
from rag.embedder.base import BaseEmbedder

logger = logging.getLogger(__name__)

_API_URL = "https://api.openai.com/v1/embeddings"
_DEFAULT_MODEL = "text-embedding-3-small"
_VECTOR_SIZE = 1536


class OpenAIEmbedder(BaseEmbedder):
    """
    Calls the OpenAI Embeddings API via httpx.
    Supports batched requests; max 2048 texts per call.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL):
        self._api_key = api_key
        self._model = model
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        logger.info("OpenAIEmbedder configured with model '%s'", model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.post(
            _API_URL,
            content=json.dumps({"input": texts, "model": self._model}),
        )
        response.raise_for_status()
        data = response.json()
        # Sort by index to preserve input order
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def vector_size(self) -> int:
        return _VECTOR_SIZE

    def close(self):
        self._client.close()
