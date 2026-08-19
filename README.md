# AI Pull Request Review Agent

An intelligent, self-hosted AI agent for automated Pull Request reviews leveraging a **Conversational Human-in-the-Loop (HITL)** architecture. 

Users can chat with the agent to list PRs, get context, and explicitly authorize deep code reviews. This project relies entirely on pure Python, avoiding heavy external agentic frameworks (like LangChain or AutoGen) to maintain strict control over the LLM execution pipeline, tool calling, and context window management. 

## Features
- **Conversational API**: A stateless `/chat` endpoint allows you to talk to the AI (like ChatGPT or Claude) to manage and review PRs.
- **Two-Tier Agent Loop**: A foreground Chat Agent handles conversation and tools, while a background Review Agent handles massive diff parsing and GitHub commenting.
- **Durable Execution (Temporal)**: PR reviews run as Temporal workflows — they survive process restarts, support native cancellation, and run truly in parallel (no shared queue) regardless of how many are triggered at once.
- **Adapter-based Architecture**: Highly decoupled design allowing seamless swapping of VCS providers (GitHub) and LLMs (OpenAI, GitHub Models, Groq, OpenRouter, with automatic fallback between them).
- **Transport-Agnostic MCP Server**: A standalone Model Context Protocol (MCP) server that supports both stateless HTTP and standard `stdio` transports, allowing direct integration with IDEs (Cursor, VS Code) and desktop agents (Claude Desktop).

## Project Structure

```text
Code_Review_Agent/
├── main.py                  # Entry point: FastAPI app for webhooks & chat
├── worker.py                # Entry point: Temporal Worker (runs PR review workflows)
├── mcp_runner.py            # Entry point: MCP stdio transport for IDEs
├── config/                  # Configuration and raw LLM Prompts
├── core/                    # Agent loop, MCP Client, adapter factory, Temporal client/workflows
├── adapters/                # Integrations for LLMs and VCS (GitHub)
├── mcp_server/              # MCP Server, Tools, HTTP & stdio Transports
├── models/                  # Pydantic schemas
├── docs/client_configs/     # Config snippets for Cursor, Claude Desktop, etc.
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
   - See [GitHub Token Permissions](#github-token-permissions) below for the exact scopes your PAT needs — an under-scoped token is the most common reason a review silently fails.
5. **Run Temporal (durable PR review execution)**

   PR reviews run as Temporal workflows, so a Temporal server and a Worker must be running before you trigger a review. Install the [Temporal CLI](https://docs.temporal.io/cli) if you don't have it, then in two separate terminals:
   ```bash
   # Terminal 1 — local Temporal server (in-memory, includes a Web UI at http://localhost:8233)
   temporal server start-dev

   # Terminal 2 — Worker process that executes PR review workflows/activities
   python worker.py
   ```
   *`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and `TEMPORAL_TASK_QUEUE` in `.env` control where these connect — defaults match `temporal server start-dev` out of the box.*
6. **Run the API Server (Webhooks & HTTP MCP)**
   ```bash
   uvicorn main:app --reload
   ```
   *Note: For local testing, use a tunneling service like [ngrok](https://ngrok.com/) to expose your local port to GitHub webhooks.*

7. **Run the MCP Server (stdio)**
   To connect the MCP server directly to an IDE like Cursor or Claude Desktop, use the stdio runner:
   ```bash
   python mcp_runner.py --github-token YOUR_GITHUB_PAT
   ```
   *Check `docs/client_configs/` for integration examples.*

## GitHub Token Permissions

The agent needs a GitHub Personal Access Token (PAT) — either set as `GITHUB_TOKEN` in
`.env`, or supplied per-request via the `X-GitHub-Token` header / the frontend's connect
screen. **The token must have write access to pull requests/issues on the target
repositories**, not just read access — the agent fetches diffs (read) but also posts the
review back as a comment (write). A read-only token will let a review run to completion
and then fail silently at the very last step with a `403 Forbidden` when posting the
comment.

Create the token at [github.com/settings/tokens](https://github.com/settings/tokens) with
one of:

- **Fine-grained PAT** (recommended — scoped to specific repos):
  - Repository access: select the specific repositories the agent should review.
  - Repository permissions:
    - **Pull requests: Read and write**
    - **Issues: Read and write** (PR review comments are posted via the Issues comments API)
    - **Contents: Read-only** (needed to fetch diffs/files)
    - **Metadata: Read-only** (required by default for any fine-grained token)
- **Classic PAT** (simpler, but repo-wide):
  - Scope: **`repo`** (full control of private repositories — includes PR/issue read+write). If you only need public repos, `public_repo` is sufficient instead of the full `repo` scope.

Without these, `/reviews/status` will report the review as `failed` with an error message
naming the exact missing scope.

## Hugging Face Token (Optional — for local RAG embeddings)

By default (`RAG_EMBEDDER=local`), the RAG knowledge base embeds text locally using the
`all-MiniLM-L6-v2` model, which `worker.py` downloads from the Hugging Face Hub the first
time it's needed. Without a token, these requests are unauthenticated and can be slow or
rate-limited — slow enough, on a cold worker, to make an otherwise-successful PR review
look like it failed (see the `Warning: You are sending unauthenticated requests to the HF
Hub` line in the worker logs).

To avoid this, generate a free token and make it available to the worker process:

1. Create a Hugging Face account, then go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and click **New token**.
2. Give it any name (e.g. `code-review-agent`), set the role to **Read** (you're only downloading a public model, never uploading), and click **Create token**. Copy the generated token (starts with `hf_`).
3. Make it available to whichever process downloads the model:
   - **Recommended**: `pip install huggingface_hub[cli]` then run `huggingface-cli login` and paste the token. This caches it at `~/.cache/huggingface/token` and every process (worker, `scripts/ingest.py`, etc.) picks it up automatically — no further config needed.
   - **Alternative**: export it as a real shell environment variable before starting the worker, e.g. `export HF_TOKEN=hf_xxx` (or add that line to your shell profile). Note that adding `HF_TOKEN=...` to this project's `.env` file alone is **not** enough for `main.py`/`worker.py` — those load `.env` only into typed config values via `pydantic-settings` (`config/settings.py`), not into the process environment, so `huggingface_hub` won't see it there. (`scripts/ingest.py` is the one exception — it calls `load_dotenv()`, so `.env` does work for that script specifically.)

This step is optional — reviews work without it — but it removes one of the slowest,
least predictable parts of a cold-start review.

## Contributing
Please refer to `Architecture.md` to understand the internal component structure before making significant changes to the agent loop or MCP pipeline.
