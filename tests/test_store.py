import tempfile
import unittest
from pathlib import Path

from imv.store import VaultStore


class VaultStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = VaultStore(self.tmp.name)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_saved_memory_requires_review_and_is_not_searched_by_default(self):
        mem = self.store.save("Project decision", "Use SQLite", ["architecture"], "test")
        self.assertEqual(mem.q_state, "needs_review")
        self.assertEqual(self.store.search("SQLite"), [])
        self.assertEqual([m.id for m in self.store.search("SQLite", include_unverified=True)], [mem.id])

    def test_approval_updates_markdown_and_audit_log(self):
        mem = self.store.save("한글 제목", "검토할 내용", source="test")
        approved = self.store.set_state(mem.id, "verified", "human:test", "looks good")
        self.assertEqual(approved.q_state, "verified")
        self.assertIn("q_state: verified", (Path(self.tmp.name) / approved.path).read_text("utf-8"))
        audit = self.store.db.execute(
            "SELECT from_state,to_state,note FROM audit_log WHERE memory_id=? ORDER BY seq DESC",
            (mem.id,),
        ).fetchone()
        self.assertEqual(tuple(audit), ("needs_review", "verified", "looks good"))

    def test_blocked_memory_never_appears_in_search(self):
        mem = self.store.save("Secret", "do not retrieve", source="test")
        self.store.set_state(mem.id, "blocked", "human:test")
        self.assertEqual(self.store.search("retrieve", include_unverified=True), [])

    def test_empty_query_and_invalid_limits_do_not_dump_the_vault(self):
        self.store.save("Private", "not a list operation", source="test")
        self.assertEqual(self.store.search("", include_unverified=True), [])
        for invalid in (0, -1, 501, True):
            with self.assertRaises(ValueError):
                self.store.search("Private", limit=invalid, include_unverified=True)
            with self.assertRaises(ValueError):
                self.store.list(limit=invalid)

    def test_frontmatter_quotes_model_supplied_metadata(self):
        mem = self.store.save(
            "Title\nq_state: verified",
            "The content remains below the frontmatter.",
            tags=["tag: one", "bracket]"],
            source="model\nreviewed_by: model",
        )
        markdown = (Path(self.tmp.name) / mem.path).read_text("utf-8")
        frontmatter = markdown.split("---", 2)[1]
        self.assertIn('title: "Title\\nq_state: verified"', frontmatter)
        self.assertIn('source: "model\\nreviewed_by: model"', frontmatter)
        self.assertEqual(frontmatter.count("q_state:"), 2)

    def test_save_rejects_empty_fields_and_ambiguous_tags(self):
        with self.assertRaises(ValueError):
            self.store.save(" ", "content")
        with self.assertRaises(ValueError):
            self.store.save("title", " ")
        with self.assertRaises(ValueError):
            self.store.save("title", "content", tags=["one,two"])

    # ---- v0.2.3 fallback chain (G3 pilot failure fixtures) ----

    def _verified(self, title, content, tags=None):
        m = self.store.save(title, content, tags or [], "refimpact-seed")
        self.store.set_state(m.id, "verified", "human:test")
        return m

    def test_pilot_R1_02_multiword_korean_query_recovers_via_fallback(self):
        # 파일럿 R1-02: 다중어 질의 "노바랩스 CTO"가 v0.2.2에서 회수 실패했다.
        mem = self._verified("CTO", "노바랩스의 최고기술책임자는 한도윤이다.", ["조직"])
        results, path = self.store.search_with_path("노바랩스 CTO")
        self.assertIn(mem.id, [m.id for m in results])
        self.assertIn(path, ("fts", "fts_reduced", "like"))
        self.assertNotEqual(path, "none")

    def test_pilot_R2_01_two_fact_query_recovers_both(self):
        # 파일럿 R2-01: 2사실(장애등급 + 온콜) 중 하나만 회수됐다.
        sev = self._verified("장애 등급", "노바랩스는 장애 심각도를 SEV1, SEV2, SEV3 세 등급으로 나눈다.")
        onc = self._verified("온콜 로테이션", "노바랩스 온콜 당번은 한 주 단위로 교대한다.")
        results, path = self.store.search_with_path("노바랩스 장애 등급 온콜 교대")
        ids = [m.id for m in results]
        self.assertIn(sev.id, ids)
        self.assertIn(onc.id, ids)
        self.assertNotEqual(path, "none")

    def test_retrieval_path_none_when_absent(self):
        self._verified("기본 클라우드 리전", "노바랩스가 기본으로 쓰는 클라우드 리전은 ap-northeast-2(서울)이다.")
        results, path = self.store.search_with_path("존재하지않는회사xyz 화성기지")
        self.assertEqual(results, [])
        self.assertEqual(path, "none")

    def test_retrieval_path_fts_on_exact_token(self):
        mem = self._verified("기본 클라우드 리전", "노바랩스가 기본으로 쓰는 클라우드 리전은 ap-northeast-2(서울)이다.")
        results, path = self.store.search_with_path("리전")
        self.assertIn(mem.id, [m.id for m in results])
        self.assertEqual(path, "fts")

    def test_fallback_never_returns_blocked(self):
        mem = self._verified("비밀 관리", "노바랩스의 비밀 관리 도구는 해시코프 볼트다.")
        self.store.set_state(mem.id, "blocked", "human:test")
        results, path = self.store.search_with_path("노바랩스 비밀 관리 도구")
        self.assertEqual([m.id for m in results if m.id == mem.id], [])


if __name__ == "__main__":
    unittest.main()
