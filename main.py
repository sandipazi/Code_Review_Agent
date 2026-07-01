from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import hmac
import hashlib
from config.settings import settings
from models.schemas import PullRequestEvent
from adapters.vcs.github import GitHubAdapter
from adapters.llm.openai import OpenAIAdapter
from core.agent import PRReviewAgent
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI PR Review Agent")

def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not settings.GITHUB_WEBHOOK_SECRET:
        # If no secret configured, accept the payload (use carefully)
        return True
    
    if not signature_header:
        return False

    # GitHub sends signature as sha256=...
    parts = signature_header.split("=")
    if len(parts) != 2 or parts[0] != "sha256":
        return False
        
    expected_mac = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_mac, parts[1])

def process_pull_request(event: PullRequestEvent):
    """
    Background task to process the pull request.
    This is where the custom agent loop will be invoked.
    """
    repo_name = event.repository.get("full_name", "unknown")
    pr_number = event.number
    logger.info(f"Starting review for PR #{pr_number} in {repo_name}")
    
    # Instantiate Adapters
    if not settings.GITHUB_TOKEN:
        logger.error("GitHub token not configured, cannot proceed.")
        return
    vcs_adapter = GitHubAdapter(token=settings.GITHUB_TOKEN)
    
    # LLM Adapter selection based on config
    if settings.LLM_PROVIDER.lower() == "openai":
        if not settings.OPENAI_API_KEY:
            logger.error("OpenAI API key not configured, cannot proceed.")
            return
        llm_adapter = OpenAIAdapter(api_key=settings.OPENAI_API_KEY)
    else:
        logger.error(f"Unsupported LLM provider: {settings.LLM_PROVIDER}")
        return
        
    try:
        agent = PRReviewAgent(vcs_adapter=vcs_adapter, llm_adapter=llm_adapter)
        agent.review_pr(repo_name, pr_number)
    except Exception as e:
        logger.error(f"Review failed: {e}")
    finally:
        vcs_adapter.close()
        llm_adapter.close()

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    # Verify GitHub signature
    signature_header = request.headers.get("x-hub-signature-256")
    body = await request.body()
    
    if not verify_signature(body, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # We only care about pull request events
    event_type = request.headers.get("x-github-event")
    if event_type == "pull_request":
        try:
            event = PullRequestEvent(**data)
        except Exception as e:
            logger.error(f"Failed to parse event: {e}")
            raise HTTPException(status_code=422, detail="Invalid event schema")

        if event.action in ["opened", "synchronize"]:
            background_tasks.add_task(process_pull_request, event)
            return JSONResponse({"status": "accepted", "message": "PR processing started in background"})
        else:
            return JSONResponse({"status": "ignored", "message": f"Action {event.action} ignored"})
    
    return JSONResponse({"status": "ignored", "message": f"Event {event_type} ignored"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
