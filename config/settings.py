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
    LLM_PROVIDER: str = "openai" # "openai", "anthropic", "gemini", "github_models", "groq"
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GITHUB_MODEL_NAME: str = "gpt-4o"
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"

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
