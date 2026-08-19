"""
RAGRetriever — the query-side of the RAG pipeline.

Workflow per retrieval:
  1. Build a composite query from the PR diff summary + changed file paths
  2. Embed the query using the configured embedder
  3. Query the vector store (filtered to the same repo)
  4. Deduplicate chunks by file_path (keep highest-ranked per file)
  5. Dynamically compress the context block to fit within a token budget
     by progressively truncating lower-ranked chunks
  6. Return a formatted context string ready for injection into the LLM prompt

Dynamic Compression (your chosen strategy):
  - Start from top-ranked chunk → accumulate text
  - Estimate tokens as len(text) // 4  (fast, no tokenizer dependency)
  - Each chunk that would exceed the remaining budget is truncated to fit
  - A summary header counts how many chunks were truncated vs included
"""
import logging
import re
from rag.chunk import Chunk
from rag.embedder.base import BaseEmbedder
from rag.vector_store.base import BaseVectorStore

logger = logging.getLogger(__name__)

# Approximate characters → tokens ratio
_CHARS_PER_TOKEN = 4

# Source-type labels for readability in the prompt
_SOURCE_LABELS = {
    "diff": "📌 Past PR Diff",
    "review_comment": "💬 Past Review Comment",
    "source_file": "📄 Codebase File",
    "doc": "📚 Project Documentation",
    "commit": "🔖 Commit Message",
}


class RAGRetriever:
    """
    Retrieves relevant knowledge-base context for a given PR diff.
    Injects a dynamically compressed context block into the review prompt.
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        top_k: int = 5,
        token_budget: int = 2000,
    ):
        self._embedder = embedder
        self._store = vector_store
        self._top_k = top_k
        self._token_budget = token_budget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, diff_text: str, repo_name: str) -> str:
        """
        Build and return a RAG context block for the given diff.
        Returns an empty string if the store is empty or retrieval fails.
        """
        if self._store.count() == 0:
            logger.info("RAG store is empty — skipping retrieval for %s", repo_name)
            return ""

        query = self._build_query(diff_text)
        if not query.strip():
            return ""

        try:
            embedding = self._embedder.embed_one(query)
        except Exception as exc:
            logger.error("RAG embedding failed: %s", exc)
            return ""

        try:
            chunks = self._store.query(
                embedding=embedding,
                top_k=self._top_k,
                filter={"repo_name": repo_name} if repo_name else None,
            )
        except Exception as exc:
            logger.error("RAG vector store query failed: %s", exc)
            return ""

        if not chunks:
            logger.info("RAG retrieval returned 0 chunks for %s", repo_name)
            return ""

        chunks = self._deduplicate(chunks)
        context_block = self._compress_and_format(chunks)

        logger.info(
            "RAG retrieved %d chunks for %s (query: %s...)",
            len(chunks), repo_name, query[:60],
        )
        return context_block

    def retrieve_for_query(self, query: str, repo_name: str) -> str:
        """
        Direct query interface — used by the `search_knowledge_base` MCP tool
        so the LLM can pull context proactively during its ReAct loop.
        """
        if self._store.count() == 0:
            return "Knowledge base is empty. Run the ingestion script first."

        try:
            embedding = self._embedder.embed_one(query)
            chunks = self._store.query(
                embedding=embedding,
                top_k=self._top_k,
                filter={"repo_name": repo_name} if repo_name else None,
            )
        except Exception as exc:
            logger.error("RAG tool query failed: %s", exc)
            return f"RAG retrieval error: {exc}"

        if not chunks:
            return "No relevant knowledge found for this query."

        return self._compress_and_format(chunks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(diff_text: str) -> str:
        """
        Construct a search query from the diff:
        - Extract changed file paths
        - Extract added lines (lines starting with '+' but not '+++')
        - Return a compact summary string
        """
        lines = diff_text.splitlines()
        file_paths: list[str] = []
        added_lines: list[str] = []

        for line in lines:
            if line.startswith("+++ b/"):
                file_paths.append(line[6:].strip())
            elif line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line[1:].strip())

        # Build query: file paths + first 20 meaningful added lines
        parts: list[str] = []
        if file_paths:
            parts.append("Changed files: " + ", ".join(file_paths[:10]))
        if added_lines:
            parts.append("New code:\n" + "\n".join(added_lines[:20]))

        return "\n".join(parts) if parts else diff_text[:500]

    @staticmethod
    def _deduplicate(chunks: list[Chunk]) -> list[Chunk]:
        """
        For source_file chunks: keep only the highest-ranked chunk per file_path.
        For other types: keep all (review comments and diffs are all unique).
        """
        seen_files: set[str] = set()
        result: list[Chunk] = []
        for chunk in chunks:
            if chunk.source_type == "source_file":
                fp = chunk.metadata.get("file_path", "")
                if fp in seen_files:
                    continue
                seen_files.add(fp)
            result.append(chunk)
        return result

    def _compress_and_format(self, chunks: list[Chunk]) -> str:
        """
        Dynamically compress chunks to fit within the token budget.
        Higher-ranked chunks (earlier in list) are included first and fully.
        Lower-ranked chunks are truncated to fill remaining budget.
        """
        budget_chars = self._token_budget * _CHARS_PER_TOKEN
        sections: list[str] = []
        chars_used = 0
        included = 0
        truncated = 0

        for chunk in chunks:
            label = _SOURCE_LABELS.get(chunk.source_type, chunk.source_type.upper())
            file_path = chunk.metadata.get("file_path", "")
            pr_num = chunk.metadata.get("pr_number", "")

            # Build header line
            header_parts = [label]
            if file_path:
                header_parts.append(f"file: {file_path}")
            if pr_num:
                header_parts.append(f"PR #{pr_num}")
            header = " | ".join(header_parts)

            section_header = f"--- {header} ---"
            remaining = budget_chars - chars_used - len(section_header) - 10

            if remaining <= 0:
                truncated += 1
                continue

            # Truncate chunk text if it would exceed the remaining budget
            chunk_text = chunk.text
            if len(chunk_text) > remaining:
                chunk_text = chunk_text[:remaining].rstrip()
                chunk_text += "\n[...truncated]"
                truncated += 1
            else:
                included += 1

            section = f"{section_header}\n{chunk_text}"
            sections.append(section)
            chars_used += len(section)

        if not sections:
            return ""

        summary = (
            f"[RAG Context: {included} chunks fully included"
            + (f", {truncated} truncated/omitted due to token budget" if truncated else "")
            + "]"
        )

        return (
            "\n\n=== Retrieved Context from Knowledge Base ===\n"
            + summary + "\n\n"
            + "\n\n".join(sections)
            + "\n=== End of Retrieved Context ===\n"
        )
