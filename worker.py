"""Temporal Worker process for durable PR-review execution.

Run this alongside a Temporal server (`temporal server start-dev` for local dev)
and the FastAPI app (main.py):

    temporal server start-dev
    python worker.py
    python main.py
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from config.settings import settings
from core.temporal_workflows import PRReviewWorkflow, run_review_activity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
    logger.info(
        f"Connected to Temporal at {settings.TEMPORAL_ADDRESS} "
        f"(namespace={settings.TEMPORAL_NAMESPACE}); polling task queue "
        f"'{settings.TEMPORAL_TASK_QUEUE}'"
    )
    worker = Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[PRReviewWorkflow],
        activities=[run_review_activity],
        # run_review_activity is sync/blocking (httpx sync clients throughout), so it needs
        # a thread-pool executor rather than running directly on the asyncio event loop.
        activity_executor=ThreadPoolExecutor(max_workers=20),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
