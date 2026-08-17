"""
RAG MCP Tool — exposes `search_knowledge_base` to the LLM.

This allows the LLM to proactively pull relevant context from the
knowledge base during its ReAct loop — not just receive it passively
via the system prompt at the start of a review.
"""
import logging
from rag.retriever import RAGRetriever

logger = logging.getLogger(__name__)


class RAGTools:
    """Wraps RAGRetriever as MCP-registered callable tools."""

    def __init__(self, retriever: RAGRetriever, default_repo: str = ""):
        self._retriever = retriever
        self._default_repo = default_repo

    def search_knowledge_base(self, query: str, repo_name: str = "") -> str:
        """
        Search the internal knowledge base for code patterns, past review
        comments, project guidelines, or historical PR context relevant to
        the given query.

        Use this tool when you need:
          - Historical review comments about a specific file or pattern
          - Project coding conventions and guidelines
          - Examples of how similar code was handled in past PRs
          - Architectural context for the code being reviewed

        Args:
            query: A natural language or code search query.
            repo_name: The full repository name (e.g. 'owner/repo').
                       Defaults to the currently reviewed repository.

        Returns:
            A formatted context block with the most relevant knowledge
            snippets, or a message indicating no results were found.
        """
        repo = repo_name or self._default_repo
        logger.info("RAG tool called: query='%s' repo='%s'", query[:80], repo)
        result = self._retriever.retrieve_for_query(query, repo)
        return result
