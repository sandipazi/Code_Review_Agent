from abc import ABC, abstractmethod

class BaseVCSAdapter(ABC):
    @abstractmethod
    def get_pull_request_diff(self, repo_name: str, pr_number: int) -> str:
        """Fetch the unified diff of the pull request."""
        pass

    @abstractmethod
    def post_review_comment(self, repo_name: str, pr_number: int, comment: str) -> None:
        """Post a general review comment on the PR."""
        pass

    @abstractmethod
    def post_inline_comment(self, repo_name: str, pr_number: int, commit_id: str, path: str, line: int, comment: str) -> None:
        """Post an inline comment on a specific line of code in a file."""
        pass
