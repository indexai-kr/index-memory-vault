# CLAUDE.md — agent instructions for index-memory-vault

Any AI agent (Claude Code, Codex, Cursor, local LLMs) working on this repo
must read this file first. These rules encode the owner's intent. When a
rule here conflicts with what seems convenient, the rule wins. When a rule
seems wrong, stop and ask the owner — do not silently "improve" it.

## What this project is

A self-hosted AI memory server where **AI-written memory has no authority
until a human approves it**. Everything below protects that one sentence.

## Invariants — never change these without explicit owner approval

1. **Review authority is human-only.** `approve`/`reject` live in the CLI
   (`imv/cli.py`), not in the MCP tool surface. The MCP `approve_memory`/
   `reject_memory` tools stay disabled by default (`IMV_ALLOW_AGENT_REVIEW`).
   Do not "unlock" them for UX reasons. Ever.
2. **`save_memory` always lands in `needs_review`.** No auto-verify paths.
3. **`blocked` memories never appear in any retrieval path**, including
   fallbacks and future search modes.
4. **Every state change writes to `audit_log`.** No code path may change
   `q_state` without an audit row.
5. **`imv doctor` must work and must stay visible in the README.** It is
   the user-facing proof that rule 4 holds. Do not remove its README line,
   and do not break its internal unlimited listing (`store.list(limit=None)`).
6. **Public vocabulary is `needs_review` / `verified` / `blocked` only.**
   Do not introduce internal INDEX terms into code, docs, or comments.
7. **Markdown vault is the source of truth**; SQLite is an index. Do not
   invert this.

## Editing rules

- **Replace, don't stack.** When changing a line, delete the old line.
  Duplicated dead code from a lazy merge is a defect.
- **Do not delete content from README/docs to "reduce noise."** Removing
  documented behavior is a product decision, which belongs to the owner.
- **Never commit `DESIGN.md`** or any file describing internal strategy.
  It is gitignored; keep it that way. If you find such a file staged,
  stop and alert the owner.
- Keep the core small: stdlib + `mcp` only. New dependencies require
  owner approval.
- Comments and identifiers in English; owner-facing reports in Korean.

## Definition of done

A change is done only when ALL of the following pass locally:

```
python -m unittest discover -s tests     # all tests, including MCP stdio
imv doctor                               # must run without crashing
```

If you add a validation or a limit anywhere, add a test that exercises
`imv doctor` and the probe path afterward — the doctor once shipped broken
because unit tests never called it. "Tests pass" is not "it works."

## Decision boundary

Agents generate; the owner decides. If a task requires choosing between
designs, expanding scope, exposing/hiding a feature, or touching any
invariant above: prepare options with trade-offs and STOP for the owner's
call. Do not pick for them.
