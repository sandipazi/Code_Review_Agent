from typing import Callable, Optional
import logging

from mcp_server.server import MCPServer
from adapters.vcs.github import GitHubAdapter
from mcp_server.tools.github_tools import GitHubTools

logger = logging.getLogger(__name__)


def _noop_review_callback(repo_name: str, pr_number: int) -> None:
    """
    Default no-op callback for MCP transport clients.
    MCP-only clients don't use FastAPI BackgroundTasks, so triggering
    a review just logs the intent without starting a background job.
    """
    logger.info(
        f"trigger_review called for PR #{pr_number} in '{repo_name}' via MCP transport. "
        "No background task runner registered — connect the /chat endpoint for full HITL flow."
    )


def build_mcp_server(
    github_token: str,
    review_callback: Optional[Callable[[str, int], None]] = None,
) -> MCPServer:
    """
    Build a fresh, stateless MCPServer with the caller-supplied GitHub token.

    This factory is called once per MCP request (HTTP transport) or once per
    stdio session (stdio transport). No credential state is stored globally.

    Args:
        github_token: GitHub PAT supplied by the connecting MCP client.
        review_callback: Optional callable to trigger background PR reviews.
                         Defaults to a logging no-op for transport-only clients.

    Returns:
        Fully configured MCPServer exposing: read_file, list_prs, trigger_review.
    """
    vcs_adapter = GitHubAdapter(token=github_token)
    callback = review_callback or _noop_review_callback
    github_tools = GitHubTools(vcs_adapter=vcs_adapter, review_callback=callback)

    server = MCPServer()
    server.register_github_tools(github_tools)
    return server
