# Comparison (draft for master review)

> Rules followed: each non-IMV cell holds only what was read directly in
> that repo's public README on 2026-09-15; the rest is "–". The IMV
> column never exceeds the README status-table labels — Experimental
> items are marked "(experimental)". No benchmark numbers anywhere.
> Do NOT copy this table into the README before the master reviews it.

Sources opened:

- mem0: https://github.com/mem0ai/mem0 (README verified)
- Graft: https://github.com/AEndrix03/Graft — the agent-memory project
  (README verified; graft-org/graft is a different project, excluded)
- memledger: https://github.com/memledger-ai/memledger-core (PyPI
  `memledger`) — the repo URL returns 404 and its README is not
  publicly readable as of 2026-09-15, so the whole column stays "–".

| | index-memory-vault (this repo) | mem0 | Graft (AEndrix03/Graft) | memledger (memledger-core) |
|---|---|---|---|---|
| License | AGPL-3.0 (LICENSE file in repo) | Apache-2.0 (LICENSE file in repo) | Apache-2.0 (LICENSE file in repo) | – |
| Storage | Markdown files + SQLite FTS5 index on your disk | – | Single local SQLite file (+ FTS5, sqlite-vec) | – |
| Human approval gate | AI-written memory has no authority until a human approves via CLI; approval is not an MCP tool | – | – | – |
| MCP server | stdio / streamable HTTP; memory tools Stable, knowledge tools (experimental) | – | MCP bridge for chat clients (Claude Desktop / ChatGPT) | – |
| Self-hosted option | docker compose / local install | Self-hosted server via docker compose | Local binary + daemon; explicitly not a hosted memory SaaS | – |
| Managed cloud option | None — portal is installers/support only, memory never uploaded | Yes — managed platform alongside OSS | None — local-first, no account, no API key | – |
| LLM requirement | None for the server; retrieval is FTS/LIKE, no model calls | Requires an LLM (a default model is named in the quickstart) | No external LLM call to store or retrieve (local embeddings) | – |

## Open questions for the master

1. memledger-core README가 공개 404라 전 열 "–" 처리함. 비교 유지가
   맞는지, 아니면 열 자체를 삭제할지 결정 필요.
2. Keep this file as a review draft, or publish a trimmed version?
