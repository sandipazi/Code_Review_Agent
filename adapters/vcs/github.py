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

    def get_review_comments(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Fetch all inline review comments for a pull request."""
        response = self.client.get(
            f"/repos/{repo_name}/pulls/{pr_number}/comments"
        )
        response.raise_for_status()
        return response.json()

    def list_files(self, repo_name: str, branch: str = "main", path: str = "") -> List[Dict[str, Any]]:
        """
        List files in a repository at a specific branch using the Git Trees API.
        Returns a flat list of file dicts with 'path' and 'type' keys.
        For large repos, the tree is returned recursively (up to GitHub's limit).
        """
        url = f"/repos/{repo_name}/git/trees/{branch}"
        response = self.client.get(url, params={"recursive": "1"})
        response.raise_for_status()
        data = response.json()
        # Filter to blobs (files) only, optionally under `path` prefix
        items = [
            {"path": item["path"], "type": "file", "size": item.get("size", 0)}
            for item in data.get("tree", [])
            if item["type"] == "blob" and item["path"].startswith(path)
        ]
        return items

    def read_file(self, repo_name: str, file_path: str, branch: str = "main") -> str:
        """
        Fetch the raw content of a file from a GitHub repository.
        Uses the raw content endpoint for efficiency.
        """
        response = self.client.get(
            f"/repos/{repo_name}/contents/{file_path}",
            params={"ref": branch},
            headers={**self.headers, "Accept": "application/vnd.github.v3.raw"},
        )
        response.raise_for_status()
        return response.text
