from typing import List, Dict, Any, Optional
from adapters.llm.base import BaseLLMAdapter, LLMMessage
from core.mcp_client import InternalMCPClient
import re
import json
import logging

logger = logging.getLogger(__name__)


class ChatAgent:
    def __init__(
        self,
        llm_adapter: BaseLLMAdapter,
        mcp_client: InternalMCPClient,
        rag_retriever=None,      # Optional[RAGRetriever] — enriches chat with KB context
        default_repo: str = "",  # Used as RAG filter when no repo is explicit in the query
    ):
        self.llm = llm_adapter
        self.mcp = mcp_client
        self.rag = rag_retriever
        self.default_repo = default_repo
        self.max_loops = 5

    def chat(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Takes a conversation history (list of dicts),
        injects system prompt (with optional RAG context), and processes the chat.
        Returns the updated conversation history.
        """
        llm_messages = []

        # Build RAG context if retriever is configured and the latest user message
        # seems to be about a specific PR, file, or code pattern
        rag_context = self._maybe_retrieve_rag_context(messages)

        system_prompt = (
            "You are an AI assistant designed to help developers manage and review Pull Requests.\n"
            "You can list open pull requests in a repository and trigger deep code reviews for them.\n"
            "If the user asks you to review a PR, use the trigger_review tool.\n"
            "If the user asks what PRs are available, use the list_prs tool.\n"
            "You can also use the `search_knowledge_base` tool to find relevant past reviews, "
            "project conventions, or historical patterns before answering questions about the codebase.\n"
        )

        if rag_context:
            system_prompt += (
                "\nThe following context was retrieved from the project knowledge base "
                "based on the current conversation:\n"
                + rag_context
                + "\n"
            )

        # Ensure system prompt is first
        llm_messages.append(LLMMessage(role="system", content=system_prompt))

        for msg in messages:
            llm_messages.append(LLMMessage(
                role=msg.get("role"),
                content=msg.get("content", ""),
                name=msg.get("name"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            ))

        tools = self.mcp.get_tools()

        for i in range(self.max_loops):
            logger.info(f"Chat Agent loop iteration {i+1}/{self.max_loops}")
            response = self.llm.generate(llm_messages, tools=tools)

            if response.tool_calls:
                llm_messages.append(response)

                for tool_call in response.tool_calls:
                    tool_id = tool_call.get("id")
                    func_call = tool_call.get("function", {})
                    name = func_call.get("name")
                    try:
                        args = json.loads(func_call.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    logger.info(f"Chat LLM called tool: {name} with {args}")
                    tool_result = self.mcp.call_tool(name, args)

                    llm_messages.append(
                        LLMMessage(
                            role="tool",
                            content=str(tool_result),
                            tool_call_id=tool_id,
                            name=name,
                        )
                    )
            else:
                llm_messages.append(response)
                break
        else:
            logger.warning("Max loops reached for Chat Agent.")

        return [m.to_dict() for m in llm_messages[1:]]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_retrieve_rag_context(self, messages: List[Dict[str, Any]]) -> str:
        """
        Retrieve RAG context only when:
        - A RAGRetriever is configured, AND
        - The last user message references a PR number, file path, or code keywords

        Returns an empty string if no retrieval is needed or RAG is disabled.
        """
        if self.rag is None:
            return ""

        # Get the last user message
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        if not last_user_msg:
            return ""

        # Heuristic: only retrieve if the message references code-related topics
        code_keywords = re.compile(
            r"\bPR\s*#?\d+\b|pull.?request|\bfix\b|\bbug\b|\breview\b|"
            r"\bsecurity\b|\bpattern\b|\bconvention\b|\bfile\b|\bclass\b|\bfunction\b|"
            r"\.py\b|\.js\b|\.ts\b|\.go\b",
            re.IGNORECASE,
        )
        if not code_keywords.search(last_user_msg):
            return ""

        # Extract repo from message or fall back to default
        repo_match = re.search(r"([\w\-]+/[\w\-]+)", last_user_msg)
        repo = repo_match.group(1) if repo_match else self.default_repo

        try:
            context = self.rag.retrieve_for_query(last_user_msg, repo)
            if context:
                logger.info("ChatAgent: RAG context retrieved (%d chars)", len(context))
            return context
        except Exception as exc:
            logger.warning("ChatAgent: RAG retrieval failed (non-critical): %s", exc)
            return ""

