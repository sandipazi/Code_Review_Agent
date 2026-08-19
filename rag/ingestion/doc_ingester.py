"""
DocIngester — indexes local documentation files (README, Architecture.md,
CONTRIBUTING.md, coding guidelines, etc.) into the RAG vector store.
"""
import logging
import os
from datetime import datetime, timezone

from rag.chunker.doc_chunker import DocChunker
from rag.chunker.code_chunker import CodeChunker
from rag.embedder.base import BaseEmbedder
from rag.vector_store.base import BaseVectorStore

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32

# File extensions treated as prose/docs
_DOC_EXTENSIONS = {".md", ".txt", ".rst"}
# File extensions treated as code (for technical docs with code blocks)
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".yaml", ".yml", ".toml"}


class DocIngester:
    """
    Walks a directory (or ingests a list of file paths) and indexes
    all doc/text files into the vector store.
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
    ):
        self._embedder = embedder
        self._store = vector_store
        self._doc_chunker = DocChunker()
        self._code_chunker = CodeChunker()

    def ingest_directory(self, directory: str, repo_name: str) -> int:
        """Walk `directory` and ingest all supported files. Returns chunk count."""
        total = 0
        for root, _, files in os.walk(directory):
            # Skip hidden dirs and virtual environments
            if any(part.startswith(".") or part in {"venv", "__pycache__", "node_modules"}
                   for part in root.split(os.sep)):
                continue
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in (_DOC_EXTENSIONS | _CODE_EXTENSIONS):
                    continue
                file_path = os.path.join(root, fname)
                total += self.ingest_file(file_path, repo_name)
        return total

    def ingest_file(self, file_path: str, repo_name: str) -> int:
        """Ingest a single file. Returns number of chunks stored."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError as exc:
            logger.warning("Could not read %s: %s", file_path, exc)
            return 0

        if not content.strip():
            return 0

        timestamp = datetime.now(timezone.utc).isoformat()
        ext = os.path.splitext(file_path)[1].lower()

        if ext in _DOC_EXTENSIONS:
            chunks = self._doc_chunker.chunk(
                content, repo_name,
                file_path=file_path,
                doc_type="doc",
                timestamp=timestamp,
            )
        else:
            chunks = self._code_chunker.chunk(
                content, repo_name,
                file_path=file_path,
                timestamp=timestamp,
            )

        return self._embed_and_store(chunks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_and_store(self, chunks) -> int:
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
                logger.error("Failed to embed/store batch: %s", exc)
        return stored
