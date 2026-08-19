"""
Qdrant vector store adapter — production-scale alternative to ChromaDB.

Requires: pip install qdrant-client
Set RAG_VECTOR_STORE=qdrant and QDRANT_URL in .env to activate.
"""
import logging
from rag.chunk import Chunk
from rag.vector_store.base import BaseVectorStore

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "pr_review_knowledge"


class QdrantVectorStore(BaseVectorStore):
    """
    Qdrant-backed vector store.
    Supports local in-memory, on-disk, or cloud deployments.
    """

    def __init__(self, url: str = "http://localhost:6333", api_key: str | None = None, vector_size: int = 384):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError as exc:
            raise ImportError(
                "qdrant-client is not installed. Run: pip install qdrant-client"
            ) from exc

        self._client = QdrantClient(url=url, api_key=api_key)
        self._vector_size = vector_size

        # Create collection if it doesn't exist
        existing = [c.name for c in self._client.get_collections().collections]
        if _COLLECTION_NAME not in existing:
            self._client.create_collection(
                collection_name=_COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s'", _COLLECTION_NAME)

    def upsert(self, chunks: list[Chunk]) -> None:
        from qdrant_client.models import PointStruct
        if not chunks:
            return
        points = [
            PointStruct(
                id=abs(hash(c.chunk_id)) % (2**63),
                vector=c.embedding,
                payload={**c.to_metadata_dict(), "text": c.text, "chunk_id": c.chunk_id},
            )
            for c in chunks
        ]
        self._client.upsert(collection_name=_COLLECTION_NAME, points=points)
        logger.debug("Upserted %d chunks into Qdrant", len(chunks))

    def query(self, embedding: list[float], top_k: int = 5, filter: dict | None = None) -> list[Chunk]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant_filter = None
        if filter:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filter.items()
            ]
            qdrant_filter = Filter(must=conditions)

        results = self._client.search(
            collection_name=_COLLECTION_NAME,
            query_vector=embedding,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        chunks = []
        for r in results:
            payload = r.payload or {}
            chunks.append(Chunk(
                chunk_id=payload.get("chunk_id", str(r.id)),
                text=payload.get("text", ""),
                source_type=payload.get("source_type", "unknown"),
                repo_name=payload.get("repo_name", ""),
                metadata=payload,
            ))
        return chunks

    def delete(self, ids: list[str]) -> None:
        hashed = [abs(hash(i)) % (2**63) for i in ids]
        self._client.delete(collection_name=_COLLECTION_NAME, points_selector=hashed)

    def count(self) -> int:
        return self._client.get_collection(_COLLECTION_NAME).points_count or 0
