"""imv — human-side CLI for index-memory-vault.

Approval authority lives here, not in the MCP tool surface.

    imv pending                 # list needs_review memories
    imv show <id>               # print one memory
    imv approve <id> [-n note]  # promote to verified (Q1)
    imv reject  <id> [-n note]  # block (Q3)
    imv list [--state STATE]
    imv doctor                  # vault health check
"""

from __future__ import annotations

import argparse
import getpass
import os

from .probe import run_probes
from .store import VaultStore


def _store() -> VaultStore:
    return VaultStore(os.environ.get("IMV_VAULT", "./vault"))


def _print(mem_dicts: list[dict]) -> None:
    for m in mem_dicts:
        print(f"[{m['q_state']:>12}] {m['id']}  {m['created_at']}  "
              f"({m['source']})  {m['title']}")


def main() -> None:
    p = argparse.ArgumentParser(prog="imv")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pending")

    sp = sub.add_parser("show")
    sp.add_argument("id")

    for name in ("approve", "reject"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("-n", "--note", default=None)

    sub.add_parser("doctor")

    sl = sub.add_parser("list")
    sl.add_argument("--state", default=None,
                    choices=["needs_review", "verified", "blocked"])

    args = p.parse_args()
    store = _store()
    actor = f"human:{getpass.getuser()}"

    if args.cmd == "pending":
        _print([m.public() for m in store.list("needs_review")])
    elif args.cmd == "list":
        _print([m.public() for m in store.list(args.state)])
    elif args.cmd == "show":
        mem = store.get(args.id)
        if not mem:
            raise SystemExit(f"not found: {args.id}")
        for k, v in mem.public().items():
            print(f"{k}: {v}")
    elif args.cmd == "doctor":
        findings = run_probes(store)
        total = len(store.list(limit=10**9))
        errors = [f for f in findings if f.severity == "error"]
        warns = [f for f in findings if f.severity == "warn"]
        print(f"OK: {total} memories checked")
        print(f"WARN: {len(warns)}")
        print(f"ERROR: {len(errors)}")
        for f in errors + warns:
            print(f"  [{f.severity.upper()}] {f.memory_id}: {f.detail}")
        if errors:
            raise SystemExit(1)
    elif args.cmd in ("approve", "reject"):
        state = "verified" if args.cmd == "approve" else "blocked"
        mem = store.set_state(args.id, state, actor=actor, note=args.note)
        print(f"{mem.id} -> {mem.q_state} (by {actor})")


if __name__ == "__main__":
    main()
