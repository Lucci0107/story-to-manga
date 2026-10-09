"""少数ページの確認・一括生成が選択外のコマを変更しないことを検証する。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import SettingsPayload, normalize_storyboard
from app.services.architect import finalize_storyboard


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "")
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "page-test@example.test", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "架空の試し生成", "story_text": "主人公が町を歩き、公園へ向かう。"}).json()["project"]
    settings = SettingsPayload(target_page_count=4, title_mode="cover", back_cover_mode="generate").model_dump()
    body = [{"id": f"body-{number}", "page_number": number, "layout": "classic", "panels": [
        {"id": f"panel-{number}-{order}", "order": order, "description": "町の道を歩く", "background": "町の道", "characters": [], "dialogue": []}
        for order in (1, 2)
    ]} for number in range(1, 5)]
    pages = normalize_storyboard(finalize_storyboard(body, {}, settings), settings)
    pages[1]["panels"][0].update(generation_status="completed", image_url="/existing-image.png")
    pages[2]["panels"][0].update(generation_status="failed", generation_error="一時的な失敗")
    project = db.update_project(project["id"], project["user_id"], settings=settings, storyboard=pages, current_step="generate", status="storyboard_ready")
    confirm_name(client, project)
    return client, project


def confirm_name(client, project):
    base = f"/api/projects/{project['id']}"
    document = client.post(base + "/name-script").json()["document"]
    assert client.get(base + f"/manga-documents/{document['id']}/download").status_code == 200
    confirmed = client.post(base + "/name-script/confirmation", json={"design_hash": document["content_hash"]})
    assert confirmed.status_code == 200, confirmed.text


def approve_selected(client, project, panel_ids):
    base = f"/api/projects/{project['id']}/panels"
    for panel_id in panel_ids:
        design = client.get(f"{base}/{panel_id}/design").json()
        result = client.post(f"{base}/{panel_id}/approval", json={"design_hash": design["design_hash"]})
        assert result.status_code == 200, result.text


@pytest.mark.parametrize("include_covers", [False, True])
def test_selected_body_pages_and_optional_covers_generate_once(workspace, include_covers):
    client, project = workspace
    before = deepcopy(project["storyboard"])
    body_ids = ["panel-1-1", "panel-1-2", "panel-2-1", "panel-2-2"]
    cover_ids = [panel["id"] for page in before if page["page_kind"] != "content" for panel in page["panels"]]
    panel_ids = body_ids + (cover_ids if include_covers else [])
    expected = set(panel_ids) - {"panel-1-1"}
    approve_selected(client, project, sorted(expected))
    url = f"/api/projects/{project['id']}/generate"
    result = client.post(url, json={"panel_ids": panel_ids, "retry_failed": True, "force": False})
    assert result.status_code == 200, result.text
    assert set(result.json()["queued_panel_ids"]) == expected
    assert result.json()["skipped_panel_ids"] == ["panel-1-1"]
    jobs = db.list_generation_jobs(project["id"])
    assert {job["target_id"] for job in jobs} == expected
    assert len({job["batch_id"] for job in jobs}) == 1
    after = db.get_project(project["id"], project["user_id"])
    for old_page, new_page in zip(before, after["storyboard"]):
        assert new_page["page_kind"] == old_page["page_kind"]
        assert new_page["page_number"] == old_page["page_number"]
        for old_panel, new_panel in zip(old_page["panels"], new_page["panels"]):
            if old_panel["id"] in expected:
                assert new_panel["generation_status"] == "completed"
                assert new_panel["image_url"].startswith(f"/media/{project['id']}/")
            else:
                assert new_panel == old_panel
    again = client.post(url, json={"panel_ids": panel_ids, "retry_failed": True})
    assert again.status_code == 200 and again.json()["queued_panel_ids"] == []
    assert len(db.list_generation_jobs(project["id"])) == len(expected)


def test_one_changed_design_stops_entire_selected_batch_before_generation(workspace):
    client, project = workspace
    selected = ["panel-1-2", "panel-2-1", "panel-2-2"]
    approve_selected(client, project, selected)
    base = f"/api/projects/{project['id']}"
    assert client.patch(base + "/panels/panel-2-2", json={"description": "公園に到着する"}).status_code == 200
    confirm_name(client, project)
    before = db.get_project(project["id"], project["user_id"])["storyboard"]
    result = client.post(base + "/generate", json={"panel_ids": selected, "retry_failed": True})
    assert result.status_code == 409
    assert db.list_generation_jobs(project["id"]) == []
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == before
