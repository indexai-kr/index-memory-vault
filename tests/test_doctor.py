"""Regression tests for the doctor/probe path.

These exist because a limit-validation change once shipped that crashed
`imv doctor` while every other test stayed green: nothing exercised the
probes. Any future change to listing, limits, or audit must keep these
passing.
"""

import tempfile
import unittest

from imv.probe import run_probes
from imv.store import VaultStore


class DoctorPathTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = VaultStore(self.dir.name)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_probes_run_on_clean_vault(self):
        mem = self.store.save("t", "x", [], source="claude")
        self.store.set_state(mem.id, "verified", actor="human:test")
        self.assertEqual(run_probes(self.store), [])

    def test_internal_unlimited_listing_stays_open(self):
        self.assertEqual(self.store.list(limit=None), [])

    def test_public_listing_stays_bounded(self):
        with self.assertRaises(ValueError):
            self.store.list(limit=10**9)

    def test_gap_probe_catches_state_change_outside_audit(self):
        mem = self.store.save("t", "x", [], source="claude")
        self.store.db.execute(
            "UPDATE memories SET q_state='verified' WHERE id=?", (mem.id,))
        self.store.db.commit()
        findings = run_probes(self.store)
        self.assertEqual([f.probe for f in findings], ["gap"])
        self.assertEqual(findings[0].severity, "error")

    def test_cycle_probe_flags_review_flip_flop(self):
        mem = self.store.save("t", "x", [], source="claude")
        for state in ("verified", "blocked", "verified"):
            self.store.set_state(mem.id, state, actor="human:test")
        findings = run_probes(self.store)
        self.assertEqual([f.probe for f in findings], ["cycle"])
        self.assertEqual(findings[0].severity, "warn")


if __name__ == "__main__":
    unittest.main()
