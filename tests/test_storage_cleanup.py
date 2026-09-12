"""Cleanup Planの再検証・認可・局所的な削除を検証する。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services.storage import LocalFileStorage
from app.services.storage_cleanup import (
    StorageCleanupConfirmationError,
    _CleanupPathError,
    _safe_delete_path,
    create_cleanup_dry_run_plan,
    execute_cleanup_plan,
)
from app.services import storage_cleanup as storage_cleanup_service


def _old(path: Path, seconds: int = 3 * 24 * 60 * 60) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)
    db.init_db()
    user = db.create_user(f"cleanup-{tmp_path.name}@example.com", "cleanup-password")
    project = db.create_project(user["id"], "Cleanup検証", "テスト本文")
    return user, project, LocalFileStorage(tmp_path)


def _orphan(storage: LocalFileStorage, key: str, payload: bytes = b"orphan") -> Path:
    storage.put_bytes(key, payload)
    path = storage.root / key
    _old(path)
    return path


def test_dry_run_persists_only_confirmed_orphans_and_mutates_no_file(tmp_path: Path, monkeypatch) -> None:
    user, _project, storage = _setup(tmp_path, monkeypatch)
    orphan_path = _orphan(storage, "assets/deleted-project/panel-old-r1.png", b"old")
    storage.put_bytes("assets/deleted-project/panel-new-r1.png", b"new")

    result = create_cleanup_dry_run_plan(user["id"], storage=storage)
    plan = result["plan"]
    assert result["dry_run"]["mode"] == "dry_run"
    assert plan["status"] == "PLANNED"
    assert plan["dry_run"] is True
    assert plan["candidate_count"] == 1
    assert plan["confirmed_orphan_count"] == 1
    assert [item["storage_key"] for item in plan["items"]] == [
        "assets/deleted-project/panel-old-r1.png"
    ]
    assert orphan_path.exists()
    assert (storage.root / "assets/deleted-project/panel-new-r1.png").exists()


def test_execute_revalidates_reference_and_keeps_file(tmp_path: Path, monkeypatch) -> None:
    user, project, storage = _setup(tmp_path, monkeypatch)
    key = f"assets/{project['id']}/panel-old-r1.png"
    path = _orphan(storage, key)
    planned = create_cleanup_dry_run_plan(user["id"], storage=storage)["plan"]
    storyboard = [
        {"id": "page-1", "panels": [{"id": "panel-old", "image_url": f"/media/{project['id']}/panel-old-r1.png"}]}
    ]
    db.update_project(project["id"], user["id"], storyboard=storyboard)

    result = execute_cleanup_plan(planned["id"], user["id"], "DELETE", storage=storage)
    assert result["summary"]["deleted_count"] == 0
    assert result["summary"]["skipped_count"] == 1
    assert result["plan"]["status"] == "COMPLETED"
    assert result["plan"]["items"][0]["delete_status"] == "SKIPPED_REFERENCED"
    assert path.exists()


def test_active_job_is_revalidated_as_uncertain(tmp_path: Path, monkeypatch) -> None:
    user, project, storage = _setup(tmp_path, monkeypatch)
    key = f"assets/{project['id']}/panel-active-r1.png"
    path = _orphan(storage, key)
    planned = create_cleanup_dry_run_plan(user["id"], storage=storage)["plan"]
    db.create_generation_job(project["id"], "panel-active", "cleanup-active", "panel_artwork")

    result = execute_cleanup_plan(planned["id"], user["id"], "DELETE", storage=storage)
    assert result["summary"]["deleted_count"] == 0
    assert result["plan"]["items"][0]["delete_status"] == "SKIPPED_UNCERTAIN"
    assert path.exists()


def test_execute_requires_exact_confirmation(tmp_path: Path, monkeypatch) -> None:
    user, _project, storage = _setup(tmp_path, monkeypatch)
    planned = create_cleanup_dry_run_plan(user["id"], storage=storage)["plan"]
    with pytest.raises(StorageCleanupConfirmationError):
        execute_cleanup_plan(planned["id"], user["id"], "delete", storage=storage)


def test_execute_is_idempotent_and_does_not_delete_db_rows(tmp_path: Path, monkeypatch) -> None:
    user, project, storage = _setup(tmp_path, monkeypatch)
    path = _orphan(storage, "exports/obsolete-export-1.pdf", b"pdf")
    planned = create_cleanup_dry_run_plan(user["id"], storage=storage)["plan"]
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, format, storage_key, file_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("export-row", project["id"], "pdf", None, None, "failed", db.utc_now()),
        )
    first = execute_cleanup_plan(planned["id"], user["id"], "DELETE", storage=storage)
    second = execute_cleanup_plan(planned["id"], user["id"], "DELETE", storage=storage)
    assert first["summary"]["deleted_count"] == 1
    assert second["idempotent"] is True
    assert second["summary"]["deleted_count"] == 1
    assert not path.exists()
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM exports WHERE id = 'export-row'").fetchone()["count"] == 1


def test_path_safety_rejects_traversal_and_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    _user, _project, storage = _setup(tmp_path, monkeypatch)
    with pytest.raises(_CleanupPathError):
        _safe_delete_path(storage, "assets/../outside-r1.png", "assets")
    outside = tmp_path.parent / f"cleanup-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        link = storage.root / "assets" / "link"
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(_CleanupPathError):
            _safe_delete_path(storage, "assets/link/panel-r1.png", "assets")
    finally:
        link.unlink(missing_ok=True)
        outside.rmdir()


def test_partial_file_failure_isolated_and_audited(tmp_path: Path, monkeypatch) -> None:
    user, _project, storage = _setup(tmp_path, monkeypatch)
    good = _orphan(storage, "assets/deleted-project/good-r1.png", b"good")
    bad = _orphan(storage, "assets/deleted-project/bad-r1.png", b"bad")
    planned = create_cleanup_dry_run_plan(user["id"], storage=storage)["plan"]
    original = storage_cleanup_service._safe_delete_path

    def fail_one(storage_value, storage_key, category):
        if storage_key.endswith("bad-r1.png"):
            raise OSError("simulated test failure")
        return original(storage_value, storage_key, category)

    monkeypatch.setattr(storage_cleanup_service, "_safe_delete_path", fail_one)
    result = execute_cleanup_plan(planned["id"], user["id"], "DELETE", storage=storage)
    assert result["plan"]["status"] == "PARTIAL"
    assert result["summary"]["deleted_count"] == 1
    assert result["summary"]["failed_count"] == 1
    assert not good.exists()
    assert bad.exists()
    statuses = {item["storage_key"]: item["delete_status"] for item in result["plan"]["items"]}
    assert statuses["assets/deleted-project/good-r1.png"] == "DELETED"
    assert statuses["assets/deleted-project/bad-r1.png"] == "FAILED"


def _admin_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_EMAIL", "cleanup-admin@example.com")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "cleanup-admin-password")
    db.init_db()
    return TestClient(app)


def test_cleanup_api_is_admin_only_and_dry_run_does_not_delete(tmp_path: Path, monkeypatch) -> None:
    client = _admin_client(tmp_path, monkeypatch)
    anonymous = TestClient(app)
    assert anonymous.post("/api/admin/storage/cleanup/dry-run").status_code == 401
    login = client.post(
        "/login",
        data={"email": "cleanup-admin@example.com", "password": "cleanup-admin-password"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    storage = LocalFileStorage(tmp_path)
    path = _orphan(storage, "assets/deleted-project/panel-old-r1.png", b"old")
    dry_run = client.post("/api/admin/storage/cleanup/dry-run")
    assert dry_run.status_code == 200
    payload = dry_run.json()
    assert payload["dry_run"]["candidate_count"] == 1
    assert path.exists()
    bad_origin = client.post(
        "/api/admin/storage/cleanup/execute",
        json={"plan_id": payload["plan"]["id"], "confirmation": "DELETE"},
        headers={"Origin": "https://evil.invalid"},
    )
    assert bad_origin.status_code == 403
    bad_confirmation = client.post(
        "/api/admin/storage/cleanup/execute",
        json={"plan_id": payload["plan"]["id"], "confirmation": "delete"},
    )
    assert bad_confirmation.status_code == 400
    assert path.exists()


def test_normal_user_cannot_create_cleanup_plan(tmp_path: Path, monkeypatch) -> None:
    client = _admin_client(tmp_path, monkeypatch)
    client.post("/register", data={"email": "normal-cleanup@example.com", "password": "normal-password"}, follow_redirects=False)
    assert client.post("/api/admin/storage/cleanup/dry-run").status_code == 403
