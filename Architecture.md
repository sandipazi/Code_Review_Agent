# Architecture Overview

This repository contains the source code for an AI-powered Pull Request Review Agent built in pure Python. The agent operates without external agentic frameworks, enforcing a custom, highly-controlled interaction loop and manual integrations.

## Core System Design

The application is structured to accept events via Webhooks, fetch the relevant changes from the VCS provider, execute a custom LLM ReAct loop (with tool-calling provided via an internal MCP server), and post feedback back to the pull request.

```mermaid
graph TD
    A[VCS Provider (e.g. GitHub)] -->|Webhook Event| B(FastAPI Receiver)
    B -->|Background Task| C[VCS Adapter]
    C -->|Fetch Diff| D[Custom Agent Loop]
    D -->|Tool Call Requests| E[MCP Client]
    E -->|JSON-RPC| F[Internal MCP Server]
    F -->|Tool Response| E
    E -->|Context| D
    D -->|System Prompt & Diff| G[LLM Adapter]
    G -->|Review Feedback| D
    D -->|Inline Comments| C
    C -->|Post Comments| A
```

## Architectural Components

### 1. Webhook Receiver
- **Technology**: FastAPI
- **Responsibility**: Listens for incoming POST requests from the VCS provider. It validates the payload signatures, extracts the relevant PR metadata, and enqueues the processing job to avoid blocking the HTTP response. Since the deployment is targeted for a local environment, jobs run as FastAPI `BackgroundTasks`.

### 2. Adapter Layer
To ensure the system is uncoupled from specific vendors, we use an **Adapter Pattern** for external dependencies.
- **VCS Adapters** (`adapters/vcs/`): Interfaces for interacting with repositories. Starts with a `GitHub` implementation that fetches code diffs and posts inline comments.
- **LLM Adapters** (`adapters/llm/`): Interfaces for executing prompts and generating responses. This allows swapping between OpenAI, Anthropic, Gemini, or open-source models without altering the core agent loop.

### 3. Internal MCP Server & Client
- **Technology**: Custom JSON-RPC over local function calls.
- **Responsibility**: Instead of running a separate process for the Model Context Protocol (MCP), the MCP Server and Client are embedded logically within the same application process.
- **Flow**: When the LLM decides it needs more context (e.g., reading a full file), the `Agent Loop` constructs an MCP-compliant JSON-RPC payload, sends it via the internal `MCP Client` to the internal `MCP Server`, and returns the result back to the LLM.

### 4. Custom Agent Loop
- **Technology**: Raw Python (`core/agent.py`).
- **Responsibility**: Implements the ReAct (Reason + Act) paradigm manually. It constructs the initial prompt containing the PR diff, handles multi-turn conversations if the LLM invokes tools, and ensures the final output conforms to the expected JSON structure for posting PR comments.
