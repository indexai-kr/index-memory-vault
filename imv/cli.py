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
from pathlib import Path

from . import __version__
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
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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

    dream = sub.add_parser("dream",
                           help="read-only dreaming pass over the knowledge base")
    dream.add_argument("--stage", default="all",
                       choices=["light", "deep", "rem", "all"])
    dream.add_argument("--db", default=None,
                       help="knowledge db path (overrides IMV_KNOWLEDGE_DB, config.yaml)")
    dream.add_argument("--out", default=None,
                       help="output directory (overrides IMV_DREAM_OUT, config.yaml)")
    dream.add_argument("--config", default=None,
                       help="config.yaml path (default: ./config.yaml if present)")

    waking = sub.add_parser("waking", help="human-only waking actions")
    waking_sub = waking.add_subparsers(dest="waking_cmd", required=True)
    apply = waking_sub.add_parser("apply",
                                  help="apply a dreaming chunk plan (dry-run unless --confirm)")
    apply.add_argument("--plan", required=True, help="dreaming chunk_plan.csv")
    apply.add_argument("--db", default=None,
                       help="knowledge db path (overrides IMV_KNOWLEDGE_DB)")
    apply.add_argument("--actor", default=None,
                       help="operator id, must be human:<id>")
    apply.add_argument("--confirm", action="store_true",
                       help="without this flag nothing is written")
    apply.add_argument("--auth", default=None,
                       help="authorization reference recorded in audit events")
    apply.add_argument("--reason", default=None,
                       help="reason recorded in audit events")

    install = sub.add_parser("install-windows")
    install.add_argument("--server", required=True)
    install.add_argument("--vault", required=True)
    install.add_argument("--report")

    args = p.parse_args()
    if args.cmd == "dream":
        from .dreaming.db import resolve_db_path, resolve_out_dir
        from .dreaming.report import run_dream
        from .knowledge import KnowledgeUnavailable
        try:
            db_path = resolve_db_path(args.db, args.config)
            out_dir = resolve_out_dir(args.out, args.config)
            report = run_dream(db_path, out_dir, stage=args.stage)
        except KnowledgeUnavailable as exc:
            raise SystemExit(f"knowledge unavailable: {exc}")
        print(f"stage={report['stage']} documents={report['active_documents']} "
              f"chunks={report['input_chunks']} proposals={report['proposals']} "
              f"changes={report['proposed_changes']} "
              f"duplicates={report['duplicates']} conflicts={report['conflicts']} "
              f"writes=0 -> {out_dir}")
        return
    if args.cmd == "waking" and args.waking_cmd == "apply":
        from .waking import (WakingRefused, apply_plan, check_actor,
                             load_plan, resolve_waking_db)
        try:
            actor = check_actor(args.actor)
            db_path = resolve_waking_db(args.db)
            plan_rows, plan_sha256 = load_plan(args.plan)
            result = apply_plan(
                db_path, plan_rows, plan_sha256, actor,
                authorization_ref=args.auth or f"waking:{Path(args.plan).name}",
                reason=args.reason or "waking apply of dreaming chunk plan",
                confirm=args.confirm)
            result.plan = args.plan
        except WakingRefused as exc:
            raise SystemExit(f"waking refused: {exc}")
        print(result.summary())
        return
    if args.cmd == "install-windows":
        from .installer.windows import configure_and_diagnose
        configure_and_diagnose(
            Path(args.server), Path(args.vault),
            Path(args.report) if args.report else None,
        )
        return
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
        total = len(store.list(limit=None))
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
