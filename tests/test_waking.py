import csv
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from imv.dreaming.report import run_dream
from imv.waking import (WakingRefused, apply_plan, check_actor, load_plan,
                        resolve_waking_db)

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_small.db"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_plan(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class WakingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "waking.db"
        shutil.copy(FIXTURE, self.work)
        self.out = Path(self.tmp.name) / "out"
        run_dream(self.work, self.out, stage="all", run_id="TEST-WAKING")
        self.plan_path = self.out / "chunk_plan.csv"
        self.plan_rows, self.plan_hash = load_plan(self.plan_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _chunk(self, chunk_id: int) -> dict:
        con = sqlite3.connect(self.work)
        con.row_factory = sqlite3.Row
        try:
            return dict(con.execute("SELECT * FROM chunks WHERE id=?",
                                    (chunk_id,)).fetchone())
        finally:
            con.close()

    def _events(self) -> list[dict]:
        con = sqlite3.connect(self.work)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                "SELECT * FROM chunk_policy_events ORDER BY id")]
        finally:
            con.close()

    # ---------- dry-run ----------

    def test_dry_run_without_confirm_writes_nothing(self):
        before = sha256(self.work)
        events_before = len(self._events())
        result = apply_plan(self.work, self.plan_rows, self.plan_hash,
                            "human:tester", "test-auth", "test reason",
                            confirm=False)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.applied, [])
        self.assertEqual(result.events_appended, 0)
        self.assertEqual(sha256(self.work), before)
        self.assertEqual(len(self._events()), events_before)

    # ---------- actor gate ----------

    def test_agent_actor_is_refused(self):
        with self.assertRaises(WakingRefused):
            check_actor("agent:tester")

    def test_missing_actor_is_refused(self):
        for bad in (None, "", "human:", "master"):
            with self.subTest(actor=bad):
                with self.assertRaises(WakingRefused):
                    check_actor(bad)

    def test_human_actor_is_accepted(self):
        self.assertEqual(check_actor("human:master"), "human:master")

    # ---------- grade gate ----------

    def test_medium_approved_proposal_is_skipped_not_applied(self):
        evil = [dict(r) for r in self.plan_rows]
        evil.append({
            "chunk_id": "7", "document_id": "3", "version_id": "103",
            "content_hash": "hash-7",
            "current_search_tier": "reference",
            "current_approval_state": "raw",
            "proposed_search_tier": "reference",
            "proposed_approval_state": "approved",
            "approval_grade": "Medium", "change": "YES", "reason": "test",
        })
        result = apply_plan(self.work, evil, self.plan_hash, "human:tester",
                            "test-auth", "test reason", confirm=True)
        skipped_ids = [s["chunk_id"] for s in result.skipped]
        self.assertIn("7", skipped_ids)
        # Chunk 7 got its legitimate in_review proposal, never approved.
        self.assertEqual(self._chunk(7)["approval_state"], "in_review")

    # ---------- confirmed apply ----------

    def test_confirmed_apply_changes_state_and_audits(self):
        result = apply_plan(self.work, self.plan_rows, self.plan_hash,
                            "human:tester", "test-auth", "test reason",
                            confirm=True)
        self.assertFalse(result.dry_run)
        self.assertEqual(len(result.applied), 9)
        self.assertEqual(result.events_appended, 9)
        # Strong evidence promoted; tier-only and in_review rows applied;
        # the previously reviewed chunk is untouched.
        self.assertEqual(self._chunk(1)["approval_state"], "approved")
        self.assertEqual(self._chunk(2)["approval_state"], "approved")
        self.assertEqual(self._chunk(4)["search_tier"], "cold")
        self.assertEqual(self._chunk(7)["approval_state"], "in_review")
        self.assertEqual(self._chunk(3)["search_tier"], "reference")
        self.assertEqual(self._chunk(3)["approval_state"], "raw")
        events = self._events()
        new = events[-9:]
        self.assertTrue(all(e["actor"] == "human:tester" for e in new))
        self.assertTrue(all(e["authorization_ref"] == "test-auth"
                            for e in new))
        self.assertTrue(all(self.plan_hash in e["reason"] for e in new))
        # Approval axis moved only where Strong evidence allowed it.
        by_chunk = {e["chunk_id"]: e for e in new}
        self.assertEqual(
            (by_chunk[1]["old_approval_state"],
             by_chunk[1]["new_approval_state"]), ("raw", "approved"))
        self.assertEqual(by_chunk[4]["old_approval_state"],
                         by_chunk[4]["new_approval_state"])

    def test_binding_mismatch_aborts_the_whole_batch(self):
        evil = [dict(r) for r in self.plan_rows]
        evil[0] = dict(evil[0])
        evil[0]["content_hash"] = "tampered-hash"
        before = sha256(self.work)
        with self.assertRaises(WakingRefused):
            apply_plan(self.work, evil, self.plan_hash, "human:tester",
                       "test-auth", "test reason", confirm=True)
        self.assertEqual(sha256(self.work), before)

    def test_non_plan_csv_is_rejected(self):
        bad = Path(self.tmp.name) / "bad.csv"
        bad.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(WakingRefused):
            load_plan(bad)

    def test_waking_db_needs_an_explicit_path(self):
        with self.assertRaises(WakingRefused):
            resolve_waking_db(None)


class WakingNotAnMCPToolTests(unittest.TestCase):
    def test_server_module_does_not_import_waking(self):
        # Static guard: server.py must not reference the waking module at
        # all, so no future edit can leak it into the MCP tool surface.
        # (A sys.modules check would be order-dependent — this test module
        # itself imports imv.waking — so the source is the honest place.)
        import imv.server as server_module
        source = Path(server_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("waking", source)

    def test_no_waking_or_apply_tool_in_source(self):
        source = Path("imv/server.py").read_text(encoding="utf-8")
        self.assertNotIn("waking", source.lower())
        self.assertNotIn("apply_plan", source)


class WakingToolSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_tool_list_has_no_waking_or_apply(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as vault:
            env = os.environ.copy()
            env["IMV_VAULT"] = vault
            env.pop("IMV_KNOWLEDGE_DB", None)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "imv.server"],
                env=env,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {t.name
                             for t in (await session.list_tools()).tools}
                    self.assertEqual(tools, {
                        "save_memory", "search_memory", "list_memory",
                        "get_memory", "approve_memory", "reject_memory",
                        "search_chunks", "get_chunk", "imv_status",
                    })
                    for name in tools:
                        self.assertNotIn("wak", name)
                        self.assertNotIn("apply", name)


if __name__ == "__main__":
    unittest.main()
