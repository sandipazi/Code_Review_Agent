from adapters.vcs.base import BaseVCSAdapter
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

class GitHubTools:
    def __init__(self, vcs_adapter: BaseVCSAdapter, review_callback: Callable[[str, int], None]):
        self.vcs = vcs_adapter
        self.review_callback = review_callback

    def list_prs(self, repo_name: str, state: str = "open") -> str:
        """List pull requests for a specific repository.
        Returns a JSON string of PRs containing their numbers, titles, and states.
        """
        try:
            prs = self.vcs.list_pull_requests(repo_name, state)
            summary = [{"number": pr.get("number"), "title": pr.get("title"), "state": pr.get("state")} for pr in prs]
            return json.dumps(summary)
        except Exception as e:
            logger.error(f"Error listing PRs: {e}")
            return f"Error listing PRs: {e}"

    def trigger_review(self, repo_name: str, pr_number: int) -> str:
        """Trigger a deep code review for a specific pull request."""
        try:
            self.review_callback(repo_name, pr_number)
            return f"Successfully triggered background review for PR #{pr_number} in {repo_name}."
        except Exception as e:
            logger.error(f"Error triggering review: {e}")
            return f"Failed to trigger review: {e}"
