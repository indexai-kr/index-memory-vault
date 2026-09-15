"""Build the tiny synthetic knowledge DB used by test_dreaming/test_waking.

Regenerate with:  python tests/fixtures/make_knowledge_small.py
Output:           tests/fixtures/knowledge_small.db  (committed; *.db
                  exception in .gitignore covers exactly this file)

All content is synthetic. Three documents, ten chunks:

- doc 1 "정본 승인 원장" (2 chunks): Strong evidence — explicit approval
  markers plus all four corroborating signals.
- doc 2 "pixel_log_2026.csv" (4 chunks): raw evidence, no approval
  markers. Chunk 3 already has a chunk_policy_events record pinning it to
  ``reference`` so dreaming must preserve it (previous_review_preserved).
- doc 3 "설계 초안" (4 chunks): Medium evidence — explicit marker plus
  two signals.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    path TEXT,
    mime_type TEXT,
    content_hash TEXT,
    current_version_id INTEGER,
    created_at TEXT,
    modified_at TEXT,
    ingested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE TABLE document_versions (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    source_modified_at TEXT,
    content_hash TEXT NOT NULL,
    raw_text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    version_id INTEGER NOT NULL,
    chunk_no INTEGER NOT NULL,
    section TEXT,
    page INTEGER,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    search_tier TEXT NOT NULL DEFAULT 'reference'
        CHECK(search_tier IN ('primary','reference','cold','review_only')),
    approval_state TEXT NOT NULL DEFAULT 'raw'
        CHECK(approval_state IN
            ('raw','candidate','in_review','approved','rejected','superseded'))
);
CREATE TABLE chunk_policy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL,
    old_search_tier TEXT NOT NULL,
    new_search_tier TEXT NOT NULL,
    old_approval_state TEXT NOT NULL,
    new_approval_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    authorization_ref TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

TS = "2026-09-10T00:00:00Z"

DOCS = [
    # id, source_type, source_id, title, identity_key, path, version_id
    (1, "gdrive", "drive-s1", "정본 승인 원장", "key-s1",
     "/docs/ledger.md", 101),
    (2, "local", "csv-p1", "pixel_log_2026.csv", "key-s2",
     "/data/pixel_log_2026.csv", 102),
    (3, "gdrive", "drive-s3", "설계 초안", "key-s3",
     "/docs/draft.md", 103),
]

STRONG_A = ("승인 완료 기록. approved_by: master, status: approved, "
            "version: v2, 2026-09-01, 결재번호: A-1, content_hash: abc.")
STRONG_B = ("후속 승인 조항. authorization_ref: AUTH-9, 승인자: master, "
            "버전: v2, 2026-09-02.")
RAW_CSV = ("pixel_log,observation events,row%d,actor_20,histogram "
           "cycle data" % 0)
MEDIUM = ("결재 완료. 버전: v1, 2026-08-01. 세부 수치는 확정 전이라 "
          "승인 근거로 쓰지 않는다.")

CHUNKS = [
    # id, doc, version, no, section, content, tier, approval
    (1, 1, 101, 1, "S1", STRONG_A, "reference", "raw"),
    (2, 1, 101, 2, "S2", STRONG_B, "reference", "raw"),
    (3, 2, 102, 1, "L1", RAW_CSV.replace("row0", "row1"),
     "reference", "raw"),
    (4, 2, 102, 2, "L2", RAW_CSV.replace("row0", "row2"),
     "reference", "raw"),
    (5, 2, 102, 3, "L3", RAW_CSV.replace("row0", "row3"),
     "reference", "raw"),
    (6, 2, 102, 4, "L4", RAW_CSV.replace("row0", "row4"),
     "reference", "raw"),
    (7, 3, 103, 1, "D1", MEDIUM + " 첫째 문단.", "reference", "raw"),
    (8, 3, 103, 2, "D2", MEDIUM + " 둘째 문단.", "reference", "raw"),
    (9, 3, 103, 3, "D3", MEDIUM + " 셋째 문단.", "reference", "raw"),
    (10, 3, 103, 4, "D4", MEDIUM + " 넷째 문단.", "reference", "raw"),
]


def build(path: Path) -> None:
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for doc_id, stype, sid, title, key, doc_path, vid in DOCS:
        db.execute(
            "INSERT INTO documents (id, source_type, source_id, title,"
            " identity_key, path, content_hash, current_version_id,"
            " created_at, ingested_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')",
            (doc_id, stype, sid, title, key, doc_path, f"dochash-{doc_id}",
             vid, TS, TS))
        db.execute(
            "INSERT INTO document_versions (id, document_id, version_no,"
            " content_hash, raw_text, created_at)"
            " VALUES (?, ?, 1, ?, ?, ?)",
            (vid, doc_id, f"vhash-{vid}", f"raw-{doc_id}", TS))
    for cid, doc, vid, no, sec, content, tier, approval in CHUNKS:
        db.execute(
            "INSERT INTO chunks (id, document_id, version_id, chunk_no,"
            " section, page, content, content_hash, created_at,"
            " search_tier, approval_state)"
            " VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (cid, doc, vid, no, sec, content, f"hash-{cid}", TS,
             tier, approval))
    # Chunk 3 was reviewed before: pinned to reference/raw. Dreaming must
    # preserve it even though raw_evidence would otherwise propose cold.
    db.execute(
        "INSERT INTO chunk_policy_events (chunk_id, old_search_tier,"
        " new_search_tier, old_approval_state, new_approval_state,"
        " actor, reason, authorization_ref, created_at)"
        " VALUES (3, 'reference', 'reference', 'raw', 'raw',"
        " 'human:fixture', 'manual review pins csv header chunk',"
        " 'fixture', ?)", (TS,))
    db.commit()
    db.close()


if __name__ == "__main__":
    out = Path(__file__).with_name("knowledge_small.db")
    build(out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
