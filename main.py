# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import hmac
import hashlib
from config.settings import settings
from models.schemas import PullRequestEvent, ChatRequest, ChatResponse
from adapters.vcs.github import GitHubAdapter
from adapters.llm.openai import OpenAIAdapter
from core.agent import PRReviewAgent
from core.chat_agent import ChatAgent
from core.mcp_client import InternalMCPClient
from core.review_jobs import review_job_store
from mcp_server.server import MCPServer
from mcp_server.http_transport import router as mcp_router
from mcp_server.tools.github_tools import GitHubTools
from mcp_server.tools.rag_tools import RAGTools
from rag.embedder.local_embedder import LocalEmbedder
from rag.embedder.openai_embedder import OpenAIEmbedder
from rag.vector_store.chroma_store import ChromaVectorStore
from rag.vector_store.qdrant_store import QdrantVectorStore
from rag.retriever import RAGRetriever
from rag.ingestion.github_ingester import GitHubIngester
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI PR Review Agent (HITL)")

# Mount the MCP HTTP transport — accepts JSON-RPC from any external MCP client
# Usage: POST /mcp  with  X-GitHub-Token: <token>  header
app.include_router(mcp_router, prefix="/mcp", tags=["MCP"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-GitHub-Token"],
)

def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not settings.GITHUB_WEBHOOK_SECRET:
        return True
    if not signature_header:
        return False
    parts = signature_header.split("=")
    if len(parts) != 2 or parts[0] != "sha256":
        return False
    expected_mac = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_mac, parts[1])

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

def run_deep_review(repo_name: str, pr_number: int, github_token: Optional[str] = None):
    """Background task to run the deep PR review."""
    logger.info(f"Starting background review for PR #{pr_number} in {repo_name}")
    try:
        vcs_adapter, llm_adapter = get_adapters(github_token)
        
        # We need an MCP Client for the deep review as well
        mcp_server = MCPServer()
        
        # Setup RAG
        rag_retriever, github_ingester = get_rag_components(vcs_adapter)
        if rag_retriever:
            rag_tools = RAGTools(retriever=rag_retriever, default_repo=repo_name)
            mcp_server.register_rag_tools(rag_tools)

        mcp_client = InternalMCPClient(mcp_server)
        
        agent = PRReviewAgent(
            vcs_adapter=vcs_adapter, 
            llm_adapter=llm_adapter, 
            mcp_client=mcp_client,
            rag_retriever=rag_retriever,
            github_ingester=github_ingester
        )
        agent.review_pr(repo_name, pr_number)
        review_job_store.mark_completed(repo_name, pr_number)
    except Exception as e:
        logger.error(f"Review failed: {e}")
        review_job_store.mark_failed(repo_name, pr_number, str(e))
    finally:
        try:
            vcs_adapter.close()
            llm_adapter.close()
        except Exception:
            pass

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Stateless conversational endpoint for HITL PR Review.
    Accepts a list of messages (full conversation history) and returns the updated history.
    Optionally accepts an X-GitHub-Token header to use a caller-supplied PAT instead of
    the server's configured GITHUB_TOKEN.
    """
    messages = [msg.model_dump() for msg in chat_request.messages]
    github_token = request.headers.get("X-GitHub-Token")

    try:
        vcs_adapter, llm_adapter = get_adapters(github_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def trigger_review_callback(repo_name: str, pr_number: int):
        # Record the job as pending before scheduling so a client that polls
        # /reviews/status immediately after this call sees "pending", not "unknown".
        review_job_store.mark_pending(repo_name, pr_number)
        # Spawn the deep review job in FastAPI background tasks
        background_tasks.add_task(run_deep_review, repo_name, pr_number, github_token)

    try:
        # Build MCPServer and register github tools
        mcp_server = MCPServer()
        github_tools = GitHubTools(vcs_adapter=vcs_adapter, review_callback=trigger_review_callback)
        mcp_server.register_github_tools(github_tools)
        
        # Setup RAG
        rag_retriever, _ = get_rag_components()
        if rag_retriever:
            rag_tools = RAGTools(retriever=rag_retriever) # default_repo can be inferred from chat context
            mcp_server.register_rag_tools(rag_tools)

        mcp_client = InternalMCPClient(mcp_server)
        
        chat_agent = ChatAgent(
            llm_adapter=llm_adapter, 
            mcp_client=mcp_client,
            rag_retriever=rag_retriever
        )
        updated_messages = chat_agent.chat(messages)
        
        return JSONResponse({"messages": updated_messages})
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        vcs_adapter.close()
        llm_adapter.close()

@app.get("/repos")
async def list_repos(request: Request):
    """List repositories accessible to the caller-supplied GitHub token."""
    github_token = request.headers.get("X-GitHub-Token")
    try:
        vcs_adapter = get_vcs_adapter(github_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        repos = vcs_adapter.list_repositories()
        return JSONResponse({"repos": repos})
    except Exception as e:
        logger.error(f"Failed to list repos: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        vcs_adapter.close()

@app.get("/repos/{repo_name:path}/pulls")
async def list_repo_pulls(repo_name: str, request: Request, state: str = "open"):
    """List pull requests for a specific repository."""
    github_token = request.headers.get("X-GitHub-Token")
    try:
        vcs_adapter = get_vcs_adapter(github_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        pulls = vcs_adapter.list_pull_requests(repo_name, state)
        return JSONResponse({"pulls": pulls})
    except Exception as e:
        logger.error(f"Failed to list pulls for {repo_name}: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        vcs_adapter.close()

@app.get("/reviews/status")
async def review_status(repo_name: str, pr_number: int):
    """Check the status of a background PR review triggered via /chat's trigger_review tool."""
    job = review_job_store.get(repo_name, pr_number)
    return JSONResponse(job or {"status": "unknown"})

@app.post("/webhook")
async def github_webhook(request: Request):
    """
    Webhook acts only as a notification receiver. Does not auto-trigger reviews.
    """
    signature_header = request.headers.get("x-hub-signature-256")
    body = await request.body()
    
    if not verify_signature(body, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = request.headers.get("x-github-event")
    if event_type == "pull_request":
        try:
            event = PullRequestEvent(**data)
            # Just log the event or send a push notification to frontend
            logger.info(f"Received PR event: {event.action} for PR #{event.number}")
            return JSONResponse({"status": "logged", "message": "Event recorded, but review not automatically started in HITL mode."})
        except Exception as e:
            logger.error(f"Failed to parse event: {e}")
            raise HTTPException(status_code=422, detail="Invalid event schema")
    
    return JSONResponse({"status": "ignored", "message": f"Event {event_type} ignored"})

if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
