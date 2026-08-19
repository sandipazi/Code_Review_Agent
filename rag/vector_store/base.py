from abc import ABC, abstractmethod
from rag.chunk import Chunk


class BaseVectorStore(ABC):
    """Abstract adapter for a vector database backend."""

    @abstractmethod
    def upsert(self, chunks: list[Chunk]) -> None:
        """Add or update chunks (identified by chunk_id) in the store."""
        ...

    @abstractmethod
    def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[Chunk]:
        """
        Return the top_k most similar chunks to the given embedding vector.
        Optional filter is a metadata key-value dict (passed to the backend).
        """
        ...

    @abstractmethod
    def delete(self, ids: list[str]) -> None:
        """Remove chunks by their chunk_id."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Return the total number of stored chunks."""
        ...
