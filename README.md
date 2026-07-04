# index-memory-vault

A self-hosted AI memory server for Claude, ChatGPT, Codex, Cursor, and local LLMs — with user-owned storage and human approval gates.

Claude, ChatGPT, Codex, Cursor, 로컬 LLM이 함께 쓰는 셀프호스팅 AI 기억 서버입니다.
기억은 사용자의 저장소에 남고, AI가 만든 기억은 사람 확인을 거쳐 승인됩니다.

> **AI memory should belong to the user, not the platform.**
> AI의 기억은 플랫폼이 아니라 사용자에게 속해야 합니다.

## Why this exists

Shared-memory systems let every AI read and write one memory pool. That solves
fragmentation — and creates a new problem: *anything a model hallucinates
becomes everyone's memory.*

index-memory-vault keeps the shared pool, but adds two constraints:

1. **The memory lives on your disk** — plain Markdown files with YAML
   frontmatter, indexed by SQLite. Delete the server; keep your memories.
2. **AI-written memory has no authority until a human approves it.**
   Every memory carries a state:

   | q_state | meaning | served by default? |
   |---|---|---|
   | `needs_review` | an AI saved this | no (opt-in, labeled) |
   | `verified` | a human approved it | **yes** |
   | `blocked` | a human rejected it | never |

Approval is deliberately **not** an MCP tool a model can call. It is a
human-side CLI action (`imv approve <id>`). A model approving its own memory
would defeat the entire point.

## Quick start

```bash
docker compose up          # HTTP MCP endpoint on :8484, vault in ./vault
# or, local stdio server:
pip install .
imv-server
```

Review loop (human, in a terminal):

```bash
imv pending                # what did my AIs try to remember?
imv show a1b2c3d4e5f6
imv approve a1b2c3d4e5f6 -n "correct"
imv reject  f6e5d4c3b2a1 -n "hallucinated"
```

## Connect a client

**Claude Code** (`.mcp.json`):

```json
{
  "mcpServers": {
    "memory-vault": {
      "command": "imv-server",
      "env": { "IMV_VAULT": "/home/you/vault" }
    }
  }
}
```

Any MCP-capable client (Codex, Cursor, Ollama tool bridges) connects the same
way — stdio locally, or streamable HTTP against the Docker endpoint.

## MCP tool surface

- `save_memory(title, content, tags?, source?)` → always `needs_review`
- `search_memory(query, limit?, include_unverified?)` → verified-only by default
- `list_memory(q_state?, limit?)`
- `get_memory(memory_id)`
- `approve_memory` / `reject_memory` → **disabled by default**
  (`IMV_ALLOW_AGENT_REVIEW=1` to override — read the warning first)

Every state transition is written to an append-only `audit_log`.

## Storage layout

```
vault/
  index.db            # SQLite: FTS5 index + states + audit
  2026/07/<id>-<slug>.md
```

Markdown is the source of truth. The vault is a normal folder — sync it,
back it up, open it in Obsidian.

## Roadmap

- v0.1 — self-hosted memory server (this)
- v0.2 — review dashboard (web)
- v0.3 — Claude Code / Codex / Ollama recipes
- v0.4 — audit ledger export
- v0.5 — team mode

## License

AGPL-3.0. Commercial licenses for closed deployments are available —
contact contact@indexai.kr.
