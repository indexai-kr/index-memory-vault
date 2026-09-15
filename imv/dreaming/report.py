"""Dreaming reports: the nine CSV outputs plus ``dream_report.json``.

Nine outputs (stages that produce them in parentheses):

1. ``lineage.csv`` — document family / version lineage (light)
2. ``evidence_registry.csv`` — per-document evidence grades (light)
3. ``strong_candidates.csv`` — the Strong subset (light)
4. ``non_approved_candidates.csv`` — latest non-Strong policy/design/financial docs (light)
5. ``conflict_candidates.csv`` — numeric/spec conflict candidates (rem)
6. ``duplicate_candidates.csv`` — duplicate / near-duplicate relations (rem)
7. ``chunk_plan.csv`` — per-chunk tier/approval proposals; the waking input (deep)
8. ``summary.md`` — aggregate counts (all)
9. ``apply_rollback_plan.md`` — apply/rollback command plan, a plan document
   not an execution record (all)

``dream_report.json`` records the stage, input chunk count, proposal
count, conflict count, the SHA-256 of the chunk plan, and the dreaming
invariants — so any consumer can verify what a dream run claimed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import DREAMING_INVARIANTS
from .db import load_corpus, open_readonly
from .deep import run_deep
from .light import normalize_family, run_light
from .rem import find_conflicts, find_duplicates

LINEAGE_COLS = ["family", "document_id", "title", "version_id",
                "version_no", "is_current", "created_at"]
EVIDENCE_COLS = ["evidence_id", "document_id", "title", "grade",
                 "signals", "decision"]
STRONG_COLS = ["evidence_id", "document_id", "title", "grade",
               "signals", "decision"]
NON_APPROVED_COLS = ["document_id", "title", "family", "taxonomy",
                     "current_version_id", "chunks", "approval_grade",
                     "current_states"]
CONFLICT_COLS = ["conflict_key", "type", "left_document_id", "left_locator",
                 "left_text", "right_document_id", "right_locator",
                 "right_text", "automatic_resolution", "review_state"]
DUPLICATE_COLS = ["left_id", "left_title", "right_id", "right_title",
                  "relation", "similarity", "auto_inherit_approval"]
PLAN_COLS = ["chunk_id", "document_id", "version_id", "content_hash",
             "current_search_tier", "current_approval_state",
             "proposed_search_tier", "proposed_approval_state",
             "approval_grade", "change", "reason"]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[dict], cols: list[str]) -> str:
    def esc(value):
        return str(value if value is not None else ""
                   ).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    lines.extend("| " + " | ".join(esc(r.get(c, "")) for c in cols) + " |"
                 for r in rows)
    return "\n".join(lines)


def run_dream(db_path: str | Path, out_dir: str | Path, stage: str = "all",
              run_id: str | None = None) -> dict:
    """Run the dreaming pipeline. Opens the DB read-only; writes only
    ``out_dir``. Returns the report dict (also written as JSON)."""
    from . import STAGES
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run_id = run_id or ("IMV-DREAM-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S"))

    con = open_readonly(db_path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        docs, chunks, reviewed = load_corpus(con)

        light = run_light(con, run_id, docs, chunks)
        doc_grades = {r["document_id"]: r["approval_grade"]
                      for r in light["doc_rows"]}
        doc_tax = {r["document_id"]: r["taxonomy"]
                   for r in light["doc_rows"]}

        plan = (run_deep(docs, chunks, reviewed, doc_grades, doc_tax)
                if stage in ("deep", "rem", "all") else [])
        duplicates, conflicts = [], []
        if stage in ("rem", "all"):
            duplicates = find_duplicates(docs, light["full_text"],
                                         normalize_family)
            docs_by_id = {d["id"]: d for d in docs}
            conflicts = find_conflicts(light["families"], docs_by_id,
                                       light["full_text"])

        strong = [r for r in light["evidence_rows"]
                  if r["grade"] == "Strong"]
        latest = [r for r in light["doc_rows"]
                  if r["approval_grade"] != "Strong" and r["taxonomy"] in {
                      "policy_contract_spec", "design_operations",
                      "business_financial_claim"}]

        write_csv(out / "lineage.csv", light["lineage_rows"], LINEAGE_COLS)
        write_csv(out / "evidence_registry.csv", light["evidence_rows"],
                  EVIDENCE_COLS)
        write_csv(out / "strong_candidates.csv", strong, STRONG_COLS)
        write_csv(out / "non_approved_candidates.csv", latest,
                  NON_APPROVED_COLS)
        if stage in ("deep", "rem", "all"):
            write_csv(out / "chunk_plan.csv", plan, PLAN_COLS)
        if stage in ("rem", "all"):
            write_csv(out / "conflict_candidates.csv", conflicts,
                      CONFLICT_COLS)
            write_csv(out / "duplicate_candidates.csv", duplicates,
                      DUPLICATE_COLS)

        plan_bytes = (out / "chunk_plan.csv").read_bytes() if (
            out / "chunk_plan.csv").exists() else b""
        plan_sha256 = (hashlib.sha256(plan_bytes).hexdigest()
                       if plan_bytes else None)
        changes = sum(1 for r in plan if r["change"] == "YES")
        counts_tax = Counter(r["taxonomy"] for r in light["doc_rows"])
        counts_grade = Counter(r["approval_grade"]
                               for r in light["doc_rows"])

        if stage == "all":
            (out / "summary.md").write_text(
                "# Dreaming summary\n\n"
                f"- run: {run_id}\n- stage: {stage}\n"
                f"- documents: {len(docs)}\n"
                f"- input chunks: {len(chunks)}\n"
                f"- proposed changes: {changes}\n"
                f"- duplicates: {len(duplicates)}\n"
                f"- conflicts: {len(conflicts)}\n"
                f"- database writes: 0\n\n"
                "## Taxonomy\n\n" + _md_table(
                    [{"taxonomy": k, "documents": v}
                     for k, v in sorted(counts_tax.items())],
                    ["taxonomy", "documents"]) + "\n\n"
                "## Evidence grades\n\n" + _md_table(
                    [{"grade": g, "documents": counts_grade.get(g, 0)}
                     for g in ("Strong", "Medium", "Weak", "None")],
                    ["grade", "documents"]) + "\n",
                encoding="utf-8")
            (out / "apply_rollback_plan.md").write_text(
                "# Apply / rollback command plan\n\n"
                "> A plan document, not an execution record. "
                "Do not execute before waking approval.\n\n"
                "## Before applying\n\n"
                "1. Back up the database and record its SHA-256\n"
                "2. Check `PRAGMA integrity_check`\n"
                "3. Re-verify content hashes in chunk_plan.csv\n"
                "4. Fix the master approval id as `authorization_ref`\n\n"
                "## Applying (human `imv waking apply` only)\n\n"
                "1. One transaction per batch\n"
                "2. Change only hash-matching approved rows\n"
                "3. Record before/after, actor, reason, authorization_ref "
                "as audit events\n"
                "4. Roll back the whole batch on a single mismatch\n\n"
                "## Rollback\n\n"
                "1. Reverse-apply from the pre-change snapshot\n"
                "2. Never delete audit events; append rollback events\n"
                "3. Re-verify hashes, row counts, search policy, integrity\n",
                encoding="utf-8")

        report = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": str(db_path),
            "database_mode": "read_only",
            "integrity_check": integrity,
            "stage": stage,
            "active_documents": len(docs),
            "input_chunks": len(chunks),
            "proposals": len(plan),
            "proposed_changes": changes,
            "duplicates": len(duplicates),
            "conflicts": len(conflicts),
            "plan_sha256": plan_sha256,
            "taxonomy": dict(counts_tax),
            "grades": {g: counts_grade.get(g, 0)
                       for g in ("Strong", "Medium", "Weak", "None")},
            **DREAMING_INVARIANTS,
        }
        (out / "dream_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return report
    finally:
        con.close()
