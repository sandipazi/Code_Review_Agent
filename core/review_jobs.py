import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class ReviewJobStore:
    """In-memory, thread-safe tracker for background PR-review jobs.

    Keyed by (repo_name, pr_number) rather than a generated job id — the frontend
    already knows both from the `trigger_review` tool-call arguments it renders, so
    no id needs to be minted or threaded back through the chat response.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _key(repo_name: str, pr_number: int) -> str:
        return f"{repo_name}#{pr_number}"

    def mark_pending(self, repo_name: str, pr_number: int) -> threading.Event:
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[self._key(repo_name, pr_number)] = {
                "status": "pending",
                "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "cancel_event": cancel_event,
            }
        return cancel_event

    def mark_completed(self, repo_name: str, pr_number: int) -> None:
        with self._lock:
            job = self._jobs.setdefault(self._key(repo_name, pr_number), {})
            job["status"] = "completed"
            job["error"] = None
            job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, repo_name: str, pr_number: int, error: str) -> None:
        with self._lock:
            job = self._jobs.setdefault(self._key(repo_name, pr_number), {})
            job["status"] = "failed"
            job["error"] = error
            job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def mark_cancelled(self, repo_name: str, pr_number: int) -> None:
        with self._lock:
            job = self._jobs.setdefault(self._key(repo_name, pr_number), {})
            job["status"] = "cancelled"
            job["error"] = None
            job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def request_cancel(self, repo_name: str, pr_number: int) -> bool:
        """Flag a running job for cooperative cancellation. Returns False if no job is tracked."""
        with self._lock:
            job = self._jobs.get(self._key(repo_name, pr_number))
            if job is None:
                return False
            job["status"] = "cancelling"
            job["cancel_event"].set()
            return True

    def get(self, repo_name: str, pr_number: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(self._key(repo_name, pr_number))
            if job is None:
                return None
            # Omit the internal Event object — callers only need the JSON-serializable status fields.
            return {k: v for k, v in job.items() if k != "cancel_event"}


review_job_store = ReviewJobStore()
