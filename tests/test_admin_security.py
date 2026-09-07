"""管理者bootstrap、server-side権限、faviconの回帰テスト。"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app


def client_for(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.init_db()
    return TestClient(app)


def clear_admin_environment(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)


def test_admin_bootstrap_is_idempotent_and_hashes_password(tmp_path: Path, monkeypatch) -> None:
    clear_admin_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "initial-admin-password")
    client_for(tmp_path, monkeypatch)

    admin = db.get_admin_user()
    assert admin is not None
    assert admin["email"] == "admin@example.com"
    assert admin["role"] == "admin"
    assert admin["password_hash"] != "initial-admin-password"
    assert db.verify_password("initial-admin-password", admin["password_hash"])

    db.init_db()
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").fetchone()["count"] == 1


def test_existing_user_is_preserved_when_explicitly_promoted(tmp_path: Path, monkeypatch) -> None:
    clear_admin_environment(monkeypatch)
    client_for(tmp_path, monkeypatch)
    existing = db.create_user("existing@example.com", "existing-password")

    monkeypatch.setenv("ADMIN_EMAIL", "existing@example.com")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "unused-initial-password")
    db.init_db()

    admin = db.get_admin_user()
    assert admin is not None
    assert admin["id"] == existing["id"]
    assert db.verify_password("existing-password", admin["password_hash"])
    assert not db.verify_password("unused-initial-password", admin["password_hash"])


def test_admin_endpoint_uses_server_side_role_check(tmp_path: Path, monkeypatch) -> None:
    clear_admin_environment(monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "initial-admin-password")
    client = client_for(tmp_path, monkeypatch)

    anonymous = TestClient(app)
    assert anonymous.get("/api/admin/status").status_code == 401
    invalid_login = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert invalid_login.status_code == 401

    login = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "initial-admin-password"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    status = client.get("/api/admin/status")
    assert status.status_code == 200
    assert status.json() == {"admin": True, "email": "admin@example.com", "role": "admin"}

    client.post("/logout", follow_redirects=False)
    client.post(
        "/register",
        data={"email": "normal@example.com", "password": "normal-password"},
        follow_redirects=False,
    )
    assert client.get("/api/admin/status").status_code == 403


def test_missing_admin_environment_does_not_break_startup(tmp_path: Path, monkeypatch) -> None:
    clear_admin_environment(monkeypatch)
    client_for(tmp_path, monkeypatch)
    assert db.get_admin_user() is None
    user = db.create_user("normal@example.com", "normal-password")
    assert user["role"] == "user"


def test_favicon_assets_and_template_links_are_reachable(tmp_path: Path, monkeypatch) -> None:
    clear_admin_environment(monkeypatch)
    client = client_for(tmp_path, monkeypatch)
    login_page = client.get("/login")
    assert login_page.status_code == 200
    for path in (
        "/static/icons/favicon.ico",
        "/static/icons/favicon.svg",
        "/static/icons/favicon-16x16.png",
        "/static/icons/favicon-32x32.png",
        "/static/icons/apple-touch-icon.png",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content
        assert path in login_page.text

    for path, expected_size in (
        ("/static/icons/favicon-16x16.png", (16, 16)),
        ("/static/icons/favicon-32x32.png", (32, 32)),
        ("/static/icons/apple-touch-icon.png", (180, 180)),
    ):
        image = Image.open(BytesIO(client.get(path).content))
        assert image.size == expected_size

    assert client.get("/static/icons/favicon.ico").headers["content-type"] == "image/x-icon"
    assert "ADMIN_INITIAL_PASSWORD" not in login_page.text
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        assert api_key not in login_page.text
