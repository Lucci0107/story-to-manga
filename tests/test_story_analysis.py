"""長い原稿の中間・最終章、解析の再利用、生成前の復旧導線を検証する。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app, process_analysis_job
from app.schemas import normalize_analysis, normalize_storyboard
from app.services.ai_pipeline import AIProviderError, DemoAIProvider, OpenAIProvider, demo_analysis
from app.services.architect import plan_event_boundaries
from app.services.character_proposal import active_characters
from app.services.story_analysis import (
    analysis_batches, analysis_content_fingerprint, analysis_source_status,
    complete_source_analysis, story_analysis_units,
)
from app.services.story_profile import story_fingerprint


def manuscript(repeats=45):
    return "\n\n".join(f"## 第{n}章 場面{n}\n始まり{n}。" + "主人公が仲間と手紙を読み、考えを整理した。" * repeats
                         + f"中間の固有事件{n}。" + "二人は答えを探して次の町へ向かった。" * repeats
                         + f"結末の固有事件{n}。" for n in range(1, 32)) + "\n\n## 編集上の注記\n物語の結末ではない出典情報。"


def parsed_sections(user):
    return json.loads(user.split("<story_content>\n", 1)[1].split("\n</story_content>", 1)[0])


def output_for(units):
    value = demo_analysis("主人公が旅を終える。", "全編の確認")
    value["source_sections"] = [{"number": unit["number"], "summary": f"区間{unit['number']}を整理する。",
                                 "events": [f"中間の固有事件{unit['number']}。", f"結末の固有事件{unit['number']}。"]} for unit in units]
    value["ending"] = "主人公が学び続ける決意を述べて旅を終える。"
    return value


def mock_transport(monkeypatch, requests):
    monkeypatch.setattr("app.services.ai_pipeline.get_settings", lambda: SimpleNamespace(
        openai_text_model="gpt-6.1-sol", openai_image_model="gpt-image-2", openai_api_key="test-key",
        openai_responses_url="https://api.openai.com/v1/responses", openai_max_retries=0,
        openai_analysis_timeout_seconds=300, openai_analysis_max_output_tokens=25000,
        storyboard_batch_pages=8,
    ))

    def respond(_url, *, payload, **kwargs):
        requests.append(payload)
        schema = payload["text"]["format"]["schema"]
        if "source_sections" in schema["properties"]:
            value = output_for(parsed_sections(payload["input"]))
        elif "pages" in schema["properties"]:
            context = json.JSONDecoder().raw_decode(payload["input"])[0]
            value = {"pages": [{"page_number": n, "title": f"本文{n}", "layout": "hero",
                                "panels": [{"description": f"場面{n}で決断する。", "background": "町の道", "characters": ["蒼"],
                                            "event_ids": context["event_plan"][n-context["page_range"]["start"]]["allowed_events"]}]}
                               for n in range(context["page_range"]["start"], context["page_range"]["end"] + 1)]}
        else:
            value = demo_analysis("主人公が旅を終える。", "統合")
            value["ending"] = "最終区間の結末を保持する。"
        return {"output_text": json.dumps(value, ensure_ascii=False)}

    monkeypatch.setattr("app.services.ai_pipeline.request_json", respond)


def test_all_31_chapters_are_read_once_including_middle_and_ending(monkeypatch):
    text = manuscript()
    requests = []
    mock_transport(monkeypatch, requests)
    value = OpenAIProvider().analyze(text, "全編", {"prompt_text": "原作の因果関係を保持する。"})
    assert len(requests) == 1
    for n in range(1, 32):
        assert f"中間の固有事件{n}。" in requests[0]["input"]
        assert f"結末の固有事件{n}。" in requests[0]["input"]
    assert "物語の結末ではない出典情報" not in requests[0]["input"]
    assert "<knowledge_reference>" in requests[0]["input"]
    assert len(value["major_events"]) == 62
    assert normalize_analysis(value) == value
    assert analysis_source_status(text, value)["complete"]
    assert requests[0]["max_output_tokens"] == 25000
    assert requests[0]["store"] is False


def test_missing_section_cannot_be_marked_complete_or_overwrite_valid_analysis(monkeypatch):
    text = manuscript()
    provider = OpenAIProvider()
    calls = []

    def incomplete(_system, user, **_kwargs):
        calls.append(user)
        value = output_for(parsed_sections(user))
        value["source_sections"].pop(15)
        return value

    monkeypatch.setattr(provider, "_json_call", incomplete)
    with pytest.raises(AIProviderError):
        provider.analyze(text, "全編")
    assert len(calls) == 2


def test_large_manuscript_reuses_completed_parts_and_still_reaches_last_chapter(monkeypatch):
    text = manuscript(100)
    units = story_analysis_units(text)
    batches = analysis_batches(units)
    assert len(batches) > 1
    requests = []
    mock_transport(monkeypatch, requests)
    provider = OpenAIProvider()
    provider.analysis_cached_parts = [output_for(batches[0])]
    saved = []
    provider.analysis_parts_callback = lambda parts: saved.append(list(parts))
    value = provider.analyze(text, "全編")
    assert len(requests) == len(batches)  # 未完了の範囲 + 全体の統合だけ。
    assert all(f"中間の固有事件{n}。" in requests[-1]["input"] for n in range(1, 32))
    assert value["major_events"][-1] == "結末の固有事件31。"
    assert len(value["source_sections"]) == len(units)
    assert saved and len(saved[-1]) == len(batches)


def test_source_chapters_and_events_are_selected_for_the_correct_storyboard_batch(monkeypatch):
    text = manuscript()
    units = story_analysis_units(text)
    value = complete_source_analysis(output_for(units), text, units)
    requests = []
    mock_transport(monkeypatch, requests)
    pages = OpenAIProvider().storyboard(text, value, {"target_page_count": 16, "language": "ja"},
                                      [{"name": "蒼", "role": "主人公"}, {"name": "原稿に登場しない人物", "appearance": "不要な外見設定"}])
    assert len(pages) == 16 and len(requests) == 8
    contexts = [json.JSONDecoder().raw_decode(request["input"])[0] for request in requests]
    assert "中間の固有事件1。" in json.dumps(contexts[0]["story_reference"], ensure_ascii=False)
    assert "結末の固有事件31。" in json.dumps(contexts[-1]["story_reference"], ensure_ascii=False)
    assert "結末の固有事件31。" not in json.dumps(contexts[0]["story_reference"], ensure_ascii=False)
    assert all(len(context["event_plan"]) == 2 for context in contexts)
    assert all("source_sections" not in context["analysis"] for context in contexts)
    assert all(len(context["characters"]) == 1 for context in contexts)
    assert all(page["source_analysis_fingerprint"] == analysis_content_fingerprint(value) for page in pages)
    assert not analysis_source_status(text, value, pages)["storyboard_needs_rebuild"]
    assert not analysis_source_status(text, value, pages + [{"page_kind": "content"}])["storyboard_needs_rebuild"]


def test_storyboard_cannot_omit_the_ending_event_even_with_the_right_page_count(monkeypatch):
    text = manuscript()
    units = story_analysis_units(text)
    value = complete_source_analysis(output_for(units), text, units)
    provider = OpenAIProvider()
    calls = []

    def ungrounded(_system, user, **_kwargs):
        context = json.JSONDecoder().raw_decode(user)[0]
        calls.append(context)
        return {"pages": [{"page_number": n, "panels": [{"description": "同じ図を静かに見せる。", "event_ids": []}]}
                          for n in range(context["page_range"]["start"], context["page_range"]["end"] + 1)]}

    monkeypatch.setattr(provider, "_json_call", ungrounded)
    with pytest.raises(AIProviderError):
        provider.storyboard(text, value, {"target_page_count": 16}, [{"name": "蒼"}])
    assert len(calls) == 4


def test_an_event_can_progress_across_multiple_pages_without_empty_allowed_events():
    events = [f"出来事{n}" for n in range(1, 7)]
    pages = plan_event_boundaries([{} for _ in range(120)], {"major_events": events})
    assert all(page["allowed_events"] for page in pages)
    assert pages[-1]["allowed_events"] == events[-1:]
    assert pages[1]["first_reveal"] == ""
    assert pages[20]["first_reveal"] == events[1]
    assert pages[1]["forbidden_until_later"] == events[1:]


def project_fixture(tmp_path):
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": f"analysis-{tmp_path.name}@example.test", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "全編の再解析", "story_text": manuscript()}).json()["project"]
    project = db.update_project(project["id"], project["user_id"], analysis=demo_analysis("古い冒頭の抜粋。", "全編"), update_analysis=True)
    base = f"/api/projects/{project['id']}"
    proposal = client.post(base + "/character-proposal").json()["project"]["character_proposal"]
    assert client.post(base + "/characters", json={"proposal_id": proposal["id"], "selected_candidate_ids": proposal["selected_candidate_ids"]}).status_code == 200
    pages = normalize_storyboard([{"id": "old-body", "page_number": 1, "name_review_required": True,
                                  "panels": [{"id": "old-panel", "description": "古い場面", "background": "街"}]}], project["settings"])
    db.update_project(project["id"], project["user_id"], storyboard=pages)
    return client, db.get_project(project["id"], project["user_id"])


class ExternalAnalysisProvider(DemoAIProvider):
    uses_external_api = True
    provider_name = "openai"


def test_reanalysis_is_background_saved_and_reuses_confirmed_characters_without_generating_profiles(tmp_path, monkeypatch):
    client, project = project_fixture(tmp_path)
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/storyboard").status_code == 409
    before_characters, before_active = project["characters"], active_characters(project)
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: ExternalAnalysisProvider())
    response = client.post(base + "/analysis")
    assert response.status_code == 202
    job_id = response.json()["job"]["id"]
    status = client.get(base + "/analysis/status").json()
    assert status["job"]["id"] == job_id and status["job"]["status"] == "completed"
    assert "input_json" not in status["job"] and "project" not in status
    after = client.get(base).json()["project"]
    assert after["analysis_source"]["complete"] and after["analysis_source"]["storyboard_needs_rebuild"]
    assert after["characters"] == before_characters and after["active_characters"] == before_active
    assert after["storyboard"] == project["storyboard"]
    assert any("ネームを作り直して" in error for error in after["name_script"]["validation"]["errors"])
    assert client.post(base + "/storyboard").status_code == 202
    rebuilt = client.get(base).json()["project"]
    assert not rebuilt["analysis_source"]["storyboard_needs_rebuild"]
    assert not any("解析が更新" in error or "ネームを作り直して" in error for error in rebuilt["name_script"]["validation"]["errors"])


def test_failed_or_stale_job_keeps_old_analysis_and_rejects_late_result(tmp_path, monkeypatch):
    client, project = project_fixture(tmp_path)
    inputs = {"source_fingerprint": story_fingerprint(project["original_text"]),
              "analysis_fingerprint": analysis_content_fingerprint(project["analysis"]), "title": project["title"],
              "settings": project["settings"], "knowledge_refs": []}
    job, _ = db.create_async_generation_job(project["id"], "analysis", f"analysis:{project['id']}", input_payload=inputs)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db.connection() as conn:
        conn.execute("UPDATE generation_jobs SET updated_at = ? WHERE id = ?", (cutoff, job["id"]))
    status = client.get(f"/api/projects/{project['id']}/analysis/status").json()
    assert status["job"]["status"] == "failed"
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: pytest.fail("失敗したJobを再実行してはいけない"))
    process_analysis_job(project["id"], project["user_id"], job["id"])
    assert not db.complete_analysis_job(job["id"], project["id"], project["user_id"], {"title": "遅れた結果"})
    assert db.get_project(project["id"], project["user_id"])["analysis"] == project["analysis"]


def test_analysis_job_is_not_duplicated_and_changed_source_does_not_trigger_api_work(tmp_path, monkeypatch):
    client, project = project_fixture(tmp_path)
    queued = []
    monkeypatch.setattr("app.main.BackgroundTasks.add_task", lambda self, func, *args, **kwargs: queued.append((func, args)))
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: ExternalAnalysisProvider())
    base = f"/api/projects/{project['id']}"
    first, second = client.post(base + "/analysis"), client.post(base + "/analysis")
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert len(queued) == 1

    class NeverCall(ExternalAnalysisProvider):
        def analyze(self, *args, **kwargs):
            pytest.fail("変更前の入力でAPIを起動してはいけない")

    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: NeverCall())
    db.update_project(project["id"], project["user_id"], original_text=project["original_text"] + "\n原稿の更新")
    process_analysis_job(project["id"], project["user_id"], first.json()["job"]["id"])
    assert db.get_generation_job(first.json()["job"]["id"])["status"] == "failed"
    assert db.get_project(project["id"], project["user_id"])["analysis"] == project["analysis"]


def test_retry_reuses_saved_parts_without_recreating_characters(tmp_path, monkeypatch):
    client, project = project_fixture(tmp_path)

    class FailsOnce(ExternalAnalysisProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.cached_on_retry = None

        def analyze(self, text, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.analysis_parts_callback([output_for(story_analysis_units(text)[:10])])
                raise AIProviderError("解析の後半だけ失敗しました", retryable=False)
            self.cached_on_retry = list(self.analysis_cached_parts)
            return super().analyze(text, *args, **kwargs)

    provider = FailsOnce()
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: provider)
    base = f"/api/projects/{project['id']}"
    failed = client.post(base + "/analysis").json()["job"]
    assert client.get(base + "/analysis/status").json()["job"]["status"] == "failed"
    before = db.get_project(project["id"], project["user_id"])
    assert before["analysis"] == project["analysis"] and before["characters"] == project["characters"]
    retried = client.post(base + "/analysis").json()["job"]
    assert retried["id"] != failed["id"]
    assert provider.cached_on_retry and len(provider.cached_on_retry[0]["source_sections"]) == 10
    assert client.get(base + "/analysis/status").json()["job"]["status"] == "completed"
