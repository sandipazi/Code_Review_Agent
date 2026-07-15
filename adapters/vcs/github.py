import httpx
from typing import List, Dict, Any
from .base import BaseVCSAdapter
import logging

logger = logging.getLogger(__name__)

class GitHubAdapter(BaseVCSAdapter):
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.client = httpx.Client(headers=self.headers, base_url=self.base_url)

    def get_pull_request_diff(self, repo_name: str, pr_number: int) -> str:
        # To get the diff, we need a special Accept header
        diff_headers = self.headers.copy()
        diff_headers["Accept"] = "application/vnd.github.v3.diff"
        
        response = self.client.get(
            f"/repos/{repo_name}/pulls/{pr_number}",
            headers=diff_headers
        )
        response.raise_for_status()
        return response.text

    def list_pull_requests(self, repo_name: str, state: str = "open") -> List[Dict[str, Any]]:
        response = self.client.get(
            f"/repos/{repo_name}/pulls",
            params={"state": state}
        )
        response.raise_for_status()
        return response.json()

    def get_pull_request_metadata(self, repo_name: str, pr_number: int) -> Dict[str, Any]:
        response = self.client.get(
            f"/repos/{repo_name}/pulls/{pr_number}"
        )
        response.raise_for_status()
        return response.json()

    def post_review_comment(self, repo_name: str, pr_number: int, comment: str) -> None:
        response = self.client.post(
            f"/repos/{repo_name}/issues/{pr_number}/comments",
            json={"body": comment}
        )
        response.raise_for_status()
        logger.info(f"Posted review comment to PR #{pr_number}")

    def post_inline_comment(self, repo_name: str, pr_number: int, commit_id: str, path: str, line: int, comment: str) -> None:
        response = self.client.post(
            f"/repos/{repo_name}/pulls/{pr_number}/comments",
            json={
                "body": comment,
                "commit_id": commit_id,
                "path": path,
                "line": line
            }
        )
        response.raise_for_status()
        logger.info(f"Posted inline comment to {path}:{line} on PR #{pr_number}")

    def close(self):
        self.client.close()
