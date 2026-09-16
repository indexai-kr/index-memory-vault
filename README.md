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

## Status — what actually works

| Feature | Status | Notes |
|---|---|---|
| MCP memory server, q_state (needs_review / verified / blocked) | Stable | since v0.2.x |
| Human-only approval via CLI (`imv approve`) | Stable | never exposed as an MCP tool |
| Knowledge-base search (`search_chunks`, `get_chunk`, `imv_status`) | Experimental | v0.3.0; served only if approval_state=approved and search_tier in (primary, reference) |
| Two-axis schema (search_tier × approval_state) | Experimental | enforced server-side in knowledge.py |
| Dreaming cycle (light / deep / rem) | Experimental | read-only; produces proposals only |
| Waking (`imv waking apply`) | Experimental | human CLI, dry-run by default, never an MCP tool |
| Pre-approval criteria v0.1 (Strong/Medium/Weak/None) | Experimental | scoring lives in dreaming; docs/design/ |
| Ref-Learning | Design-only | no code; docs pending |
| Q-Cache | Design-only | no code in this repo; docs pending |

Everything above is open for argument. If a design is wrong, open an issue — that is why it is published unfinished.

## Measured (reproducible)

Ref Impact bench, 40 synthetic memories, 3 runs per condition, model: opencode/muse-spark-1.3-contributor-free, IMV v0.3.0 (bench code at 65cdab2, logs at 8434ad9):

| Condition | Recall (30 verified) | Leak (10 needs_review/blocked) | Tool calls / q |
|---|---|---|---|
| A no vault | 0.0 % (0.0–0.0) | 0.0 % | 0.6 |
| B vault, default | 100.0 % (100.0–100.0) | 6.7 % | 4.3 |
| C vault + needs_review opt-in | 100.0 % (100.0–100.0) | 0.0 % blocked (labeled) | 3.3 |

`imv-bench ref-impact` reproduces this. Raw logs: bench/ref_impact/results/.

## Quick start

```bash
docker compose up          # HTTP MCP endpoint on :8484, vault in ./vault
# or, local stdio server:
pip install .
imv-server
```

## Official Windows build and member portal

The AGPL source remains public and requires no account. The member portal is
for verified official Windows installers, automatic Claude Desktop setup, and
support; membership does not grant access to otherwise closed source.

The portal stores account, terms-consent, and download-ledger records only.
Memory content and vault files are never uploaded to INDEX servers.

Run the optional portal locally:

```bash
pip install ".[portal]"
imv-portal                         # http://127.0.0.1:8486/member/register
```

Build the Windows executables and Inno Setup wrapper on Windows:

```powershell
.\scripts\build_windows.ps1
```

Outputs: `imv-server.exe`, `imv.exe`, and `imv-setup-0.2.1.exe`. The installer
backs up and JSON-merges only `mcpServers.memory-vault`; malformed Claude
configuration is backed up before a clean config is created.

Review loop (human, in a terminal):

```bash
imv pending                # what did my AIs try to remember?
imv show a1b2c3d4e5f6
imv approve a1b2c3d4e5f6 -n "correct"
imv reject  f6e5d4c3b2a1 -n "hallucinated"
imv doctor                 # vault health check: reports any memory whose
                           # state changed without an audit record
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

- v0.1 — self-hosted memory server
- v0.2 — member portal, official release library, and Windows installer (current)
- v0.3 — Claude Code / Codex / Ollama recipes
- v0.4 — audit ledger export
- v0.5 — team mode

## License

AGPL-3.0. Commercial licenses for closed deployments are available —
contact contact@indexai.kr.

Running this server for others over a network counts as conveying it:
if you modify the code and let people use it remotely, share your
modified source under the same license. See the LICENSE file for the
full terms.
