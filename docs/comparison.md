# Comparison (draft for master review)

> Rule followed while writing this: every non-IMV cell contains only
> what was read directly in that project's public README on 2026-09-15;
> everything else is "–". No benchmark numbers anywhere — none of the
> original logs are in this repo. Do NOT copy this table into the README
> before the master reviews it.

Sources opened:

- mem0: https://github.com/mem0ai/mem0 (README; License: Apache-2.0 section)
- Graft: ambiguous — at least three unrelated public projects share the
  name (AEndrix03/Graft: local-first agentic memory in C; NanoNets/Graft:
  repo code-graph; flyingrobots/graft: context governor). Which one is
  meant is undecided, so the whole column is "–".
- memledger: ambiguous — memledger-ai/memledger-core (trust layer over
  pgvector, Apache-2.0), riktar/memledger (single-SQLite agent memory,
  MIT), selfradiance/memledger (TypeScript claim ledger, MIT). Which one
  is meant is undecided, so the whole column is "–".

| | index-memory-vault (this repo) | mem0 | Graft | memledger |
|---|---|---|---|---|
| License | AGPL-3.0 (LICENSE file in repo) | Apache-2.0 (LICENSE file in repo) | – | – |
| Storage | Markdown files + SQLite FTS5 index on your disk (verified in repo) | – (default OSS store not confirmed from README) | – | – |
| Human approval gate | AI-written memory has no authority until a human approves via CLI; approval is not an MCP tool (verified in repo) | – (no human-approval gate found in README) | – | – |
| MCP server | stdio / streamable HTTP memory + knowledge tools (verified in repo) | – (MCP surface not confirmed from README) | – | – |
| Self-hosted option | docker compose / local install (verified in repo) | Self-hosted server via `docker compose` (README "Self-Hosted Server") | – | – |
| Managed cloud option | None — member portal is installers/support only, memory never uploaded (verified in repo) | Yes — managed platform alongside OSS (README Library / Self-Hosted / Cloud table) | – | – |
| LLM requirement | None for the server; retrieval is FTS/LIKE, no model calls (verified in repo) | Requires an LLM (default model named in README quickstart) | – | – |

## Open questions for the master

1. Which "Graft" and which "memledger" are the comparison targets?
   Pin exact repo URLs before filling any "–".
2. Keep this file as a review draft, or publish a trimmed version?
