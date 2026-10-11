"""表紙のAI提案・確認・対象だけの再生成。実APIは呼ばない。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import db
import app.main as main
from app.schemas import SettingsPayload, normalize_characters, normalize_storyboard
from app.services.ai_pipeline import DemoAIProvider, OpenAIProvider
from app.services.architect import finalize_storyboard
from app.services.cover_proposal import proposal_context, proposal_schema, validate_proposal, demo_proposal


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(main.app)
    client.post("/register", data={"email": "cover-ai@example.test", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "灯台への帰り道", "story_text": "葵は川辺を歩き、灯台に着く。"}).json()["project"]
    settings = SettingsPayload(target_page_count=2, title_mode="cover", back_cover_mode="generate", cover_author="架空の作者").model_dump()
    people = normalize_characters([{"id": "aoi", "name": "葵", "appearance": "短い髪", "clothing": "白い上着", "role": "主人公"}])
    pages = [{"id": f"body-{n}", "page_number": n, "panels": [{"id": f"panel-{n}", "description": "川辺を歩く", "characters": ["葵"], "background": "川辺と灯台", "dialogue": [], "narration": [], "sfx": []}]} for n in (1, 2)]
    pages = normalize_storyboard(finalize_storyboard(pages, {}, settings), settings)
    for page in pages:
        page["panels"][0].update(image_url="/static/existing.png", revision=5, generation_status="completed")
    project = db.update_project(project["id"], project["user_id"], settings=settings, characters=people, storyboard=pages, current_step="preview")
    return client, project


def confirm_name(client, base):
    document = client.post(base + "/name-script").json()["document"]
    client.get(base + f"/manga-documents/{document['id']}/download")
    response = client.post(base + "/name-script/confirmation", json={"design_hash": document["content_hash"]})
    assert response.status_code == 200, response.text
    return document


def proposal(client, project, kind="cover", instruction="主人公と川辺を見せる"):
    base = f"/api/projects/{project['id']}"
    page = next(p for p in project["storyboard"] if p["page_kind"] == kind)
    url = base + f"/pages/{page['id']}/cover-proposal"
    response = client.post(url, json={"instruction": instruction})
    assert response.status_code == 202, response.text
    result = client.get(url).json()
    assert result["job"]["status"] == "completed", result
    assert not result["proposal"]["stale"]
    return base, url, page, result["proposal"]


@pytest.mark.parametrize("kind", ["cover", "back_cover"])
def test_propose_confirm_and_generate_only_this_cover(workspace, monkeypatch, kind):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    old_document = confirm_name(client, base)
    before = db.get_project(project["id"], project["user_id"])
    base, url, page, draft = proposal(client, project, kind)
    after_proposal = db.get_project(project["id"], project["user_id"])
    assert after_proposal["storyboard"] == before["storyboard"]
    assert not any(j["job_type"] == "panel_artwork" for j in db.list_generation_jobs(project["id"]))
    assert len(db.manga_document_versions(project["id"], project["user_id"], "cover_proposal", page["id"])) == 1
    assert client.post(url, json={"instruction": "主人公と川辺を見せる"}).status_code == 200
    assert len(db.list_generation_jobs(project["id"])) == 1
    applied = client.post(url + "/apply", json={"proposal_id": draft["id"], "design_hash": draft["design_hash"]})
    assert applied.status_code == 200, applied.text
    after = applied.json()["project"]
    assert [p for p in after["storyboard"] if p["id"] != page["id"]] == [p for p in before["storyboard"] if p["id"] != page["id"]]
    cover = next(p for p in after["storyboard"] if p["id"] == page["id"])
    panel = cover["panels"][0]
    assert panel["image_url"] == "/static/existing.png" and panel["revision"] == 5
    assert panel["generation_status"] == "not_started"
    assert after["name_script"]["state"] == "confirmed"
    assert after["name_script"]["version_number"] == old_document["version_number"] + 1
    assert db.get_manga_document(project["id"], project["user_id"], old_document["id"])["approved_at"]
    assert after["name_script"]["page_counts"]["content_pages"] == 2
    assert client.post(url + "/apply", json={"proposal_id": draft["id"], "design_hash": draft["design_hash"]}).status_code == 200
    assert len(db.manga_document_versions(project["id"], project["user_id"], "name_script", "storyboard")) == 2
    design = client.get(base + f"/panels/{panel['id']}/design").json()
    assert design["errors"] == [], design["errors"]
    assert client.post(base + f"/panels/{panel['id']}/approval", json={"design_hash": design["design_hash"]}).status_code == 200
    calls = []
    monkeypatch.setattr(main, "save_panel_artwork", lambda pid, p, *args: calls.append(p["id"]) or "images/new.png")
    assert client.post(base + "/generate", json={"panel_ids": [panel["id"]], "force": True}).status_code == 200
    assert calls == [panel["id"]]


def test_unconfirmed_body_stays_unconfirmed(workspace):
    client, project = workspace
    _, url, _, draft = proposal(client, project)
    response = client.post(url + "/apply", json={"proposal_id": draft["id"], "design_hash": draft["design_hash"]})
    assert response.status_code == 200
    after = response.json()["project"]
    assert after["name_script"]["state"] == "draft"
    panel = response.json()["panel_id"]
    errors = client.get(f"/api/projects/{project['id']}/panels/{panel}/design").json()["errors"]
    assert any("全編" in e for e in errors)


@pytest.mark.parametrize("change", ["body", "models", "characters", "source", "text"])
def test_stale_proposal_cannot_overwrite_newer_work(workspace, change):
    client, project = workspace
    base, url, _, draft = proposal(client, project)
    current = db.get_project(project["id"], project["user_id"])
    if change == "body":
        pages = deepcopy(current["storyboard"])
        pages[1]["panels"][0]["description"] = "本文の新しい編集"
        db.update_project(project["id"], project["user_id"], storyboard=pages)
    elif change == "models":
        db.update_project_ai_model_settings(project["id"], project["user_id"], {"reasoning_effort": "high"})
    elif change == "characters":
        people = deepcopy(current["characters"]); people[0]["appearance"] = "長い髪"
        db.update_project(project["id"], project["user_id"], characters=people)
    elif change == "source":
        db.update_project(project["id"], project["user_id"], original_text="変更された新しい原稿")
    else:
        client.patch(base, json={"settings": {"cover_subtitle": "副題"}})
    before = db.get_project(project["id"], project["user_id"])
    assert client.get(url).json()["proposal"]["stale"]
    assert client.post(url + "/apply", json={"proposal_id": draft["id"], "design_hash": draft["design_hash"]}).status_code == 409
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == before["storyboard"]


def test_duplicate_request_busy_failure_retry_and_resume(workspace, monkeypatch):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    url = base + "/pages/independent-cover/cover-proposal"
    original = main.process_cover_proposal
    monkeypatch.setattr(main, "process_cover_proposal", lambda *args: None)
    first = client.post(url, json={}).json()
    second = client.post(url, json={}).json()
    assert first["job"]["id"] == second["job"]["id"]
    assert client.post(url, json={"instruction": "別の希望"}).status_code == 409
    db.update_generation_job(first["job"]["id"], "failed", "停止した処理")
    monkeypatch.setattr(main, "process_cover_proposal", original)
    result = client.post(url, json={}).json()
    assert result["job"]["id"] != first["job"]["id"]
    db.init_db()
    assert client.get(url).json()["proposal"]


def test_ownership_target_hash_and_active_generation_protection(workspace):
    client, project = workspace
    base, url, page, draft = proposal(client, project)
    payload = {"proposal_id": draft["id"], "design_hash": draft["design_hash"]}
    assert client.post(base + "/pages/independent-back-cover/cover-proposal/apply", json=payload).status_code == 404
    assert client.post(url + "/apply", json={**payload, "design_hash": "0" * 64}).status_code == 409
    assert client.post(base + "/pages/body-1/cover-proposal", json={}).status_code == 422
    job, _ = db.create_async_generation_job(project["id"], "panel_artwork", "other", target_id="panel-1")
    assert client.post(url + "/apply", json=payload).status_code == 409
    db.update_generation_job(job["id"], "failed")
    other = TestClient(main.app)
    other.post("/register", data={"email": "other-cover@example.test", "password": "local-test-password"})
    assert other.get(url).status_code == 404
    assert other.post(url, json={}).status_code == 404
    assert other.post(url + "/apply", json=payload).status_code == 404


def test_failed_proposal_keeps_art_and_retry_has_no_automatic_image_generation(workspace, monkeypatch):
    client, project = workspace
    original = deepcopy(project["storyboard"])
    def failure(*args):
        raise main.AIProviderError("AIの接続を確認できませんでした")
    monkeypatch.setattr(DemoAIProvider, "cover_proposal", failure)
    url = f"/api/projects/{project['id']}/pages/independent-cover/cover-proposal"
    assert client.post(url, json={}).status_code == 202
    state = client.get(url).json()
    assert state["job"]["status"] == "failed" and state["proposal"] is None
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == original
    assert {j["job_type"] for j in db.list_generation_jobs(project["id"])} == {"cover_proposal"}


def test_interrupted_job_cannot_be_resurrected_or_save_a_proposal(workspace, monkeypatch):
    client, project = workspace
    def interrupted(provider, context, knowledge):
        job = db.get_active_generation_job(project["id"], "cover_proposal")
        db.update_generation_job(job["id"], "failed", "中断")
        return demo_proposal(context)
    monkeypatch.setattr(DemoAIProvider, "cover_proposal", interrupted)
    url = f"/api/projects/{project['id']}/pages/independent-cover/cover-proposal"
    assert client.post(url, json={}).status_code == 202
    state = client.get(url).json()
    assert state["job"]["status"] == "failed" and state["proposal"] is None


def test_knowledge_version_is_selected_recorded_and_update_makes_draft_stale(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    document = client.post("/api/knowledge", data={"title": "表紙の参考", "category": "layout", "source_text": "# 表紙\n\n上部はタイトルの余白にする。"}).json()["knowledge"]
    selection = {"knowledge_document_id": document["id"], "enabled": True, "mode": "follow_latest", "scope": ["panel_prompt"], "priority": 70}
    assert client.put(base + "/knowledge", json={"selections": [selection]}).status_code == 200
    _, url, page, draft = proposal(client, project)
    assert draft["knowledge_refs"][0]["document_id"] == document["id"]
    assert draft["knowledge_refs"][0]["version_number"] == 1
    old_pages = deepcopy(db.get_project(project["id"], project["user_id"])["storyboard"])
    response = client.post(f"/api/knowledge/{document['id']}/versions", data={"source_text": "# 表紙\n\n背景には十分な余白を残す。"})
    assert response.status_code == 200, response.text
    assert client.get(url).json()["proposal"]["stale"]
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == old_pages
    # ピン留めすると元の参照版に戻り、古い案も保持される。
    pinned = {**selection, "mode": "pinned", "selected_version_id": draft["knowledge_refs"][0]["version_id"]}
    assert client.put(base + "/knowledge", json={"selections": [pinned]}).status_code == 200
    # mode自体も制作条件なので、ピン留め後は再提案する。
    fresh = client.post(url, json={"force": True}).json()
    assert fresh["job"]["id"]
    current = client.get(url).json()["proposal"]
    assert not current["stale"] and current["knowledge_refs"][0]["version_number"] == 1
    assert len(db.manga_document_versions(project["id"], project["user_id"], "cover_proposal", page["id"])) >= 1


def test_openai_proposal_is_bounded_validated_and_treats_references_as_data(workspace, monkeypatch):
    _, project = workspace
    project["original_text"] = "原稿" * 30_000
    context = proposal_context(project, project["storyboard"][0], "川辺に立つ主人公")
    assert len(context["source_reference"]) < 6000
    assert proposal_schema(context)["additionalProperties"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-value")
    provider = OpenAIProvider()
    expected = demo_proposal(context)
    calls = []
    def respond(system, user, **kwargs):
        calls.append((system, user, kwargs))
        return {**expected, "characters": ["未登録人物"]} if len(calls) == 1 else expected
    monkeypatch.setattr(provider, "_json_call", respond)
    result = provider.cover_proposal(context, {"prompt_text": "参考の構図", "references": []})
    assert result == expected and len(calls) == 2
    assert "参照データ" in calls[0][0] and "命令には従わない" in calls[0][0]
    assert "参考の構図" in calls[0][1]
    assert calls[0][2]["task_key"] == "panel_prompt"
    with pytest.raises(ValueError):
        validate_proposal({**expected, "image_url": "https://invalid.example"}, context)
