from typing import Dict, Any, Callable, List, Optional
from adapters.vcs.base import BaseVCSAdapter
from adapters.llm.base import BaseLLMAdapter, LLMMessage
from core.mcp_client import InternalMCPClient
from config.settings import settings
import json
import logging

logger = logging.getLogger(__name__)


class PRReviewAgent:
    def __init__(
        self,
        vcs_adapter: BaseVCSAdapter,
        llm_adapter: BaseLLMAdapter,
        mcp_client: InternalMCPClient,
        rag_retriever=None,       # Optional[RAGRetriever] — avoids circular import
        github_ingester=None,     # Optional[GitHubIngester] — for auto-ingest post-review
        heartbeat: Optional[Callable[[], None]] = None,  # called each loop iteration; may raise to abort
    ):
        self.vcs = vcs_adapter
        self.llm = llm_adapter
        self.mcp = mcp_client
        self.rag = rag_retriever        # None → RAG disabled, fully backward-compatible
        self.ingester = github_ingester # None → auto-ingest disabled
        self.heartbeat = heartbeat
        self.max_loops = 5

    def review_pr(self, repo_name: str, pr_number: int):
        logger.info(f"Starting agent loop for PR #{pr_number} in {repo_name}")

        # 1. Fetch Diff
        try:
            diff_text = self.vcs.get_pull_request_diff(repo_name, pr_number)
        except Exception as e:
            logger.error(f"Failed to fetch PR diff: {e}")
            return

        if not diff_text:
            logger.info("Empty PR diff, skipping review.")
            return

        # 1b. Bound diff size so large PRs don't blow past provider token/size limits
        diff_truncated = False
        if len(diff_text) > settings.MAX_DIFF_CHARS:
            diff_text = diff_text[: settings.MAX_DIFF_CHARS]
            diff_truncated = True
            logger.warning(
                "PR #%d diff truncated to %d chars to stay within LLM payload limits",
                pr_number, settings.MAX_DIFF_CHARS,
            )

        # 2. Get available tools from MCP
        tools = self.mcp.get_tools()

        # 3. Retrieve RAG context (if retriever is configured)
        rag_context = ""
        if self.rag is not None:
            try:
                rag_context = self.rag.retrieve(diff_text, repo_name)
                if rag_context:
                    if len(rag_context) > settings.MAX_RAG_CONTEXT_CHARS:
                        rag_context = rag_context[: settings.MAX_RAG_CONTEXT_CHARS]
                    logger.info("RAG context retrieved (%d chars) for PR #%d", len(rag_context), pr_number)
                else:
                    logger.info("RAG returned no relevant context for PR #%d", pr_number)
            except Exception as exc:
                logger.warning("RAG retrieval failed (review will continue without it): %s", exc)

        # 4. Build System Prompt — inject RAG context if available
        system_prompt = (
            "You are an expert AI software engineer and code reviewer.\n"
            "Review the provided pull request diff. Look for bugs, anti-patterns, security issues, "
            "and readability problems.\n"
            "You can use tools to read the full context of files if the diff isn't enough.\n"
            "You can also use the `search_knowledge_base` tool to find relevant past reviews, "
            "project conventions, or similar patterns from the codebase history.\n"
        )

        if rag_context:
            system_prompt += (
                "\nThe following context was automatically retrieved from the project knowledge base "
                "to help you give a more informed review. Use it to check for recurring patterns, "
                "enforce project conventions, and reference historical decisions:\n"
                + rag_context
                + "\n"
            )

        system_prompt += (
            "\nWhen you are done reviewing, reply with your final review in the following JSON format ONLY:\n"
            "{\n"
            "  \"general_comment\": \"Overall feedback on the PR\",\n"
            "  \"inline_comments\": [\n"
            "    {\"path\": \"file/path.py\", \"line\": 42, \"comment\": \"Your review comment here\"}\n"
            "  ]\n"
            "}\n"
        )

        diff_truncation_note = (
            f"\n\n[Note: diff truncated to {settings.MAX_DIFF_CHARS} characters to stay within "
            "LLM provider limits; this review may not cover the full PR.]"
            if diff_truncated else ""
        )
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(
                role="user",
                content=f"Please review the following diff:\n\n```diff\n{diff_text}\n```{diff_truncation_note}",
            ),
        ]

        # 5. Agent ReAct Loop
        final_review_data: Optional[dict] = None
        for i in range(self.max_loops):
            if self.heartbeat is not None:
                self.heartbeat()  # e.g. Temporal's activity.heartbeat — raises if cancelled

            logger.info(f"Agent loop iteration {i+1}/{self.max_loops}")
            response = self.llm.generate(messages, tools=tools)

            if response.tool_calls:
                messages.append(response)  # Add assistant's tool call message

                # Execute tools sequentially
                for tool_call in response.tool_calls:
                    tool_id = tool_call.get("id")
                    func_call = tool_call.get("function", {})
                    name = func_call.get("name")
                    try:
                        args = json.loads(func_call.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    logger.info(f"LLM called tool: {name} with {args}")
                    tool_result = self.mcp.call_tool(name, args)

                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=str(tool_result),
                            tool_call_id=tool_id,
                            name=name,
                        )
                    )
            else:
                # No tool calls — this is the final response
                final_content = response.content
                final_review_data = self._process_final_review(repo_name, pr_number, final_content)
                break
        else:
            logger.warning("Max loops reached, agent did not finish tool execution properly.")
            final_review_data = self._process_final_review(repo_name, pr_number, messages[-1].content)

        # 6. Auto-ingest the completed review back into the vector store
        if final_review_data and self.ingester is not None:
            try:
                self.ingester.ingest_review_result(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    general_comment=final_review_data.get("general_comment", ""),
                    inline_comments=final_review_data.get("inline_comments", []),
                    pr_outcome="reviewed",
                )
            except Exception as exc:
                logger.warning("Auto-ingest of review failed (non-critical): %s", exc)

    def _process_final_review(self, repo_name: str, pr_number: int, content: str) -> Optional[dict]:
        """
        Parse the LLM's JSON review output, post it to GitHub, and return
        the parsed dict so the caller can auto-ingest it. Returns None on failure.
        """
        try:
            clean_content = content.strip()
            if clean_content.startswith("```json"):
                clean_content = clean_content[7:]
            if clean_content.endswith("```"):
                clean_content = clean_content[:-3]

            review_data = json.loads(clean_content.strip())

            general = review_data.get("general_comment")
            if general:
                self.vcs.post_review_comment(repo_name, pr_number, general)

            inline_comments = review_data.get("inline_comments", [])
            for c in inline_comments:
                # NOTE: Real inline comments on GitHub require complex diff parsing
                # to map the PR lines to commit side (RIGHT vs LEFT) and position.
                # For this proof of concept, we just log it or post as general.
                path = c.get("path")
                line = c.get("line")
                comment = c.get("comment")
                logger.info(f"Prepared inline comment for {path}:{line} - {comment}")

            return review_data

        except json.JSONDecodeError:
            logger.error(f"Failed to parse LLM JSON output. Raw output: {content}")
            # Fallback: post the raw text as a general comment
            self.vcs.post_review_comment(repo_name, pr_number, content)
            return None
