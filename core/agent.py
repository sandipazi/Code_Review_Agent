from typing import Dict, Any, List
from adapters.vcs.base import BaseVCSAdapter
from adapters.llm.base import BaseLLMAdapter, LLMMessage
from core.mcp_client import InternalMCPClient
import json
import logging

logger = logging.getLogger(__name__)

class PRReviewAgent:
    def __init__(self, vcs_adapter: BaseVCSAdapter, llm_adapter: BaseLLMAdapter, mcp_client: InternalMCPClient):
        self.vcs = vcs_adapter
        self.llm = llm_adapter
        self.mcp = mcp_client
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

        # 2. Get available tools from MCP
        tools = self.mcp.get_tools()

        # 3. Setup Initial Prompt
        system_prompt = (
            "You are an expert AI software engineer and code reviewer.\n"
            "Review the provided pull request diff. Look for bugs, anti-patterns, and readability issues.\n"
            "You can use tools to read the full context of files if the diff isn't enough.\n"
            "When you are done reviewing, reply with your final review in the following JSON format ONLY:\n"
            "{\n"
            "  \"general_comment\": \"Overall feedback on the PR\",\n"
            "  \"inline_comments\": [\n"
            "    {\"path\": \"file/path.py\", \"line\": 42, \"comment\": \"Your review comment here\"}\n"
            "  ]\n"
            "}\n"
        )
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=f"Please review the following diff:\n\n```diff\n{diff_text}\n```")
        ]

        # 4. Agent Loop
        for i in range(self.max_loops):
            logger.info(f"Agent loop iteration {i+1}/{self.max_loops}")
            response = self.llm.generate(messages, tools=tools)
            
            if response.tool_calls:
                messages.append(response) # Add assistant's tool call message
                
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
                    
                    # Call MCP Client
                    tool_result = self.mcp.call_tool(name, args)
                    
                    # Append result as a tool role message
                    messages.append(
                        LLMMessage(
                            role="tool", 
                            content=str(tool_result),
                            tool_call_id=tool_id,
                            name=name
                        )
                    )
            else:
                # No tool calls, this is the final response
                final_content = response.content
                self._process_final_review(repo_name, pr_number, final_content)
                break
        else:
            logger.warning("Max loops reached, agent did not finish tool execution properly.")
            self._process_final_review(repo_name, pr_number, messages[-1].content)
            
    def _process_final_review(self, repo_name: str, pr_number: int, content: str):
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
                # For this proof of concept, we just log it or you could post it as general.
                path = c.get("path")
                line = c.get("line")
                comment = c.get("comment")
                logger.info(f"Prepared inline comment for {path}:{line} - {comment}")
                
        except json.JSONDecodeError:
            logger.error(f"Failed to parse LLM JSON output. Raw output: {content}")
            # Fallback: Just post the raw text as a general comment
            self.vcs.post_review_comment(repo_name, pr_number, content)
