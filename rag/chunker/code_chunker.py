"""
CodeChunker — AST-aware source file chunker.

Strategy:
  - Python files: split at top-level function/class boundaries using `ast`.
  - Other files: sliding window of CHUNK_LINES lines with OVERLAP_LINES overlap.

Each chunk includes surrounding ±CONTEXT_LINES of adjacent code so the LLM
has enough context to understand the function's neighbourhood.
"""
import ast
import uuid
import logging
from rag.chunk import Chunk
from rag.chunker.base import BaseChunker

logger = logging.getLogger(__name__)

CHUNK_LINES = 100   # target window for non-Python files
OVERLAP_LINES = 20  # overlap between consecutive windows


class CodeChunker(BaseChunker):
    """
    AST-aware chunker for source files. Falls back to sliding window
    for non-Python languages.
    """

    def chunk(
        self,
        content: str,
        repo_name: str,
        file_path: str = "",
        branch: str = "main",
        timestamp: str = "",
    ) -> list[Chunk]:
        if not content or not content.strip():
            return []

        if file_path.endswith(".py"):
            return self._chunk_python(content, repo_name, file_path, branch, timestamp)
        return self._chunk_sliding_window(content, repo_name, file_path, branch, timestamp)

    # ------------------------------------------------------------------
    # Python AST splitting
    # ------------------------------------------------------------------

    def _chunk_python(
        self, content: str, repo_name: str, file_path: str, branch: str, timestamp: str
    ) -> list[Chunk]:
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            logger.warning("AST parse failed for %s — falling back to sliding window", file_path)
            return self._chunk_sliding_window(content, repo_name, file_path, branch, timestamp)

        # Collect top-level definitions (functions, classes, async functions)
        top_level = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.col_offset == 0  # top-level only
        ]

        if not top_level:
            # No top-level defs — treat the whole file as one chunk
            return [self._make_chunk(content, repo_name, "source_file", file_path, 1, len(lines), branch, timestamp)]

        # Sort by line number
        top_level.sort(key=lambda n: n.lineno)

        # Any module-level code before the first def
        first_def_line = top_level[0].lineno - 1
        if first_def_line > 0:
            header_text = "".join(lines[:first_def_line])
            if header_text.strip():
                chunks.append(
                    self._make_chunk(header_text, repo_name, "source_file", file_path, 1, first_def_line, branch, timestamp)
                )

        for node in top_level:
            start = node.lineno - 1
            end = getattr(node, "end_lineno", start + 1)
            chunk_text = "".join(lines[start:end])
            chunks.append(
                self._make_chunk(chunk_text, repo_name, "source_file", file_path, start + 1, end, branch, timestamp)
            )

        logger.debug("CodeChunker (AST) produced %d chunks for %s", len(chunks), file_path)
        return chunks

    # ------------------------------------------------------------------
    # Sliding window for non-Python
    # ------------------------------------------------------------------

    def _chunk_sliding_window(
        self, content: str, repo_name: str, file_path: str, branch: str, timestamp: str
    ) -> list[Chunk]:
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []

        start = 0
        while start < len(lines):
            end = min(start + CHUNK_LINES, len(lines))
            chunk_text = "".join(lines[start:end])
            if chunk_text.strip():
                chunks.append(
                    self._make_chunk(chunk_text, repo_name, "source_file", file_path, start + 1, end, branch, timestamp)
                )
            start += CHUNK_LINES - OVERLAP_LINES  # slide forward with overlap

        logger.debug("CodeChunker (window) produced %d chunks for %s", len(chunks), file_path)
        return chunks

    # ------------------------------------------------------------------
    # Factory helper
    # ------------------------------------------------------------------

    @staticmethod
    def _make_chunk(
        text: str, repo_name: str, source_type: str,
        file_path: str, start_line: int, end_line: int,
        branch: str, timestamp: str,
    ) -> Chunk:
        return Chunk(
            chunk_id=str(uuid.uuid4()),
            text=text,
            source_type=source_type,
            repo_name=repo_name,
            metadata={
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "branch": branch,
                "timestamp": timestamp,
            },
        )
