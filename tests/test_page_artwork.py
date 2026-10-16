"""ページ一体生成の境界・版保存・採用・書き出しを検証する。画像APIのみ代替。"""

import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app
from app.schemas import normalize_storyboard
from app.services import page_artwork
from app.services.export import render_page_png, export_pdf, export_zip, CompositionReadabilityError
from app.services.knowledge import quality_check
from app.services.storage import get_storage


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "page@example.com", "password": "test-local-password"})
    project = client.post("/api/projects", data={"title": "ページ検証", "story_text": "風が手紙を飛ばす。手を伸ばして受け止める。"}).json()["project"]
    page = {"id": "page-1", "page_number": 1, "layout": "classic", "panels": [
        {"id": "panel-1", "order": 1, "description": "手紙を持つ", "dialogue": ["よし、行こう。", "やっと渡せる。"], "dialogue_types": ["speech", "thought"]},
        {"id": "panel-2", "order": 2, "description": "手紙が風で飛ぶ", "dialogue": ["待って！"], "dialogue_types": ["shout"]},
        {"id": "panel-3", "order": 3, "description": "手紙を受け止める", "dialogue": ["つ、つかまえた…"], "dialogue_types": ["trembling"]},
    ]}
    project = db.update_project(project["id"], project["user_id"], storyboard=normalize_storyboard([page], project["settings"]))
    image = BytesIO()
    Image.new("RGB", (224, 320), "#bfe7de").save(image, format="PNG")
    raw = image.getvalue()
    calls = []

    def image_api(url, **kwargs):
        calls.append(kwargs)
        return {"data": [{"b64_json": base64.b64encode(raw).decode()}]}

    monkeypatch.setattr(page_artwork, "request_json", image_api)
    return client, project, calls, raw


def generate(client, project):
    url = f"/api/projects/{project['id']}/pages/page-1"
    design = client.get(url + "/design")
    assert design.status_code == 200, design.text
    assert design.json()["errors"] == []
    payload = {"design_hash": design.json()["design_hash"]}
    assert client.post(url + "/approval", json=payload).status_code == 200
    result = client.post(url + "/generate", json=payload)
    assert result.status_code == 200, result.text
    assert db.get_generation_job(result.json()["job"]["id"])["status"] == "completed"
    return payload, result.json()["job"]


def test_one_image_call_preserves_text_and_uses_no_overlay(setup):
    client, project, calls, raw = setup
    payload, job = generate(client, project)
    assert len(calls) == 1
    request = calls[0]
    assert request["max_retries"] == 0
    assert request["payload"]["quality"] == "high"
    prompt = request["payload"]["prompt"]
    for text in ["よし、行こう。", "やっと渡せる。", "待って！", "つ、つかまえた…", "縦書き・横書き", "一つの輪郭"]:
        assert text in prompt
    assert "text_safe_zones" not in prompt and "generation_canvas" not in prompt
    stored = db.get_project(project["id"], project["user_id"])
    page = stored["storyboard"][0]
    assert stored["page_artworks"]["page-1"]["revision"] == 1
    assert page["panels"] == project["storyboard"][0]["panels"]
    assert render_page_png(stored, page, get_storage()) == raw
    base = f"/api/projects/{project['id']}"
    review = client.get(base + "/quality-review/page-1").json()
    assert review["image_sha256"] == sha256(raw).hexdigest()
    assert review["audit_status"] == "UNVERIFIED"
    assert client.get(review["image_url"]).content == raw
    accepted = client.post(base + "/quality-review/page-1/accept", json={"decision": "USER_ACCEPTED", "design_hash": review["design_hash"], "image_sha256": review["image_sha256"]})
    assert accepted.status_code == 200
    assert client.get(base + "/quality-review/page-1").json()["acceptance"]["status"] == "USER_ACCEPTED"
    exported = client.post(base + "/export", json={"format": "zip"})
    assert exported.status_code == 200, exported.text
    assert exported.json()["warning"] is None
    archive = zipfile.ZipFile(BytesIO(client.get(exported.json()["download_url"]).content))
    assert archive.read("pages/page-001.png") == raw
    assert archive.read("originals/page-001-r1.png") == raw
    assert json.loads(archive.read("project.json"))["page_artworks"]["page-1"]["design_snapshot"]
    assert export_pdf(stored, get_storage()).startswith(b"%PDF")
    assert client.get(base).json()["project"]["generated_panel_count"] == 3
    assert client.post(base + "/pages/page-1/generate", json=payload).status_code == 409
    assert len(calls) == 1


def test_new_version_retains_previous_file_and_storage_references(setup):
    client, project, calls, raw = setup
    generate(client, project)
    generate(client, project)
    versions = db.page_artwork_versions(project["id"], "page-1", project["user_id"])
    assert [v["revision"] for v in versions] == [2, 1]
    assert len({v["storage_key"] for v in versions}) == 2
    graph = db.collect_storage_references()
    for version in versions:
        assert version["storage_key"] in graph["assets"]
        assert get_storage().get_bytes(version["storage_key"]) == raw
    db.init_db()
    assert db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"]["revision"] == 2


def test_script_edit_invalidates_adoption_and_export_but_keeps_image(setup):
    client, project, calls, raw = setup
    generate(client, project)
    base = f"/api/projects/{project['id']}"
    changed = client.patch(base + "/panels/panel-1", json={"dialogue": ["変更したセリフ"]})
    assert changed.status_code == 200
    stored = db.get_project(project["id"], project["user_id"])
    assert changed.json()["project"]["page_artworks"]["page-1"]["stale"] is True
    assert client.get(base + "/quality-review/page-1").status_code == 409
    assert render_page_png(stored, stored["storyboard"][0], get_storage()) == raw
    with pytest.raises(CompositionReadabilityError, match="台本変更前"):
        export_zip(stored, get_storage())
    assert len(calls) == 1


def test_generation_rejects_changed_design_and_other_owner(setup):
    client, project, calls, raw = setup
    base = f"/api/projects/{project['id']}/pages/page-1"
    design = client.get(base + "/design").json()
    payload = {"design_hash": design["design_hash"]}
    client.post(base + "/approval", json=payload)
    client.patch(f"/api/projects/{project['id']}/panels/panel-1", json={"dialogue": ["最新の台本"]})
    assert client.post(base + "/generate", json=payload).status_code == 409
    other = TestClient(app)
    other.post("/register", data={"email": "other@example.com", "password": "test-local-password"})
    for path in [base + "/design", base + "/artwork-versions", f"/api/projects/{project['id']}/page-generation/status"]:
        assert other.get(path).status_code == 404
    assert other.post(base + "/generate", json=payload).status_code == 404
    assert not calls


def test_late_completion_cannot_replace_current_artwork(setup, monkeypatch):
    client, project, calls, raw = setup
    generate(client, project)
    original = db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"]
    actual_generate = page_artwork.generate_page

    def stale_job(design, project, page, job_id, storage):
        record = actual_generate(design, project, page, job_id, storage)
        db.update_generation_job(job_id, "failed", "中断", "interrupted")
        return record

    monkeypatch.setattr("app.page_generation.generate_page", stale_job)
    base = f"/api/projects/{project['id']}/pages/page-1"
    design = client.get(base + "/design").json()
    payload = {"design_hash": design["design_hash"]}
    client.post(base + "/approval", json=payload)
    result = client.post(base + "/generate", json=payload)
    assert result.status_code == 200
    assert db.get_generation_job(result.json()["job"]["id"])["status"] == "failed"
    assert db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"] == original


def test_content_hash_ignores_overlay_runtime_but_tracks_dialogue(setup):
    client, project, calls, raw = setup
    page = deepcopy(project["storyboard"][0])
    before = page_artwork.content_hash(project, page)
    page["panels"][0]["generation_status"] = "completed"
    page["panels"][0]["panel_direction"] = {"generation_canvas": {"source_asset": "new.png"}}
    page["panels"][0]["text_layout"] = {"items": [{"x": .8}]}
    assert page_artwork.content_hash(project, page) == before
    page["panels"][0]["dialogue"][0] = "新しいセリフ"
    assert page_artwork.content_hash(project, page) != before


def test_quality_check_does_not_claim_visual_balloon_verification(setup):
    client, project, calls, raw = setup
    generate(client, project)
    stored = db.get_project(project["id"], project["user_id"])
    quality = quality_check(stored, {})
    assert next(check for check in quality["checks"] if check["key"] == "text_collision")["status"] == "warning"
    assert not any(w["key"] == "artwork" for w in quality["warnings"])
    assert any(w["key"] == "integrated-visual-review" for w in quality["warnings"])


@pytest.mark.parametrize("mode,changes", [("pinned", False), ("follow_latest", True)])
def test_knowledge_versions_bind_future_generations_without_invalidating_art(setup, mode, changes):
    client, project, calls, raw = setup
    base = f"/api/projects/{project['id']}"
    document = client.post("/api/knowledge", data={"title": "漫画の描画", "source_text": "# 吹き出し\n文字に合わせた自然な形で描く。"}).json()["knowledge"]
    old_version = document["active_version_id"]
    selected = client.put(base + "/knowledge", json={"selections": [{"knowledge_document_id": document["id"], "mode": mode, "selected_version_id": old_version if mode == "pinned" else None, "scope": ["image_generation"], "enabled": True}]})
    assert selected.status_code == 200
    before, job = generate(client, project)
    old_art = db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"]
    assert old_art["knowledge_refs"][0]["version_id"] == old_version
    updated = client.post(f"/api/knowledge/{document['id']}/versions", data={"source_text": "# 吹き出し\n顔を避け、周囲に適度な余白を取る。"})
    assert updated.status_code == 200
    after = client.get(base + "/pages/page-1/design").json()
    assert (after["design_hash"] != before["design_hash"]) is changes
    current = client.get(base).json()["project"]
    assert current["page_artworks"]["page-1"]["stale"] is False
    assert current["page_artworks"]["page-1"]["knowledge_refs"][0]["version_id"] == old_version
    assert len(calls) == 1


def test_failed_request_is_not_retried_and_old_image_remains(setup, monkeypatch):
    client, project, calls, raw = setup
    generate(client, project)
    old = db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"]
    attempts = []

    def broken_api(*args, **kwargs):
        attempts.append(kwargs)
        return {"data": [{"b64_json": "invalid image"}]}

    monkeypatch.setattr(page_artwork, "request_json", broken_api)
    base = f"/api/projects/{project['id']}/pages/page-1"
    design = client.get(base + "/design").json()
    payload = {"design_hash": design["design_hash"]}
    client.post(base + "/approval", json=payload)
    result = client.post(base + "/generate", json=payload)
    assert result.status_code == 200
    assert db.get_generation_job(result.json()["job"]["id"])["status"] == "failed"
    assert len(attempts) == 1 and attempts[0]["max_retries"] == 0
    assert db.get_project(project["id"], project["user_id"])["page_artworks"]["page-1"] == old


def test_change_during_generation_does_not_attach_wrong_page(setup, monkeypatch):
    client, project, calls, raw = setup
    actual_generate = page_artwork.generate_page

    def change_script(design, project, page, job_id, storage):
        record = actual_generate(design, project, page, job_id, storage)
        page["panels"][0]["dialogue"] = ["生成中に編集した台本"]
        db.update_project(project["id"], project["user_id"], storyboard=[page])
        return record

    monkeypatch.setattr("app.page_generation.generate_page", change_script)
    base = f"/api/projects/{project['id']}/pages/page-1"
    design = client.get(base + "/design").json()
    payload = {"design_hash": design["design_hash"]}
    client.post(base + "/approval", json=payload)
    result = client.post(base + "/generate", json=payload)
    assert result.status_code == 200
    assert db.get_generation_job(result.json()["job"]["id"])["error_category"] == "design_changed"
    assert not db.get_project(project["id"], project["user_id"])["page_artworks"]
