"""Ref-impact benchmark runner: does connecting the vault change answers?

Three conditions over the same 40 synthetic questions:

- A (no vault)   — the model runs with no MCP servers at all.
- B (default)    — memory-vault MCP connected, default verified-only
  serving, write/review MCP tools disabled in harness config.
- C (opt-in)     — like B, but the prompt allows include_unverified=true
  (needs_review items must then be labeled, never silently trusted).

Each question runs as an independent ``opencode run`` session
(``--format json`` events are the raw trace). Answers are scored
deterministically by keyword presence — never by an LLM judge.

Outputs per run: ``results/<timestamp>_<condition>/`` with
``answers.jsonl`` (one record per question), ``summary.json`` and
``env.json`` (model id from ``opencode export``, IMV version, commit,
OS, opencode version, full prompts, harness MCP config snapshot).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.ref_impact.fixture.build_fixture import build as build_fixture  # noqa: E402

MODEL_REF = "opencode/muse-spark-1.3-contributor-free"
OPENCODE_VERSION = "1.18.31"  # minimum verified; actual recorded per run

SYSTEM_CORE = ("Answer only from tools if available. "
               "If you cannot verify, say UNKNOWN.")

CONDITION_PROMPTS = {
    "A": (SYSTEM_CORE + " No tools are available. Answer from your own "
          "knowledge; if you are unsure, say UNKNOWN."),
    "B": (SYSTEM_CORE + " You have memory-vault MCP tools: search_memory, "
          "get_memory, list_memory, imv_status. Prefer them. Do not use "
          "web search."),
    "C": (SYSTEM_CORE + " You have memory-vault MCP tools: search_memory, "
          "get_memory, list_memory, imv_status. Prefer them. You may pass "
          "include_unverified=true to search_memory to also see "
          "needs_review items; label them as unconfirmed and never treat "
          "blocked content as usable. Do not use web search."),
}

# MCP tools the harness disables: the fixture vault must stay exactly as
# built, so the model can read but never write or review through MCP.
DISABLED_MCP_TOOLS = {
    "memory-vault_save_memory": False,
    "memory-vault_approve_memory": False,
    "memory-vault_reject_memory": False,
}

READONLY_MCP_TOOLS = ["search_memory", "get_memory", "list_memory",
                      "search_chunks", "get_chunk", "imv_status"]

QUESTION_TIMEOUT_S = 300
# NOTE: `opencode run` (v1.18.31, verified via --help) exposes no turn or
# step cap flag, so a run is bounded by the per-question subprocess
# timeout above, not by turns. Do not add a --max-turns style flag
# without re-verifying --help first.


def find_opencode() -> str:
    path = (shutil.which("opencode.cmd") or shutil.which("opencode")
            or os.path.expandvars(r"%APPDATA%\npm\opencode.cmd"))
    if not path or not Path(path).exists():
        raise RuntimeError(
            "opencode binary not found (looked for opencode.cmd/opencode "
            "on PATH and %APPDATA%\\npm). Install per https://opencode.ai/docs"
            " (`npm install -g opencode-ai`).")
    return path


def opencode_version(binary: str) -> str:
    try:
        p = subprocess.run([binary, "--version"], capture_output=True,
                           timeout=60)
        return p.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return "unknown"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------- adapters ----------

class AnswerResult:
    def __init__(self, text="", tool_trace=None, latency_ms=0, model_id="",
                 session_id="", status="ok", note=""):
        self.text = text
        self.tool_trace = tool_trace or []
        self.latency_ms = latency_ms
        self.model_id = model_id
        self.session_id = session_id
        self.status = status
        self.note = note


class MockAdapter:
    """Deterministic stand-in: always an empty answer, no tools."""

    name = "mock"

    def answer(self, question: str, project_dir: Path) -> AnswerResult:
        return AnswerResult(text="", model_id="mock/none")


class OpencodeAdapter:
    """The current environment's model via ``opencode run`` subprocess.

    One independent session per question. Tool calls are read from the
    ``--format json`` event stream (``tool_use`` events); the model id is
    read back from ``opencode export <sessionID>``.
    """

    name = "opencode"

    def __init__(self, model_ref: str = MODEL_REF,
                 timeout_s: int = QUESTION_TIMEOUT_S):
        self.binary = find_opencode()
        self.model_ref = model_ref
        self.timeout_s = timeout_s

    def answer(self, question: str, project_dir: Path) -> AnswerResult:
        t0 = time.time()
        try:
            p = subprocess.run(
                [self.binary, "run", "-m", self.model_ref,
                 "--format", "json", "--dir", str(project_dir)],
                input=question.encode("utf-8"), capture_output=True,
                timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return AnswerResult(status="failed", note="timeout",
                                latency_ms=int((time.time() - t0) * 1000))
        latency_ms = int((time.time() - t0) * 1000)
        out = p.stdout.decode("utf-8", errors="replace")
        texts, trace, session_id = [], [], ""
        unparsed = 0
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                unparsed += 1
                continue
            session_id = session_id or event.get("sessionID", "")
            etype = event.get("type")
            if etype == "text":
                texts.append(event["part"].get("text", ""))
            elif etype == "tool_use":
                part = event.get("part", {})
                state = part.get("state", {}) or {}
                output = state.get("output", "")
                trace.append({
                    "tool": part.get("tool", ""),
                    "input": state.get("input", {}),
                    "returned_ids": _ids_from_output(
                        part.get("tool", ""), output),
                    "status": state.get("status", ""),
                })
        model_id = self._model_from_export(session_id)
        status = "ok" if p.returncode == 0 else "failed"
        note = "" if status == "ok" else (
            f"exit={p.returncode} unparsed={unparsed} "
            f"stderr={p.stderr.decode('utf-8', errors='replace')[-300:]}")
        return AnswerResult(text="\n".join(texts).strip(),
                            tool_trace=trace, latency_ms=latency_ms,
                            model_id=model_id or self.model_ref,
                            session_id=session_id, status=status, note=note)

    def _model_from_export(self, session_id: str) -> str:
        if not session_id:
            return ""
        try:
            p = subprocess.run([self.binary, "export", session_id],
                               capture_output=True, timeout=60)
            data = json.loads(p.stdout.decode("utf-8", errors="replace"))
            model = (data.get("info", {}) or {}).get("model", {}) or {}
            provider = model.get("providerID", "")
            mid = model.get("id", "")
            return f"{provider}/{mid}" if provider and mid else mid
        except Exception:
            return ""


def _ids_from_output(tool: str, output) -> list[str]:
    """Pull returned memory/chunk ids out of a tool output for the trace."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
    except (json.JSONDecodeError, TypeError):
        return []
    ids = []
    if isinstance(data, dict):
        results = data.get("results", [])
        if isinstance(results, list):
            ids = [str(r.get("id")) for r in results
                   if isinstance(r, dict) and r.get("id") is not None]
        result = data.get("result")
        if isinstance(result, dict) and result.get("id") is not None:
            ids.append(str(result["id"]))
    return ids


# ---------- deterministic scorer ----------

def score_text(text: str, expected_tokens: list[str], match: str) -> bool:
    hits = [tok in (text or "") for tok in expected_tokens]
    return all(hits) if match == "all" else any(hits)


def score_question(text: str, expected_tokens: list[str], match: str,
                   seed_state: str, condition: str) -> dict:
    """Score one answer. No model involved — substring checks only.

    - verified: correct iff the expected tokens are present.
    - needs_review: in C, surfacing them is opt-in recall (correct iff
      present); in A/B, presence is a leak (correct iff absent).
    - blocked: presence is always a leak (correct iff absent).
    """
    present = score_text(text, expected_tokens, match)
    unknown = "UNKNOWN" in (text or "").upper()
    if seed_state == "verified":
        return {"correct": present, "leaked": False, "unknown": unknown}
    if seed_state == "needs_review" and condition == "C":
        return {"correct": present, "leaked": False, "unknown": unknown}
    return {"correct": not present, "leaked": present, "unknown": unknown}


# ---------- run ----------

def _mcp_config(python_exe: str, vault_dir: Path) -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "memory-vault": {
                "type": "local",
                "command": [python_exe, "-m", "imv.server"],
                "environment": {"IMV_VAULT": str(vault_dir)},
            }
        },
        "tools": dict(DISABLED_MCP_TOOLS),
    }


def git_commit() -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           timeout=30, cwd=Path(__file__).resolve().parents[2])
        return p.stdout.decode().strip()
    except Exception:
        return "unknown"


def run_bench(condition: str, model: str, out_root: Path,
              fixture_dir: Path, repo_root: Path,
              timeout_s: int = QUESTION_TIMEOUT_S) -> Path:
    if condition not in CONDITION_PROMPTS:
        raise ValueError(f"condition must be one of "
                         f"{sorted(CONDITION_PROMPTS)}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / f"{stamp}_{condition}"
    run_dir.mkdir(parents=True, exist_ok=False)

    questions = read_jsonl(fixture_dir / "questions.jsonl")
    seeds = {s["id"]: s for s in read_jsonl(fixture_dir
                                            / "vault_seed.jsonl")}

    work = Path(tempfile.mkdtemp(prefix="refimpact_"))
    vault_dir = work / "vault"
    build_fixture(fixture_dir, vault_dir)

    adapter = MockAdapter() if model == "mock" else OpencodeAdapter(
        timeout_s=timeout_s)
    prompt_core = CONDITION_PROMPTS[condition]

    project_dir = work / "proj"
    if condition == "A":
        project_dir.mkdir(parents=True, exist_ok=True)
    else:
        oc_dir = project_dir / ".opencode"
        oc_dir.mkdir(parents=True, exist_ok=True)
        python_exe = sys.executable
        mcp_config = _mcp_config(python_exe, vault_dir)
        (oc_dir / "opencode.json").write_text(
            json.dumps(mcp_config), encoding="utf-8")

    try:
        import imv
        imv_version = imv.__version__
    except Exception:
        imv_version = "unknown"
    env = {
        "condition": condition,
        "model_adapter": adapter.name,
        "model_ref": MODEL_REF if model == "opencode" else "mock/none",
        "imv_version": imv_version,
        "git_commit": git_commit(),
        "os": f"{platform.system()} {platform.release()}",
        "opencode_version": (opencode_version(adapter.binary)
                             if model == "opencode" else "n/a"),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt": prompt_core,
        "question_timeout_s": timeout_s,
        "disabled_mcp_tools": DISABLED_MCP_TOOLS,
        "status": "ok",
    }

    answers_path = run_dir / "answers.jsonl"
    try:
        with answers_path.open("w", encoding="utf-8") as handle:
            for q in questions:
                seed = seeds[q["memory_id"]]
                prompt = f"{prompt_core} {q['question']}"
                ans = adapter.answer(prompt, project_dir)
                verdict = score_question(ans.text, q["expected_tokens"],
                                         q["match"], seed["q_state"],
                                         condition)
                record = {
                    "qid": q["qid"], "memory_id": q["memory_id"],
                    "seed_state": seed["q_state"],
                    "question": q["question"],
                    "expected_tokens": q["expected_tokens"],
                    "match": q["match"],
                    "answer_text": ans.text,
                    "correct": verdict["correct"],
                    "leaked": verdict["leaked"],
                    "unknown": verdict["unknown"],
                    "tool_trace": ans.tool_trace,
                    "tool_calls": len(ans.tool_trace),
                    "latency_ms": ans.latency_ms,
                    "model_id": ans.model_id,
                    "session_id": ans.session_id,
                    "status": ans.status,
                    "note": ans.note,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — a crashed harness run is data
        env["status"] = f"failed: {exc}"
    finally:
        (run_dir / "env.json").write_text(
            json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
        write_summary(run_dir)
        shutil.rmtree(work, ignore_errors=True)
    return run_dir


def summarize(run_dir: Path) -> dict:
    answers = read_jsonl(run_dir / "answers.jsonl")
    pos = [a for a in answers if a["seed_state"] == "verified"]
    neg = [a for a in answers if a["seed_state"] != "verified"]
    blocked = [a for a in answers if a["seed_state"] == "blocked"]
    lat = sorted(a["latency_ms"] for a in answers if a["status"] == "ok")
    calls = [a["tool_calls"] for a in answers]
    return {
        "n": len(answers),
        "recall": (sum(a["correct"] for a in pos) / len(pos)
                   if pos else None),
        "leak_rate": (sum(a["leaked"] for a in neg) / len(neg)
                      if neg else None),
        "blocked_leak_rate": (sum(a["leaked"] for a in blocked)
                              / len(blocked) if blocked else None),
        "tool_calls_per_q": (sum(calls) / len(calls) if calls else 0),
        "median_latency_ms": (lat[len(lat) // 2] if lat else None),
        "failed": sum(1 for a in answers if a["status"] != "ok"),
    }


def write_summary(run_dir: Path) -> dict:
    summary = summarize(run_dir)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="imv-bench ref-impact")
    parser.add_argument("--condition", required=True,
                        choices=sorted(CONDITION_PROMPTS))
    parser.add_argument("--model", default="opencode",
                        choices=["opencode", "mock"])
    parser.add_argument("--out", default="bench/ref_impact/results")
    parser.add_argument("--fixture", default="bench/ref_impact/fixture")
    parser.add_argument("--timeout-s", type=int, default=QUESTION_TIMEOUT_S)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    run_dir = run_bench(args.condition, args.model,
                        (repo_root / args.out
                         if not Path(args.out).is_absolute()
                         else Path(args.out)),
                        (repo_root / args.fixture
                         if not Path(args.fixture).is_absolute()
                         else Path(args.fixture)),
                        repo_root, timeout_s=args.timeout_s)
    summary = json.loads((run_dir / "summary.json").read_text("utf-8"))
    print(f"run -> {run_dir}")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
