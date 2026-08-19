import os
import sys
import logging
from dotenv import load_dotenv

# Ensure the root directory is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import settings
from main import get_adapters, get_rag_components
from rag.ingestion.doc_ingester import DocIngester

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest.py <repo_name> [branch] [path]")
        sys.exit(1)

    repo_name = sys.argv[1]
    branch = sys.argv[2] if len(sys.argv) > 2 else "main"
    path = sys.argv[3] if len(sys.argv) > 3 else ""

    load_dotenv()
    if not settings.RAG_ENABLED:
        logger.error("RAG_ENABLED is false in settings. Cannot ingest.")
        sys.exit(1)

    # Setup adapters
    try:
        vcs_adapter, _ = get_adapters()
    except Exception as e:
        logger.error(f"Failed to initialize adapters: {e}")
        sys.exit(1)

    # Setup RAG components
    retriever, github_ingester = get_rag_components(vcs_adapter)
    if not github_ingester or not retriever:
        logger.error("Failed to initialize RAG components.")
        sys.exit(1)
        
    doc_ingester = DocIngester(
        embedder=retriever._embedder,
        vector_store=retriever._store
    )

    logger.info(f"Starting ingestion for {repo_name}...")
    
    total_chunks = 0
    
    # 1. Ingest closed PRs (history)
    logger.info(f"--- Ingesting up to {settings.RAG_INGEST_CLOSED_PRS} closed PRs ---")
    chunks = github_ingester.ingest_closed_prs(repo_name, max_prs=settings.RAG_INGEST_CLOSED_PRS)
    total_chunks += chunks
    
    # 2. Ingest source code files
    logger.info(f"--- Ingesting source files from {branch} ---")
    chunks = github_ingester.ingest_source_files(repo_name, branch=branch, path=path)
    total_chunks += chunks
    
    # 3. Ingest local docs (if running from the same directory as the repo, optional)
    # This checks if the repo exists locally to ingest things like README.md directly
    local_repo_dir = os.path.join(os.getcwd(), repo_name.split("/")[-1])
    if os.path.isdir(local_repo_dir):
        logger.info(f"--- Ingesting local docs from {local_repo_dir} ---")
        chunks = doc_ingester.ingest_directory(local_repo_dir, repo_name)
        total_chunks += chunks
    else:
        logger.info(f"Local directory {local_repo_dir} not found, skipping local doc ingestion.")

    logger.info(f"Ingestion complete! Total chunks stored: {total_chunks}")
    
    # Close connections
    vcs_adapter.close()

if __name__ == "__main__":
    main()
