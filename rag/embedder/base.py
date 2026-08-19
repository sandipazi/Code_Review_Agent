from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    """Abstract adapter for embedding models."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of texts.
        Returns a list of float vectors, one per input text.
        """
        ...

    @abstractmethod
    def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text and return its vector."""
        ...

    @property
    @abstractmethod
    def vector_size(self) -> int:
        """Dimensionality of the embedding vectors produced by this model."""
        ...
