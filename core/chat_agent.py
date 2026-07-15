from typing import List, Dict, Any
from adapters.llm.base import BaseLLMAdapter, LLMMessage
from core.mcp_client import InternalMCPClient
import json
import logging

logger = logging.getLogger(__name__)

class ChatAgent:
    def __init__(self, llm_adapter: BaseLLMAdapter, mcp_client: InternalMCPClient):
        self.llm = llm_adapter
        self.mcp = mcp_client
        self.max_loops = 5

    def chat(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Takes a conversation history (list of dicts), 
        injects system prompt, and processes the chat.
        Returns the updated conversation history.
        """
        llm_messages = []
        
        system_prompt = (
            "You are an AI assistant designed to help developers manage and review Pull Requests.\n"
            "You can list open pull requests in a repository and trigger deep code reviews for them.\n"
            "If the user asks you to review a PR, use the trigger_review tool.\n"
            "If the user asks what PRs are available, use the list_prs tool.\n"
        )
        
        # Ensure system prompt is first
        llm_messages.append(LLMMessage(role="system", content=system_prompt))
        
        for msg in messages:
            llm_messages.append(LLMMessage(
                role=msg.get("role"),
                content=msg.get("content", ""),
                name=msg.get("name"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id")
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
                            name=name
                        )
                    )
            else:
                llm_messages.append(response)
                break
        else:
            logger.warning("Max loops reached for Chat Agent.")

        return [m.to_dict() for m in llm_messages[1:]]
