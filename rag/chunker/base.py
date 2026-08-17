from abc import ABC, abstractmethod
from rag.chunk import Chunk


class BaseChunker(ABC):
    """Base interface for all chunking strategies."""

    @abstractmethod
    def chunk(self, content: str, repo_name: str, **kwargs) -> list[Chunk]:
        """
        Split `content` into a list of Chunk objects.
        `kwargs` carries source-specific metadata (file_path, pr_number, etc.).
        """
        ...
