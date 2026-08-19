# Improvement Opportunities

Concrete, code-grounded notes to guide future changes — organized by known gaps, architecture/scalability, testing/observability, and open-ended roadmap ideas. Each item names the file it comes from so it's easy to verify it's still current before acting on it.

## Known gaps & unfinished code

- **`mcp_server/tools/linter.py` is empty.** No linter tool is implemented or registered anywhere in `MCPServer`. If a linting tool was planned (the filename suggests it), it never got built.
- **Inline review comments aren't actually posted inline.** `PRReviewAgent._process_final_review()` (`core/agent.py:230-238`) only `logger.info`s each inline comment (`path:line - comment`) with a note that "real inline comments on GitHub require complex diff parsing to map PR lines to commit side (RIGHT/LEFT) and position." Only the `general_comment` is posted. Anyone relying on inline comments today is not getting them.
- **The webhook endpoint (`/webhook`) never triggers a review.** It verifies the signature, parses `pull_request` events, and just logs/returns `"Event recorded, but review not automatically started in HITL mode."` This is called out as intentional in `Architecture.md`, but it's worth double-checking it still matches the product intent before someone assumes webhook = auto-review.
- **HTTP MCP transport doesn't expose RAG tools.** `mcp_server/http_transport.py` calls `build_mcp_server(github_token=...)`, which only calls `register_github_tools` — `register_rag_tools` is never invoked over HTTP, unlike the in-process (`/chat`) and Temporal-activity paths. Remote MCP clients over HTTP can't call `search_knowledge_base` even when `RAG_ENABLED=true`.
- **`scripts/ingest.py` imports from `main`** (`from main import get_adapters, get_rag_components`) purely to reach two functions that actually live in `core/adapter_factory.py` and are simply re-exported by `main.py`'s module-level import. This drags the entire FastAPI app (and its `include_router`, CORS middleware, etc.) into a CLI script's import graph for no reason — importing `core.adapter_factory` directly would be equivalent and lighter.
- **`utils/logger.py` exists but is empty.** Every module currently calls `logging.getLogger(__name__)` directly with ad hoc `logging.basicConfig(level=...)` calls in `main.py` and `worker.py` — there's no shared formatter, no structured/JSON logging, and no correlation ID tying a chat request to the Temporal workflow it triggered.

## Architecture & scalability

- **Fixed-size activity executor.** `worker.py` hardcodes `ThreadPoolExecutor(max_workers=20)` for Temporal's `activity_executor`. There's no backpressure signal if 20 reviews are genuinely in flight at once (e.g. large diffs holding threads for the full LLM fallback chain) — the 21st just queues invisibly. Worth deciding whether this should scale with expected load or at least be config-driven like everything else in `config/settings.py`.
- **RAG context budget isn't tied to the model's actual context window.** `MAX_RAG_CONTEXT_CHARS` (default 4000) and `RAGRetriever`'s internal `token_budget` (hardcoded `2000` tokens in the constructor default, `rag/retriever.py:51`) are both static regardless of which LLM provider/model is actually serving the request. A model with a much larger or smaller context window gets the same budget.
- **Single shared Chroma collection across all repos.** `ChromaVectorStore` always uses one collection (`pr_review_knowledge`) and relies on a `repo_name` metadata filter (`rag/vector_store/chroma_store.py:10,61-63`) to scope queries per repo. This works today, but at real multi-repo scale (many repos, frequent ingestion) per-repo collections would isolate index growth/rebuild costs and make it possible to wipe/reset one repo's knowledge without touching others.
- **`FallbackLLMAdapter` only distinguishes `httpx.HTTPStatusError`/`RequestError`, not error *kind*.** (`adapters/llm/fallback.py:26`) A 429 rate-limit and a 413 payload-too-large are both just "try the next provider" — there's no distinction between "this provider is temporarily rate-limited, maybe retry it later" and "this payload will never fit anywhere," so a systematically-too-large diff will burn through every fallback provider before failing.
- **Ingestion has no incremental/delta mode.** `scripts/ingest.py` + `GitHubIngester.ingest_closed_prs`/`ingest_source_files` always re-walk up to `RAG_INGEST_CLOSED_PRS` PRs and the whole file tree; `ChromaVectorStore.upsert` will overwrite existing chunks by ID but there's no "only ingest what changed since last run" tracking.

## Testing & observability

- **No test suite exists.** Confirmed via `find . -iname "*test*"` (only matches inside `venv/`) and `requirements.txt` — no `pytest`, no test runner of any kind is declared. Given the amount of branching logic in `core/agent.py` (heartbeat wrapping, truncation, JSON-parse fallback), `core/adapter_factory.py` (provider fallback wiring), and `rag/retriever.py` (dedup + compression), these are strong candidates for unit tests with mocked adapters — they're pure-Python logic with no real I/O needed to test the branching itself.
- **No structured logging or request correlation.** As noted above, there's no way to grep logs across `main.py` (chat request) → Temporal workflow → `worker.py` (activity execution) for a single review by one ID. Adding a correlation ID (e.g. the Temporal workflow ID, already computed as `pr-review-{repo}-{pr_number}`) to every log line in that path would make debugging a specific failed review much faster.
- **Silent `except Exception: pass` in a few places** (e.g. `FallbackLLMAdapter.close()`, `adapters/llm/fallback.py:38`) swallow shutdown errors entirely — fine for a `close()` best-effort cleanup, but worth at least a debug-level log so a leaking connection isn't invisible forever.

## Open-ended roadmap ideas

*(Ideas, not commitments — flagged as roadmap material rather than gaps in existing code.)*

- Implement real inline PR comments (resolve the diff-position mapping noted above) instead of general-comment-only feedback.
- Build out the `linter.py` tool so the LLM can run static analysis as an explicit tool call rather than relying purely on its own judgment from the diff.
- Per-repo Chroma/Qdrant collections, with an admin tool to inspect/reset a single repo's knowledge base.
- A lightweight dashboard over `/reviews/status` for multiple repos/PRs at once, rather than one-PR-at-a-time polling.
- Push-based completion notification (webhook/WebSocket back to the frontend) instead of `/reviews/status` polling, now that a real event pipe (`/webhook`) already exists for inbound GitHub events.
