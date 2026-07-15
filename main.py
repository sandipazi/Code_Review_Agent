# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse
import hmac
import hashlib
from config.settings import settings
from models.schemas import PullRequestEvent, ChatRequest, ChatResponse
from adapters.vcs.github import GitHubAdapter
from adapters.llm.openai import OpenAIAdapter
from core.agent import PRReviewAgent
from core.chat_agent import ChatAgent
from core.mcp_client import InternalMCPClient
from mcp_server.server import MCPServer
from mcp_server.http_transport import router as mcp_router
from mcp_server.tools.github_tools import GitHubTools
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI PR Review Agent (HITL)")

# Mount the MCP HTTP transport — accepts JSON-RPC from any external MCP client
# Usage: POST /mcp  with  X-GitHub-Token: <token>  header
app.include_router(mcp_router, prefix="/mcp", tags=["MCP"])

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

def get_adapters():
    """Helper to instantiate VCS and LLM adapters."""
    if not settings.GITHUB_TOKEN:
        raise Exception("GitHub token not configured.")
    vcs_adapter = GitHubAdapter(token=settings.GITHUB_TOKEN)
    
    llm_provider_lower = settings.LLM_PROVIDER.lower()
    if llm_provider_lower == "openai":
        if not settings.OPENAI_API_KEY:
            raise Exception("OpenAI API key not configured.")
        llm_adapter = OpenAIAdapter(api_key=settings.OPENAI_API_KEY)
    elif llm_provider_lower == "github_models":
        from adapters.llm.github_models import GitHubModelsAdapter
        llm_adapter = GitHubModelsAdapter(token=settings.GITHUB_TOKEN, model=settings.GITHUB_MODEL_NAME)
    else:
        raise Exception(f"Unsupported LLM provider: {settings.LLM_PROVIDER}")
        
    return vcs_adapter, llm_adapter

def run_deep_review(repo_name: str, pr_number: int):
    """Background task to run the deep PR review."""
    logger.info(f"Starting background review for PR #{pr_number} in {repo_name}")
    try:
        vcs_adapter, llm_adapter = get_adapters()
        
        # We need an MCP Client for the deep review as well
        mcp_server = MCPServer()
        mcp_client = InternalMCPClient(mcp_server)
        
        agent = PRReviewAgent(vcs_adapter=vcs_adapter, llm_adapter=llm_adapter, mcp_client=mcp_client)
        agent.review_pr(repo_name, pr_number)
    except Exception as e:
        logger.error(f"Review failed: {e}")
    finally:
        try:
            vcs_adapter.close()
            llm_adapter.close()
        except Exception:
            pass

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest, background_tasks: BackgroundTasks):
    """
    Stateless conversational endpoint for HITL PR Review.
    Accepts a list of messages (full conversation history) and returns the updated history.
    """
    messages = [msg.model_dump() for msg in chat_request.messages]

    try:
        vcs_adapter, llm_adapter = get_adapters()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def trigger_review_callback(repo_name: str, pr_number: int):
        # Spawn the deep review job in FastAPI background tasks
        background_tasks.add_task(run_deep_review, repo_name, pr_number)

    try:
        # Build MCPServer and register github tools
        mcp_server = MCPServer()
        github_tools = GitHubTools(vcs_adapter=vcs_adapter, review_callback=trigger_review_callback)
        mcp_server.register_github_tools(github_tools)
        
        mcp_client = InternalMCPClient(mcp_server)
        
        chat_agent = ChatAgent(llm_adapter=llm_adapter, mcp_client=mcp_client)
        updated_messages = chat_agent.chat(messages)
        
        return JSONResponse({"messages": updated_messages})
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        vcs_adapter.close()
        llm_adapter.close()

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
