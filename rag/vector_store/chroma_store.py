import logging
import chromadb
from chromadb.config import Settings as ChromaSettings
from rag.chunk import Chunk
from rag.vector_store.base import BaseVectorStore

logger = logging.getLogger(__name__)

# Collection name used across the entire agent
_COLLECTION_NAME = "pr_review_knowledge"


class ChromaVectorStore(BaseVectorStore):
    """
    ChromaDB-backed vector store.
    Persists embeddings to a local directory (RAG_DB_PATH in settings).
    Uses cosine similarity for nearest-neighbour search.
    """

    def __init__(self, persist_directory: str = ".rag_db"):
        self._client = chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._col = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaVectorStore initialised at '%s' (%d chunks stored)",
            persist_directory,
            self._col.count(),
        )

    # ------------------------------------------------------------------
    # BaseVectorStore interface
    # ------------------------------------------------------------------

    def upsert(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self._col.upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.to_metadata_dict() for c in chunks],
        )
        logger.debug("Upserted %d chunks into ChromaDB", len(chunks))

    def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[Chunk]:
        kwargs: dict = {
            "query_embeddings": [embedding],
            "n_results": min(top_k, max(self._col.count(), 1)),
            "include": ["documents", "metadatas", "distances"],
        }
        if filter:
            # ChromaDB where clause — e.g. {"repo_name": "owner/repo"}
            kwargs["where"] = filter

        results = self._col.query(**kwargs)

        chunks: list[Chunk] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        for chunk_id, doc, meta in zip(ids, docs, metas):
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    text=doc,
                    source_type=meta.get("source_type", "unknown"),
                    repo_name=meta.get("repo_name", ""),
                    metadata=meta,
                )
            )
        return chunks

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)

    def count(self) -> int:
        return self._col.count()
