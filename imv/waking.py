"""index-memory-vault: waking — human-only application of dreaming plans.

Waking is the *only* writer in the dreaming cycle, and it is deliberately
**not** an MCP tool (see ``imv/server.py``, which must never import this
module). A human runs it in a terminal::

    imv waking apply --plan chunk_plan.csv --db imv_knowledge.db \\
        --actor human:master --confirm

Rules, all enforced here rather than trusted to the caller:

- No ``--confirm`` means dry-run: zero writes, the planned changes are
  only printed.
- ``--actor`` must start with ``human:``. Anything else (including a
  missing actor) is refused — agents cannot wake.
- Guarded updates: every row is applied only when the live
  ``(version_id, content_hash, search_tier, approval_state)`` still
  matches the plan's ``current_*`` snapshot. One mismatch aborts the
  whole batch (rollback).
- Grade gate: a row proposing ``approved`` whose evidence grade is not
  ``Strong`` is skipped with a printed reason. Medium-or-lower evidence
  never becomes ``approved`` through waking.
- No automatic inheritance, no automatic conflict resolution: waking
  applies exactly the explicit rows of the plan, nothing derived.
- Every applied row appends one ``chunk_policy_events`` record carrying
  actor, before/after values, the plan SHA-256, and a timestamp.
"""

from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_PLAN_COLUMNS = (
    "chunk_id", "document_id", "version_id", "content_hash",
    "current_search_tier", "current_approval_state",
    "proposed_search_tier", "proposed_approval_state",
    "approval_grade",
)


class WakingRefused(RuntimeError):
    """The waking request itself is invalid (actor, plan, or binding)."""


@dataclass
class WakingResult:
    dry_run: bool
    actor: str
    plan: str
    plan_sha256: str
    applied: list[int] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    events_appended: int = 0

    def summary(self) -> str:
        lines = [
            f"waking {'dry-run' if self.dry_run else 'applied'} "
            f"actor={self.actor} plan={self.plan}",
            f"plan_sha256={self.plan_sha256}",
            f"applied={len(self.applied)} skipped={len(self.skipped)} "
            f"events={self.events_appended}",
        ]
        for item in self.skipped:
            lines.append(f"  skip chunk {item['chunk_id']}: {item['reason']}")
        return "\n".join(lines)


def check_actor(actor: str | None) -> str:
    """Accept only human actors. Agents cannot wake, by construction."""
    if not actor or not actor.startswith("human:") or len(actor) <= len("human:"):
        raise WakingRefused(
            "waking requires --actor human:<id> (a human operator, "
            "never an agent). Refused.")
    return actor


def load_plan(plan_path: str | Path) -> tuple[list[dict], str]:
    """Read the dreaming chunk plan and hash it. The hash is recorded in
    every audit event so the applied rows stay bound to the exact file."""
    path = Path(plan_path)
    if not path.is_file():
        raise WakingRefused(f"plan not found: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = [c for c in REQUIRED_PLAN_COLUMNS
               if c not in (rows[0].keys() if rows else [])]
    if missing:
        raise WakingRefused(
            f"plan {path} is not a dreaming chunk plan; "
            f"missing columns: {', '.join(missing)}")
    return rows, digest


def _bindable(row: dict) -> tuple[bool, str]:
    """Decide whether a plan row may be applied at all."""
    if row.get("change", "YES").upper() == "NO" and (
            row["current_search_tier"] == row["proposed_search_tier"]
            and row["current_approval_state"]
            == row["proposed_approval_state"]):
        return False, "no change proposed"
    if (row["proposed_approval_state"] == "approved"
            and row.get("approval_grade", "None") != "Strong"):
        return False, (
            f"proposed approved with {row.get('approval_grade', 'None')} "
            "evidence (Strong required)")
    return True, ""


def apply_plan(db_path: str | Path, plan_rows: list[dict], plan_sha256: str,
               actor: str, authorization_ref: str, reason: str,
               confirm: bool) -> WakingResult:
    """Apply (or dry-run) a loaded plan inside one guarded transaction."""
    result = WakingResult(dry_run=not confirm, actor=actor,
                          plan="", plan_sha256=plan_sha256)
    targets: list[dict] = []
    for row in plan_rows:
        ok, why = _bindable(row)
        if ok:
            targets.append(row)
        else:
            result.skipped.append(
                {"chunk_id": row.get("chunk_id"), "reason": why})

    if not confirm:
        # Dry-run: report what *would* change without opening a writer.
        return result

    con = sqlite3.connect(Path(db_path))
    con.row_factory = sqlite3.Row
    try:
        live: dict[int, dict] = {}
        for row in targets:
            chunk_id = int(row["chunk_id"])
            current = con.execute(
                "SELECT id, search_tier, approval_state, content_hash,"
                " version_id FROM chunks WHERE id=?",
                (chunk_id,)).fetchone()
            if current is None:
                raise WakingRefused(f"chunk {chunk_id} does not exist")
            if (str(current["version_id"]) != row["version_id"]
                    or current["content_hash"] != row["content_hash"]
                    or current["search_tier"] != row["current_search_tier"]
                    or current["approval_state"]
                    != row["current_approval_state"]):
                raise WakingRefused(
                    f"chunk {chunk_id} drifted since dreaming; "
                    "plan binding mismatch — whole batch aborted")
            live[chunk_id] = dict(current)

        created_at = datetime.now(timezone.utc).isoformat()
        con.execute("BEGIN IMMEDIATE")
        try:
            for row in targets:
                chunk_id = int(row["chunk_id"])
                before = live[chunk_id]
                updated = con.execute(
                    "UPDATE chunks SET search_tier=?, approval_state=?"
                    " WHERE id=? AND search_tier=? AND approval_state=?"
                    " AND content_hash=? AND version_id=?",
                    (row["proposed_search_tier"],
                     row["proposed_approval_state"], chunk_id,
                     before["search_tier"], before["approval_state"],
                     before["content_hash"], before["version_id"]))
                if updated.rowcount != 1:
                    raise RuntimeError(
                        f"guarded update failed for chunk {chunk_id}")
                con.execute(
                    "INSERT INTO chunk_policy_events"
                    " (chunk_id, old_search_tier, new_search_tier,"
                    "  old_approval_state, new_approval_state,"
                    "  actor, reason, authorization_ref, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (chunk_id, before["search_tier"],
                     row["proposed_search_tier"], before["approval_state"],
                     row["proposed_approval_state"], actor,
                     f"{reason}; plan_sha256={plan_sha256}",
                     authorization_ref, created_at))
                result.applied.append(chunk_id)
            con.commit()
        except Exception:
            con.rollback()
            raise
        result.events_appended = len(result.applied)
        return result
    finally:
        con.close()


def resolve_waking_db(db_arg: str | None) -> Path:
    """Waking needs an explicit database. ``--db`` wins; the same
    ``IMV_KNOWLEDGE_DB`` environment variable dreaming uses is accepted
    as a fallback. Never a silent default."""
    if db_arg:
        return Path(db_arg)
    env = os.environ.get("IMV_KNOWLEDGE_DB", "").strip()
    if env:
        return Path(env)
    raise WakingRefused(
        "no database given. Pass --db (or set IMV_KNOWLEDGE_DB).")
