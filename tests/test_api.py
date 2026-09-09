"""認証からProject生成までのAPI統合テスト。"""

from __future__ import annotations

import io
import os
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services.ai_pipeline import DemoAIProvider
from app.services.knowledge import retrieve_knowledge_context
from app.services.storage import get_storage


def client_for(tmp_path: Path) -> TestClient:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    return TestClient(app)


def test_private_screen_redirects_without_session(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    settings = client.get("/settings", follow_redirects=False)
    assert settings.status_code == 303


def test_final_composition_preview_enforces_ownership(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "preview-owner@example.com", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "配置確認", "story_text": "検証用"}).json()["project"]
    from app.services.layout import reflow_page
    from app.services.export import render_page_png
    page = reflow_page({"id": "preview-page", "page_number": 1, "panels": [{"id": "panel-a", "dialogue": ["確認"], "image_url": "/media/demo.svg"}]}, project["settings"])
    db.update_project(project["id"], project["user_id"], storyboard=[page])
    stored = db.get_project(project["id"], project["user_id"])
    url = f"/api/projects/{project['id']}/pages/preview-page/composition.png"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == render_page_png(stored, stored["storyboard"][0], get_storage())
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == stored["storyboard"]
    other = TestClient(app)
    other.post("/register", data={"email": "preview-other@example.com", "password": "local-test-password"})
    assert other.get(url).status_code == 404


def test_settings_screen_hides_server_secret(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "settings@example.com", "password": "long-password"}, follow_redirects=False)
    response = client.get("/settings")
    assert response.status_code == 200
    assert "制作環境の設定" in response.text
    assert "OPENAI_API_KEY" not in response.text


def test_project_pipeline_and_export(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = client.post("/register", data={"email": "writer@example.com", "password": "long-password"}, follow_redirects=False)
    assert response.status_code == 303

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    created = client.post(
        "/api/projects",
        data={"title": "帰り道", "story_text": "蒼は夕暮れの灯台へ向かった。凛がそこで待っていた。"},
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]

    analysis = client.post(f"/api/projects/{project_id}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["project"]["analysis"]
    characters = client.post(f"/api/projects/{project_id}/characters")
    assert characters.status_code == 200
    storyboard = client.post(f"/api/projects/{project_id}/storyboard")
    assert storyboard.status_code == 200
    assert storyboard.json()["project"]["storyboard"]

    generated = client.post(f"/api/projects/{project_id}/generate", json={"panel_ids": []})
    assert generated.status_code == 200
    status = client.get(f"/api/projects/{project_id}/generation/status")
    assert status.status_code == 200
    assert status.json()["panels"]
    assert all(panel["status"] == "completed" for panel in status.json()["panels"])

    duplicate = client.post(f"/api/projects/{project_id}/generate", json={"panel_ids": []})
    assert duplicate.status_code == 200
    assert duplicate.json()["queued_panel_ids"] == []
    first_panel_id = status.json()["panels"][0]["id"]
    retried = client.post(f"/api/projects/{project_id}/panels/{first_panel_id}/retry")
    assert retried.status_code == 200
    retried_status = client.get(f"/api/projects/{project_id}/generation/status")
    retried_panel = next(panel for panel in retried_status.json()["panels"] if panel["id"] == first_panel_id)
    assert retried_panel["status"] == "completed"
    assert retried_panel["revision"] == 2

    exported = client.post(f"/api/projects/{project_id}/export", json={"format": "pdf"})
    assert exported.status_code == 200
    export_record = db.get_export(exported.json()["export"]["id"])
    assert export_record is not None
    assert export_record["storage_key"].startswith("exports/")
    assert not Path(export_record["storage_key"]).is_absolute()
    downloaded = client.get(exported.json()["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/pdf")
    assert b"/Subtype /Image" in downloaded.content

    zipped = client.post(f"/api/projects/{project_id}/export", json={"format": "zip"})
    assert zipped.status_code == 200
    zip_download = client.get(zipped.json()["download_url"])
    assert zip_download.status_code == 200
    assert zip_download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(zip_download.content)) as archive:
        assert any(name.startswith("pages/") and name.endswith(".png") for name in archive.namelist())


def test_external_storyboard_uses_persisted_background_job(
    tmp_path: Path, monkeypatch
) -> None:
    """外部AIの長時間StoryboardをHTTPタイムアウトから切り離す。"""

    client = client_for(tmp_path)
    client.post(
        "/register",
        data={"email": "storyboard-job@example.com", "password": "long-password"},
        follow_redirects=False,
    )
    created = client.post(
        "/api/projects",
        data={"title": "Storyboard Job", "story_text": "蒼は灯台へ向かった。"},
    )
    project_id = created.json()["project"]["id"]

    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: DemoAIProvider())
    assert client.post(f"/api/projects/{project_id}/analysis").status_code == 200
    assert client.post(f"/api/projects/{project_id}/characters").status_code == 200

    class ExternalDemoProvider(DemoAIProvider):
        provider_name = "openai"
        uses_external_api = True

    monkeypatch.setattr(
        "app.main.get_ai_provider", lambda _settings: ExternalDemoProvider()
    )
    storyboard = client.post(f"/api/projects/{project_id}/storyboard")
    assert storyboard.status_code == 202
    assert storyboard.json()["accepted"] is True
    job_id = storyboard.json()["job"]["id"]

    status = client.get(f"/api/projects/{project_id}/generation/status")
    job = next(item for item in status.json()["jobs"] if item["id"] == job_id)
    assert job["job_type"] == "storyboard"
    assert job["status"] == "completed"
    reloaded = client.get(f"/api/projects/{project_id}").json()["project"]
    assert reloaded["storyboard"]


def test_project_ownership_is_enforced(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "owner@example.com", "password": "long-password"}, follow_redirects=False)
    created = client.post("/api/projects", data={"title": "所有権", "story_text": "本文"})
    project_id = created.json()["project"]["id"]
    client.post("/logout", follow_redirects=False)
    client.post("/register", data={"email": "other@example.com", "password": "long-password"}, follow_redirects=False)
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_project_delete_cleans_owned_storage_objects(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post(
        "/register",
        data={"email": "storage-cleanup@example.com", "password": "long-password"},
        follow_redirects=False,
    )
    first = client.post("/api/projects", data={"title": "削除対象", "story_text": "本文"}).json()["project"]
    second = client.post("/api/projects", data={"title": "保持対象", "story_text": "本文"}).json()["project"]
    storage = get_storage()
    first_asset = storage.asset_key(first["id"], "panel.png")
    first_export = storage.export_key(first["id"], "export", "pdf")
    second_asset = storage.asset_key(second["id"], "panel.png")
    for key in (first_asset, first_export, second_asset):
        storage.put_bytes(key, b"data")

    deleted = client.delete(f"/api/projects/{first['id']}")
    assert deleted.status_code == 200
    assert not storage.exists(first_asset)
    assert not storage.exists(first_export)
    assert storage.exists(second_asset)


def test_invalid_upload_is_rejected(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "upload@example.com", "password": "long-password"}, follow_redirects=False)
    response = client.post(
        "/api/projects",
        data={"title": "不正ファイル", "story_text": ""},
        files={"story_file": ("malware.exe", b"bad", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_upload_mime_and_size_validation_is_enforced(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "upload-validation@example.com", "password": "long-password"}, follow_redirects=False)
    mismatched = client.post(
        "/api/projects",
        data={"title": "MIME不一致", "story_text": ""},
        files={"story_file": ("story.md", b"# story", "application/pdf")},
    )
    assert mismatched.status_code == 422
    oversized = client.post(
        "/api/knowledge",
        data={"title": "大きすぎる資料", "category": "other"},
        files={"knowledge_file": ("large.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )
    assert oversized.status_code == 422


def test_knowledge_versions_project_modes_and_traceability(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "knowledge@example.com", "password": "long-password"}, follow_redirects=False)

    first = client.post(
        "/api/knowledge",
        data={
            "title": "灯台の世界観",
            "description": "舞台と描写の基準",
            "category": "world",
            "source_text": "# 舞台\n\n灯台の町は霧が多い。\n\n## 禁則\n\n参照資料として扱う。",
        },
    )
    assert first.status_code == 200
    first_knowledge = first.json()["knowledge"]
    first_id = first_knowledge["id"]
    first_v1 = first_knowledge["versions"][0]
    assert first_knowledge["active"] is True
    assert first_v1["version_number"] == 1
    assert first_v1["chunk_count"] == 2

    duplicate = client.post(
        f"/api/knowledge/{first_id}/versions",
        data={"source_text": "# 舞台\n\n灯台の町は霧が多い。\n\n## 禁則\n\n参照資料として扱う。"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    second_version = client.post(
        f"/api/knowledge/{first_id}/versions",
        data={"source_text": "# 舞台\n\n灯台の町は晴れの日もある。"},
    )
    assert second_version.status_code == 200
    assert second_version.json()["duplicate"] is False
    versions = client.get(f"/api/knowledge/{first_id}").json()["knowledge"]["versions"]
    first_v2 = next(version for version in versions if version["version_number"] == 2)
    assert first_v2["is_active"] is True

    second = client.post(
        "/api/knowledge",
        data={"title": "会話ルール", "category": "dialogue", "source_text": "# 会話\n\n短い台詞を使う。"},
    )
    assert second.status_code == 200
    second_knowledge = second.json()["knowledge"]
    second_id = second_knowledge["id"]
    second_v1 = second_knowledge["versions"][0]

    created = client.post(
        "/api/projects",
        data={"title": "Knowledge作品", "story_text": "蒼は灯台へ向かった。", "knowledge_ids": first_id},
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    initial_selection = created.json()["project"]["knowledge"]
    assert len(initial_selection) == 1
    assert initial_selection[0]["mode"] == "follow_latest"

    configured = client.put(
        f"/api/projects/{project_id}/knowledge",
        json={
            "selections": [
                {
                    "knowledge_document_id": first_id,
                    "enabled": True,
                    "priority": 90,
                    "mode": "follow_latest",
                    "scope": ["story_analysis"],
                },
                {
                    "knowledge_document_id": second_id,
                    "enabled": True,
                    "priority": 20,
                    "mode": "pinned",
                    "selected_version_id": second_v1["id"],
                    "scope": ["quality_check"],
                },
            ]
        },
    )
    assert configured.status_code == 200
    selections = configured.json()["knowledge"]
    assert len(selections) == 2
    assert next(item for item in selections if item["knowledge_document_id"] == first_id)["priority"] == 90
    assert next(item for item in selections if item["knowledge_document_id"] == second_id)["mode"] == "pinned"

    analysis_context = retrieve_knowledge_context(project_id, db.get_user_by_email("knowledge@example.com")["id"], "story_analysis", "灯台")
    assert analysis_context["references"]
    assert analysis_context["references"][0]["version_number"] == 2
    assert all(chunk["title"] == "灯台の世界観" for chunk in analysis_context["chunks"])
    quality_context = retrieve_knowledge_context(project_id, db.get_user_by_email("knowledge@example.com")["id"], "quality_check", "会話")
    assert quality_context["references"][0]["title"] == "会話ルール"
    assert quality_context["references"][0]["version_number"] == 1

    activated = client.post(f"/api/knowledge/{first_id}/versions/{first_v1['id']}/activate")
    assert activated.status_code == 200
    follow_latest = retrieve_knowledge_context(project_id, db.get_user_by_email("knowledge@example.com")["id"], "story_analysis", "灯台")
    assert follow_latest["references"][0]["version_number"] == 1

    analysis = client.post(f"/api/projects/{project_id}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["project"]["analysis"]["knowledge_refs"][0]["version_number"] == 1
    quality = client.post(f"/api/projects/{project_id}/quality-check")
    assert quality.status_code == 200
    assert quality.json()["quality_check"]["knowledge_refs"]
    assert quality.json()["project"]["quality_check"]["status"] == "attention"


def test_knowledge_ownership_is_enforced(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "knowledge-owner@example.com", "password": "long-password"}, follow_redirects=False)
    created = client.post(
        "/api/knowledge",
        data={"title": "所有権確認", "category": "other", "source_text": "本文"},
    )
    knowledge_id = created.json()["knowledge"]["id"]
    version_id = created.json()["knowledge"]["versions"][0]["id"]
    client.post("/logout", follow_redirects=False)
    client.post("/register", data={"email": "knowledge-other@example.com", "password": "long-password"}, follow_redirects=False)
    assert client.get(f"/api/knowledge/{knowledge_id}").status_code == 404
    assert client.post(f"/api/knowledge/{knowledge_id}/versions/{version_id}/activate").status_code == 404


def test_knowledge_file_upload_and_archive_lifecycle(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    client.post("/register", data={"email": "knowledge-file@example.com", "password": "long-password"}, follow_redirects=False)
    uploaded = client.post(
        "/api/knowledge",
        data={"title": "ファイルKnowledge", "category": "style"},
        files={"knowledge_file": ("guide.md", "# 見出し\n\n本文".encode("utf-8"), "text/markdown")},
    )
    assert uploaded.status_code == 200
    knowledge_id = uploaded.json()["knowledge"]["id"]
    assert uploaded.json()["knowledge"]["versions"][0]["source_filename"] == "guide.md"

    archived = client.patch(f"/api/knowledge/{knowledge_id}", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["knowledge"]["archived"] is True
    assert client.get("/api/knowledge").json()["knowledge"][0]["archived"] is True

    restored = client.patch(f"/api/knowledge/{knowledge_id}", json={"archived": False})
    assert restored.status_code == 200
    assert restored.json()["knowledge"]["archived"] is False
