"""VCS/LLM/RAG adapter construction helpers.

Pulled out of main.py so both the FastAPI app and the Temporal worker process
(worker.py / core/temporal_workflows.py) can build adapters without either one
importing the other — the worker must not pull in FastAPI/route-registration code.
"""
from typing import Optional
import logging

from config.settings import settings
from adapters.vcs.github import GitHubAdapter
from adapters.llm.openai import OpenAIAdapter
from rag.embedder.local_embedder import LocalEmbedder
from rag.embedder.openai_embedder import OpenAIEmbedder
from rag.vector_store.chroma_store import ChromaVectorStore
from rag.vector_store.qdrant_store import QdrantVectorStore
from rag.retriever import RAGRetriever
from rag.ingestion.github_ingester import GitHubIngester

logger = logging.getLogger(__name__)


def get_vcs_adapter(github_token: Optional[str] = None) -> GitHubAdapter:
    """Helper to instantiate the VCS adapter alone (no LLM key required)."""
    token = github_token or settings.GITHUB_TOKEN
    if not token:
        raise Exception("GitHub token not configured.")
    return GitHubAdapter(token=token)


def _build_llm_adapter(provider_name: str, github_token: str):
    """Build a single LLM adapter for the named provider, or None if it's
    unconfigured/unsupported (callers decide whether that's fatal)."""
    provider = provider_name.lower()
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            return None
        return OpenAIAdapter(api_key=settings.OPENAI_API_KEY)
    elif provider == "github_models":
        from adapters.llm.github_models import GitHubModelsAdapter
        return GitHubModelsAdapter(token=github_token, model=settings.GITHUB_MODEL_NAME)
    elif provider == "groq":
        if not settings.GROQ_API_KEY:
            return None
        from adapters.llm.groq import GroqAdapter
        return GroqAdapter(api_key=settings.GROQ_API_KEY, model=settings.GROQ_MODEL_NAME)
    elif provider == "openrouter":
        if not settings.OPENROUTER_API_KEY:
            return None
        from adapters.llm.openrouter import OpenRouterAdapter
        return OpenRouterAdapter(
            api_key=settings.OPENROUTER_API_KEY,
            model=settings.OPENROUTER_MODEL_NAME,
            site_url=settings.OPENROUTER_SITE_URL,
            app_name=settings.OPENROUTER_APP_NAME,
        )
    return None


def get_adapters(github_token: Optional[str] = None):
    """Helper to instantiate VCS and LLM adapters."""
    vcs_adapter = get_vcs_adapter(github_token)
    token = github_token or settings.GITHUB_TOKEN

    primary = _build_llm_adapter(settings.LLM_PROVIDER, token)
    if primary is None:
        raise Exception(f"Unsupported or unconfigured LLM provider: {settings.LLM_PROVIDER}")

    fallback_names = [p.strip() for p in settings.LLM_FALLBACK_PROVIDERS.split(",") if p.strip()]
    fallback_adapters = []
    for name in fallback_names:
        adapter = _build_llm_adapter(name, token)
        if adapter is None:
            logger.warning(f"Skipping fallback LLM provider '{name}': not configured or unsupported.")
            continue
        fallback_adapters.append(adapter)

    if fallback_adapters:
        from adapters.llm.fallback import FallbackLLMAdapter
        llm_adapter = FallbackLLMAdapter([primary] + fallback_adapters)
    else:
        llm_adapter = primary

    return vcs_adapter, llm_adapter


def get_rag_components(vcs_adapter=None):
    """Helper to instantiate RAG components based on settings."""
    if not settings.RAG_ENABLED:
        return None, None

    # Embedder
    if settings.RAG_EMBEDDER.lower() == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI API key missing, falling back to local embedder.")
            embedder = LocalEmbedder()
        else:
            embedder = OpenAIEmbedder(api_key=settings.OPENAI_API_KEY)
    else:
        embedder = LocalEmbedder()

    # Vector Store
    if settings.RAG_VECTOR_STORE.lower() == "qdrant":
        vector_store = QdrantVectorStore(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, vector_size=embedder.vector_size)
    else:
        vector_store = ChromaVectorStore(persist_directory=settings.RAG_DB_PATH)

    # Retriever
    retriever = RAGRetriever(
        embedder=embedder,
        vector_store=vector_store,
        top_k=settings.RAG_TOP_K
    )

    # Ingester (optional, needs VCS adapter)
    ingester = None
    if vcs_adapter:
        ingester = GitHubIngester(
            vcs_adapter=vcs_adapter,
            embedder=embedder,
            vector_store=vector_store
        )

    return retriever, ingester
