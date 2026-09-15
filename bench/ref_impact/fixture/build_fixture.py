"""Build a throwaway benchmark vault from the synthetic fixture.

Reads ``vault_seed.jsonl`` + ``questions.jsonl``, creates a fresh vault
directory with an index.db, saves every memory, and promotes each one to
its fixture state (verified / needs_review / blocked).

Self-checks (fail loudly, fix the wording, never the checker):

- 40 seeds / 40 questions, states exactly 30 verified + 6 needs_review
  + 4 blocked, every question points at an existing seed.
- Every answer_key is unique across all 40 seeds, so one memory's leak
  can never score as another memory's hit.
- Every verified question retrieves its own memory through the same
  ``VaultStore.search`` path the MCP server uses. A question the
  product cannot retrieve is a broken question, not a model failure.

Usage: ``python build_fixture.py <fixture_dir> <vault_dir>``.
Nothing is written outside ``vault_dir``. The caller deletes it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from imv.store import VaultStore


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build(fixture_dir: str | Path, vault_dir: str | Path) -> dict[str, str]:
    fixture = Path(fixture_dir)
    seeds = read_jsonl(fixture / "vault_seed.jsonl")
    questions = read_jsonl(fixture / "questions.jsonl")

    assert len(seeds) == 40, f"want 40 seeds, got {len(seeds)}"
    assert len(questions) == 40, f"want 40 questions, got {len(questions)}"
    states = [s["q_state"] for s in seeds]
    assert states.count("verified") == 30, states
    assert states.count("needs_review") == 6, states
    assert states.count("blocked") == 4, states

    keys = [s["answer_key"] for s in seeds]
    assert len(set(keys)) == 40, "answer_key must be unique per memory"

    by_id = {s["id"]: s for s in seeds}
    for q in questions:
        assert q["memory_id"] in by_id, f"{q['qid']} points nowhere"
        assert q["expected_tokens"], f"{q['qid']} has no expected tokens"
        assert q["match"] in ("any", "all"), q

    store = VaultStore(vault_dir)
    vault_ids: dict[str, str] = {}
    for seed in seeds:
        mem = store.save(title=seed["title"], content=seed["body"],
                         tags=["bench", seed["id"]], source="bench")
        vault_ids[seed["id"]] = mem.id
        if seed["q_state"] != "needs_review":
            store.set_state(mem.id, seed["q_state"],
                            actor="human:bench-fixture")

    misses = []
    for q in questions:
        seed = by_id[q["memory_id"]]
        if seed["q_state"] != "verified":
            continue
        hits = store.search(q["question"], limit=10)
        if vault_ids[seed["id"]] not in {h.id for h in hits}:
            misses.append(q["qid"])
    store.db.close()
    if misses:
        raise SystemExit(
            f"unretrievable verified questions (fix the wording): {misses}")
    return vault_ids


def main() -> None:
    fixture_dir, vault_dir = sys.argv[1], sys.argv[2]
    vault_ids = build(fixture_dir, vault_dir)
    print(f"fixture OK: 40 seeds in {vault_dir} "
          f"({len(vault_ids)} ids mapped)")


if __name__ == "__main__":
    main()
