# pyrefly: ignore [missing-import]
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Webhook server settings
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Git Provider settings
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None

    # LLM Settings
    LLM_PROVIDER: str = "openai" # "openai", "anthropic", "gemini", "github_models", "groq", "openrouter"
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GITHUB_MODEL_NAME: str = "gpt-4o"
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL_NAME: str = "meta-llama/llama-3.3-70b-instruct"
    OPENROUTER_SITE_URL: Optional[str] = None
    OPENROUTER_APP_NAME: Optional[str] = None

    # Comma-separated provider names (e.g. "groq,openrouter") tried in order if the
    # primary LLM_PROVIDER errors out (rate limit, payload too large, connection error).
    LLM_FALLBACK_PROVIDERS: str = ""

    # PR review payload limits — bounds diff/RAG-context size sent to the LLM to avoid
    # blowing past provider token-per-minute limits on large PRs.
    MAX_DIFF_CHARS: int = 24000
    MAX_RAG_CONTEXT_CHARS: int = 4000

    # Temporal (durable execution for PR review jobs)
    TEMPORAL_ADDRESS: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"
    TEMPORAL_TASK_QUEUE: str = "pr-review-task-queue"

    # RAG Configuration
    RAG_ENABLED: bool = True
    RAG_DB_PATH: str = "./.rag_db"
    RAG_EMBEDDER: str = "local" # "local" | "openai"
    RAG_VECTOR_STORE: str = "chroma" # "chroma" | "qdrant"
    RAG_TOP_K: int = 5
    RAG_INGEST_CLOSED_PRS: int = 50
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
