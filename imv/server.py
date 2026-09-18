"""index-memory-vault: MCP server.

Tool surface exposed to AI clients (Claude Code, Codex, Cursor, local LLMs):

    save_memory    -> always lands in needs_review (Q2)
    search_memory  -> verified-only by default
    list_memory    -> verified-only by default
    get_memory     -> verified-only by default
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
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from .knowledge import GATE_DESCRIPTION, KnowledgeStore, KnowledgeUnavailable
from .store import VaultStore

VAULT_DIR = os.environ.get("IMV_VAULT", "./vault")
ALLOW_AGENT_REVIEW = os.environ.get("IMV_ALLOW_AGENT_REVIEW", "0") == "1"
ALLOW_UNVERIFIED_READ = os.environ.get("IMV_ALLOW_UNVERIFIED_READ", "0") == "1"
KNOWLEDGE_DB = os.environ.get("IMV_KNOWLEDGE_DB", "")

# FastMCP's settings are pydantic-settings, which by default reads a `.env`
# from the *current working directory*. The cwd belongs to whichever host
# launches us, so that file is not ours: a stray .env crashes startup if it is
# not UTF-8, and one containing FASTMCP_* keys would silently rewrite our
# server settings. Every option this server has comes from os.environ, so the
# cwd .env is opted out of entirely.
FastMCPSettings.model_config["env_file"] = None

mcp = FastMCP("index-memory-vault")
store = VaultStore(VAULT_DIR)

_knowledge: KnowledgeStore | None = None


def knowledge() -> KnowledgeStore:
    """Open the knowledge base on first use. Absence raises rather than
    returning empty, so "not configured" never reads as "no evidence"."""
    global _knowledge
    if _knowledge is None:
        if not KNOWLEDGE_DB:
            raise KnowledgeUnavailable(
                "IMV_KNOWLEDGE_DB is not set. The knowledge base is a "
                "separate read-only store from the memory vault.")
        _knowledge = KnowledgeStore(KNOWLEDGE_DB)
    return _knowledge

REVIEW_LOCKED_MSG = (
    "Review is a human-only action on this server. "
    "Run `imv approve {id}` or `imv reject {id}` in a terminal. "
    "(Server admins can override with IMV_ALLOW_AGENT_REVIEW=1.)"
)

UNVERIFIED_READ_LOCKED_MSG = (
    "Unverified memory is hidden from the MCP surface. "
    "A server operator may start a dedicated review/test instance with "
    "IMV_ALLOW_UNVERIFIED_READ=1."
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
    returned. include_unverified is accepted only on a server instance whose
    operator explicitly enabled IMV_ALLOW_UNVERIFIED_READ=1."""
    if include_unverified and not ALLOW_UNVERIFIED_READ:
        return {"results": [], "surface": "verified-only",
                "retrieval_path": "none",
                "error": UNVERIFIED_READ_LOCKED_MSG}
    hits, retrieval_path = store.search_with_path(
        query, limit=limit, include_unverified=include_unverified)
    return {"results": [m.public() for m in hits],
            "surface": "verified+needs_review" if include_unverified
                       else "verified-only",
            "retrieval_path": retrieval_path}


@mcp.tool()
def list_memory(q_state: str | None = None, limit: int = 50) -> dict:
    """List verified memories. Non-verified q_state filters are accepted only
    on an operator-enabled review/test instance."""
    if q_state not in (None, "verified") and not ALLOW_UNVERIFIED_READ:
        return {"results": [], "surface": "verified-only",
                "error": UNVERIFIED_READ_LOCKED_MSG}
    effective_state = q_state if ALLOW_UNVERIFIED_READ else "verified"
    return {"results": [m.public() for m in store.list(effective_state, limit)],
            "surface": "operator-unverified" if ALLOW_UNVERIFIED_READ
                       else "verified-only"}


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """Fetch a verified memory by id. Knowing an unverified id does not grant
    access unless the server operator enabled the review/test surface."""
    mem = store.get(memory_id)
    if mem is not None and mem.q_state != "verified" and not ALLOW_UNVERIFIED_READ:
        return {"result": None, "surface": "verified-only",
                "note": "No verified memory with that id is retrievable."}
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


@mcp.tool()
def search_chunks(query: str, limit: int = 10) -> dict:
    """Search the knowledge base for evidence chunks.

    Serves only chunks a human approved AND placed in a retrievable search
    tier. The gate is enforced by this server and cannot be turned off from
    the client. Every hit carries chunk / document / version / source ids so
    a citation can be traced back to its origin. `withheld_by_policy` counts
    chunks whose text matched but that the gate excluded, so "no evidence"
    is never confused with "blocked by policy"."""
    try:
        hits, retrieval_path, withheld = knowledge().search(query, limit=limit)
    except KnowledgeUnavailable as exc:
        return {"error": str(exc), "knowledge_connected": False}
    return {"results": [h.public(snippet_for=query) for h in hits],
            "retrieval_path": retrieval_path,
            "withheld_by_policy": withheld,
            "gate": GATE_DESCRIPTION}


@mcp.tool()
def get_chunk(chunk_id: int) -> dict:
    """Fetch one knowledge chunk in full.

    Subject to the same policy gate as search_chunks: knowing an id does not
    grant access to an unapproved chunk. A withheld chunk and a nonexistent
    one are reported identically, on purpose."""
    try:
        chunk = knowledge().get(chunk_id)
    except KnowledgeUnavailable as exc:
        return {"error": str(exc), "knowledge_connected": False}
    except ValueError as exc:
        return {"error": str(exc)}
    if chunk is None:
        return {"result": None,
                "note": "No approved chunk with that id is retrievable. It "
                        "either does not exist or the policy gate withholds it."}
    return {"result": chunk.public(max_chars=100_000)}


@mcp.tool()
def imv_status() -> dict:
    """Report which stores this server is actually connected to, and how
    much of each is retrievable. All counts are measured, not configured."""
    by_state = {state: len(store.list(state, limit=500))
                for state in ("verified", "needs_review", "blocked")}
    status = {
        "memory_vault": {
            "vault_dir": str(store.vault),
            "db_path": str(store.db_path),
            "memories_by_q_state": by_state,
            "agent_review_allowed": ALLOW_AGENT_REVIEW,
            "unverified_read_allowed": ALLOW_UNVERIFIED_READ,
        }
    }
    try:
        status["knowledge_base"] = {"connected": True, **knowledge().policy_snapshot()}
    except KnowledgeUnavailable as exc:
        status["knowledge_base"] = {"connected": False, "reason": str(exc)}
    return status


def main() -> None:
    if os.environ.get("IMV_HTTP", "0") == "1":
        mcp.settings.host = os.environ.get("IMV_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("IMV_PORT", "8484"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
