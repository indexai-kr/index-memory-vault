"""Dreaming deep stage: per-chunk tier and approval proposals.

Ported from the dreaming batch scripts: the tier / approval proposal rules
(``run_imv_dreaming_batch_a.py``) and the tier-only proposal passes
(``run_imv_dreaming_batch_b.py``), generalized to the whole corpus.

Proposal rules:

- The evidence grade outranks everything: only Strong proposes
  ``approved``; Medium proposes ``in_review``; Weak (and policy / financial
  claims regardless of grade) proposes ``candidate``; no evidence proposes
  ``raw``.
- The search tier is proposed from taxonomy and retrieval purpose only and
  is never auto-linked to the approval state.
- A chunk with a previous review (a ``chunk_policy_events`` record) keeps
  its current tier and approval — ``previous_review_preserved``.
"""

from __future__ import annotations

import re


def tier_for(taxonomy: str, title: str) -> str:
    """Propose a search tier from taxonomy and title signals only."""
    if taxonomy == "raw_evidence":
        return "cold"
    if taxonomy == "business_financial_claim":
        return "review_only"
    if taxonomy == "archive_duplicate":
        return "cold"
    if taxonomy == "policy_contract_spec" and re.search(
            r"anchor|핵심|master|정본", title or "", re.I):
        return "primary"
    return "reference"


def approval_for(grade: str, taxonomy: str) -> str:
    """Propose an approval state from the evidence grade.

    Only Strong evidence proposes ``approved``. Everything else stays
    below the retrieval gate on purpose — promotion is a human waking
    decision, never a dreaming default.
    """
    if grade == "Strong":
        return "approved"
    if grade == "Medium":
        return "in_review"
    if grade == "Weak" or taxonomy in {"policy_contract_spec",
                                       "business_financial_claim"}:
        return "candidate"
    return "raw"


def run_deep(docs: list[dict], chunks: list[dict], reviewed_ids: set[int],
             doc_grades: dict[int, str],
             doc_taxonomies: dict[int, str]) -> list[dict]:
    """Propose tier/approval per current-version chunk. Read-only."""
    by_doc: dict[int, list[dict]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk["document_id"], []).append(chunk)

    plan = []
    for doc in docs:
        current = [c for c in by_doc.get(doc["id"], [])
                   if c["version_id"] == doc["current_version_id"]]
        taxonomy = doc_taxonomies[doc["id"]]
        grade = doc_grades[doc["id"]]
        proposed_tier = tier_for(taxonomy, doc["title"])
        proposed_approval = approval_for(grade, taxonomy)
        for chunk in current:
            preserved = chunk["id"] in reviewed_ids
            chunk_tier = (chunk["search_tier"] if preserved
                          else proposed_tier)
            chunk_approval = (chunk["approval_state"] if preserved
                              else proposed_approval)
            plan.append({
                "chunk_id": chunk["id"],
                "document_id": doc["id"],
                "version_id": chunk["version_id"],
                "content_hash": chunk["content_hash"],
                "current_search_tier": chunk["search_tier"],
                "current_approval_state": chunk["approval_state"],
                "proposed_search_tier": chunk_tier,
                "proposed_approval_state": chunk_approval,
                "approval_grade": grade,
                "change": ("YES" if (chunk["search_tier"],
                                     chunk["approval_state"])
                           != (chunk_tier, chunk_approval) else "NO"),
                "reason": ("previous_review_preserved" if preserved
                           else taxonomy),
            })
    return plan
