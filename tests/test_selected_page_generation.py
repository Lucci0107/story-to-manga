"""少数ページの確認・一括生成が選択外のコマを変更しないことを検証する。"""

from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import SettingsPayload, normalize_characters, normalize_storyboard
from app.services.architect import finalize_storyboard
from app.services.ai_pipeline import compose_panel_prompt
from app.services.character_references import anonymous_characters, registered_character, unresolved_characters
from app.services.character_proposal import build_character_proposal


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


@pytest.mark.parametrize("name,anonymous", [("患者", True), ("弟", True), ("未登録の固有人物", False)])
def test_anonymous_and_named_extras_generate_without_new_profiles(workspace, name, anonymous, monkeypatch):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    panel_url = base + "/panels/panel-1-2"
    assert client.patch(panel_url, json={"characters": [name]}).status_code == 200
    confirm_name(client, project)
    design = client.get(panel_url + "/design").json()
    assert design["anonymous_characters"] == ([name] if anonymous else [])
    assert design["supporting_characters"] == ([] if anonymous else [name])
    from app.services.ai_pipeline import DemoAIProvider
    def unexpected(*args, **kwargs):
        pytest.fail("脇役の人物設定を追加生成してはいけない")
    monkeypatch.setattr(DemoAIProvider, "characters", unexpected)
    approved = client.post(panel_url + "/approval", json={"design_hash": design["design_hash"]})
    assert approved.status_code == 200, approved.text
    generated = client.post(base + "/generate", json={"panel_ids": ["panel-1-2"]})
    assert generated.status_code == 200, generated.text
    after = db.get_project(project["id"], project["user_id"])
    panel = after["storyboard"][1]["panels"][1]
    assert panel["generation_status"] == "completed"
    assert ("匿名の脇役" if anonymous else "ネームに登場する脇役") in panel["generation_prompt"]
    assert after["characters"] == project["characters"] == []


def test_registered_anonymous_role_uses_its_existing_profile_and_alias():
    person = {"name": "患者", "aliases": ["入院中の人物"], "appearance": "登録済みの容貌"}
    for name in ("患者", "入院中の人物"):
        panel = {"characters": [name]}
        assert registered_character([person], name) is person
        assert anonymous_characters(panel, [person]) == []
        prompt = compose_panel_prompt(panel, [person], {})
        assert "登録済みの容貌" in prompt and "匿名の脇役" not in prompt


def test_shared_role_alias_does_not_merge_two_registered_people():
    characters = [{"name": "葵", "aliases": ["患者"]}, {"name": "凛", "aliases": ["患者"]}]
    panel = {"characters": ["患者"]}
    assert registered_character(characters, "患者") is None
    assert anonymous_characters(panel, characters) == []
    assert unresolved_characters(panel, characters) == ["患者"]


@pytest.mark.parametrize("kind", ["missing_profile", "ambiguous"])
def test_only_missing_selected_profiles_and_ambiguous_registered_names_block_approval(workspace, kind):
    client, project = workspace
    name = "葵" if kind == "missing_profile" else "先輩"
    source = "葵は灯と話した。"
    stored = [] if kind == "missing_profile" else normalize_characters([
        {"name": "葵", "aliases": ["先輩"]}, {"name": "灯", "aliases": ["先輩"]},
    ])
    project = db.update_project(project["id"], project["user_id"], original_text=source, characters=stored)
    if kind == "missing_profile":
        proposal = build_character_proposal([{"name": "葵", "source_quotes": [source], "importance": 5}], project)
        proposal["confirmed_at"] = "2026-10-09T00:00:00+00:00"
        with db.connection() as conn:
            conn.execute("UPDATE projects SET character_proposal_json = ? WHERE id = ? AND user_id = ?",
                         (json.dumps(proposal, ensure_ascii=False), project["id"], project["user_id"]))
    target = f"/api/projects/{project['id']}/panels/panel-1-2"
    assert client.patch(target, json={"characters": [name]}).status_code == 200
    confirm_name(client, project)
    design = client.get(target + "/design").json()
    assert design["unresolved_characters"] == [name]
    assert design["character_references"][0]["kind"] == kind
    assert client.post(target + "/approval", json={"design_hash": design["design_hash"]}).status_code == 422
    assert not db.list_generation_jobs(project["id"])
    assert db.get_project(project["id"], project["user_id"])["characters"] == stored


def test_registered_child_and_unselected_cast_generate_without_new_profiles(workspace, monkeypatch):
    client, project = workspace
    source = "葵と葵の兄、葵の弟、山田先生が町の公園に集まった。"
    stored = normalize_characters([
        {"id": "aoi", "name": "葵", "appearance": "成人の固有容貌", "clothing": "成人の制服"},
        {"id": "brother", "name": "葵の兄", "appearance": "登録済みの兄"},
    ])
    project = db.update_project(project["id"], project["user_id"], original_text=source, characters=stored)
    roster = [{"name": name, "aliases": [], "role": "公園に集まる人物", "source_quotes": [source],
               "importance": 5 if name == "葵" else 3}
              for name in ("葵", "葵の兄", "葵の弟", "山田先生")]
    proposal = build_character_proposal(roster, project)
    proposal["confirmed_at"] = "2026-10-09T00:00:00+00:00"
    with db.connection() as conn:
        conn.execute("UPDATE projects SET character_proposal_json = ? WHERE id = ? AND user_id = ?",
                     (json.dumps(proposal, ensure_ascii=False), project["id"], project["user_id"]))
    project = db.get_project(project["id"], project["user_id"])
    base = f"/api/projects/{project['id']}"
    target = base + "/panels/panel-1-2"
    assert client.patch(target, json={"characters": ["兄", "葵（幼少期）", "弟", "山田先生"]}).status_code == 200
    confirm_name(client, project)
    before = db.get_project(project["id"], project["user_id"])
    design = client.get(target + "/design").json()
    assert design["errors"] == [] and design["state"] == "DESIGN_READY"
    assert design["supporting_characters"] == ["弟", "山田先生"]
    assert design["character_references"][1]["registered_name"] == "葵"
    from app.services.ai_pipeline import DemoAIProvider
    def unexpected(*args, **kwargs):
        pytest.fail("脇役の人物設定を追加生成してはいけない")
    monkeypatch.setattr(DemoAIProvider, "characters", unexpected)
    assert client.post(target + "/approval", json={"design_hash": design["design_hash"]}).status_code == 200
    assert client.post(base + "/generate", json={"panel_ids": ["panel-1-2"]}).status_code == 200
    after = db.get_project(project["id"], project["user_id"])
    panel = after["storyboard"][1]["panels"][1]
    assert panel["generation_status"] == "completed"
    assert "同一人物の幼少期" in panel["generation_prompt"]
    assert "原稿に登場する脇役" in panel["generation_prompt"]
    assert "成人の固有容貌" not in panel["generation_prompt"]
    assert "成人の制服" not in panel["generation_prompt"]
    assert after["characters"] == before["characters"] == stored
    assert after["character_proposal"] == before["character_proposal"]
    assert after["storyboard"][0] == before["storyboard"][0]
    assert after["storyboard"][2:] == before["storyboard"][2:]
