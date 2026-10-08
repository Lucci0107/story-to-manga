"""表紙・裏表紙の選択、本文の保持、確認から画像生成・書き出しまでの回帰。"""

from copy import deepcopy
from io import BytesIO
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import db
from app.main import app, get_ai_provider
from app.schemas import SettingsPayload, normalize_storyboard
from app.services.architect import add_requested_covers, finalize_storyboard
from app.services.export import export_pdf, export_zip
from app.services.knowledge import quality_check
from app.services.manga_documents import name_page_counts, name_script_summary, name_snapshot


def body_pages(count=2):
    return [{"id": f"body-{number}", "page_number": number, "layout": "hero", "panels": [{
        "id": f"body-panel-{number}", "order": 1, "description": "町の道を歩く", "background": "町の道",
        "characters": [], "dialogue": [], "narration": [], "sfx": [],
    }]} for number in range(1, count + 1)]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "covers@example.test", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "架空の物語", "story_text": "主人公が町の道を歩く。"}).json()["project"]
    settings = SettingsPayload(target_page_count=2).model_dump()
    pages = normalize_storyboard(finalize_storyboard(body_pages(), {}, settings), settings)
    project = db.update_project(project["id"], project["user_id"], settings=settings, storyboard=pages, current_step="storyboard", status="storyboard_ready")
    return client, project


@pytest.mark.parametrize("title_mode", ["none", "first_page", "cover"])
@pytest.mark.parametrize("back_mode", ["none", "generate"])
def test_selected_covers_are_separate_from_body(title_mode, back_mode):
    settings = SettingsPayload(target_page_count=2, title_mode=title_mode, back_cover_mode=back_mode).model_dump()
    pages = normalize_storyboard(finalize_storyboard(body_pages(), {"title": "架空"}, settings), settings)
    content = [page for page in pages if page["page_kind"] == "content"]
    assert [page["page_number"] for page in content] == [1, 2]
    assert [page["title"] for page in content] == ["ページ 1", "ページ 2"]
    assert len(pages) == 2 + int(title_mode == "cover") + int(back_mode == "generate")
    if title_mode == "cover":
        assert pages[0]["page_kind"] == "cover" and pages[0]["page_number"] == 0
    if back_mode == "generate":
        assert pages[-1]["page_kind"] == "back_cover" and pages[-1]["page_number"] == 0
        assert pages[-1]["show_title"] is False
        assert pages[-1]["panels"][0]["dialogue"] == []
        assert pages[-1]["panels"][0]["background"] == "町の道"
    assert len({page["id"] for page in pages}) == len(pages)
    report = quality_check({"storyboard": pages, "settings": settings, "characters": []}, {})
    assert not any(issue["key"] == "page-number" for issue in report["issues"])


def test_120_body_pages_plus_two_covers_are_not_truncated():
    settings = SettingsPayload(target_page_count=120, title_mode="cover", back_cover_mode="generate").model_dump()
    pages = normalize_storyboard(finalize_storyboard(body_pages(120), {}, settings), settings)
    assert len(pages) == 122
    assert pages[-2]["id"] == "body-120" and pages[-2]["page_number"] == 120
    assert pages[-1]["page_kind"] == "back_cover"
    assert len(normalize_storyboard(pages, settings)) == 122
    with pytest.raises(ValueError, match="本文は120ページ"):
        normalize_storyboard(finalize_storyboard(body_pages(121), {}, settings), settings)


@pytest.mark.parametrize("pages", [
    [{"page_kind": "content"}, {"page_kind": "cover"}],
    [{"page_kind": "back_cover"}, {"page_kind": "content"}],
    [{"page_kind": "cover"}, {"page_kind": "cover"}],
    [{"page_kind": "back_cover"}, {"page_kind": "back_cover"}],
])
def test_cover_positions_are_validated_without_changing_their_kind(pages):
    with pytest.raises(ValueError, match="1ページだけ"):
        normalize_storyboard(pages)


def test_add_covers_preserves_existing_story_and_artwork():
    settings = SettingsPayload(title_mode="cover", back_cover_mode="generate").model_dump()
    pages = body_pages()
    pages[-1]["panels"][0].update(image_url="/media/existing.png", revision=8)
    original = deepcopy(pages)
    result = add_requested_covers(pages, {"title": "架空"}, settings)
    assert result[1:-1] == original
    saved = deepcopy(result)
    assert add_requested_covers(result, {}, settings) == saved
    with pytest.raises(ValueError):
        SettingsPayload(back_cover_mode="unknown")


def test_existing_project_adds_only_covers_and_generates_only_back_cover(workspace, monkeypatch):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    old = client.post(base + "/name-script").json()["document"]
    old_markdown = client.get(base + f"/manga-documents/{old['id']}/download").text
    chosen = client.patch(base, json={"settings": {"title_mode": "cover", "back_cover_mode": "generate"}}).json()["project"]
    assert chosen["storyboard"] == project["storyboard"]
    assert chosen["name_script"]["missing_covers"] == ["cover", "back_cover"]
    assert any("裏表紙" in error for error in chosen["name_script"]["validation"]["errors"])

    def forbid_text_ai(*args, **kwargs):
        pytest.fail("表紙類の追加に本文のAI生成を呼ばない")

    monkeypatch.setattr("app.main.get_ai_provider", forbid_text_ai)
    added = client.post(base + "/storyboard/covers", json={"design_hash": chosen["name_script"]["content_hash"]})
    assert added.status_code == 200, added.text
    data = added.json()
    saved = data["project"]
    assert saved["storyboard"][1:-1] == project["storyboard"]
    assert saved["settings"]["target_page_count"] == 2
    assert data["validation"]["errors"] == []
    assert data["name_script"]["page_counts"]["content_pages"] == 2
    assert data["name_script"]["page_counts"]["total_pages"] == 4
    assert data["name_script"]["missing_covers"] == []
    assert "## 裏表紙" in data["document"]["markdown"]
    assert client.get(base + f"/manga-documents/{old['id']}/download").text == old_markdown
    again = client.post(base + "/storyboard/covers", json={"design_hash": data["name_script"]["content_hash"]})
    assert again.status_code == 200 and again.json()["document"]["id"] == data["document"]["id"]
    monkeypatch.setattr("app.main.get_ai_provider", get_ai_provider)
    client.get(base + f"/manga-documents/{data['document']['id']}/download")
    assert client.post(base + "/name-script/confirmation", json={"design_hash": data["name_script"]["content_hash"]}).status_code == 200
    back_id = saved["storyboard"][-1]["panels"][0]["id"]
    design = client.get(base + f"/panels/{back_id}/design").json()
    assert design["errors"] == [], design["errors"]
    assert client.post(base + f"/panels/{back_id}/approval", json={"design_hash": design["design_hash"]}).status_code == 200
    generated = client.post(base + "/generate", json={"panel_ids": [back_id]})
    assert generated.status_code == 200, generated.text
    refreshed = client.get(base).json()["project"]
    assert refreshed["storyboard"][-1]["panels"][0]["generation_status"] == "completed"
    assert all(p["generation_status"] == "not_started" for page in refreshed["storyboard"][:-1] for p in page["panels"])
    assert refreshed["name_script"]["state"] == "confirmed"


def test_covers_do_not_change_adopted_body_target(workspace):
    client, project = workspace
    settings = {**project["settings"], "target_page_count": 3, "title_mode": "cover", "back_cover_mode": "generate"}
    pages = normalize_storyboard(finalize_storyboard(body_pages(), {}, settings), settings)
    project = db.update_project(project["id"], project["user_id"], settings=settings, storyboard=pages)
    response = client.post(f"/api/projects/{project['id']}/name-script/page-count", json={"design_hash": name_script_summary(project)["content_hash"]})
    assert response.status_code == 200
    assert response.json()["project"]["settings"]["target_page_count"] == 2
    assert response.json()["name_script"]["page_counts"]["total_pages"] == 4


def test_cover_addition_rejects_other_owner_stale_and_active_jobs(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    initial_hash = name_script_summary(project)["content_hash"]
    fresh = client.patch(base, json={"settings": {"back_cover_mode": "generate"}}).json()["project"]
    assert client.post(base + "/storyboard/covers", json={"design_hash": initial_hash}).status_code == 409
    other = TestClient(app)
    other.post("/register", data={"email": "other-covers@example.test", "password": "local-test-password"})
    assert other.post(base + "/storyboard/covers", json={"design_hash": fresh["name_script"]["content_hash"]}).status_code == 404
    db.create_async_generation_job(project["id"], "storyboard", "cover-active")
    denied = client.post(base + "/storyboard/covers", json={"design_hash": fresh["name_script"]["content_hash"]})
    assert denied.status_code == 409 and "生成処理中" in denied.json()["detail"]
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == project["storyboard"]


def test_pdf_and_zip_include_back_cover_separately_at_end(workspace):
    _, project = workspace
    settings = {**project["settings"], "title_mode": "cover", "back_cover_mode": "generate"}
    project["settings"] = settings
    project["storyboard"] = normalize_storyboard(finalize_storyboard(body_pages(), {}, settings), settings)
    pdf = PdfReader(BytesIO(export_pdf(project)))
    assert len(pdf.pages) == 4
    assert "0" not in (pdf.pages[0].extract_text() or "").splitlines()
    assert "0" not in (pdf.pages[-1].extract_text() or "").splitlines()
    with zipfile.ZipFile(BytesIO(export_zip(project))) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert [name for name in names if name.endswith(".png")] == ["pages/page-000.png", "pages/page-001.png", "pages/page-002.png", "pages/back-cover.png"]
        manifest = json.loads(archive.read("project.json"))
        assert manifest["storyboard"][-1]["page_kind"] == "back_cover"
        counts = name_page_counts(name_snapshot({**project, "storyboard": manifest["storyboard"]}))
        assert (counts["content_pages"], counts["cover_pages"], counts["back_cover_pages"]) == (2, 1, 1)
