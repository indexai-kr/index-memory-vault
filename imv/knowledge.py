"""index-memory-vault: knowledge-base read surface.

A second, read-only store alongside the memory vault. Where `VaultStore`
holds whole memories, this holds ingested documents split into chunks with
independent policy axes (approval_state x search_tier), as specified in
INDEX Memory Vault v0.2.3-r1.

Design invariants:
- Read-only. This module opens the database with mode=ro and issues no
  writes; ingestion and approval happen elsewhere.
- The policy gate is enforced here, in the server, not by the caller.
  Only approval_state='approved' AND search_tier IN ('primary','reference')
  is retrievable. There is no parameter to bypass it.
- Every result carries the identifiers needed to trace a citation back to
  its origin: chunk, document, version, and source.
- Nothing is invented. A query with no admissible chunk returns an empty
  result plus the count that the gate withheld.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

RETRIEVABLE_TIERS = ("primary", "reference")
RETRIEVABLE_APPROVAL = "approved"

_GATE_SQL = (
    f"c.approval_state = '{RETRIEVABLE_APPROVAL}' "
    f"AND c.search_tier IN {RETRIEVABLE_TIERS}"
)

GATE_DESCRIPTION = (
    f"approval_state={RETRIEVABLE_APPROVAL} "
    f"AND search_tier IN {RETRIEVABLE_TIERS}"
)

# Korean particles / interrogatives that carry no discriminative value once
# an exact multi-token match has already failed. Mirrors VaultStore._STOPWORDS.
_STOPWORDS = {
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "와", "과",
    "도", "로", "으로", "및", "그리고", "무엇", "어디", "누구", "언제",
    "어떻게", "알려줘", "알려", "the", "a", "an", "of", "is", "are",
    "what", "who", "where", "when", "how",
}


class KnowledgeUnavailable(RuntimeError):
    """The knowledge base is not configured or not readable.

    Raised instead of returning an empty result, so an absent database is
    never mistaken for "no evidence exists".
    """


def _limit(value: int, maximum: int = 100) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


@dataclass
class ChunkRef:
    """One admissible chunk plus the full citation coordinate."""

    chunk_id: int
    document_id: int
    version_id: int | None
    version_no: int | None
    chunk_no: int | None
    section: str | None
    page: int | None
    approval_state: str
    search_tier: str
    content_hash: str | None
    document_title: str | None
    document_status: str | None
    source_type: str | None
    source_id: str | None
    source_path: str | None
    content: str

    def public(self, *, snippet_for: str | None = None,
               max_chars: int = 1200) -> dict:
        d = asdict(self)
        d["content_chars"] = len(self.content)
        d["content"] = _excerpt(self.content, snippet_for, max_chars)
        d["truncated"] = d["content_chars"] > len(d["content"])
        return d


def _excerpt(content: str, term: str | None, max_chars: int) -> str:
    """Window the content around the match so a citation shows why it matched.

    The whole query rarely appears verbatim, so fall back to its longest
    individual terms before giving up and returning the head of the chunk.
    """
    if len(content) <= max_chars:
        return content
    haystack = content.lower()
    candidates = [term] if term else []
    if term:
        candidates += sorted(
            (t for t in term.split() if t.lower() not in _STOPWORDS),
            key=len, reverse=True)
    for candidate in candidates:
        pos = haystack.find(candidate.lower())
        if pos >= 0:
            start = max(0, pos - max_chars // 3)
            return content[start:start + max_chars]
    return content[:max_chars]


_SELECT = """
SELECT c.id            AS chunk_id,
       c.document_id   AS document_id,
       c.version_id    AS version_id,
       v.version_no    AS version_no,
       c.chunk_no      AS chunk_no,
       c.section       AS section,
       c.page          AS page,
       c.approval_state AS approval_state,
       c.search_tier   AS search_tier,
       c.content_hash  AS content_hash,
       d.title         AS document_title,
       d.status        AS document_status,
       d.source_type   AS source_type,
       d.source_id     AS source_id,
       d.path          AS source_path,
       c.content       AS content
FROM chunks c
LEFT JOIN documents d ON d.id = c.document_id
LEFT JOIN document_versions v ON v.id = c.version_id
"""


class KnowledgeStore:
    """Read-only accessor for an imv_knowledge.db."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise KnowledgeUnavailable(f"knowledge db not found: {self.db_path}")
        self.db = sqlite3.connect(
            f"file:{self.db_path.as_posix()}?mode=ro", uri=True,
            check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("SELECT 1 FROM chunks LIMIT 1")
        except sqlite3.Error as exc:
            self.db.close()
            raise KnowledgeUnavailable(
                f"knowledge db not readable: {self.db_path} ({exc})") from exc

    def close(self) -> None:
        self.db.close()

    # ---------- read path ----------

    def search(self, query: str, limit: int = 10) -> tuple[list[ChunkRef], str, int]:
        """Search admissible chunks.

        There is no FTS index on chunks, so this is a bounded LIKE scan over
        the gated subset (small by construction). Stages, first non-empty wins:
          1. like_and - every core token present in one chunk
          2. like_or  - any core token present
          3. none     - empty; never invents content

        Returns (results, retrieval_path, withheld_by_policy) where the last
        value counts chunks that matched the text but failed the policy gate.
        """
        limit = _limit(limit)
        query = (query or "").strip()
        if not query:
            return [], "none", 0

        tokens = [t for t in query.split() if t.lower() not in _STOPWORDS]
        # Short tokens are demoted, not banned: a one-character CJK term may be
        # the whole query. Stopwords are dropped outright at both stages, so a
        # query made only of particles matches nothing instead of everything.
        core = [t for t in tokens if len(t) >= 2] or tokens
        core = sorted(core, key=len, reverse=True)[:6]
        core = [re.sub(r"[%_]", " ", t).strip() for t in core]
        core = [t for t in core if t]
        if not core:
            return [], "none", 0

        withheld = self._withheld(core)

        for path, joiner in (("like_and", " AND "), ("like_or", " OR ")):
            clauses, params = [], []
            for token in core:
                clauses.append("c.content LIKE ?")
                params.append(f"%{token}%")
            sql = (f"{_SELECT} WHERE {_GATE_SQL} AND ("
                   + joiner.join(clauses)
                   + ") ORDER BY c.search_tier = 'primary' DESC, c.id LIMIT ?")
            params.append(limit)
            rows = self.db.execute(sql, params).fetchall()
            if rows:
                return [ChunkRef(**dict(r)) for r in rows], path, withheld
            if len(core) == 1:
                break  # AND and OR are identical for a single token

        return [], "none", withheld

    def get(self, chunk_id: int) -> ChunkRef | None:
        """Fetch one admissible chunk in full. Returns None when the chunk
        does not exist OR is withheld by the policy gate — the caller cannot
        distinguish the two, which is deliberate."""
        if not isinstance(chunk_id, int) or isinstance(chunk_id, bool):
            raise ValueError("chunk_id must be an integer")
        row = self.db.execute(
            f"{_SELECT} WHERE {_GATE_SQL} AND c.id = ?", (chunk_id,)).fetchone()
        return ChunkRef(**dict(row)) if row else None

    def policy_snapshot(self) -> dict:
        """Measured counts, for honest status reporting."""
        total = self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        retrievable = self.db.execute(
            f"SELECT count(*) FROM chunks c WHERE {_GATE_SQL}").fetchone()[0]
        docs = self.db.execute(
            f"SELECT count(DISTINCT c.document_id) FROM chunks c WHERE {_GATE_SQL}"
        ).fetchone()[0]
        return {
            "knowledge_db": str(self.db_path),
            "chunks_total": total,
            "chunks_retrievable": retrievable,
            "documents_retrievable": docs,
            "gate": GATE_DESCRIPTION,
        }

    # ---------- internals ----------

    def _withheld(self, core: list[str]) -> int:
        clauses = " OR ".join("c.content LIKE ?" for _ in core)
        params = [f"%{t}%" for t in core]
        return self.db.execute(
            f"SELECT count(*) FROM chunks c WHERE NOT ({_GATE_SQL}) "
            f"AND ({clauses})", params).fetchone()[0]
