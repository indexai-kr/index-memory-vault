from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

KST = ZoneInfo("Asia/Seoul")
TERMS_VERSION = "2026-07-05"


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def future_kst(**kwargs: int) -> str:
    return (datetime.now(KST) + timedelta(**kwargs)).isoformat(timespec="seconds")


def email_digest(email: str) -> str:
    value = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"sha256:{value}"


class PortalStore:
    def __init__(self, data_dir: str | Path):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.members = sqlite3.connect(root / "members.db", check_same_thread=False)
        self.ledger = sqlite3.connect(root / "ledger.db", check_same_thread=False)
        self.members.row_factory = sqlite3.Row
        self.ledger.row_factory = sqlite3.Row
        self.hasher = PasswordHasher()
        self._init_schema()

    def _init_schema(self) -> None:
        self.members.executescript("""
        CREATE TABLE IF NOT EXISTS members (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, email_hash TEXT NOT NULL,
          name TEXT NOT NULL, phone TEXT, pw_hash TEXT NOT NULL,
          email_verified INTEGER NOT NULL DEFAULT 0, created_kst TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS email_tokens (
          token TEXT PRIMARY KEY, member_id TEXT NOT NULL, purpose TEXT NOT NULL,
          expires_kst TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
          sid TEXT PRIMARY KEY, member_id TEXT NOT NULL, created_kst TEXT NOT NULL,
          expires_kst TEXT NOT NULL
        );
        """)
        self.ledger.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
          timestamp_kst TEXT NOT NULL, member_id TEXT, payload TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        """)

    def event(self, event: str, member_id: str | None, payload: dict) -> None:
        self.ledger.execute(
            "INSERT INTO events(event,timestamp_kst,member_id,payload) VALUES(?,?,?,?)",
            (event, now_kst(), member_id, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        self.ledger.commit()

    def register(self, email: str, name: str, phone: str | None, password: str) -> tuple[str, str]:
        email = email.strip().lower()
        member_id = "index_member_" + secrets.token_hex(13)
        token = secrets.token_urlsafe(32)
        try:
            self.members.execute(
                "INSERT INTO members VALUES(?,?,?,?,?,?,0,?,'active')",
                (member_id, email, email_digest(email), name.strip(), phone, self.hasher.hash(password), now_kst()),
            )
        except sqlite3.IntegrityError as exc:
            self.members.rollback()
            raise ValueError("email already registered") from exc
        self.members.execute(
            "INSERT INTO email_tokens VALUES(?,?, 'verify', ?,0)",
            (token, member_id, future_kst(hours=24)),
        )
        self.members.commit()
        self.event("member_created", member_id, {"email_hash": email_digest(email)})
        self.event("terms_accepted", member_id, {"terms_version": TERMS_VERSION})
        return member_id, token

    def verify_email(self, token: str) -> str:
        row = self.members.execute(
            "SELECT * FROM email_tokens WHERE token=? AND purpose='verify' AND used=0", (token,)
        ).fetchone()
        if not row or datetime.fromisoformat(row["expires_kst"]) < datetime.now(KST):
            raise ValueError("invalid or expired token")
        self.members.execute("UPDATE email_tokens SET used=1 WHERE token=?", (token,))
        self.members.execute("UPDATE members SET email_verified=1 WHERE id=?", (row["member_id"],))
        self.members.commit()
        self.event("email_verified", row["member_id"], {})
        return row["member_id"]

    def login(self, email: str, password: str) -> str:
        row = self.members.execute(
            "SELECT * FROM members WHERE email=? AND status='active'", (email.strip().lower(),)
        ).fetchone()
        if not row:
            raise ValueError("invalid credentials")
        try:
            self.hasher.verify(row["pw_hash"], password)
        except VerifyMismatchError as exc:
            raise ValueError("invalid credentials") from exc
        if not row["email_verified"]:
            raise PermissionError("email verification required")
        sid = secrets.token_urlsafe(32)
        self.members.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)", (sid, row["id"], now_kst(), future_kst(days=30))
        )
        self.members.commit()
        return sid

    def member_for_session(self, sid: str | None):
        if not sid:
            return None
        return self.members.execute(
            "SELECT m.* FROM sessions s JOIN members m ON m.id=s.member_id "
            "WHERE s.sid=? AND s.expires_kst>? AND m.status='active'",
            (sid, now_kst()),
        ).fetchone()

    def logout(self, sid: str | None) -> None:
        if sid:
            self.members.execute("DELETE FROM sessions WHERE sid=?", (sid,))
            self.members.commit()

    def downloads(self, member_id: str) -> list[dict]:
        rows = self.ledger.execute(
            "SELECT payload FROM events WHERE event='mcp_download' AND member_id=? ORDER BY seq DESC",
            (member_id,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.members.close()
        self.ledger.close()
