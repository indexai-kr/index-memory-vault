"""Dreaming light stage: families, taxonomy, evidence grades.

Ported from the dreaming batch scripts (``run_imv_dreaming_batch_a.py``):
family normalization, taxonomy classification, and the Strong / Medium /
Weak / None pre-approval evidence grading. Generalized from the original
hard-coded 30-document ID windows to the whole ACTIVE corpus; the scoring
rules themselves are unchanged.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

GRADES = ("Strong", "Medium", "Weak", "None")

TAXONOMIES = (
    "approval_audit_record",
    "business_financial_claim",
    "archive_duplicate",
    "raw_evidence",
    "implementation_test",
    "policy_contract_spec",
    "aggregate_analysis",
    "design_operations",
)


def normalize_family(title: str) -> str:
    """Map near-identical titles (versions, copies, backups) to one family."""
    s = (title or "").lower().strip()
    s = re.sub(r"\.(md|txt|docx?|pdf|jsx?|tsx?|py|csv|jsonl?|ya?ml)$", "", s)
    s = re.sub(r"\b(v(?:er(?:sion)?)?[\s._-]*\d+(?:[._-]\d+)*)\b", "", s)
    s = re.sub(r"\b(final|draft|copy|backup|archive|old|new)\b", "", s)
    s = re.sub(r"\((?:\d+|copy|복사본)\)", "", s)
    s = re.sub(r"(?:정본|초안|최종|사본|복사본|백업|아카이브)", "", s)
    return re.sub(r"[^0-9a-z가-힣]+", " ", s).strip() or s


def classify_taxonomy(title: str, path: str | None,
                      mime: str | None = None) -> str:
    """Classify a document by name/path signals. Heuristic, documented."""
    text = f"{title or ''} {path or ''}".lower()
    ext = Path(path or title or "").suffix.lower()
    if any(k in text for k in ("approval", "audit", "승인", "결재", "ledger_event")):
        return "approval_audit_record"
    if any(k in text for k in ("proposal", "kpi", "revenue", "forecast", "제안", "매출", "전망")):
        return "business_financial_claim"
    if any(k in text for k in ("archive", "backup", "copy", "old", "복사", "사본", "백업")):
        return "archive_duplicate"
    if ext == ".csv" or any(k in text for k in ("event", "pixel", "observer", "actor_", "cycle", "histogram")):
        return "raw_evidence"
    if ext in {".py", ".js", ".jsx", ".ts", ".tsx"} or "test" in text:
        return "implementation_test"
    if any(k in text for k in ("spec", "contract", "policy", "architecture", "명세", "계약", "정책", "아키텍처")):
        return "policy_contract_spec"
    if any(k in text for k in ("summary", "report", "analysis", "요약", "보고", "분석")):
        return "aggregate_analysis"
    return "design_operations"


_EXPLICIT_APPROVAL = re.compile(
    r"approved_by\s*[:=]|approval[_ -]?id\s*[:=]|authorization_ref\s*[:=]|"
    r"status\s*[:=]\s*approved|승인\s*(?:완료|됨)|결재\s*(?:완료|승인)|"
    r"승인자\s*[:=]")


def grade_evidence(content: str) -> tuple[str, list[str]]:
    """Grade pre-approval evidence: Strong / Medium / Weak / None.

    Mentions that merely *discuss* approval are not approval records: an
    explicit marker (approved_by, approval_id, authorization_ref,
    status=approved, 승인완료/결재완료, …) is required before any grade
    above None. Of the four corroborating signals (time, version, actor,
    reference), all four make Strong, two or more make Medium, fewer
    make Weak.
    """
    low = (content or "").lower()
    if not _EXPLICIT_APPROVAL.search(low):
        return "None", []
    signals = []
    if re.search(r"\b20\d{2}[-./]\d{1,2}[-./]\d{1,2}\b|\b20\d{2}-\d{2}-\d{2}t", low):
        signals.append("time")
    if re.search(r"\bv\d+(?:\.\d+)*\b|version\s*[:=]?\s*\d+|버전\s*[:=]?", low):
        signals.append("version")
    if re.search(r"approver|approved_by|actor|master|관리자|승인자|책임자", low):
        signals.append("actor")
    if re.search(r"authorization_ref|approval[_ -]?id|결재번호|승인번호|sha-?256|content_hash", low):
        signals.append("reference")
    if all(s in signals for s in ("time", "version", "actor", "reference")):
        return "Strong", signals
    if len(signals) >= 2:
        return "Medium", signals
    return "Weak", signals


def run_light(con: sqlite3.Connection, run_id: str,
              docs: list[dict], chunks: list[dict]) -> dict:
    """Light pass over the corpus. Read-only; returns plain row dicts."""
    by_doc: dict[int, list[dict]] = defaultdict(list)
    for chunk in chunks:
        by_doc[chunk["document_id"]].append(chunk)

    families: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        families[normalize_family(doc["title"])].append(doc)

    full_text: dict[int, str] = {}
    for doc in docs:
        current = [c for c in by_doc.get(doc["id"], [])
                   if c["version_id"] == doc["current_version_id"]]
        full_text[doc["id"]] = "\n".join(c["content"] or "" for c in current)

    doc_rows, evidence_rows, lineage_rows = [], [], []
    for doc in docs:
        current = [c for c in by_doc.get(doc["id"], [])
                   if c["version_id"] == doc["current_version_id"]]
        tax = classify_taxonomy(doc["title"], doc.get("path"),
                                doc.get("mime_type"))
        grade, signals = grade_evidence(full_text[doc["id"]])
        states: dict[tuple[str | None, str | None], int] = defaultdict(int)
        for c in current:
            states[(c["search_tier"], c["approval_state"])] += 1
        doc_rows.append({
            "document_id": doc["id"],
            "title": doc["title"],
            "family": normalize_family(doc["title"]),
            "taxonomy": tax,
            "current_version_id": doc["current_version_id"],
            "chunks": len(current),
            "approval_grade": grade,
            "current_states": "; ".join(
                f"{tier}/{state}:{n}" for (tier, state), n in states.items()),
        })
        if grade == "Strong":
            decision = "migration_candidate"
        elif grade in {"Medium", "Weak"}:
            decision = "manual_review"
        else:
            decision = "no_approval_evidence"
        evidence_rows.append({
            "evidence_id": f"{run_id}-D{doc['id']}",
            "document_id": doc["id"],
            "title": doc["title"],
            "grade": grade,
            "signals": ",".join(signals),
            "decision": decision,
        })
        for v in con.execute(
                "SELECT id, version_no, created_at FROM document_versions "
                "WHERE document_id=? ORDER BY id", (doc["id"],)):
            lineage_rows.append({
                "family": normalize_family(doc["title"]),
                "document_id": doc["id"],
                "title": doc["title"],
                "version_id": v["id"],
                "version_no": v["version_no"],
                "is_current": ("YES" if v["id"] == doc["current_version_id"]
                               else "NO"),
                "created_at": v["created_at"],
            })

    return {
        "doc_rows": doc_rows,
        "evidence_rows": evidence_rows,
        "lineage_rows": lineage_rows,
        "full_text": full_text,
        "families": {k: [d["id"] for d in v]
                     for k, v in families.items()},
    }
