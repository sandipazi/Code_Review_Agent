"""Lazily-created, process-wide Temporal client, shared across requests.

Unlike the per-request VCS/LLM adapters, a Temporal Client is cheap to hold onto
and reuse — it just manages a gRPC connection to the Temporal server.
"""
from typing import Optional

from temporalio.client import Client

from config.settings import settings

_client: Optional[Client] = None


async def get_temporal_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
    return _client
