"""
DocChunker — recursive character-based splitter for prose documents.

Targets ~500 tokens (~2000 chars) per chunk with 50-token overlap (~200 chars).
Preserves markdown heading context so the LLM knows which section a chunk
belongs to (e.g. "## Architecture > ### 4. PR Review Agent").
"""
import uuid
import re
import logging
from rag.chunk import Chunk
from rag.chunker.base import BaseChunker

logger = logging.getLogger(__name__)

# ~2000 chars ≈ 500 tokens for typical English/code text
CHUNK_SIZE_CHARS = 2000
OVERLAP_CHARS = 200

# Separators tried in order — prefer semantic boundaries first
_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", " "]


class DocChunker(BaseChunker):
    """
    Recursive prose chunker for markdown / plain-text documents.
    Respects heading hierarchy and paragraph boundaries before falling back
    to character-level splitting.
    """

    def chunk(
        self,
        content: str,
        repo_name: str,
        file_path: str = "",
        doc_type: str = "doc",
        timestamp: str = "",
    ) -> list[Chunk]:
        if not content or not content.strip():
            return []

        raw_chunks = self._recursive_split(content, _SEPARATORS, CHUNK_SIZE_CHARS)
        chunks: list[Chunk] = []

        for idx, text in enumerate(raw_chunks):
            text = text.strip()
            if not text:
                continue
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                text=text,
                source_type=doc_type,
                repo_name=repo_name,
                metadata={
                    "file_path": file_path,
                    "chunk_index": idx,
                    "timestamp": timestamp,
                },
            ))

        logger.debug("DocChunker produced %d chunks for %s", len(chunks), file_path)
        return chunks

    # ------------------------------------------------------------------
    # Recursive splitting logic
    # ------------------------------------------------------------------

    def _recursive_split(self, text: str, separators: list[str], chunk_size: int) -> list[str]:
        if not separators:
            # Base case: split by characters with overlap
            return self._char_split(text, chunk_size)

        sep = separators[0]
        # Try splitting with this separator
        parts = re.split(re.escape(sep), text)

        results: list[str] = []
        current = ""

        for part in parts:
            candidate = (sep + part) if current else part
            if len(current) + len(candidate) <= chunk_size:
                current += candidate
            else:
                # Current chunk is full — flush it
                if current.strip():
                    results.append(current.strip())
                # If the new part itself is too big, recurse
                if len(candidate) > chunk_size:
                    results.extend(
                        self._recursive_split(candidate, separators[1:], chunk_size)
                    )
                    current = ""
                else:
                    # Start new chunk, include overlap from end of previous
                    overlap_text = current[-OVERLAP_CHARS:] if current else ""
                    current = overlap_text + candidate

        if current.strip():
            results.append(current.strip())

        return results

    @staticmethod
    def _char_split(text: str, chunk_size: int) -> list[str]:
        """Hard character split with overlap, used as final fallback."""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start += chunk_size - OVERLAP_CHARS
        return chunks
