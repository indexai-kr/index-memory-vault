"""index-memory-vault: storage core.

Design invariants:
- Markdown files in the vault are the source of truth for memory content.
- SQLite is a rebuildable index (FTS5 search + state machine).
- Every memory carries a q_state:
    needs_review : default for anything an AI saved (Q2)
    verified     : promoted only by a human action (Q1)
    blocked      : rejected; excluded from retrieval (Q3)
- State transitions are recorded in an append-only audit table.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

Q_STATES = ("needs_review", "verified", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'unknown',
    q_state     TEXT NOT NULL DEFAULT 'needs_review'
                CHECK (q_state IN ('needs_review','verified','blocked')),
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, tags, content='memories', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    actor      TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT,
    note       TEXT,
    at         TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str, limit: int = 40) -> str:
    # Python's Unicode-aware \w keeps Korean and other letter scripts.
    s = re.sub(r"[^\w]+", "-", text.strip().lower()).strip("-")
    return s[:limit] or "memory"


def _limit(value: int, maximum: int = 500) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


@dataclass
class Memory:
    id: str
    title: str
    content: str
    tags: str
    source: str
    q_state: str
    path: str
    created_at: str
    updated_at: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None

    def public(self) -> dict:
        d = asdict(self)
        d["tags"] = [t for t in self.tags.split(",") if t]
        return d


class VaultStore:
    def __init__(self, vault_dir: str | Path, db_path: str | Path | None = None):
        self.vault = Path(vault_dir)
        self.vault.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.vault / "index.db"
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        """Release the SQLite handle. Required on Windows before deleting
        the vault directory (open handles block deletion, WinError 32)."""
        self.db.close()

    # ---------- write path ----------

    def save(self, title: str, content: str, tags: list[str] | None = None,
             source: str = "unknown") -> Memory:
        """Save a new memory. Always lands in needs_review (Q2)."""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must not be empty")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must not be empty")
        clean_tags = []
        for tag in tags or []:
            if not isinstance(tag, str) or not tag.strip() or "," in tag:
                raise ValueError("tags must be non-empty strings without commas")
            clean_tags.append(tag.strip())
        title = title.strip()
        source = source.strip()
        mid = uuid.uuid4().hex[:12]
        now = _now()
        rel = self._write_markdown(mid, title, content, clean_tags, source,
                                   "needs_review", now)
        mem = Memory(mid, title, content, ",".join(clean_tags), source,
                     "needs_review", str(rel), now, now)
        self.db.execute(
            "INSERT INTO memories (id,title,content,tags,source,q_state,path,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (mem.id, mem.title, mem.content, mem.tags, mem.source,
             mem.q_state, mem.path, mem.created_at, mem.updated_at))
        self._audit(mid, "save", source, None, "needs_review")
        self.db.commit()
        return mem

    def set_state(self, memory_id: str, to_state: str, actor: str,
                  note: str | None = None) -> Memory:
        """Transition q_state. Callers enforce WHO may call this;
        the store only enforces valid states + audit."""
        if to_state not in Q_STATES:
            raise ValueError(f"invalid q_state: {to_state}")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"memory not found: {memory_id}")
        now = _now()
        self.db.execute(
            "UPDATE memories SET q_state=?, reviewed_by=?, reviewed_at=?,"
            " review_note=?, updated_at=? WHERE id=?",
            (to_state, actor, now, note, now, memory_id))
        self._audit(memory_id, "set_state", actor, mem.q_state, to_state, note)
        self.db.commit()
        mem = self.get(memory_id)
        assert mem is not None
        self._write_markdown(mem.id, mem.title, mem.content,
                             mem.tags.split(",") if mem.tags else [],
                             mem.source, mem.q_state, mem.created_at,
                             existing_path=mem.path)
        return mem

    # ---------- read path ----------

    def get(self, memory_id: str) -> Memory | None:
        row = self.db.execute("SELECT * FROM memories WHERE id=?",
                              (memory_id,)).fetchone()
        return Memory(**dict(row)) if row else None

    def search(self, query: str, limit: int = 10,
               include_unverified: bool = False) -> list[Memory]:
        """FTS search with a substring fallback.

        Default surface = verified only; unverified results are opt-in and
        always labeled by q_state. blocked never returns.

        FTS5's unicode61 tokenizer splits on whitespace, so agglutinative
        text (e.g. Korean compounds) saved as one token is invisible to a
        partial MATCH. When FTS returns nothing, or the query is short,
        we supplement with a plain LIKE substring scan. This is a fallback,
        not a language solution.
        """
        limit = _limit(limit)
        query = query.strip()
        if not query:
            return []
        states = ("verified", "needs_review") if include_unverified else ("verified",)
        marks = ",".join("?" for _ in states)

        # Quote tokens so user input can't break FTS5 query syntax.
        fts_query = " ".join('"{}"'.format(t.replace('"', '""'))
                             for t in query.split()) or '""'
        rows = self.db.execute(
            f"SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid=f.rowid "
            f"WHERE memories_fts MATCH ? AND m.q_state IN ({marks}) "
            f"ORDER BY rank LIMIT ?", (fts_query, *states, limit)).fetchall()
        results = [Memory(**dict(r)) for r in rows]

        if len(results) == 0 or len(query.strip()) <= 4:
            seen = {m.id for m in results}
            like = f"%{query.strip()}%"
            extra = self.db.execute(
                f"SELECT * FROM memories WHERE q_state IN ({marks}) "
                f"AND (title LIKE ? OR content LIKE ? OR tags LIKE ?) "
                f"ORDER BY created_at DESC LIMIT ?",
                (*states, like, like, like, limit)).fetchall()
            for r in extra:
                m = Memory(**dict(r))
                if m.id not in seen and len(results) < limit:
                    results.append(m)
                    seen.add(m.id)
        return results

    def list(self, q_state: str | None = None,
             limit: int | None = 50) -> list[Memory]:
        """limit=None is for internal callers (probes, doctor) and means
        no cap; the MCP tool surface always passes a bounded int."""
        limit = -1 if limit is None else _limit(limit)
        if q_state and q_state not in Q_STATES:
            raise ValueError(f"invalid q_state: {q_state}")
        if q_state:
            rows = self.db.execute(
                "SELECT * FROM memories WHERE q_state=? "
                "ORDER BY created_at DESC LIMIT ?", (q_state, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [Memory(**dict(r)) for r in rows]

    # ---------- internals ----------

    def _write_markdown(self, mid, title, content, tags, source, q_state,
                        created_at, existing_path=None) -> Path:
        if existing_path:
            path = self.vault / existing_path if not Path(existing_path).is_absolute() \
                else Path(existing_path)
        else:
            d = datetime.fromisoformat(created_at)
            sub = self.vault / f"{d:%Y}" / f"{d:%m}"
            sub.mkdir(parents=True, exist_ok=True)
            path = sub / f"{mid}-{_slug(title)}.md"
        # JSON strings are valid YAML scalars and prevent frontmatter injection
        # through model-supplied newlines, colons, brackets, or quotes.
        yaml_string = lambda value: json.dumps(value, ensure_ascii=False)
        fm = "\n".join([
            "---",
            f"id: {mid}",
            f"title: {yaml_string(title)}",
            f"tags: {yaml_string(tags)}",
            f"source: {yaml_string(source)}",
            f"q_state: {q_state}",
            f"created_at: {created_at}",
            "---",
            "",
        ])
        path.write_text(fm + content + "\n", encoding="utf-8")
        return path.relative_to(self.vault) if path.is_relative_to(self.vault) else path

    def _audit(self, mid, action, actor, from_state, to_state, note=None):
        self.db.execute(
            "INSERT INTO audit_log (memory_id,action,actor,from_state,"
            "to_state,note,at) VALUES (?,?,?,?,?,?,?)",
            (mid, action, actor, from_state, to_state, note, _now()))
