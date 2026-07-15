"""
External Agent Client Example — HTTP MCP Transport

Demonstrates how any Python AI agent (LangChain, AutoGen, or raw) can
connect to the running FastAPI MCP server and use its tools.

Prerequisites:
    1. Start the FastAPI server: uvicorn main:app --reload
    2. Set your GitHub token below (or via env var)
    3. Run: python examples/external_agent_client.py
"""
import httpx
import json
import os

# --- Config ---
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "ghp_YOUR_TOKEN_HERE")
REPO_NAME = os.getenv("REPO_NAME", "owner/repo-name")  # e.g. "sandipazi/Code_Review_Agent"

HEADERS = {
    "X-GitHub-Token": GITHUB_TOKEN,
    "Content-Type": "application/json",
}


def mcp_request(method: str, params: dict = None, req_id: str = "1") -> dict:
    """Send a single MCP JSON-RPC request and return the parsed response."""
    payload = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params:
        payload["params"] = params

    resp = httpx.post(MCP_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    print("=" * 60)
    print("PR Review Agent — External MCP Client Demo")
    print("=" * 60)

    # Step 1: MCP handshake (initialize)
    print("\n[1] Sending initialize handshake...")
    init_resp = mcp_request("initialize", params={
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "external-agent-demo", "version": "1.0.0"},
    })
    server_info = init_resp.get("result", {}).get("serverInfo", {})
    print(f"    Connected to: {server_info.get('name')} v{server_info.get('version')}")

    # Step 2: Discover available tools
    print("\n[2] Discovering tools (tools/list)...")
    tools_resp = mcp_request("tools/list")
    tools = tools_resp.get("result", {}).get("tools", [])
    print(f"    Found {len(tools)} tools:")
    for t in tools:
        print(f"      • {t['name']}: {t.get('description', '')[:60]}")

    # Step 3: List open PRs
    print(f"\n[3] Calling list_prs for '{REPO_NAME}'...")
    list_resp = mcp_request("tools/call", params={
        "name": "list_prs",
        "arguments": {"repo_name": REPO_NAME, "state": "open"},
    }, req_id="2")

    content = list_resp.get("result", {}).get("content", [])
    if content:
        raw_text = content[0].get("text", "[]")
        try:
            prs = json.loads(raw_text)
            if prs:
                print(f"    Found {len(prs)} open PR(s):")
                for pr in prs[:5]:  # Show max 5
                    print(f"      PR #{pr.get('number')}: {pr.get('title')}")
            else:
                print("    No open PRs found.")
        except json.JSONDecodeError:
            print(f"    Raw result: {raw_text}")
    elif list_resp.get("error"):
        print(f"    Error: {list_resp['error'].get('message')}")

    # Step 4: (Optional) Trigger a review — uncomment to use
    # pr_number = 1  # Replace with a real PR number
    # print(f"\n[4] Triggering review for PR #{pr_number}...")
    # review_resp = mcp_request("tools/call", params={
    #     "name": "trigger_review",
    #     "arguments": {"repo_name": REPO_NAME, "pr_number": pr_number},
    # }, req_id="3")
    # result_content = review_resp.get("result", {}).get("content", [])
    # print(f"    {result_content[0]['text'] if result_content else review_resp.get('error')}")

    print("\n" + "=" * 60)
    print("Demo complete. Your external agent can now use these tools!")
    print("=" * 60)


if __name__ == "__main__":
    main()
