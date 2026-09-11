"""Persistent Disk容量ガードと読み取り監査のテスト。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services.artwork import save_panel_artwork
from app.services.export import export_pdf, export_zip
from app.services.storage import (
    LocalFileStorage,
    StorageCapacityError,
    ensure_storage_capacity,
    find_orphan_storage_objects,
    storage_status,
    storage_usage,
)


def _disk_usage(total: int, used: int):
    return lambda _path: SimpleNamespace(total=total, used=used, free=max(0, total - used))


def test_storage_thresholds_warning_critical_and_block(tmp_path: Path) -> None:
    warning = storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 80))
    critical = storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 90))
    assert warning.status == "warning"
    assert critical.status == "critical"
    with pytest.raises(StorageCapacityError) as blocked:
        ensure_storage_capacity(tmp_path, operation="image_generation", disk_usage_fn=_disk_usage(100, 95))
    assert blocked.value.retryable is True
    assert blocked.value.usage.usage_percent == 95.0
    assert blocked.value.as_dict()["code"] == "storage_capacity_exceeded"


def test_storage_status_reports_breakdown_and_retention_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "assets" / "project").mkdir(parents=True)
    (tmp_path / "exports").mkdir(parents=True)
    (tmp_path / "assets" / "project" / "panel.png").write_bytes(b"asset")
    (tmp_path / "exports" / "project-export.pdf").write_bytes(b"export")
    (tmp_path / "story_manga.sqlite3").write_bytes(b"db")
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    status = storage_status(tmp_path, disk_usage_fn=_disk_usage(1000, 800))
    assert status["usage_percent"] == 80.0
    assert status["assets_bytes"] == 5
    assert status["exports_bytes"] == 6
    assert status["database_bytes"] == 2
    assert status["retention_policy"]["automatic_deletion"] is False


def test_orphan_report_only_does_not_delete_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    storage.put_bytes("assets/p/used.png", b"used")
    storage.put_bytes("assets/p/orphan.png", b"orphan")
    storage.put_bytes("exports/p-used.pdf", b"used-export")
    storage.put_bytes("exports/p-orphan.zip", b"orphan-export")
    report = find_orphan_storage_objects(
        storage,
        referenced_asset_keys=["assets/p/used.png"],
        referenced_export_keys=["exports/p-used.pdf"],
    )
    assert [item["key"] for item in report["assets"]] == ["assets/p/orphan.png"]
    assert [item["key"] for item in report["exports"]] == ["exports/p-orphan.zip"]
    assert (tmp_path / "assets" / "p" / "orphan.png").exists()
    assert (tmp_path / "exports" / "p-orphan.zip").exists()


def test_artwork_generation_is_blocked_before_revision_or_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    storage = LocalFileStorage(tmp_path)
    monkeypatch.setattr(
        "app.services.artwork.ensure_storage_capacity",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            StorageCapacityError("image_generation", storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 95)))
        ),
    )
    panel = {"id": "panel-a", "revision": 3}
    with pytest.raises(StorageCapacityError):
        save_panel_artwork("project-a", panel, {}, storage=storage)
    assert panel["revision"] == 3
    assert not list((tmp_path / "assets").rglob("*"))


def test_exports_are_blocked_before_render_and_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = LocalFileStorage(tmp_path)
    project = {"id": "project-a", "title": "test", "settings": {}, "storyboard": []}
    error = StorageCapacityError("pdf_export", storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 95)))
    monkeypatch.setattr("app.services.export.ensure_storage_capacity", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(StorageCapacityError):
        export_pdf(project, storage)
    with pytest.raises(StorageCapacityError):
        export_zip(project, storage)
    assert not list((tmp_path / "exports").iterdir())


def test_generation_api_blocks_before_job_creation_at_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "capacity-generation@example.com", "password": "long-password"}, follow_redirects=False)
    project = client.post("/api/projects", data={"title": "容量", "story_text": "本文"}).json()["project"]
    from app.services.layout import reflow_page

    page = reflow_page({"id": "page-a", "page_number": 1, "panels": [{"id": "panel-a", "dialogue": [], "image_url": None}]}, project["settings"])
    db.update_project(project["id"], project["user_id"], storyboard=[page])
    usage = storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 95))
    error = StorageCapacityError("image_generation", usage)
    monkeypatch.setattr("app.main.ensure_storage_capacity", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    blocked = client.post(f"/api/projects/{project['id']}/generate", json={"panel_ids": []})
    assert blocked.status_code == 507
    assert blocked.json()["detail"]["retryable"] is True
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM generation_jobs WHERE project_id = ?", (project["id"],)).fetchone()["count"] == 0


def test_export_api_blocks_before_export_record_at_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "capacity-export@example.com", "password": "long-password"}, follow_redirects=False)
    project = client.post("/api/projects", data={"title": "容量Export", "story_text": "本文"}).json()["project"]
    usage = storage_usage(tmp_path, disk_usage_fn=_disk_usage(100, 95))
    error = StorageCapacityError("pdf_export", usage)
    monkeypatch.setattr("app.main.ensure_storage_capacity", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    blocked = client.post(f"/api/projects/{project['id']}/export", json={"format": "pdf"})
    assert blocked.status_code == 507
    assert blocked.json()["detail"]["operation"] == "pdf_export"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM exports WHERE project_id = ?", (project["id"],)).fetchone()["count"] == 0


def test_admin_storage_endpoint_is_read_only_and_reports_db_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_EMAIL", "storage-admin@example.com")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "storage-admin-password")
    db.init_db()
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "storage-admin@example.com", "password": "storage-admin-password"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    response = client.get("/api/admin/storage")
    assert response.status_code == 200
    payload = response.json()["storage"]
    assert {"total_bytes", "used_bytes", "free_bytes", "usage_percent", "assets_bytes", "exports_bytes", "database_bytes"} <= payload.keys()
    assert payload["orphan_candidates"]["mode"] == "report_only"
    assert client.get("/admin/storage").status_code == 200
