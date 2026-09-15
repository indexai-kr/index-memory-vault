import sqlite3
import tempfile
import unittest
from pathlib import Path

from imv.knowledge import KnowledgeStore, KnowledgeUnavailable

SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    source_modified_at TEXT,
    content_hash TEXT NOT NULL,
    raw_text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""

# (chunk_no, content, approval_state, search_tier)
CHUNKS = [
    (1, "Safe hit 조건은 Q-Cache 정책 버전이 일치할 때만 성립한다.", "approved", "primary"),
    (2, "TTL 감쇠 모델은 신선도에 따라 가중치를 낮춘다.", "approved", "reference"),
    (3, "미승인 초안: Q-Cache 를 무조건 신뢰한다.", "raw", "reference"),
    (4, "승인됐지만 검색 대상이 아닌 냉각 청크. Q-Cache 관련.", "approved", "cold"),
    (5, "검토 중인 Q-Cache 메모.", "in_review", "review_only"),
]

RETRIEVABLE_CHUNK_NOS = {1, 2}


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "imv_knowledge.db"
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO documents (id, source_type, source_id, title, identity_key,"
            " path, ingested_at, status) VALUES"
            " (7, 'gdrive', 'drive-abc', '통합설계안', 'key-1',"
            " '/docs/design.md', '2026-09-10T00:00:00Z', 'ACTIVE')"
        )
        db.execute(
            "INSERT INTO document_versions (id, document_id, version_no,"
            " content_hash, created_at) VALUES"
            " (3, 7, 2, 'vhash', '2026-09-10T00:00:00Z')"
        )
        for chunk_no, content, approval, tier in CHUNKS:
            db.execute(
                "INSERT INTO chunks (document_id, version_id, chunk_no, section,"
                " page, content, content_hash, created_at, search_tier,"
                " approval_state) VALUES (7, 3, ?, 'S1', 4, ?, ?,"
                " '2026-09-10T00:00:00Z', ?, ?)",
                (chunk_no, content, f"hash-{chunk_no}", tier, approval),
            )
        db.commit()
        db.close()
        self.store = KnowledgeStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _chunk_id(self, chunk_no: int) -> int:
        db = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        row = db.execute(
            "SELECT id FROM chunks WHERE chunk_no = ?", (chunk_no,)).fetchone()
        db.close()
        return row[0]

    # ---------- policy gate ----------

    def test_search_serves_only_approved_and_retrievable_tiers(self):
        hits, path, withheld = self.store.search("Q-Cache")
        self.assertEqual(path, "like_and")
        self.assertEqual({h.chunk_no for h in hits}, {1})
        for hit in hits:
            self.assertEqual(hit.approval_state, "approved")
            self.assertIn(hit.search_tier, ("primary", "reference"))
        # 3 unretrievable chunks also contain the term; the gate withheld them.
        self.assertEqual(withheld, 3)

    def test_approved_but_cold_chunk_is_never_served(self):
        """search_tier alone does not grant access, and neither does approval."""
        hits, _, _ = self.store.search("냉각")
        self.assertEqual(hits, [])
        self.assertIsNone(self.store.get(self._chunk_id(4)))

    def test_known_id_does_not_bypass_the_gate(self):
        for blocked in (3, 4, 5):
            with self.subTest(chunk_no=blocked):
                self.assertIsNone(self.store.get(self._chunk_id(blocked)))
        served = self.store.get(self._chunk_id(1))
        self.assertIsNotNone(served)
        self.assertEqual(served.chunk_no, 1)

    def test_no_parameter_can_widen_the_gate(self):
        self.assertNotIn("include_unapproved", KnowledgeStore.search.__code__.co_varnames)
        self.assertEqual(KnowledgeStore.get.__code__.co_argcount, 2)

    # ---------- citation traceability ----------

    def test_result_carries_full_citation_coordinate(self):
        hits, _, _ = self.store.search("Safe hit")
        self.assertEqual(len(hits), 1)
        hit = hits[0].public(snippet_for="Safe hit")
        self.assertEqual(hit["chunk_id"], self._chunk_id(1))
        self.assertEqual(hit["document_id"], 7)
        self.assertEqual(hit["version_id"], 3)
        self.assertEqual(hit["version_no"], 2)
        self.assertEqual(hit["chunk_no"], 1)
        self.assertEqual(hit["section"], "S1")
        self.assertEqual(hit["page"], 4)
        self.assertEqual(hit["document_title"], "통합설계안")
        self.assertEqual(hit["document_status"], "ACTIVE")
        self.assertEqual(hit["source_type"], "gdrive")
        self.assertEqual(hit["source_id"], "drive-abc")
        self.assertEqual(hit["source_path"], "/docs/design.md")
        self.assertEqual(hit["content_hash"], "hash-1")

    def test_full_text_is_available_by_id_without_truncation(self):
        chunk = self.store.get(self._chunk_id(1))
        rendered = chunk.public(max_chars=100_000)
        self.assertEqual(rendered["content"], CHUNKS[0][1])
        self.assertFalse(rendered["truncated"])

    def test_excerpt_windows_around_the_match_not_the_head_of_the_chunk(self):
        """A citation must show why the chunk matched. The full query never
        appears verbatim, so the window has to key off individual terms."""
        chunk = self.store.get(self._chunk_id(1))
        chunk.content = ("서두 " * 400) + "결정적근거 여기" + (" 말미" * 400)
        rendered = chunk.public(snippet_for="결정적근거 는 무엇", max_chars=120)
        self.assertIn("결정적근거", rendered["content"])
        self.assertTrue(rendered["truncated"])

    def test_excerpt_falls_back_to_head_when_no_term_is_present(self):
        chunk = self.store.get(self._chunk_id(1))
        chunk.content = "머리말" + ("본문 " * 500)
        rendered = chunk.public(snippet_for="전혀없는용어zzz", max_chars=60)
        self.assertTrue(rendered["content"].startswith("머리말"))

    def test_long_content_is_marked_truncated_rather_than_silently_cut(self):
        chunk = self.store.get(self._chunk_id(1))
        rendered = chunk.public(max_chars=10)
        self.assertTrue(rendered["truncated"])
        self.assertEqual(rendered["content_chars"], len(CHUNKS[0][1]))

    # ---------- honesty when there is nothing to serve ----------

    def test_absent_evidence_returns_empty_not_invented_content(self):
        hits, path, withheld = self.store.search("존재하지않는용어xyzzy")
        self.assertEqual(hits, [])
        self.assertEqual(path, "none")
        self.assertEqual(withheld, 0)

    def test_withheld_count_distinguishes_blocked_from_absent(self):
        _, _, blocked = self.store.search("초안")
        self.assertEqual(blocked, 1)
        _, _, absent = self.store.search("존재하지않는용어xyzzy")
        self.assertEqual(absent, 0)

    def test_or_fallback_runs_only_after_and_finds_nothing(self):
        hits, path, _ = self.store.search("Safe hit TTL 감쇠")
        self.assertEqual(path, "like_or")
        self.assertEqual({h.chunk_no for h in hits}, {1, 2})

    def test_missing_database_raises_rather_than_reporting_no_evidence(self):
        missing = Path(self.tmp.name) / "not_here.db"
        with self.assertRaises(KnowledgeUnavailable):
            KnowledgeStore(missing)

    def test_non_knowledge_database_raises_rather_than_reporting_no_evidence(self):
        wrong = Path(self.tmp.name) / "wrong.db"
        db = sqlite3.connect(wrong)
        db.execute("CREATE TABLE memories (id TEXT)")
        db.commit()
        db.close()
        with self.assertRaises(KnowledgeUnavailable):
            KnowledgeStore(wrong)

    # ---------- input handling ----------

    def test_empty_query_does_not_dump_the_corpus(self):
        self.assertEqual(self.store.search("   "), ([], "none", 0))

    def test_stopword_only_query_matches_nothing_rather_than_everything(self):
        """Bare particles carry no discriminative value; falling back to them
        would serve arbitrary chunks that merely contain '은'."""
        self.assertEqual(self.store.search("무엇 은 는"), ([], "none", 0))

    def test_single_character_term_is_still_searchable(self):
        """A one-character CJK token may be the entire query."""
        hits, path, _ = self.store.search("때")
        self.assertEqual(path, "like_and")
        self.assertEqual({h.chunk_no for h in hits}, {1})

    def test_wildcards_in_query_do_not_match_everything(self):
        hits, _, _ = self.store.search("%")
        self.assertEqual(hits, [])

    def test_invalid_limits_are_rejected(self):
        for bad in (0, -1, 101, True, "5"):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    self.store.search("Q-Cache", limit=bad)

    def test_non_integer_chunk_id_is_rejected(self):
        for bad in ("1", True, 1.0):
            with self.subTest(chunk_id=bad):
                with self.assertRaises(ValueError):
                    self.store.get(bad)

    # ---------- status reporting ----------

    def test_policy_snapshot_counts_are_measured_not_configured(self):
        snap = self.store.policy_snapshot()
        self.assertEqual(snap["chunks_total"], len(CHUNKS))
        self.assertEqual(snap["chunks_retrievable"], len(RETRIEVABLE_CHUNK_NOS))
        self.assertEqual(snap["documents_retrievable"], 1)
        self.assertIn("approval_state=approved", snap["gate"])

    def test_store_opens_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.store.db.execute("UPDATE chunks SET approval_state='approved'")


if __name__ == "__main__":
    unittest.main()
