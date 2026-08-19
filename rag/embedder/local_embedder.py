"""
LocalEmbedder — uses sentence-transformers (all-MiniLM-L6-v2).

Free, offline, 384-dimensional vectors.
Lazy-loads the model on first use to keep startup time fast.

Install: pip install sentence-transformers
"""
import logging
from rag.embedder.base import BaseEmbedder

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"

# Process-wide cache of loaded SentenceTransformer models, keyed by model name.
# A fresh LocalEmbedder is constructed on every single review activity
# (core/adapter_factory.py: get_rag_components), so without this cache each review
# would re-touch the Hugging Face Hub (cache-validation HEAD requests at minimum,
# a full model download on a cold worker) even though the underlying model object
# is identical every time within this process.
_MODEL_CACHE: dict = {}


class LocalEmbedder(BaseEmbedder):
    """
    Wraps sentence-transformers for fully local, zero-cost embeddings.
    Uses `all-MiniLM-L6-v2` by default (384 dims, ~90 MB).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model_name = model_name
        self._model = _MODEL_CACHE.get(model_name)  # reuse if already loaded in this process
        logger.info("LocalEmbedder configured with model '%s' (lazy load)", model_name)

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is not installed. "
                    "Run: pip install sentence-transformers"
                ) from exc
            logger.info("Loading sentence-transformer model '%s'...", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            _MODEL_CACHE[self._model_name] = self._model
            logger.info("Model loaded. Vector size: %d", self.vector_size)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        # encode returns a numpy array; convert to plain Python lists
        embeddings = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,  # cosine similarity works better normalised
        )
        return embeddings.tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def vector_size(self) -> int:
        self._load()
        return self._model.get_sentence_embedding_dimension()
