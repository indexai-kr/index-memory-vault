from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .store import PortalStore, TERMS_VERSION, now_kst

PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{6,24}$")


class RegisterBody(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = None
    password: str = Field(min_length=10, max_length=200)
    terms_accepted: bool
    privacy_accepted: bool

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("invalid email")
        return value


class LoginBody(BaseModel):
    email: str
    password: str


class AttemptGuard:
    def __init__(self):
        self.attempts: dict[tuple[str, str], list[float]] = {}
        self.failures: dict[tuple[str, str], list[float]] = {}

    def check(self, action: str, ip: str) -> None:
        now = time.monotonic()
        key = (action, ip)
        recent = [value for value in self.attempts.get(key, []) if now - value < 60]
        failed = [value for value in self.failures.get(key, []) if now - value < 600]
        self.attempts[key], self.failures[key] = recent, failed
        if len(failed) >= 5:
            raise HTTPException(429, "too many failures; retry in 10 minutes")
        if len(recent) >= 5:
            raise HTTPException(429, "rate limit exceeded")
        recent.append(now)

    def failed(self, action: str, ip: str) -> None:
        self.failures.setdefault((action, ip), []).append(time.monotonic())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _send_verification(email: str, token: str, base_url: str) -> None:
    host = os.environ.get("IMV_SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP is not configured")
    message = EmailMessage()
    message["From"] = "contact@indexai.kr"
    message["To"] = email
    message["Subject"] = "INDEX memory-vault 이메일 인증"
    message.set_content(f"{base_url}/api/member/verify?token={token}")
    port = int(os.environ.get("IMV_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as client:
        client.starttls()
        user, password = os.environ.get("IMV_SMTP_USER"), os.environ.get("IMV_SMTP_PASS")
        if user and password:
            client.login(user, password)
        client.send_message(message)


def create_app(data_dir=None, releases_dir=None, testing: bool = False) -> FastAPI:
    app = FastAPI(title="INDEX memory-vault member portal", version="0.2.1")
    app.state.store = PortalStore(data_dir or os.environ.get("IMV_PORTAL_DATA", "portal-data"))
    app.state.releases = Path(releases_dir or os.environ.get("IMV_RELEASES", "releases"))
    app.state.releases.mkdir(parents=True, exist_ok=True)
    app.state.outbox = []
    app.state.guard = AttemptGuard()
    static_dir = Path(__file__).with_name("static")
    app.mount("/member/assets", StaticFiles(directory=static_dir), name="member-assets")

    @app.get("/member/register", include_in_schema=False)
    def register_page():
        return FileResponse(static_dir / "register.html")

    @app.get("/member/login", include_in_schema=False)
    def login_page():
        return FileResponse(static_dir / "login.html")

    @app.get("/member/releases", include_in_schema=False)
    def releases_page():
        return FileResponse(static_dir / "releases.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        return FileResponse(static_dir / "privacy.html")

    def release_list() -> list[dict]:
        manifest = app.state.releases / "releases.json"
        return json.loads(manifest.read_text("utf-8")) if manifest.exists() else []

    @app.post("/api/member/register", status_code=201)
    def register(body: RegisterBody, request: Request):
        ip = request.client.host if request.client else "unknown"
        app.state.guard.check("register", ip)
        if not body.terms_accepted or not body.privacy_accepted:
            raise HTTPException(400, "terms and privacy consent are required")
        if body.phone and not PHONE_RE.fullmatch(body.phone):
            raise HTTPException(400, "invalid phone number")
        try:
            member_id, token = app.state.store.register(body.email, body.name, body.phone, body.password)
        except ValueError as exc:
            app.state.guard.failed("register", ip)
            raise HTTPException(409, str(exc)) from exc
        if testing:
            app.state.outbox.append({"email": body.email, "token": token})
        else:
            try:
                base_url = os.environ.get("IMV_PUBLIC_BASE_URL", str(request.base_url)).rstrip("/")
                _send_verification(body.email, token, base_url)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from exc
        return {"member_id": member_id, "email_verification": "sent"}

    @app.get("/api/member/verify")
    def verify(token: str):
        try:
            app.state.store.verify_email(token)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"verified": True}

    @app.post("/api/member/login")
    def login(body: LoginBody, response: Response, request: Request):
        ip = request.client.host if request.client else "unknown"
        app.state.guard.check("login", ip)
        try:
            sid = app.state.store.login(body.email, body.password)
        except PermissionError as exc:
            app.state.guard.failed("login", ip)
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            app.state.guard.failed("login", ip)
            raise HTTPException(401, str(exc)) from exc
        response.set_cookie("imv_sid", sid, max_age=30 * 86400, httponly=True, secure=not testing, samesite="lax")
        return {"authenticated": True}

    @app.post("/api/member/logout")
    def logout(response: Response, imv_sid: str | None = Cookie(default=None)):
        app.state.store.logout(imv_sid)
        response.delete_cookie("imv_sid")
        return {"authenticated": False}

    @app.get("/api/releases")
    def releases():
        return release_list()

    @app.get("/api/member/me")
    def me(imv_sid: str | None = Cookie(default=None)):
        member = app.state.store.member_for_session(imv_sid)
        if not member:
            raise HTTPException(401, "authentication required")
        phone = member["phone"]
        if phone:
            digits = re.sub(r"\D", "", phone)
            phone = f"{digits[:3]}-****-{digits[-4:]}" if len(digits) >= 7 else "****"
        return {"id": member["id"], "email": member["email"], "name": member["name"],
                "phone": phone, "downloads": app.state.store.downloads(member["id"])}

    @app.post("/api/download/{version}")
    def download(version: str, imv_sid: str | None = Cookie(default=None)):
        member = app.state.store.member_for_session(imv_sid)
        if not member:
            raise HTTPException(401, "authentication required")
        release = next((item for item in release_list() if item["version"] == version), None)
        if not release:
            raise HTTPException(404, "release not found")
        path = (app.state.releases / release["file"]).resolve()
        if path.parent != app.state.releases.resolve() or not path.is_file():
            raise HTTPException(404, "release file not found")
        actual = _sha256(path)
        if actual != release["sha256"]:
            raise HTTPException(500, "release checksum mismatch")
        payload = {"event": "mcp_download", "timestamp_kst": now_kst(), "user_id": member["id"],
                   "email_hash": member["email_hash"], "package": "index-memory-vault-mcp",
                   "version": version, "license": "personal",
                   "download_id": "dl_" + now_kst()[:10].replace("-", "") + "_" + os.urandom(4).hex(),
                   "file_sha256": "sha256:" + actual, "terms_version": TERMS_VERSION}
        app.state.store.event("mcp_download", member["id"], payload)
        return FileResponse(path, filename=path.name, headers={"X-IMV-SHA256": actual})

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("imv.portal.app:app", host="127.0.0.1", port=8486)
