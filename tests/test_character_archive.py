"""人物の削除・復元で設定を失わず、使用中の人物と所有権を保護する。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import MAX_STORED_CHARACTERS
from app.services.character_proposal import build_character_proposal


def setup_project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": f"archive-{tmp_path.name}@example.test", "password": "long-password"})
    project = client.post("/api/projects", data={"title": "人物の整理", "story_text": "葵は灯と玲に相談した。"}).json()["project"]
    characters = [
        {"id": "a", "name": "葵", "appearance": "確認済みの外見", "aliases": ["主人公"],
         "identity_notes": "左側の特徴", "reference_image_url": "https://example.test/reference.png",
         "knowledge_refs": [{"document_id": "doc", "version_id": "v1", "version_number": 1}]},
        {"id": "b", "name": "灯", "appearance": "確認済みの外見", "aliases": ["コーチ"]},
        {"id": "c", "name": "玲", "appearance": "確認済みの外見", "aliases": []},
    ]
    project = db.update_project(project["id"], project["user_id"], characters=characters,
                                analysis={"main_characters": ["葵", "灯"]}, update_analysis=True)

    def unexpected_ai(_settings):
        raise AssertionError("削除・復元でAI要求を開始してはいけない")

    monkeypatch.setattr("app.main.get_ai_provider", unexpected_ai)
    return client, project, characters


def test_delete_and_restore_preserve_character_ids_references_and_definitions(tmp_path, monkeypatch):
    client, project, characters = setup_project(tmp_path, monkeypatch)
    base = f"/api/projects/{project['id']}"
    response = client.post(base + "/characters/delete", json={"character_ids": ["a", "c"]})
    assert response.status_code == 200 and response.json()["profiles_generated"] == 0
    deleted = response.json()["project"]
    assert deleted["characters"] == [characters[1]]
    assert [item["id"] for item in deleted["deleted_characters"]] == ["a", "c"]
    assert all(item["deleted_at"] for item in deleted["deleted_characters"])
    response = client.post(base + "/characters/restore", json={"character_ids": ["a", "c"]})
    assert response.status_code == 200 and response.json()["profiles_generated"] == 0
    restored = response.json()["project"]
    assert restored["deleted_characters"] == []
    assert sorted(restored["characters"], key=lambda person: person["id"]) == characters
    assert client.get(base).json()["project"]["characters"] == restored["characters"]


def test_active_people_and_aliases_used_in_existing_panels_cannot_be_deleted(tmp_path, monkeypatch):
    client, project, characters = setup_project(tmp_path, monkeypatch)
    proposal = build_character_proposal([{"name": "葵", "aliases": [], "role": "主人公", "source_quotes": []}], project)
    job, _ = db.create_async_generation_job(project["id"], "character", "proposal", target_id="proposal")
    assert db.complete_character_proposal_job(job["id"], project["id"], project["user_id"], proposal)
    db.save_character_selection(project["id"], project["user_id"], proposal["id"], proposal["selected_candidate_ids"], confirmed=True)
    db.update_project(project["id"], project["user_id"], storyboard=[{"id": "page", "panels": [{"id": "panel", "description": "相談する場面", "characters": ["コーチ"]}]}])
    base = f"/api/projects/{project['id']}"
    assert client.get(base).json()["project"]["protected_character_ids"] == ["a", "b"]
    for ids in (["a"], ["b"], ["a", "c"]):
        response = client.post(base + "/characters/delete", json={"character_ids": ids})
        assert response.status_code == 409 and "使用中" in response.json()["detail"]
    current = client.get(base).json()["project"]
    assert current["characters"] == characters and current["deleted_characters"] == []


@pytest.mark.parametrize("job_type", ["character", "storyboard", "panel"])
def test_delete_and_restore_are_blocked_during_generation(tmp_path, monkeypatch, job_type):
    client, project, characters = setup_project(tmp_path, monkeypatch)
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/characters/delete", json={"character_ids": ["c"]}).status_code == 200
    db.create_async_generation_job(project["id"], job_type, f"active-{job_type}")
    assert client.post(base + "/characters/delete", json={"character_ids": ["a"]}).status_code == 409
    assert client.post(base + "/characters/restore", json={"character_ids": ["c"]}).status_code == 409
    current = client.get(base).json()["project"]
    assert current["characters"] == characters[:2] and current["deleted_characters"][0]["id"] == "c"


def test_invalid_ids_and_other_users_cannot_change_stored_or_deleted_people(tmp_path, monkeypatch):
    client, project, characters = setup_project(tmp_path, monkeypatch)
    base = f"/api/projects/{project['id']}"
    other = TestClient(app)
    other.post("/register", data={"email": "other-archive@example.test", "password": "long-password"})
    for operation in ("delete", "restore"):
        assert other.post(base + "/characters/" + operation, json={"character_ids": ["a"]}).status_code == 404
    assert client.post(base + "/characters/delete", json={"character_ids": []}).status_code == 422
    for ids in (["a", "a"], ["a", "unknown"]):
        assert client.post(base + "/characters/delete", json={"character_ids": ids}).status_code == 409
    assert client.get(base).json()["project"]["characters"] == characters


def test_restore_does_not_overwrite_newer_definition_or_exceed_storage_capacity(tmp_path, monkeypatch):
    client, project, characters = setup_project(tmp_path, monkeypatch)
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/characters/delete", json={"character_ids": ["a"]}).status_code == 200
    newer = {**characters[0], "id": "new-a", "appearance": "新しく確認した外見"}
    db.update_project(project["id"], project["user_id"], characters=[newer, *characters[1:]])
    assert client.post(base + "/characters/restore", json={"character_ids": ["a"]}).status_code == 409
    assert client.get(base).json()["project"]["characters"][0] == newer
    full = [{"id": f"full-{i}", "name": f"保管人物{i}"} for i in range(MAX_STORED_CHARACTERS)]
    db.update_project(project["id"], project["user_id"], characters=full)
    response = client.post(base + "/characters/restore", json={"character_ids": ["a"]})
    assert response.status_code == 409 and "保管上限" in response.json()["detail"]
    assert client.get(base).json()["project"]["characters"] == full
