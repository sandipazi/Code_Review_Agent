"""
DiffChunker — splits a unified git diff into per-hunk Chunks.

Each hunk (`@@ ... @@`) becomes one independent chunk, preserving:
  - The file path header (--- a/  +++ b/)
  - ±CONTEXT_LINES surrounding lines of unchanged context
  - Metadata: file_path, hunk_index, pr_number, pr_outcome, author, timestamp
"""
import re
import uuid
import logging
from rag.chunk import Chunk
from rag.chunker.base import BaseChunker

logger = logging.getLogger(__name__)

# Regex to detect hunk headers:  @@ -L,N +L,N @@ optional_func_name
_HUNK_HEADER = re.compile(r"^@@[^@]*@@.*$", re.MULTILINE)


class DiffChunker(BaseChunker):
    """
    Splits a unified diff string at @@ hunk boundaries.
    Keeps each hunk together with its file header for full context.
    """

    def chunk(
        self,
        content: str,
        repo_name: str,
        pr_number: int = 0,
        pr_outcome: str = "unknown",
        author: str = "",
        timestamp: str = "",
    ) -> list[Chunk]:
        chunks: list[Chunk] = []

        if not content or not content.strip():
            return chunks

        # Split diff into per-file sections using "diff --git" lines
        file_sections = re.split(r"(?=^diff --git )", content, flags=re.MULTILINE)

        for section in file_sections:
            if not section.strip():
                continue

            # Extract the file path from the +++ b/... line
            file_path = self._extract_file_path(section)

            # Find all hunk positions
            hunk_positions = [m.start() for m in _HUNK_HEADER.finditer(section)]
            if not hunk_positions:
                continue

            # The file header is everything before the first hunk
            file_header = section[: hunk_positions[0]]

            for idx, start in enumerate(hunk_positions):
                end = hunk_positions[idx + 1] if idx + 1 < len(hunk_positions) else len(section)
                hunk_text = section[start:end].strip()

                if not hunk_text:
                    continue

                # Combine file header + hunk for full context
                chunk_text = f"{file_header.strip()}\n{hunk_text}"

                chunk = Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=chunk_text,
                    source_type="diff",
                    repo_name=repo_name,
                    metadata={
                        "file_path": file_path,
                        "hunk_index": idx,
                        "pr_number": pr_number,
                        "pr_outcome": pr_outcome,
                        "author": author,
                        "timestamp": timestamp,
                    },
                )
                chunks.append(chunk)

        logger.debug(
            "DiffChunker produced %d chunks for PR #%d (%s)",
            len(chunks), pr_number, repo_name,
        )
        return chunks

    @staticmethod
    def _extract_file_path(section: str) -> str:
        """Extract the new file path from a +++ b/... line."""
        for line in section.splitlines():
            if line.startswith("+++ b/"):
                return line[6:].strip()
            if line.startswith("+++ "):
                return line[4:].strip()
        # Fallback: parse from "diff --git a/X b/X"
        match = re.search(r"^diff --git a/\S+ b/(\S+)", section, re.MULTILINE)
        return match.group(1) if match else "unknown"
