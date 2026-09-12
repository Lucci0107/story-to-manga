"""Storage参照グラフと孤児分類の読み取り専用テスト。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app import db
from app.services.storage import (
    LocalFileStorage,
    classify_storage_objects,
    normalize_storage_reference,
    storage_cleanup_dry_run,
)


def _setup_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.init_db()
    user = db.create_user("orphan@example.com", "orphan-password")
    project = db.create_project(user["id"], "監査用Project", "本文")
    return user, project


def _set_old(path: Path, seconds: int = 2 * 24 * 60 * 60) -> None:
    timestamp = time.time() - seconds
    os.utime(path, (timestamp, timestamp))


def test_canonical_graph_keeps_current_historical_and_character_assets_referenced(tmp_path: Path, monkeypatch) -> None:
    user, project = _setup_db(tmp_path, monkeypatch)
    project_slug = project["id"]
    storyboard = [
        {
            "id": "page-1",
            "page_number": 1,
            "panels": [
                {
                    "id": "panel-a",
                    "revision": 2,
                    "generation_status": "completed",
                    "image_url": f"/media/{project_slug}/panel-a-r2.png",
                    "panel_direction": {
                        "generation_canvas": {
                            "source_asset": f"assets/{project_slug}/panel-a-r1.png"
                        }
                    },
                }
            ],
        }
    ]
    characters = [
        {
            "id": "character-a",
            "name": "人物",
            "reference_image_url": f"/media/{project_slug}/character-ref.png",
        }
    ]
    db.update_project(project["id"], user["id"], storyboard=storyboard, characters=characters)
    storage = LocalFileStorage(tmp_path)
    for filename in ("panel-a-r1.png", "panel-a-r2.png", "panel-a-r3.png", "character-ref.png"):
        storage.put_bytes(f"assets/{project_slug}/{filename}", b"asset")
    for filename in ("panel-a-r1.png", "panel-a-r2.png", "panel-a-r3.png", "character-ref.png"):
        _set_old(tmp_path / "assets" / project_slug / filename)

    report = classify_storage_objects(storage, reference_graph=db.collect_storage_references(), now_epoch=time.time())
    by_key = {item["key"]: item for item in report["files"]}
    assert all(by_key[f"assets/{project_slug}/{name}"]["classification"] == "REFERENCED" for name in ("panel-a-r1.png", "panel-a-r2.png", "panel-a-r3.png"))
    assert by_key[f"assets/{project_slug}/character-ref.png"]["classification"] == "REFERENCED"
    assert any(owner.get("source") == "panel_revision_series" for owner in by_key[f"assets/{project_slug}/panel-a-r3.png"]["referencing_entities"])


def test_export_storage_key_and_legacy_file_path_are_referenced(tmp_path: Path, monkeypatch) -> None:
    user, project = _setup_db(tmp_path, monkeypatch)
    storage = LocalFileStorage(tmp_path)
    modern = storage.export_key(project["id"], "export-modern", "pdf")
    legacy = storage.export_key(project["id"], "export-legacy", "zip")
    storage.put_bytes(modern, b"pdf")
    storage.put_bytes(legacy, b"zip")
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, format, storage_key, file_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("export-modern", project["id"], "pdf", modern, None, "completed", db.utc_now()),
        )
        conn.execute(
            "INSERT INTO exports (id, project_id, format, storage_key, file_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("export-legacy", project["id"], "zip", None, str(tmp_path / legacy), "completed", db.utc_now()),
        )
    report = classify_storage_objects(storage, reference_graph=db.collect_storage_references(), now_epoch=time.time())
    by_key = {item["key"]: item for item in report["files"]}
    assert by_key[modern]["classification"] == "REFERENCED"
    assert by_key[legacy]["classification"] == "REFERENCED"
    assert by_key[legacy]["legacy_reference_detected"] is True


def test_unresolvable_legacy_reference_protects_matching_filename_as_uncertain(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    key = "assets/project-1/panel-r1.png"
    storage.put_bytes(key, b"legacy")
    _set_old(tmp_path / key)
    report = classify_storage_objects(
        storage,
        reference_graph={
            "unresolved": {
                "assets": [
                    {
                        "source": "project_json",
                        "source_field": "file_path",
                        "value": "/old-volume/assets/project-1/panel-r1.png",
                        "basename": "panel-r1.png",
                    }
                ]
            }
        },
        now_epoch=time.time(),
    )
    item = report["files"][0]
    assert item["classification"] == "UNCERTAIN"
    assert "解決できない" in item["reason"]


def test_old_managed_unreferenced_file_is_confirmed_orphan(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    key = "assets/deleted-project/panel-old-r1.png"
    storage.put_bytes(key, b"orphan")
    _set_old(tmp_path / key)
    report = classify_storage_objects(storage, now_epoch=time.time())
    item = report["files"][0]
    assert item["classification"] == "CONFIRMED_ORPHAN"
    assert report["reclaimable_bytes"] == len(b"orphan")


def test_recent_unknown_and_active_job_files_are_uncertain(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    storage.put_bytes("assets/panel/recent-r1.png", b"new")
    storage.put_bytes("assets/panel/unknown.bin", b"unknown")
    _set_old(tmp_path / "assets/panel/unknown.bin")
    graph = {
        "protected_prefixes": {
            "assets": {
                "assets/panel/active-r": [{"source": "generation_job", "job_id": "job-1"}]
            }
        }
    }
    storage.put_bytes("assets/panel/active-r1.png", b"active")
    _set_old(tmp_path / "assets/panel/active-r1.png")
    report = classify_storage_objects(storage, reference_graph=graph, now_epoch=time.time())
    by_key = {item["key"]: item for item in report["files"]}
    assert by_key["assets/panel/recent-r1.png"]["classification"] == "UNCERTAIN"
    assert by_key["assets/panel/unknown.bin"]["classification"] == "UNCERTAIN"
    assert by_key["assets/panel/active-r1.png"]["classification"] == "UNCERTAIN"
    assert "Job" in by_key["assets/panel/active-r1.png"]["reason"]


def test_system_files_and_traversal_are_excluded(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    (tmp_path / "assets" / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "p" / ".panel-r1.png").write_bytes(b"hidden")
    (tmp_path / "assets" / "p" / "panel-r1.png-wal").write_bytes(b"wal")
    report = classify_storage_objects(storage, now_epoch=time.time() + 3 * 86400)
    assert {item["classification"] for item in report["files"]} == {"IGNORED_SYSTEM_FILE"}
    assert normalize_storage_reference(storage, "assets/../outside.png") is None
    assert normalize_storage_reference(storage, "https://example.invalid/assets/x.png") is None
    assert normalize_storage_reference(storage, "/tmp/other/assets/x.png") is None


def test_dry_run_only_lists_confirmed_orphans_and_does_not_mutate(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    key = "exports/old-export.pdf"
    storage.put_bytes(key, b"old")
    _set_old(tmp_path / key)
    before = (tmp_path / key).read_bytes()
    report = classify_storage_objects(storage, now_epoch=time.time())
    dry_run = storage_cleanup_dry_run(report)
    assert dry_run["mode"] == "dry_run"
    assert dry_run["file_count"] == 1
    assert dry_run["would_delete"][0]["classification"] == "CONFIRMED_ORPHAN"
    assert (tmp_path / key).read_bytes() == before
