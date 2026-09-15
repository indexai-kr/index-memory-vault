# Dreaming cycle: Light → Deep → REM → Waking

Dreaming is a read-only pass over the knowledge base
(`imv_knowledge.db`). It proposes; it never changes state. State changes
happen only through `imv waking apply`, run by a human in a terminal.

```bash
imv dream --stage light|deep|rem|all --db <imv_knowledge.db> --out <dir>
```

Path priority: `--db` / `--out` > `IMV_KNOWLEDGE_DB` / `IMV_DREAM_OUT` >
`config.yaml` (`knowledge.db_path`, `dreaming.out_dir`). There is no
default: an unconfigured path ends with `KnowledgeUnavailable`, never an
empty result. The database is opened with `mode=ro`; any write attempt
fails inside SQLite itself.

## Stages (progressive — each includes everything above it)

- **Light** (`imv/dreaming/light.py`) — document families (version/copy/
  backup titles collapsed to one family), taxonomy classification, and
  the Strong / Medium / Weak / None evidence grading. Only explicit
  approval markers (`approved_by`, `approval_id`, `authorization_ref`,
  `status: approved`, 승인완료/결재완료, …) count; mere discussion of
  approval grades None. Outputs: `lineage.csv`, `evidence_registry.csv`,
  `strong_candidates.csv`, `non_approved_candidates.csv`.
- **Deep** (`imv/dreaming/deep.py`) — per-chunk tier and approval
  proposals. The grade outranks everything: only Strong proposes
  `approved`; Medium proposes `in_review`; Weak (and policy/financial
  claims) proposes `candidate`; no evidence proposes `raw`. The search
  tier comes from taxonomy alone and is never auto-linked to approval.
  Output: `chunk_plan.csv` — the waking input.
- **REM** (`imv/dreaming/rem.py`) — duplicate and conflict candidates.
  Outputs: `duplicate_candidates.csv`, `conflict_candidates.csv`.
- **all** — everything plus `summary.md`, `apply_rollback_plan.md`
  (a plan document, not an execution record), and `dream_report.json`
  (stage, input chunk count, proposal count, conflict count, plan
  SHA-256, invariants).

## The nine CSV outputs

| # | file | content |
|---|---|---|
| 1 | `lineage.csv` | family, document_id, title, version_id, version_no, is_current, created_at |
| 2 | `evidence_registry.csv` | evidence_id, document_id, title, grade, signals, decision |
| 3 | `strong_candidates.csv` | the Strong subset of 2 |
| 4 | `non_approved_candidates.csv` | latest non-Strong policy/design/financial docs |
| 5 | `conflict_candidates.csv` | conflict_key, type, left/right document+locator+text, automatic_resolution=PROHIBITED, review_state |
| 6 | `duplicate_candidates.csv` | left/right id+title, relation, similarity, auto_inherit_approval=NO |
| 7 | `chunk_plan.csv` | chunk_id, document_id, version_id, content_hash, current/proposed tier+approval, approval_grade, change, reason |
| 8 | `summary.md` | aggregate counts |
| 9 | `apply_rollback_plan.md` | apply/rollback command plan |

## Waking procedure (human only)

```bash
imv waking apply --plan chunk_plan.csv --db <imv_knowledge.db> \
    --actor human:<id> [--confirm] [--auth <ref>] [--reason <text>]
```

- Without `--confirm`: dry-run. Zero writes; planned changes printed.
- `--actor` must be `human:<id>`; agents are refused.
- Guarded updates: a row applies only if the live
  `(version_id, content_hash, search_tier, approval_state)` still
  matches the plan snapshot. One mismatch aborts the whole batch.
- Grade gate: `approved` proposed on Medium-or-lower evidence is
  skipped with a printed reason.
- Every applied row appends one `chunk_policy_events` record (actor,
  before/after, plan SHA-256, timestamp).

## Invariants

1. `previous_review_preserved=True` — a chunk with a past review keeps
   its tier/approval; proposals never overwrite it.
2. `duplicate_inherits_approval=False` — duplicates never inherit
   approval automatically.
3. `conflict_auto_resolve=False` — conflicts are detected only;
   resolution is human.
4. `waking_executed=False` — dreaming writes nothing; waking is a
   human CLI action and never an MCP tool.
