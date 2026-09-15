"""Dreaming database access: read-only connections and path resolution.

Path priority (highest first):

1. CLI arguments (``--db`` / ``--out`` / ``--config``)
2. Environment variables (``IMV_KNOWLEDGE_DB`` / ``IMV_DREAM_OUT``)
3. ``config.yaml`` keys ``knowledge.db_path`` / ``dreaming.out_dir``

There is no default. When the database path cannot be resolved — or the
file is missing or unreadable — this raises :class:`KnowledgeUnavailable`
instead of returning an empty result, so "not configured" is never
mistaken for "no evidence exists" (same principle as ``imv/server.py``).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ..knowledge import KnowledgeUnavailable

ENV_DB = "IMV_KNOWLEDGE_DB"
ENV_OUT = "IMV_DREAM_OUT"

CONFIG_DB_KEYS = ("knowledge.db_path", "dreaming.db_path")
CONFIG_OUT_KEYS = ("dreaming.out_dir", "dream.out_dir")


def _read_config_values(config_path: Path) -> dict[str, str]:
    """Read two scalar keys out of a YAML file using only the stdlib.

    This is deliberately a minimal section-aware reader, not a YAML
    parser: it understands ``section:`` headers and ``key: value`` lines
    (``#`` comments stripped, single/double quotes unwrapped). Anything
    fancier belongs in a real YAML dependency, which dreaming refuses to
    take so the module stays dependency-free.
    """
    values: dict[str, str] = {}
    section = ""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.endswith(":") and indent == 0:
            section = stripped[:-1].strip()
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().strip("'").strip('"')
        if value:
            values[f"{section}.{key.strip()}"] = value
    return values


def resolve_db_path(db_arg: str | None = None,
                    config_arg: str | None = None) -> Path:
    """Resolve the knowledge database path or raise KnowledgeUnavailable."""
    if db_arg:
        return Path(db_arg)
    env = os.environ.get(ENV_DB, "").strip()
    if env:
        return Path(env)
    candidates = ([Path(config_arg)] if config_arg
                  else [Path("config.yaml")])
    for candidate in candidates:
        if candidate.is_file():
            values = _read_config_values(candidate)
            for key in CONFIG_DB_KEYS:
                if values.get(key):
                    return Path(values[key])
    raise KnowledgeUnavailable(
        f"Knowledge database is not configured. Pass --db, set {ENV_DB}, "
        "or add knowledge.db_path to config.yaml. Refusing to dream "
        "over an empty result set.")


def resolve_out_dir(out_arg: str | None = None,
                    config_arg: str | None = None) -> Path:
    """Resolve the dreaming output directory (created on write)."""
    if out_arg:
        return Path(out_arg)
    env = os.environ.get(ENV_OUT, "").strip()
    if env:
        return Path(env)
    candidates = ([Path(config_arg)] if config_arg
                  else [Path("config.yaml")])
    for candidate in candidates:
        if candidate.is_file():
            values = _read_config_values(candidate)
            for key in CONFIG_OUT_KEYS:
                if values.get(key):
                    return Path(values[key])
    raise KnowledgeUnavailable(
        f"Dreaming output directory is not configured. Pass --out, set "
        f"{ENV_OUT}, or add dreaming.out_dir to config.yaml.")


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the knowledge database strictly read-only.

    The ``mode=ro`` URI flag makes SQLite itself reject any write with
    ``sqlite3.OperationalError`` — dreaming holds no write cursor, ever.
    """
    path = Path(db_path)
    if not path.exists():
        raise KnowledgeUnavailable(f"knowledge db not found: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM chunks LIMIT 1")
        con.execute("SELECT 1 FROM documents LIMIT 1")
    except sqlite3.Error as exc:
        con.close()
        raise KnowledgeUnavailable(
            f"knowledge db not readable as a dreaming source: "
            f"{path} ({exc})") from exc
    return con


def load_corpus(con: sqlite3.Connection) -> tuple[list[dict], list[dict], set[int]]:
    """Load active documents, all chunks, and previously reviewed chunk ids."""
    docs = [dict(r) for r in con.execute(
        "SELECT * FROM documents WHERE status='ACTIVE' ORDER BY id")]
    chunks = [dict(r) for r in con.execute(
        "SELECT * FROM chunks ORDER BY document_id, version_id, chunk_no")]
    try:
        reviewed = {r[0] for r in con.execute(
            "SELECT DISTINCT chunk_id FROM chunk_policy_events")}
    except sqlite3.Error:
        # Small / older databases may have no audit table yet; then no
        # chunk carries a previous review to preserve.
        reviewed = set()
    return docs, chunks, reviewed
