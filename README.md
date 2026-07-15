# AI Pull Request Review Agent

An intelligent, self-hosted AI agent for automated Pull Request reviews leveraging a **Conversational Human-in-the-Loop (HITL)** architecture. 

Users can chat with the agent to list PRs, get context, and explicitly authorize deep code reviews. This project relies entirely on pure Python, avoiding heavy external agentic frameworks (like LangChain or AutoGen) to maintain strict control over the LLM execution pipeline, tool calling, and context window management. 

## Features
- **Conversational API**: A stateless `/chat` endpoint allows you to talk to the AI (like ChatGPT or Claude) to manage and review PRs.
- **Two-Tier Agent Loop**: A foreground Chat Agent handles conversation and tools, while a background Review Agent handles massive diff parsing and GitHub commenting.
- **Adapter-based Architecture**: Highly decoupled design allowing seamless swapping of VCS providers (GitHub) and LLMs (OpenAI, GitHub Models).
- **Internal MCP Server**: Uses the Model Context Protocol (MCP) via an internal JSON-RPC pipeline to safely expose repository codebase tools to the LLM.

## Project Structure

```text
Code_Review_Agent/
├── main.py                  # Entry point: FastAPI app for webhooks
├── config/                  # Configuration and raw LLM Prompts
├── core/                    # Custom agent loop and MCP Client
├── adapters/                # Integrations for LLMs and VCS (GitHub)
├── mcp_server/              # Internal MCP JSON-RPC Server and Tools
├── models/                  # Pydantic schemas
├── utils/                   # Shared utilities (logging)
└── Architecture.md          # In-depth design documentation
```

## Setup & Local Development

1. **Clone the Repository**
2. **Create a Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
3. **Install Dependencies** (Ensure you populate the requirements.txt with `fastapi`, `httpx`, `pydantic`, `uvicorn`, etc.)
   ```bash
   pip install -r requirements.txt
   ```
4. **Environment Variables**
   - Copy `.env.example` to `.env` and configure your API keys (LLM, GitHub App / PAT).
5. **Run the Server**
   ```bash
   uvicorn main:app --reload
   ```
   *Note: For local testing, use a tunneling service like [ngrok](https://ngrok.com/) to expose your local port to GitHub webhooks.*

## Contributing
Please refer to `Architecture.md` to understand the internal component structure before making significant changes to the agent loop or MCP pipeline.
