"""Dreaming REM stage: duplicate and conflict candidate detection.

Ported from ``run_imv_dreaming_batch_c.py`` / ``run_imv_dreaming_batch_d.py``.

Both detectors only *propose candidates for human review*:

- duplicates always carry ``auto_inherit_approval=NO`` — approval is never
  inherited automatically (``duplicate_inherits_approval=False``);
- conflicts always carry ``automatic_resolution=PROHIBITED`` — resolution
  is a human waking action (``conflict_auto_resolve=False``).
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

# Full texts are capped before comparison, as in the original scripts: this
# is a candidate sieve, not a proof.
COMPARE_CHARS = 12000
NEAR_DUPLICATE_THRESHOLD = 0.88
MIN_TEXT_CHARS = 300
MIN_TOKEN_SET = 20


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[0-9a-z가-힣]{2,}", text.lower()))


def find_duplicates(docs: list[dict], full_text: dict[int, str],
                    normalize_family) -> list[dict]:
    """Detect exact-hash, same-family, and near-duplicate relations."""
    rows = []
    seen: set[tuple[int, int]] = set()
    for doc in docs:
        for other in docs:
            if doc["id"] == other["id"]:
                continue
            pair = tuple(sorted((doc["id"], other["id"])))
            if pair in seen:
                continue
            seen.add(pair)
            relation, score = None, 0.0
            if doc.get("content_hash") and (doc["content_hash"]
                                            == other.get("content_hash")):
                relation, score = "exact_document_hash", 1.0
            elif normalize_family(doc["title"]) == normalize_family(
                    other["title"]):
                relation, score = "same_document_family", 1.0
            else:
                a = re.sub(r"\s+", " ",
                           full_text.get(doc["id"], "")[:COMPARE_CHARS]).strip()
                b = re.sub(r"\s+", " ",
                           full_text.get(other["id"], "")[:COMPARE_CHARS]).strip()
                if min(len(a), len(b)) >= MIN_TEXT_CHARS:
                    aset, bset = _token_set(a), _token_set(b)
                    if min(len(aset), len(bset)) > MIN_TOKEN_SET:
                        score = len(aset & bset) / max(1, len(aset | bset))
                        if score >= NEAR_DUPLICATE_THRESHOLD:
                            relation = "near_duplicate_token_set"
            if relation:
                rows.append({
                    "left_id": doc["id"],
                    "left_title": doc["title"],
                    "right_id": other["id"],
                    "right_title": other["title"],
                    "relation": relation,
                    "similarity": f"{score:.3f}",
                    # Never auto-inherit: a duplicate's approval state is
                    # decided by a human, row by row, at waking time.
                    "auto_inherit_approval": "NO",
                })
    return rows


_SPEC_LINE = re.compile(
    r"\d|must|shall|required|default|enum|해야|금지|기본값|필수", re.I)


def find_conflicts(families: dict[str, list[int]],
                   docs_by_id: dict[int, dict],
                   full_text: dict[int, str]) -> list[dict]:
    """Detect same-family lines that carry differing numeric/spec claims.

    Lines are grouped by a number-masked signature; a signature with two
    different surface texts across family members becomes a candidate.
    Detection only — ``automatic_resolution`` is always PROHIBITED.
    """
    rows = []
    for _family, member_ids in families.items():
        if len(member_ids) < 2:
            continue
        lines_by_sig: dict[str, list[tuple[dict, int, str]]] = defaultdict(list)
        for doc_id in member_ids:
            doc = docs_by_id.get(doc_id)
            if doc is None:
                continue
            for line_no, line in enumerate(
                    full_text.get(doc_id, "").splitlines(), 1):
                compact = re.sub(r"\s+", " ", line).strip()
                if len(compact) < 12 or not _SPEC_LINE.search(compact):
                    continue
                sig = re.sub(r"\d+(?:\.\d+)?%?", "<NUM>", compact.lower())
                sig = re.sub(r"[^a-z가-힣<>]+", " ", sig).strip()[:180]
                lines_by_sig[sig].append((doc, line_no, compact))
        for sig, items in lines_by_sig.items():
            if len(items) < 2 or len({text for _, _, text in items}) < 2:
                continue
            left, right = items[0], items[1]
            rows.append({
                "conflict_key": hashlib.sha256(sig.encode()).hexdigest()[:12],
                "type": "numeric_or_spec_candidate",
                "left_document_id": left[0]["id"],
                "left_locator": f"line {left[1]}",
                "left_text": left[2][:240],
                "right_document_id": right[0]["id"],
                "right_locator": f"line {right[1]}",
                "right_text": right[2][:240],
                # Detection is where automation stops.
                "automatic_resolution": "PROHIBITED",
                "review_state": "in_review_candidate",
            })
    return rows
