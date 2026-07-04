"""index-memory-vault: MCP server.

Tool surface exposed to AI clients (Claude Code, Codex, Cursor, local LLMs):

    save_memory    -> always lands in needs_review (Q2)
    search_memory  -> verified-only by default; unverified opt-in, labeled
    list_memory    -> browse by q_state
    get_memory     -> fetch one record
    approve_memory / reject_memory
                   -> DISABLED by default. Approval authority belongs to
                      the human via CLI (`imv approve <id>`). Set
                      IMV_ALLOW_AGENT_REVIEW=1 only if you understand that
                      this lets a model promote its own memories.

Transports: stdio (default) or streamable HTTP (IMV_HTTP=1, for Docker).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .store import VaultStore

VAULT_DIR = os.environ.get("IMV_VAULT", "./vault")
ALLOW_AGENT_REVIEW = os.environ.get("IMV_ALLOW_AGENT_REVIEW", "0") == "1"

mcp = FastMCP("index-memory-vault")
store = VaultStore(VAULT_DIR)

REVIEW_LOCKED_MSG = (
    "Review is a human-only action on this server. "
    "Run `imv approve {id}` or `imv reject {id}` in a terminal. "
    "(Server admins can override with IMV_ALLOW_AGENT_REVIEW=1.)"
)


@mcp.tool()
def save_memory(title: str, content: str, tags: list[str] | None = None,
                source: str = "ai") -> dict:
    """Save a memory to the user's vault. It is stored as needs_review and
    has no authority until a human approves it."""
    mem = store.save(title=title, content=content, tags=tags, source=source)
    return {"saved": mem.public(),
            "note": "q_state=needs_review. A human must approve this memory "
                    "before it is served as verified."}


@mcp.tool()
def search_memory(query: str, limit: int = 10,
                  include_unverified: bool = False) -> dict:
    """Full-text search. By default only human-verified memories are
    returned. Set include_unverified=true to also see needs_review items,
    which are labeled and must be treated as unconfirmed."""
    hits = store.search(query, limit=limit,
                        include_unverified=include_unverified)
    return {"results": [m.public() for m in hits],
            "surface": "verified+needs_review" if include_unverified
                       else "verified-only"}


@mcp.tool()
def list_memory(q_state: str | None = None, limit: int = 50) -> dict:
    """List memories, optionally filtered by q_state
    (needs_review | verified | blocked)."""
    return {"results": [m.public() for m in store.list(q_state, limit)]}


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Fetch a single memory by id."""
    mem = store.get(memory_id)
    return {"result": mem.public() if mem else None}


@mcp.tool()
def approve_memory(memory_id: str, note: str | None = None) -> dict:
    """Promote a memory to verified. Disabled by default: approval is a
    human-only action performed via the imv CLI."""
    if not ALLOW_AGENT_REVIEW:
        return {"error": REVIEW_LOCKED_MSG.format(id=memory_id)}
    mem = store.set_state(memory_id, "verified", actor="agent", note=note)
    return {"result": mem.public()}


@mcp.tool()
def reject_memory(memory_id: str, note: str | None = None) -> dict:
    """Block a memory (excluded from all retrieval). Disabled by default:
    review is a human-only action performed via the imv CLI."""
    if not ALLOW_AGENT_REVIEW:
        return {"error": REVIEW_LOCKED_MSG.format(id=memory_id)}
    mem = store.set_state(memory_id, "blocked", actor="agent", note=note)
    return {"result": mem.public()}


def main() -> None:
    if os.environ.get("IMV_HTTP", "0") == "1":
        mcp.settings.host = os.environ.get("IMV_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("IMV_PORT", "8484"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
