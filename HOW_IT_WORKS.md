# How It Works — End-to-End Walkthrough

This document traces one real PR-review request from a user's chat message to a posted GitHub comment. It complements the existing docs rather than repeating them:

- For the system diagram and component-by-component responsibilities, see [Architecture.md](Architecture.md).
- For setup/run commands and required tokens, see [README.md](README.md).
- For the MCP server and RAG pipeline internals, see [MCP.md](MCP.md) and [RAG.md](RAG.md).

## The trigger path has no direct "review this PR" endpoint

There's no `POST /reviews`. Reviews are always triggered conversationally — the LLM decides to call the `trigger_review` tool during a chat turn.

## Step by step

### 1. User chats

`POST /chat` (`main.py:80`) receives the full conversation history (stateless — no server-side session). It builds a `vcs_adapter` + `llm_adapter` via `get_adapters()`, an in-process `MCPServer` with GitHub tools (and RAG tools, if `get_rag_components()` returns a retriever), wraps that in `InternalMCPClient`, and hands everything to `ChatAgent.chat()`.

`ChatAgent.chat()` (`core/chat_agent.py:25`) runs a ReAct loop (max 5 iterations): build a system prompt (optionally with RAG context — see [RAG.md](RAG.md)) → call `llm.generate(messages, tools)` → if the response has `tool_calls`, execute each via `mcp.call_tool()` and append the results as `role="tool"` messages → repeat until the LLM responds with no tool calls.

Because this does blocking sync HTTP calls (LLM + tool calls), `main.py` runs it via `asyncio.to_thread(chat_agent.chat, messages)` rather than directly on the event loop — otherwise a single slow chat turn would freeze `/reviews/status` polling for everyone.

### 2. The LLM calls `trigger_review`

When the user asks to review a specific PR, the LLM's tool call reaches `GitHubTools.trigger_review()` (`mcp_server/tools/github_tools.py:25`), which just calls whatever `review_callback` it was built with. In the `/chat` flow, that's `trigger_review_callback` (`main.py:97`):

```python
asyncio.run_coroutine_threadsafe(
    _start_review_workflow(repo_name, pr_number, github_token), loop
).result(timeout=15)
```

`chat_agent.chat()` is running on a worker thread (step 1), so it can't just `await` or `asyncio.create_task()` — there's no running event loop on that thread. `run_coroutine_threadsafe` schedules the actual Temporal call back onto the *main* event loop thread, and only blocks the worker thread while waiting for it to start (not the loop itself).

`_start_review_workflow()` (`main.py:64`) starts a durable Temporal workflow:

```python
await client.start_workflow(
    PRReviewWorkflow.run,
    args=[repo_name, pr_number, github_token],
    id=f"pr-review-{repo_name}-{pr_number}",
    task_queue=settings.TEMPORAL_TASK_QUEUE,
    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
)
```
`WorkflowAlreadyStartedError` is caught and logged rather than raised — re-triggering a review already in flight for the same PR is a no-op, not an error.

At this point `/chat` returns control to the user immediately — starting the workflow just enqueues it; nothing has actually run yet.

### 3. The worker picks it up

`worker.py` is a **separate, always-running process** that polls `settings.TEMPORAL_TASK_QUEUE`. It registers `PRReviewWorkflow` and `run_review_activity` with a 20-thread `activity_executor`, because the activity itself is fully synchronous (`httpx` sync clients throughout) and can't run directly on the asyncio event loop.

`PRReviewWorkflow.run()` (`core/temporal_workflows.py:51`) is a thin wrapper: it executes `run_review_activity` with a 10-minute `start_to_close_timeout`, a 45-second `heartbeat_timeout`, and `RetryPolicy(maximum_attempts=1)` — deliberately no automatic retries, because posting the review comment is a non-idempotent side effect (`_process_final_review`) and a retry after partial failure could double-post.

### 4. The activity builds everything fresh and runs the agent

`run_review_activity()` (`core/temporal_workflows.py:15`) is a plain function, not a class — it builds its own `vcs_adapter`/`llm_adapter` (`get_adapters`), its own `rag_retriever`/`github_ingester` (`get_rag_components`), its own `MCPServer` (with RAG tools attached if available), and constructs a fresh `PRReviewAgent`. This keeps `core/agent.py` itself free of any Temporal import — it only knows about a generic `heartbeat: Callable` passed in as `activity.heartbeat`.

`PRReviewAgent.review_pr(repo_name, pr_number)` (`core/agent.py:55`) then:

1. **Fetch the diff** via the VCS adapter. Empty diff → return early, no review posted.
2. **Truncate** to `MAX_DIFF_CHARS` (default 24000) if needed, noting the truncation in the eventual prompt.
3. **Retrieve RAG context** (if enabled) — `self.rag.retrieve(diff_text, repo_name)`, capped again at `MAX_RAG_CONTEXT_CHARS`. See [RAG.md](RAG.md) for the retrieval algorithm.
4. **Build the system prompt** — reviewer persona + the RAG context block (if any) + a strict instruction to reply with a single JSON object (`general_comment` + `inline_comments`).
5. **Run the ReAct loop** (max 5 iterations) — same shape as `ChatAgent`, but here every LLM call and every RAG call is wrapped in `_run_with_heartbeat()` (`core/agent.py:38`): the call runs on a 1-worker `ThreadPoolExecutor`, and every `HEARTBEAT_POLL_INTERVAL_SECONDS` (10s) while it's still in flight, `self.heartbeat()` (i.e. `activity.heartbeat`) fires — comfortably inside Temporal's 45s `heartbeat_timeout`, even though a single LLM call through the fallback chain can legitimately take up to ~120s, and a cold-start embedding-model download can take even longer.
6. **Parse and post** — `_process_final_review()` strips a possible ` ```json ` fence, `json.loads()`s the result, posts `general_comment` via `vcs.post_review_comment()`. Inline comments are currently only logged, not posted as real GitHub inline comments — see [IMPROVEMENTS.md](IMPROVEMENTS.md). If JSON parsing fails, the raw LLM text is posted as a fallback general comment instead of being silently dropped.
7. **Auto re-ingest** — on success, `github_ingester.ingest_review_result(...)` embeds the posted comments back into the RAG store (also heartbeat-wrapped, for the same cold-start reason as step 3).

A `VCSPermissionError` (under-scoped GitHub token) is re-raised as a non-retryable Temporal `ApplicationError` tagged `type="GitHubPermissionError"`, so step 5 below can surface the exact missing scope instead of a generic failure.

### 5. The frontend watches it finish

`GET /reviews/status?repo_name=...&pr_number=...` (`main.py:205`) calls `handle.describe()` on the Temporal workflow and maps its status to one of `pending` / `completed` / `failed` / `cancelled` / `unreachable` / `not_found` / `error` (see the table in `Architecture.md`). On `failed`, it walks the Temporal error's `.cause` chain down to the innermost message (`_unwrap_failure_message`) so the frontend gets something actionable (e.g. the GitHub-permission message) instead of a generic "Workflow execution failed".

`POST /reviews/cancel` cancels the workflow cooperatively — it takes effect at the next heartbeat/loop checkpoint inside `review_pr()`, not instantly.

## Two side topics worth knowing

**Threading, three different reasons:**
- `core/agent.py`'s `ThreadPoolExecutor(max_workers=1)` + heartbeat loop exists purely so a blocking network call (LLM/RAG) doesn't sit silently past Temporal's heartbeat timeout.
- `worker.py`'s `ThreadPoolExecutor(max_workers=20)` exists because `run_review_activity` is synchronous code being run by an otherwise-asyncio Temporal worker.
- `main.py`'s `asyncio.to_thread(...)` + `run_coroutine_threadsafe(...)` pairing exists so a blocking chat turn doesn't freeze the FastAPI event loop, while still letting that thread kick off an async Temporal call correctly.

This replaced an earlier, simpler in-process daemon-thread-per-review model (`core/review_jobs.py`, commit `7801a29`), which was removed when Temporal was introduced (commit `e52a442`) so reviews would survive process restarts and run independently rather than queuing behind a shared list.

**LLM provider routing:** `core/adapter_factory.py:get_adapters()` builds a primary adapter from `LLM_PROVIDER` (openai / github_models / groq / openrouter) and, if `LLM_FALLBACK_PROVIDERS` names any configured providers, wraps the whole chain in `FallbackLLMAdapter` (`adapters/llm/fallback.py`), which tries each adapter in order and only moves to the next on an `httpx` HTTP-status or connection error.
