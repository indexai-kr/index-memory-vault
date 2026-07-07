import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from imv.portal.app import create_app


@pytest.fixture
def portal(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()
    package = releases / "imv-setup-0.2.0.exe"
    package.write_bytes(b"official-imv-build")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    (releases / "releases.json").write_text(json.dumps([{
        "version": "0.2.0", "file": package.name, "sha256": checksum,
        "size": package.stat().st_size, "notes": "v0.2 test release"
    }]), encoding="utf-8")
    app = create_app(tmp_path / "data", releases, testing=True)
    with TestClient(app) as client:
        yield app, client, checksum
    app.state.store.close()


def body(email="master@example.com"):
    return {"email": email, "name": "마스터", "phone": "010-1234-5678",
            "password": "correct horse battery staple", "terms_accepted": True,
            "privacy_accepted": True}


def register(portal, email="master@example.com"):
    app, client, _ = portal
    response = client.post("/api/member/register", json=body(email))
    assert response.status_code == 201
    return app.state.outbox[-1]["token"]


def login_verified(portal):
    _, client, _ = portal
    token = register(portal)
    assert client.get("/api/member/verify", params={"token": token}).status_code == 200
    assert client.post("/api/member/login", json={
        "email": "master@example.com", "password": "correct horse battery staple"
    }).status_code == 200


def test_register_creates_unverified_member(portal):
    app, _, _ = portal
    register(portal)
    row = app.state.store.members.execute("SELECT * FROM members").fetchone()
    assert row["email_verified"] == 0
    assert row["pw_hash"].startswith("$argon2id$")


def test_duplicate_registration_rejected(portal):
    _, client, _ = portal
    register(portal)
    assert client.post("/api/member/register", json=body()).status_code == 409


def test_duplicate_registration_releases_database_lock(portal):
    app, client, _ = portal
    register(portal)
    assert client.post("/api/member/register", json=body()).status_code == 409
    assert client.post("/api/member/register", json=body("next@example.com")).status_code == 201
    other = sqlite3.connect(app.state.store.members.execute("PRAGMA database_list").fetchone()[2])
    try:
        other.execute("UPDATE members SET name=name WHERE email='next@example.com'")
        other.commit()
    finally:
        other.close()


def test_consent_required(portal):
    _, client, _ = portal
    payload = body()
    payload["privacy_accepted"] = False
    assert client.post("/api/member/register", json=payload).status_code == 400


def test_invalid_phone_rejected(portal):
    _, client, _ = portal
    payload = body()
    payload["phone"] = "call-me"
    assert client.post("/api/member/register", json=payload).status_code == 400


def test_unverified_login_rejected(portal):
    _, client, _ = portal
    register(portal)
    response = client.post("/api/member/login", json={"email": "master@example.com",
                           "password": "correct horse battery staple"})
    assert response.status_code == 403


def test_verify_then_login(portal):
    _, client, _ = portal
    login_verified(portal)
    assert client.get("/api/member/me").status_code == 200


def test_verification_token_is_one_time(portal):
    _, client, _ = portal
    token = register(portal)
    assert client.get("/api/member/verify", params={"token": token}).status_code == 200
    assert client.get("/api/member/verify", params={"token": token}).status_code == 400


def test_terms_event_is_separate(portal):
    app, _, _ = portal
    register(portal)
    events = [row[0] for row in app.state.store.ledger.execute("SELECT event FROM events")]
    assert events == ["member_created", "terms_accepted"]


def test_release_list_is_public(portal):
    _, client, checksum = portal
    response = client.get("/api/releases")
    assert response.status_code == 200
    assert response.json()[0]["sha256"] == checksum


def test_download_requires_login(portal):
    _, client, _ = portal
    assert client.post("/api/download/0.2.0").status_code == 401


def test_download_records_matching_checksum(portal):
    app, client, checksum = portal
    login_verified(portal)
    response = client.post("/api/download/0.2.0")
    assert response.status_code == 200
    row = app.state.store.ledger.execute(
        "SELECT payload FROM events WHERE event='mcp_download'"
    ).fetchone()
    payload = json.loads(row["payload"])
    assert response.headers["x-imv-sha256"] == checksum
    assert payload["file_sha256"] == "sha256:" + checksum


def test_ledger_update_and_delete_are_blocked(portal):
    app, _, _ = portal
    register(portal)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        app.state.store.ledger.execute("UPDATE events SET event='tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        app.state.store.ledger.execute("DELETE FROM events")


def test_logout_invalidates_session(portal):
    _, client, _ = portal
    login_verified(portal)
    assert client.post("/api/member/logout").status_code == 200
    assert client.get("/api/member/me").status_code == 401


def test_phone_is_masked(portal):
    _, client, _ = portal
    login_verified(portal)
    assert client.get("/api/member/me").json()["phone"] == "010-****-5678"


def test_five_failed_logins_lock_ip(portal):
    _, client, _ = portal
    register(portal)
    for _ in range(5):
        client.post("/api/member/login", json={"email": "master@example.com", "password": "wrong"})
    assert client.post("/api/member/login", json={
        "email": "master@example.com", "password": "wrong"
    }).status_code == 429
