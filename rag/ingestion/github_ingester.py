"""
GitHubIngester — fetches and indexes data from GitHub into the RAG vector store.

Ingests:
  1. Closed PR diffs + review comments (with pr_outcome: merged / closed)
  2. Source files from the default branch
  3. Recent commit messages

Uses the existing GitHubAdapter so no new HTTP client is introduced.
Embeds chunks in batches of EMBED_BATCH_SIZE to limit memory usage.
"""
import logging
import uuid
from datetime import datetime, timezone

from adapters.vcs.base import BaseVCSAdapter
from rag.chunk import Chunk
from rag.chunker.diff_chunker import DiffChunker
from rag.chunker.code_chunker import CodeChunker
from rag.embedder.base import BaseEmbedder
from rag.vector_store.base import BaseVectorStore

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32
# Extensions considered worth indexing as source files
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".cpp", ".c",
    ".h", ".cs", ".php", ".kt", ".swift", ".sh", ".yaml", ".yml", ".json",
    ".toml", ".md", ".txt",
}


class GitHubIngester:
    """
    Pulls data from GitHub via the VCS adapter, chunks it, embeds it,
    and upserts into the vector store.
    """

    def __init__(
        self,
        vcs_adapter: BaseVCSAdapter,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
    ):
        self._vcs = vcs_adapter
        self._embedder = embedder
        self._store = vector_store
        self._diff_chunker = DiffChunker()
        self._code_chunker = CodeChunker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_closed_prs(self, repo_name: str, max_prs: int = 50) -> int:
        """
        Fetch up to `max_prs` closed PRs, chunk their diffs and review
        comments, embed, and store. Returns total chunks ingested.
        """
        logger.info("Ingesting closed PRs for %s (max=%d)", repo_name, max_prs)
        total = 0

        try:
            prs = self._vcs.list_pull_requests(repo_name, state="closed")
        except Exception as exc:
            logger.error("Failed to list closed PRs: %s", exc)
            return 0

        for pr in prs[:max_prs]:
            pr_number = pr.get("number", 0)
            pr_outcome = "merged" if pr.get("merged_at") else "closed"
            author = pr.get("user", {}).get("login", "") if isinstance(pr.get("user"), dict) else ""
            timestamp = pr.get("closed_at", "") or pr.get("merged_at", "") or ""

            # --- Diff chunks ---
            try:
                diff_text = self._vcs.get_pull_request_diff(repo_name, pr_number)
                diff_chunks = self._diff_chunker.chunk(
                    diff_text, repo_name,
                    pr_number=pr_number,
                    pr_outcome=pr_outcome,
                    author=author,
                    timestamp=timestamp,
                )
                ingested = self._embed_and_store(diff_chunks)
                total += ingested
            except Exception as exc:
                logger.warning("Skipping diff for PR #%d: %s", pr_number, exc)

            # --- Review comment chunks ---
            try:
                comments = self._vcs.get_review_comments(repo_name, pr_number)
                comment_chunks = self._build_comment_chunks(
                    comments, repo_name, pr_number, pr_outcome, timestamp
                )
                ingested = self._embed_and_store(comment_chunks)
                total += ingested
            except Exception as exc:
                logger.debug("No review comments for PR #%d: %s", pr_number, exc)

        logger.info("Closed PR ingestion complete: %d chunks stored for %s", total, repo_name)
        return total

    def ingest_source_files(self, repo_name: str, branch: str = "main", path: str = "") -> int:
        """
        Fetch and index source files from a branch. Returns total chunks ingested.
        """
        logger.info("Ingesting source files from %s@%s path='%s'", repo_name, branch, path)
        total = 0
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            file_list = self._vcs.list_files(repo_name, branch=branch, path=path)
        except Exception as exc:
            logger.error("Failed to list files for %s: %s", repo_name, exc)
            return 0

        for file_info in file_list:
            file_path = file_info if isinstance(file_info, str) else file_info.get("path", "")
            ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
            if ext.lower() not in _SOURCE_EXTENSIONS:
                continue

            try:
                content = self._vcs.read_file(repo_name, file_path, branch=branch)
                chunks = self._code_chunker.chunk(
                    content, repo_name,
                    file_path=file_path,
                    branch=branch,
                    timestamp=timestamp,
                )
                ingested = self._embed_and_store(chunks)
                total += ingested
            except Exception as exc:
                logger.debug("Skipping file %s: %s", file_path, exc)

        logger.info("Source file ingestion complete: %d chunks for %s", total, repo_name)
        return total

    def ingest_review_result(
        self,
        repo_name: str,
        pr_number: int,
        general_comment: str,
        inline_comments: list[dict],
        pr_outcome: str = "reviewed",
    ) -> int:
        """
        Auto-ingest a newly generated review back into the vector store.
        Called automatically by PRReviewAgent after posting a review.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        chunks: list[Chunk] = []

        # General comment as one chunk
        if general_comment and general_comment.strip():
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                text=f"[General Review Comment for PR #{pr_number}]\n{general_comment}",
                source_type="review_comment",
                repo_name=repo_name,
                metadata={
                    "pr_number": pr_number,
                    "pr_outcome": pr_outcome,
                    "comment_type": "general",
                    "timestamp": timestamp,
                },
            ))

        # Each inline comment as its own chunk
        for c in inline_comments:
            text = (
                f"[Inline Review on {c.get('path','?')} line {c.get('line','?')} "
                f"for PR #{pr_number}]\n{c.get('comment', '')}"
            )
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                text=text,
                source_type="review_comment",
                repo_name=repo_name,
                metadata={
                    "pr_number": pr_number,
                    "pr_outcome": pr_outcome,
                    "file_path": c.get("path", ""),
                    "line": c.get("line", 0),
                    "comment_type": "inline",
                    "timestamp": timestamp,
                },
            ))

        total = self._embed_and_store(chunks)
        logger.info("Auto-ingested %d review chunks for PR #%d in %s", total, pr_number, repo_name)
        return total

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_and_store(self, chunks: list[Chunk]) -> int:
        """Embed chunks in batches and upsert into vector store."""
        if not chunks:
            return 0
        stored = 0
        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i : i + EMBED_BATCH_SIZE]
            texts = [c.text for c in batch]
            try:
                embeddings = self._embedder.embed(texts)
                for chunk, emb in zip(batch, embeddings):
                    chunk.embedding = emb
                self._store.upsert(batch)
                stored += len(batch)
            except Exception as exc:
                logger.error("Embedding/upsert failed for batch starting at %d: %s", i, exc)
        return stored

    @staticmethod
    def _build_comment_chunks(
        comments: list[dict],
        repo_name: str,
        pr_number: int,
        pr_outcome: str,
        timestamp: str,
    ) -> list[Chunk]:
        chunks = []
        for c in comments:
            body = c.get("body", "").strip()
            if not body:
                continue
            file_path = c.get("path", "")
            line = c.get("line") or c.get("original_line", 0)
            text = f"[Review comment on {file_path}:{line} for PR #{pr_number}]\n{body}"
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                text=text,
                source_type="review_comment",
                repo_name=repo_name,
                metadata={
                    "pr_number": pr_number,
                    "pr_outcome": pr_outcome,
                    "file_path": file_path,
                    "line": line,
                    "timestamp": timestamp,
                },
            ))
        return chunks
