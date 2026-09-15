"""index-memory-vault: dreaming cycle (read-only proposal pass).

Dreaming reads the knowledge base and produces *proposals* — CSV files and
a JSON report. It never writes to the database.

Invariants (kept as named constants so they stay visible, not buried):

- ``PREVIOUS_REVIEW_PRESERVED`` — a chunk that already has a
  ``chunk_policy_events`` record keeps its current tier/approval; a new
  proposal never silently overwrites a past human decision.
- ``DUPLICATE_INHERITS_APPROVAL`` — always False. A duplicate never
  inherits another document's approval state automatically.
- ``CONFLICT_AUTO_RESOLVE`` — always False. Conflicts are detected only;
  resolution is a human waking action.
- ``WAKING_EXECUTED`` — always False here. Dreaming only plans; state
  changes happen exclusively through ``imv waking`` run by a human.

Stages (progressive — each stage includes everything above it):

- light — document families, taxonomy, evidence grades
- deep  — per-chunk tier / approval proposals
- rem   — duplicate and conflict candidates
- all   — everything plus summary and apply/rollback plan documents
"""

from __future__ import annotations

# Dreaming is read-only. These constants are the machine-readable form of
# that promise; report.py stamps them into every dream_report.json.
PREVIOUS_REVIEW_PRESERVED = True
DUPLICATE_INHERITS_APPROVAL = False
CONFLICT_AUTO_RESOLVE = False
WAKING_EXECUTED = False

DREAMING_INVARIANTS = {
    "previous_review_preserved": PREVIOUS_REVIEW_PRESERVED,
    "duplicate_inherits_approval": DUPLICATE_INHERITS_APPROVAL,
    "conflict_auto_resolve": CONFLICT_AUTO_RESOLVE,
    "waking_executed": WAKING_EXECUTED,
    "database_mode": "read_only",
}

STAGES = ("light", "deep", "rem", "all")
