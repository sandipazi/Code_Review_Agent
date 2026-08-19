from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A single unit of text retrieved or stored in the vector store."""
    chunk_id: str
    text: str
    source_type: str       # "diff" | "source_file" | "review_comment" | "doc" | "commit"
    repo_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Optional — populated after embedding
    embedding: list[float] = field(default_factory=list)

    def to_metadata_dict(self) -> dict[str, Any]:
        """Flatten into a ChromaDB-compatible flat metadata dict (no nested dicts)."""
        flat = {
            "source_type": self.source_type,
            "repo_name": self.repo_name,
        }
        for k, v in self.metadata.items():
            # ChromaDB only accepts str/int/float/bool values
            if isinstance(v, (str, int, float, bool)):
                flat[k] = v
            else:
                flat[k] = str(v)
        return flat
