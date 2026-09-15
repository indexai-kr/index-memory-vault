import csv
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from imv.dreaming import DREAMING_INVARIANTS
from imv.dreaming.db import open_readonly, resolve_db_path, resolve_out_dir
from imv.dreaming.light import (classify_taxonomy, grade_evidence,
                                normalize_family)
from imv.dreaming.report import run_dream
from imv.knowledge import KnowledgeUnavailable

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_small.db"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DreamingReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "knowledge_small.db"
        shutil.copy(FIXTURE, self.work)
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dream_all_leaves_the_database_bit_identical(self):
        before = sha256(self.work)
        report = run_dream(self.work, self.out, stage="all",
                           run_id="TEST-DREAM")
        self.assertEqual(sha256(self.work), before)
        self.assertEqual(report["database_mode"], "read_only")
        self.assertFalse(report["waking_executed"])

    def test_dream_report_counts_are_measured(self):
        report = run_dream(self.work, self.out, stage="all",
                           run_id="TEST-DREAM")
        self.assertEqual(report["stage"], "all")
        self.assertEqual(report["active_documents"], 3)
        self.assertEqual(report["input_chunks"], 10)
        self.assertEqual(report["proposals"], 10)
        # 9 of 10 chunks change; chunk 3 is preserved by previous review.
        self.assertEqual(report["proposed_changes"], 9)
        self.assertEqual(report["grades"],
                         {"Strong": 1, "Medium": 1, "Weak": 0, "None": 1})
        self.assertIn("plan_sha256", report)
        self.assertIsNotNone(report["plan_sha256"])
        for key, value in DREAMING_INVARIANTS.items():
            self.assertEqual(report[key], value)

    def test_nine_csv_outputs_plus_report(self):
        run_dream(self.work, self.out, stage="all", run_id="TEST-DREAM")
        names = {p.name for p in self.out.iterdir()}
        self.assertEqual(names, {
            "lineage.csv", "evidence_registry.csv",
            "strong_candidates.csv", "non_approved_candidates.csv",
            "conflict_candidates.csv", "duplicate_candidates.csv",
            "chunk_plan.csv", "summary.md", "apply_rollback_plan.md",
            "dream_report.json",
        })

    def test_previous_review_is_preserved_not_overwritten(self):
        run_dream(self.work, self.out, stage="all", run_id="TEST-DREAM")
        with (self.out / "chunk_plan.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            plan = {r["chunk_id"]: r for r in csv.DictReader(handle)}
        row = plan["3"]
        self.assertEqual(row["proposed_search_tier"], "reference")
        self.assertEqual(row["proposed_approval_state"], "raw")
        self.assertEqual(row["change"], "NO")
        self.assertEqual(row["reason"], "previous_review_preserved")

    def test_strong_doc_proposes_approved_others_do_not(self):
        run_dream(self.work, self.out, stage="all", run_id="TEST-DREAM")
        with (self.out / "chunk_plan.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            plan = {r["chunk_id"]: r for r in csv.DictReader(handle)}
        for cid in ("1", "2"):
            self.assertEqual(plan[cid]["proposed_approval_state"], "approved")
            self.assertEqual(plan[cid]["approval_grade"], "Strong")
        for cid in ("7", "8", "9", "10"):
            self.assertEqual(plan[cid]["proposed_approval_state"], "in_review")
        for cid in ("4", "5", "6"):
            self.assertEqual(plan[cid]["proposed_search_tier"], "cold")
            self.assertEqual(plan[cid]["proposed_approval_state"], "raw")

    def test_light_stage_writes_no_chunk_plan(self):
        run_dream(self.work, self.out, stage="light", run_id="TEST-LIGHT")
        self.assertFalse((self.out / "chunk_plan.csv").exists())
        self.assertTrue((self.out / "evidence_registry.csv").exists())
        self.assertTrue((self.out / "dream_report.json").exists())

    def test_write_attempt_on_dreaming_connection_fails(self):
        con = open_readonly(self.work)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("UPDATE chunks SET approval_state='approved'")
        finally:
            con.close()


class DreamingPathTests(unittest.TestCase):
    def test_unconfigured_paths_raise_instead_of_empty_results(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("IMV_KNOWLEDGE_DB", "IMV_DREAM_OUT")}
        with tempfile.TemporaryDirectory() as cwd:
            # No config.yaml in this empty cwd either.
            import unittest.mock as mock
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("os.getcwd", return_value=cwd):
                    # resolve_* only consults ./config.yaml; run it with a
                    # cwd that has none. os.chdir is process-global, so
                    # point at the empty dir via config_arg instead.
                    missing = Path(cwd) / "no-such-config.yaml"
                    with self.assertRaises(KnowledgeUnavailable):
                        resolve_db_path(None, str(missing))
                    with self.assertRaises(KnowledgeUnavailable):
                        resolve_out_dir(None, str(missing))

    def test_env_vars_resolve_paths(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ,
                             {"IMV_KNOWLEDGE_DB": str(FIXTURE),
                              "IMV_DREAM_OUT": str(FIXTURE.parent)}):
            self.assertEqual(resolve_db_path(), FIXTURE)
            self.assertEqual(resolve_out_dir(), FIXTURE.parent)

    def test_missing_database_raises(self):
        with self.assertRaises(KnowledgeUnavailable):
            open_readonly(Path(tempfile.gettempdir()) / "no-such-imv.db")


class GradingUnitTests(unittest.TestCase):
    def test_explicit_markers_with_all_signals_is_strong(self):
        grade, signals = grade_evidence(
            "approved_by: master, status: approved, version: v2,"
            " 2026-09-01, 결재번호: A-1")
        self.assertEqual(grade, "Strong")
        self.assertEqual(set(signals), {"time", "version", "actor",
                                        "reference"})

    def test_discussion_without_markers_is_none(self):
        grade, signals = grade_evidence(
            "승인 절차에 대해 논의한다. 관리자와 버전 이야기가 나왔다.")
        self.assertEqual(grade, "None")
        self.assertEqual(signals, [])

    def test_two_signals_is_medium(self):
        grade, _ = grade_evidence("결재 완료. 버전: v1, 2026-08-01.")
        self.assertEqual(grade, "Medium")

    def test_families_collapse_versions_and_copies(self):
        self.assertEqual(normalize_family("설계안 v2 최종"),
                         normalize_family("설계안(복사본)"))

    def test_taxonomy_routes_csv_to_raw_evidence(self):
        self.assertEqual(
            classify_taxonomy("pixel_log_2026.csv", "/data/pixel_log_2026.csv"),
            "raw_evidence")

    def test_report_json_is_parseable_and_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dream(FIXTURE, Path(tmp), stage="rem", run_id="TEST-REM")
            report = json.loads(
                (Path(tmp) / "dream_report.json").read_text("utf-8"))
            self.assertEqual(report["run_id"], "TEST-REM")
            self.assertIsInstance(report["conflicts"], int)
            self.assertIsInstance(report["duplicates"], int)


if __name__ == "__main__":
    unittest.main()
