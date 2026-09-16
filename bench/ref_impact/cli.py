"""imv-bench — reproducible benchmark entry point.

    imv-bench ref-impact --condition A|B|C --model opencode|mock
    imv-bench report [results_root]
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="imv-bench")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("ref-impact")
    run_p.add_argument("--condition", required=True,
                       choices=["A", "B", "C"])
    run_p.add_argument("--model", default="opencode",
                       choices=["opencode", "mock"])
    run_p.add_argument("--out", default="bench/ref_impact/results")
    run_p.add_argument("--fixture", default="bench/ref_impact/fixture")
    run_p.add_argument("--timeout-s", type=int, default=300)
    rep_p = sub.add_parser("report")
    rep_p.add_argument("results_root", nargs="?",
                       default="bench/ref_impact/results")
    args = parser.parse_args()

    if args.cmd == "ref-impact":
        from bench.ref_impact.run import run_bench, write_summary
        from pathlib import Path
        import json
        repo_root = Path(__file__).resolve().parents[2]
        out = Path(args.out)
        fixture = Path(args.fixture)
        run_dir = run_bench(
            args.condition, args.model,
            repo_root / out if not out.is_absolute() else out,
            repo_root / fixture if not fixture.is_absolute() else fixture,
            repo_root, timeout_s=args.timeout_s)
        summary = json.loads((run_dir / "summary.json").read_text("utf-8"))
        print(f"run -> {run_dir}")
        print(json.dumps(summary, ensure_ascii=False))
    elif args.cmd == "report":
        from bench.ref_impact import report as report_mod
        sys.argv = ["imv-bench report", args.results_root]
        report_mod.main()


if __name__ == "__main__":
    main()
