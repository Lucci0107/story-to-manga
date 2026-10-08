"""候補提案で停止し、確認した対象だけ生成・保存する境界を検証する。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import MAX_STORED_CHARACTERS
from app.services.ai_pipeline import AIProviderError, DemoAIProvider
from app.services.character_cast_review import normalize_cast_review
from app.services.character_proposal import active_characters, build_character_proposal, panel_characters, validate_selection


SOURCE = "## 第1章\n葵は灯と会った。葵は朝日へ相談した。\n\n## 第2章\n灯が葵に助言した。\n\n## 第3章\n受付が葵を案内した。"


def candidates() -> list[dict]:
    return [{"name": name, "aliases": [], "role": role, "source_quotes": [quote], "importance": importance,
             "recommendation_reason": reason}
            for name, role, quote, importance, reason in [
                ("葵", "語り手", "葵は灯と会った。", 5, "全編を通じて選択する主役です。"),
                ("灯", "相談相手", "灯が葵に助言した。", 4, "主役の決断に関わる重要な支援者です。"),
                ("朝日", "協力者", "葵は朝日へ相談した。", 3, "限定した場面の協力者です。"),
                ("受付", "一時的な案内役", "受付が葵を案内した。", 1, "初期の固定設定は不要ですが、必要なら選べます。"),
            ]]


class ObservedProvider(DemoAIProvider):
    uses_external_api = True
    provider_name = "openai"

    def __init__(self):
        super().__init__()
        self.proposal_calls = 0
        self.profile_targets: list[list[str]] = []
        self.storyboard_targets: list[list[str]] = []

    def characters(self, *args, **kwargs):
        raise AssertionError("確認前に全員の設定を作成してはいけない")

    def propose_characters(self, *args, **kwargs):
        self.proposal_calls += 1
        self._record_demo("character_proposal")
        return candidates()

    def generate_character_profiles(self, targets, *args, **kwargs):
        self.profile_targets.append([item["name"] for item in targets])
        assert all(set(item) == {"name", "aliases", "role", "source_quotes"} for item in targets)
        return super().generate_character_profiles(targets, *args, **kwargs)

    def storyboard(self, text, analysis, settings, characters, *args, **kwargs):
        self.storyboard_targets.append([item["name"] for item in characters])
        return super().storyboard(text, analysis, settings, characters, *args, **kwargs)


def setup_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": f"proposal-{tmp_path.name}@example.test", "password": "long-password"})
    project = client.post("/api/projects", data={"title": "人物の選択", "story_text": SOURCE}).json()["project"]
    client.post(f"/api/projects/{project['id']}/analysis")
    provider = ObservedProvider()
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: provider)
    return client, project, provider


def propose(client, project):
    response = client.post(f"/api/projects/{project['id']}/character-proposal")
    assert response.status_code in {200, 202}
    return client.get(f"/api/projects/{project['id']}").json()["project"]["character_proposal"]


def selection(proposal, *names):
    return {"proposal_id": proposal["id"], "selected_candidate_ids": [item["id"] for item in proposal["candidates"] if item["name"] in names]}


def test_proposal_frequency_and_importance_are_separate_and_do_not_force_all_candidates():
    proposal = build_character_proposal(candidates(), {"original_text": SOURCE, "analysis": {}})
    assert proposal["section_count"] == 3 and proposal["section_label"] == "章"
    people = {item["name"]: item for item in proposal["candidates"]}
    assert people["葵"]["appearance_rate"] == 100
    assert people["葵"]["mention_count"] == 4
    assert people["灯"]["appearance_rate"] == 66.7
    assert people["朝日"]["appearance_rate"] == 33.3
    assert [item["name"] for item in proposal["candidates"] if item["recommended"]] == ["葵", "灯"]
    assert "代名詞" in proposal["frequency_note"]


def test_more_than_sixty_four_candidates_can_be_proposed_without_generating_them():
    roster = [{**candidates()[0], "name": f"候補{i}", "source_parts": [1]} for i in range(80)]
    review = {"groups": [{"name": item["name"], "members": [item["name"]], "importance": 4,
                           "recommendation_reason": "重要な役割がある候補です。"} for item in roster], "excluded": []}
    reviewed = normalize_cast_review(review, roster, for_proposal=True)
    project = {"original_text": SOURCE, "analysis": {}, "characters": []}
    proposal = build_character_proposal(reviewed, project)
    assert len(proposal["candidates"]) == 80
    assert len(proposal["selected_candidate_ids"]) == 12
    project["character_proposal"] = proposal
    assert len(validate_selection(project, proposal["id"], [item["id"] for item in proposal["candidates"][:64]])) == 64
    with pytest.raises(ValueError, match="1〜64人"):
        validate_selection(project, proposal["id"], [item["id"] for item in proposal["candidates"][:65]])


def test_proposal_and_draft_save_stop_before_profiles_and_survive_reload(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    proposal = propose(client, project)
    assert provider.profile_targets == []
    assert client.get(f"/api/projects/{project['id']}").json()["project"]["characters"] == []
    draft = selection(proposal, "朝日")
    assert client.put(f"/api/projects/{project['id']}/character-proposal/selection", json=draft).status_code == 200
    saved = client.get(f"/api/projects/{project['id']}").json()["project"]["character_proposal"]
    assert saved["selected_candidate_ids"] == draft["selected_candidate_ids"] and not saved["confirmed_at"]
    assert provider.profile_targets == []
    assert client.post(f"/api/projects/{project['id']}/character-proposal").json()["cached"] is True
    assert provider.proposal_calls == 1


def test_only_confirmed_people_are_generated_and_existing_profiles_are_reused(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    manual = {"id": "saved-person", "name": "葵", "appearance": "ユーザーが確認した外見"}
    db.update_project(project["id"], project["user_id"], characters=[manual])
    proposal = propose(client, project)
    body = selection(proposal, "葵", "朝日")
    assert client.post(f"/api/projects/{project['id']}/characters", json=body).status_code == 202
    saved = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert [item["name"] for item in saved["characters"]] == ["葵", "朝日"]
    assert saved["characters"][0] == manual
    assert saved["character_proposal"]["status"] == "generated"
    assert provider.profile_targets == [["朝日"]]
    assert client.post(f"/api/projects/{project['id']}/characters", json=body).status_code == 202
    assert provider.profile_targets == [["朝日"]]
    assert provider.proposal_calls == 1


def test_confirmation_is_required_and_storyboard_cannot_generate_profiles_implicitly(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/characters").status_code == 409
    proposal = propose(client, project)
    assert client.post(base + "/characters", json=selection(proposal)).status_code == 409
    assert client.post(base + "/characters", json={"proposal_id": proposal["id"], "selected_candidate_ids": ["unknown"]}).status_code == 409
    assert client.post(base + "/storyboard").status_code == 400
    assert provider.profile_targets == []


def test_changed_source_invalidates_the_proposal_and_prevents_costly_generation(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    proposal = propose(client, project)
    client.patch(f"/api/projects/{project['id']}", json={"original_text": SOURCE + "\n原稿を更新した。"})
    current = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert current["character_proposal"]["stale"] is True
    assert client.post(f"/api/projects/{project['id']}/characters", json=selection(proposal, "葵")).status_code == 409
    assert provider.profile_targets == []


def test_completed_profiles_survive_a_later_failure_and_retry_only_missing_people(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    archived = [{"id": f"archived-{i}", "name": f"保管人物{i}", "appearance": "保存済みの外見"} for i in range(64)]
    db.update_project(project["id"], project["user_id"], characters=archived)
    proposal = propose(client, project)
    body = selection(proposal, "葵", "灯", "朝日")
    normal = provider.generate_character_profiles

    def fail_after_one(targets, *args, **kwargs):
        batch = normal(targets[:1], *args, **kwargs)
        provider.character_profiles_callback(batch)
        raise AIProviderError("後続の対象がタイムアウトしました", retryable=False, error_category="timeout")

    provider.generate_character_profiles = fail_after_one
    assert client.post(f"/api/projects/{project['id']}/characters", json=body).status_code == 202
    saved = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert saved["characters"][:64] == archived
    assert [item["name"] for item in saved["characters"][64:]] == ["葵"]
    assert db.latest_generation_job(project["id"], "character")["status"] == "failed"
    provider.generate_character_profiles = normal
    assert client.post(f"/api/projects/{project['id']}/characters", json=body).status_code == 202
    assert provider.profile_targets == [["葵"], ["灯", "朝日"]]
    retried = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert len(retried["characters"]) == 67 and len(retried["active_characters"]) == 3
    assert retried["characters"][:64] == archived


def test_selection_and_confirmation_require_project_ownership(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    proposal = propose(client, project)
    other = TestClient(app)
    other.post("/register", data={"email": "other-proposal@example.test", "password": "long-password"})
    base = f"/api/projects/{project['id']}"
    assert other.post(base + "/character-proposal").status_code == 404
    assert other.put(base + "/character-proposal/selection", json=selection(proposal, "葵")).status_code == 404
    assert other.post(base + "/characters", json=selection(proposal, "葵")).status_code == 404
    assert provider.profile_targets == []


def test_confirmed_subset_is_used_for_storyboard_and_all_sixty_four_profiles_are_preserved(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    stored = [{"id": f"saved-{i}", "name": name, "appearance": "保存済みの外見"}
              for i, name in enumerate(["葵", "灯", "朝日", *[f"保管人物{i}" for i in range(61)]])]
    db.update_project(project["id"], project["user_id"], characters=stored)
    proposal = propose(client, project)
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/storyboard").status_code == 400
    assert client.post(base + "/characters", json=selection(proposal, "葵", "朝日")).status_code == 202
    saved = client.get(base).json()["project"]
    assert [item["name"] for item in saved["active_characters"]] == ["葵", "朝日"]
    assert saved["characters"] == stored and provider.profile_targets == []
    assert client.post(base + "/storyboard").status_code == 202
    assert provider.storyboard_targets == [["葵", "朝日"]]
    assert client.get(base).json()["project"]["characters"] == stored
    assert client.put(base + "/character-proposal/selection", json=selection(proposal, "葵")).status_code == 200
    draft = client.get(base).json()["project"]
    assert draft["active_characters"] == [] and draft["characters"] == stored
    assert client.post(base + "/storyboard").status_code == 400
    assert provider.storyboard_targets == [["葵", "朝日"]] and provider.profile_targets == []


def test_only_panel_actors_are_supplied_and_incomplete_confirmation_cannot_be_used():
    stored = [{"id": "saved-main", "name": "葵", "aliases": ["主人公"]},
              {"id": "saved-support", "name": "灯", "aliases": []}]
    project = {"original_text": SOURCE, "analysis": {}, "characters": stored}
    proposal = build_character_proposal(candidates(), project)
    project["character_proposal"] = proposal
    assert active_characters(project) == []
    assert panel_characters(project, {"characters": ["主人公"]}) == stored[:1]
    proposal.update(confirmed_at="2026-10-08T00:00:00+00:00",
                    selected_candidate_ids=selection(proposal, "葵", "朝日")["selected_candidate_ids"])
    assert active_characters(project) == []
    proposal["selected_candidate_ids"] = selection(proposal, "葵")["selected_candidate_ids"]
    assert active_characters(project) == stored[:1]
    # 選択から外した人物も、既存のコマに登場している場合は参照する。
    assert panel_characters(project, {"characters": ["灯"]}) == stored[1:]
    project["original_text"] += "原稿を更新した。"
    assert active_characters(project) == []


def test_twelve_selected_people_can_add_two_new_profiles_beside_sixty_four_stored_profiles(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    names = [f"選択人物{i:02d}" for i in range(1, 13)]
    roster = [{**candidates()[0], "name": name, "aliases": [], "source_quotes": [f"{name}は主役を支えた。"],
               "importance": 5 if i == 0 else 4} for i, name in enumerate(names)]
    source = "\n\n".join(f"## 第{i}章\n{item['source_quotes'][0]}" for i, item in enumerate(roster, 1))
    stored = [{"id": f"stored-{i}", "name": name, "appearance": "保存済みの外見", "identity_notes": "固定する特徴"}
              for i, name in enumerate([*names[:10], *[f"保管人物{i}" for i in range(54)]])]
    db.update_project(project["id"], project["user_id"], original_text=source, characters=stored)
    monkeypatch.setattr(provider, "propose_characters", lambda *args, **kwargs: roster)
    proposal = propose(client, project)
    body = selection(proposal, *names)
    base = f"/api/projects/{project['id']}"
    assert client.put(base + "/character-proposal/selection", json=body).status_code == 200
    assert provider.profile_targets == []
    assert client.post(base + "/characters", json=body).status_code == 202
    saved = client.get(base).json()["project"]
    assert [item["name"] for item in saved["active_characters"]] == names
    assert saved["characters"][:64] == stored and len(saved["characters"]) == 66
    assert provider.profile_targets == [names[10:]]

    # 64番目より後の人物も編集・保存し、後続の処理で欠落させない。
    edited = saved["characters"]
    edited[-1]["appearance"] = "ユーザーが編集した新規人物の外見"
    response = client.patch(base, json={"characters": edited})
    assert response.status_code == 200
    edited_project = response.json()["project"]
    assert len(edited_project["characters"]) == 66
    assert [item["id"] for item in edited_project["characters"]] == [item["id"] for item in edited]
    assert all(item["identity_notes"] == "固定する特徴" for item in edited_project["characters"][:64])
    assert edited_project["characters"][-1]["appearance"] == edited[-1]["appearance"]
    assert client.post(base + "/storyboard").status_code == 202
    after_storyboard = client.get(base).json()["project"]
    assert provider.storyboard_targets == [names]
    assert after_storyboard["characters"] == edited_project["characters"]
    assert panel_characters(after_storyboard, {"characters": names[10:]}) == after_storyboard["characters"][64:]
    assert client.post(base + "/characters", json=body).status_code == 202
    assert provider.profile_targets == [names[10:]]


def test_storage_capacity_is_validated_before_ai_and_oversized_save_cannot_discard_profiles(tmp_path, monkeypatch):
    client, project, provider = setup_project(tmp_path, monkeypatch)
    stored = [{"id": f"stored-{i}", "name": f"保管人物{i}", "appearance": "保存済みの外見"}
              for i in range(MAX_STORED_CHARACTERS)]
    db.update_project(project["id"], project["user_id"], characters=stored)
    proposal = propose(client, project)
    base = f"/api/projects/{project['id']}"
    response = client.post(base + "/characters", json=selection(proposal, "葵"))
    assert response.status_code == 409 and "保管上限" in response.json()["detail"]
    assert provider.profile_targets == []
    response = client.patch(base, json={"characters": [*stored, {"id": "new", "name": "新規人物"}]})
    assert response.status_code == 422
    assert client.get(base).json()["project"]["characters"] == stored
