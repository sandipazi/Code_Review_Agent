"""Temporal Workflow + Activity for durable PR-review execution.

Only this module (and worker.py) import temporalio — core/agent.py stays
framework-agnostic, driven via a generic `heartbeat` callback instead.
"""
from datetime import timedelta
from typing import Optional

from temporalio import workflow, activity
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


@activity.defn
def run_review_activity(repo_name: str, pr_number: int, github_token: Optional[str]) -> None:
    from adapters.vcs.base import VCSPermissionError
    from core.adapter_factory import get_adapters, get_rag_components
    from core.agent import PRReviewAgent
    from core.mcp_client import InternalMCPClient
    from mcp_server.server import MCPServer
    from mcp_server.tools.rag_tools import RAGTools

    vcs_adapter, llm_adapter = get_adapters(github_token)
    try:
        mcp_server = MCPServer()
        rag_retriever, github_ingester = get_rag_components(vcs_adapter)
        if rag_retriever:
            mcp_server.register_rag_tools(RAGTools(retriever=rag_retriever, default_repo=repo_name))

        agent = PRReviewAgent(
            vcs_adapter=vcs_adapter,
            llm_adapter=llm_adapter,
            mcp_client=InternalMCPClient(mcp_server),
            rag_retriever=rag_retriever,
            github_ingester=github_ingester,
            # activity.heartbeat raises temporalio.exceptions.CancelledError when Temporal
            # has requested cancellation — propagates naturally out of review_pr().
            heartbeat=activity.heartbeat,
        )
        try:
            agent.review_pr(repo_name, pr_number)
        except VCSPermissionError as e:
            # Tagged with a distinguishable type so /reviews/status can recognize this
            # specific failure and surface the scope-fix message to the frontend.
            raise ApplicationError(str(e), type="GitHubPermissionError", non_retryable=True) from e
    finally:
        vcs_adapter.close()
        llm_adapter.close()


@workflow.defn
class PRReviewWorkflow:
    @workflow.run
    async def run(self, repo_name: str, pr_number: int, github_token: Optional[str]) -> None:
        await workflow.execute_activity(
            run_review_activity,
            args=[repo_name, pr_number, github_token],
            start_to_close_timeout=timedelta(minutes=10),
            # core/agent.py heartbeats roughly every 10s while an LLM call is in
            # flight (see HEARTBEAT_POLL_INTERVAL_SECONDS) — this only needs to be a
            # small safety margin over that cadence, not over LLM call latency itself,
            # since the in-agent wrapper is what decouples the two.
            heartbeat_timeout=timedelta(seconds=45),
            # Deliberately no automatic retry: _process_final_review posts a comment to
            # GitHub as a non-idempotent side effect, so retrying after a partial failure
            # could double-post. Enabling safe retries would need idempotent posting first.
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
