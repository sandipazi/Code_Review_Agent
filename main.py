# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import asyncio
import hmac
import hashlib
from temporalio.common import WorkflowIDReusePolicy
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
import concurrent.futures
from config.settings import settings
from models.schemas import PullRequestEvent, ChatRequest, ChatResponse, CancelReviewRequest
from core.adapter_factory import get_vcs_adapter, get_adapters, get_rag_components
from core.temporal_client import get_temporal_client
from core.temporal_workflows import PRReviewWorkflow
from core.chat_agent import ChatAgent
from core.mcp_client import InternalMCPClient
from mcp_server.server import MCPServer
from mcp_server.http_transport import router as mcp_router
from mcp_server.tools.github_tools import GitHubTools
from mcp_server.tools.rag_tools import RAGTools
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

def _review_workflow_id(repo_name: str, pr_number: int) -> str:
    return f"pr-review-{repo_name}-{pr_number}"

async def _start_review_workflow(repo_name: str, pr_number: int, github_token: Optional[str]):
    """Starts the durable review workflow. Raises on failure (Temporal unreachable,
    start_workflow error) rather than swallowing — callers need to know the review
    didn't actually start rather than being told it was "successfully triggered"."""
    client = await get_temporal_client()
    try:
        await client.start_workflow(
            PRReviewWorkflow.run,
            args=[repo_name, pr_number, github_token],
            id=_review_workflow_id(repo_name, pr_number),
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        logger.info(f"Review for PR #{pr_number} in {repo_name} is already running.")

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest, request: Request):
    """
    Stateless conversational endpoint for HITL PR Review.
    Accepts a list of messages (full conversation history) and returns the updated history.
    Optionally accepts an X-GitHub-Token header to use a caller-supplied PAT instead of
    the server's configured GITHUB_TOKEN.
    """
    messages = [msg.model_dump() for msg in chat_request.messages]
    github_token = request.headers.get("X-GitHub-Token")
    loop = asyncio.get_running_loop()

    try:
        vcs_adapter, llm_adapter = get_adapters(github_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    def trigger_review_callback(repo_name: str, pr_number: int):
        # Starts a durable Temporal workflow rather than a thread/BackgroundTask — survives
        # process restarts, and multiple reviews triggered in one chat turn run independently
        # instead of queuing behind a shared sequential-await list.
        #
        # chat_agent.chat(...) below runs on a worker thread (via asyncio.to_thread), so
        # this callback also runs on that worker thread — asyncio.create_task() would raise
        # "no running event loop" here. run_coroutine_threadsafe schedules the actual
        # Temporal gRPC call back onto the main event loop thread where it belongs, while
        # blocking only this worker thread (not the loop) while waiting for it to start.
        try:
            asyncio.run_coroutine_threadsafe(
                _start_review_workflow(repo_name, pr_number, github_token), loop
            ).result(timeout=15)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(
                "Timed out starting the review workflow — the Temporal server may be "
                "unreachable or overloaded."
            )

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
        # chat_agent.chat() does blocking sync HTTP calls (LLM + tool calls) — running it
        # directly on the event loop would freeze the whole app (including /reviews/status
        # polling) for the duration of every chat turn, sometimes minutes.
        updated_messages = await asyncio.to_thread(chat_agent.chat, messages)

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

_TEMPORAL_STATUS_MAP = {
    WorkflowExecutionStatus.RUNNING: "pending",
    WorkflowExecutionStatus.COMPLETED: "completed",
    WorkflowExecutionStatus.FAILED: "failed",
    WorkflowExecutionStatus.TIMED_OUT: "failed",
    WorkflowExecutionStatus.CANCELED: "cancelled",
    WorkflowExecutionStatus.TERMINATED: "cancelled",
}

def _unwrap_failure_message(exc: BaseException) -> str:
    """Walk a Temporal WorkflowFailureError's cause chain (WorkflowFailureError ->
    ActivityError -> ApplicationError) to the innermost message — this is where an
    actionable error like the GitHub-permission message set in adapters/vcs/github.py
    actually lives, rather than a generic "Workflow execution failed"."""
    current = exc
    while getattr(current, "cause", None) is not None:
        current = current.cause
    return getattr(current, "message", str(current))


@app.get("/reviews/status")
async def review_status(repo_name: str, pr_number: int):
    """Check the status of a background PR review triggered via /chat's trigger_review tool.

    Distinguishes three failure modes that all used to collapse into an unhelpful
    "unknown": the Temporal server being unreachable, a review that was never
    triggered (or whose trigger failed before the workflow started), and any other
    error fetching status — vs. a genuinely running/completed/failed review.
    """
    try:
        client = await get_temporal_client()
    except Exception as e:
        logger.warning(f"Could not reach Temporal server for PR #{pr_number} in {repo_name}: {e}")
        return JSONResponse({"status": "unreachable", "error": "Cannot reach the Temporal server — the review backend may be down."})

    handle = client.get_workflow_handle(_review_workflow_id(repo_name, pr_number))
    try:
        desc = await handle.describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return JSONResponse({
                "status": "not_found",
                "error": "No review found for this PR — it may never have been triggered, or the trigger failed before the workflow started.",
            })
        logger.warning(f"Error fetching review status for PR #{pr_number} in {repo_name}: {e}")
        return JSONResponse({"status": "error", "error": str(e)})
    except Exception as e:
        logger.warning(f"Error fetching review status for PR #{pr_number} in {repo_name}: {e}")
        return JSONResponse({"status": "error", "error": str(e)})

    status = _TEMPORAL_STATUS_MAP.get(desc.status, "unknown")
    error_message = None
    if status == "failed":
        try:
            await handle.result()
        except WorkflowFailureError as wfe:
            error_message = _unwrap_failure_message(wfe)
        except Exception as e:
            error_message = str(e)
        else:
            error_message = str(desc.status)

    return JSONResponse({
        "status": status,
        "error": error_message,
        "started_at": desc.start_time.isoformat() if desc.start_time else None,
        "finished_at": desc.close_time.isoformat() if desc.close_time else None,
    })

@app.post("/reviews/cancel")
async def cancel_review(payload: CancelReviewRequest):
    """Cooperatively cancel a running PR review. Takes effect at the next ReAct-loop
    checkpoint, not instantly — the job may still finish if it was already about to."""
    try:
        client = await get_temporal_client()
    except Exception as e:
        logger.error(f"Could not reach Temporal server: {e}")
        raise HTTPException(status_code=503, detail="Temporal server unreachable.")
    try:
        await client.get_workflow_handle(_review_workflow_id(payload.repo_name, payload.pr_number)).cancel()
    except Exception:
        raise HTTPException(status_code=404, detail="No review job found for that repo/PR.")
    return JSONResponse({"status": "cancelling"})

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
