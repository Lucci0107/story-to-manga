"""ネーム生成の分割・途中保存・再開と、古い内容の保護を検証する。"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import BackgroundTasks

from app import db
from app.main import app, process_storyboard_job
from app.services.ai_pipeline import AIProviderError, DemoAIProvider, OpenAIProvider, STORYBOARD_SCHEMA
from app.services.architect import finalize_storyboard
from app.services.openai_client import OpenAIRequestError
from app.services.story_analysis import analysis_content_fingerprint, analysis_source_status
from app.services.storyboard_jobs import public_storyboard_job, storyboard_project_inputs


def source_and_analysis():
    text = "\n\n".join(f"## 第{number}章\n" + "主人公は仲間と旅の計画を考えた。" * 650
                        + f"最終事件{number}。" for number in range(1, 4))
    return text, DemoAIProvider().analyze(text, "旅の記録")


def page(number, analysis, events=None):
    return {"id": f"generated-page-{number}", "page_number": number, "title": f"本文{number}", "layout": "hero",
            "source_analysis_fingerprint": analysis_content_fingerprint(analysis),
            "panels": [{"id": f"generated-{number}", "description": f"場面{number}で主人公が決意を伝える。",
                        "characters": ["主人公"], "background": "町の広場", "event_ids": events or []}]}


def configure_provider(monkeypatch):
    monkeypatch.setattr("app.services.ai_pipeline.get_settings", lambda: SimpleNamespace(
        storyboard_batch_pages=8, storyboard_full_source_batch_pages=2,
        openai_api_key="test-key", openai_text_model="gpt-6.1-sol", openai_image_model="gpt-image-2",
        openai_responses_url="https://api.openai.com/v1/responses", openai_storyboard_max_retries=1,
    ))


@pytest.mark.parametrize("category", ["timeout", "output_limit", "validation"])
def test_only_failed_range_is_split_and_completed_pages_are_saved(monkeypatch, category):
    text, analysis = source_and_analysis()
    configure_provider(monkeypatch)
    provider = OpenAIProvider({"storyboard_model": "gpt-6.1-sol", "reasoning_effort": "xhigh"})
    model_settings = provider.model_settings.copy()
    ranges, checkpoints = [], []

    def respond(_system, user, **kwargs):
        context = json.JSONDecoder().raw_decode(user)[0]
        start, end = context["page_range"]["start"], context["page_range"]["end"]
        ranges.append((start, end))
        if (start, end) == (1, 2):
            raise AIProviderError("範囲が大き過ぎます", error_category=category, retryable=False)
        assert kwargs["schema"]["properties"]["pages"]["maxItems"] == end - start + 1
        return {"pages": [page(number, analysis, context["event_plan"][number-start]["allowed_events"])
                          for number in range(start, end+1)]}

    provider._json_call = respond
    provider.storyboard_pages_callback = lambda pages: checkpoints.append(copy.deepcopy(pages))
    result = provider.storyboard(text, analysis, {"target_page_count": 4, "language": "ja"}, [{"name": "主人公"}])
    assert ranges == [(1, 2), (1, 1), (2, 2), (3, 4)]
    assert [len(pages) for pages in checkpoints] == [1, 2, 4]
    assert [entry["page_number"] for entry in result] == [1, 2, 3, 4]
    assert provider.model_settings == model_settings


def test_resume_skips_completed_pages_and_keeps_the_previous_context(monkeypatch):
    text, analysis = source_and_analysis()
    configure_provider(monkeypatch)
    provider, requests, saved = OpenAIProvider(), [], []

    def respond(_system, user, **_kwargs):
        context = json.JSONDecoder().raw_decode(user)[0]
        requests.append(context)
        start, end = context["page_range"]["start"], context["page_range"]["end"]
        if start <= 2:
            raise AIProviderError("応答が止まりました", error_category="timeout", retryable=False)
        return {"pages": [page(number, analysis, context["event_plan"][number-start]["allowed_events"])
                          for number in range(start, end+1)]}

    provider.storyboard_cached_pages = [page(1, analysis), page(2, analysis)]
    provider._json_call = respond
    provider.storyboard_pages_callback = lambda pages: saved.append(copy.deepcopy(pages))
    result = provider.storyboard(text, analysis, {"target_page_count": 4, "language": "ja"}, [{"name": "主人公"}])
    assert [(item["page_range"]["start"], item["page_range"]["end"]) for item in requests] == [(3, 4)]
    assert [item["page_number"] for item in requests[0]["previous_batch_context"]] == [1, 2]
    assert result[0]["panels"][0]["id"] == "generated-1" and len(saved[-1]) == 4


def test_storyboard_timeout_does_not_repeat_the_same_paid_request(monkeypatch):
    configure_provider(monkeypatch)
    calls = []

    def timeout(_url, **kwargs):
        calls.append(kwargs)
        raise OpenAIRequestError("タイムアウトしました", category="timeout")

    monkeypatch.setattr("app.services.ai_pipeline.request_json", timeout)
    with pytest.raises(AIProviderError) as error:
        OpenAIProvider()._json_call("編集者", "原稿", schema_name="manga_storyboard", schema=STORYBOARD_SCHEMA, task_key="storyboard")
    assert len(calls) == 1 and calls[0]["retry_timeouts"] is False
    assert error.value.error_category == "timeout" and error.value.retryable is False


def test_fixed_event_ids_are_constrained_then_saved_as_exact_source_events(monkeypatch):
    text, analysis = source_and_analysis()
    configure_provider(monkeypatch)
    provider, contexts, progress, saved = OpenAIProvider(), [], [], []

    def respond(_system, user, **kwargs):
        context = json.JSONDecoder().raw_decode(user)[0]
        contexts.append(context)
        enum = kwargs["schema"]["properties"]["pages"]["items"]["properties"]["panels"]["items"]["properties"]["event_ids"]["items"]["enum"]
        assert set(enum) == {event["id"] for event in context["event_catalog"]}
        if len(contexts) == 1:
            # 原文の要約・独自IDは受け付けず、特定した不備を次の要求に含める。
            return {"pages": [page(planned["page_number"], analysis, ["独自の出来事参照"])
                              for planned in context["event_plan"]]}
        if len(contexts) == 2:
            assert "本文1ページのevent_idsは" in user and "event_catalogのid" in user
        return {"pages": [page(planned["page_number"], analysis, planned["allowed_event_ids"])
                          for planned in context["event_plan"]]}

    provider._json_call = respond
    provider.storyboard_request_callback = progress.append
    provider.storyboard_pages_callback = lambda pages: saved.append(copy.deepcopy(pages))
    result = provider.storyboard(text, analysis, {"target_page_count": 4}, [{"name": "主人公"}])
    assert [item["phase"] for item in progress] == ["generating", "repairing", "generating"]
    assert [(item["page_start"], item["page_end"]) for item in progress] == [(1, 2), (1, 2), (3, 4)]
    assert [len(value) for value in saved] == [2, 4]
    source_events = set(analysis["major_events"])
    assert all(set(panel["event_ids"]).issubset(source_events) for value in result for panel in value["panels"])


def test_invalid_event_references_split_only_the_failed_range_and_stop_at_one_page(monkeypatch):
    text, analysis = source_and_analysis()
    configure_provider(monkeypatch)
    provider, ranges = OpenAIProvider(), []
    provider.storyboard_cached_pages = [page(number, analysis) for number in range(1, 19)]

    def respond(_system, user, **_kwargs):
        context = json.JSONDecoder().raw_decode(user)[0]
        start, end = context["page_range"]["start"], context["page_range"]["end"]
        ranges.append((start, end))
        return {"pages": [page(number, analysis, ["非公開原稿に存在しない参照"])
                          for number in range(start, end + 1)]}

    provider._json_call = respond
    with pytest.raises(AIProviderError) as error:
        provider.storyboard(text, analysis, {"target_page_count": 20}, [{"name": "主人公"}])
    assert ranges == [(19, 20), (19, 20), (19, 19), (19, 19)]
    assert len(provider.storyboard_cached_pages) == 18
    assert error.value.error_category == "validation"
    assert "本文19ページの1コマ目" in str(error.value) and "再試行" in str(error.value)
    assert "非公開原稿" not in str(error.value)


class PartialProvider(DemoAIProvider):
    uses_external_api = True
    provider_name = "openai"

    def __init__(self):
        super().__init__()
        self.fail = True
        self.generated = []

    def storyboard(self, text, analysis, settings, characters, knowledge_context=None):
        pages = copy.deepcopy(self.storyboard_cached_pages)
        for number in range(len(pages)+1, settings["target_page_count"]+1):
            if self.fail and number == 3:
                raise AIProviderError("応答がタイムアウトしました", error_category="timeout", retryable=False)
            pages.append(page(number, analysis))
            self.generated.append(number)
            self.storyboard_pages_callback(pages)
        return finalize_storyboard(pages, analysis, settings)


def project_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_PROVIDER", "demo")
    monkeypatch.setenv("IMAGE_PROVIDER", "demo")
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "resume@example.com", "password": "long-password"})
    text, analysis = source_and_analysis()
    project = client.post("/api/projects", data={"title": "再開の検証", "story_text": text}).json()["project"]
    project = db.update_project(project["id"], project["user_id"], analysis=analysis, update_analysis=True,
                                characters=[{"id": "main-person", "name": "主人公", "role": "主人公"}],
                                settings={**project["settings"], "target_page_count": 4, "title_mode": "cover", "back_cover_mode": "generate"},
                                storyboard=[page(1, {"ending": "旧解析"})], status="storyboard_ready", current_step="storyboard")
    provider = PartialProvider()
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings=None: provider)
    return client, project, provider


def test_job_retry_reuses_completed_pages_and_preserves_old_name_and_characters(tmp_path, monkeypatch):
    client, project, provider = project_fixture(tmp_path, monkeypatch)
    first = client.post(f"/api/projects/{project['id']}/storyboard")
    assert first.status_code == 202 and "input_json" not in first.json()["job"]
    status = client.get(f"/api/projects/{project['id']}/generation/status").json()
    assert status["storyboard_job"]["status"] == "failed"
    assert status["storyboard_job"]["completed_pages"] == 2
    assert status["storyboard_job"]["target_pages"] == 4
    assert all("input_json" not in job for job in status["jobs"])
    saved = db.get_project(project["id"], project["user_id"])
    assert saved["storyboard"] == project["storyboard"] and saved["characters"] == project["characters"]
    # 同じ失敗JobをWorkerが再実行しても復活・課金しない。
    process_storyboard_job(project["id"], project["user_id"], first.json()["job"]["id"])
    assert provider.generated == [1, 2]
    provider.fail = False
    second = client.post(f"/api/projects/{project['id']}/storyboard")
    assert second.status_code == 202 and second.json()["job"]["completed_pages"] == 2
    assert provider.generated == [1, 2, 3, 4]
    saved = db.get_project(project["id"], project["user_id"])
    assert len(saved["storyboard"]) == 6 and saved["characters"] == project["characters"]
    assert not analysis_source_status(saved["original_text"], saved["analysis"], saved["storyboard"])["storyboard_needs_rebuild"]
    assert client.get(f"/api/projects/{project['id']}/generation/status").json()["storyboard_job"]["status"] == "completed"


@pytest.mark.parametrize("change", ["settings", "analysis", "characters", "storyboard", "knowledge", "model"])
def test_changed_inputs_do_not_reuse_old_generated_pages(tmp_path, monkeypatch, change):
    client, project, provider = project_fixture(tmp_path, monkeypatch)
    client.post(f"/api/projects/{project['id']}/storyboard")
    current = db.get_project(project["id"], project["user_id"])
    if change == "settings":
        db.update_project(project["id"], project["user_id"], settings={**current["settings"], "dialogue_density": "high"})
    elif change == "analysis":
        db.update_project(project["id"], project["user_id"], analysis={**current["analysis"], "tone": "希望"}, update_analysis=True)
    elif change == "characters":
        db.update_project(project["id"], project["user_id"], characters=[{**current["characters"][0], "appearance": "短髪"}])
    elif change == "storyboard":
        db.update_project(project["id"], project["user_id"], storyboard=[{**current["storyboard"][0], "title": "手動編集"}])
    elif change == "knowledge":
        monkeypatch.setattr("app.main.retrieve_knowledge_context", lambda *_args: {"references": [{"document_id": "updated-reference", "version": 2}]})
    else:
        monkeypatch.setattr("app.main.project_ai_model_settings", lambda *_args: {"storyboard_model": "gpt-6.1-sol", "reasoning_effort": "xhigh"})
    provider.fail = False
    result = client.post(f"/api/projects/{project['id']}/storyboard")
    assert result.status_code == 202 and result.json()["job"]["completed_pages"] == 0
    assert provider.generated == [1, 2, 1, 2, 3, 4]


def test_checkpoint_rejects_changed_source_and_late_failed_results(tmp_path, monkeypatch):
    _client, project, _provider = project_fixture(tmp_path, monkeypatch)
    inputs = storyboard_project_inputs(project)
    job, _ = db.create_async_generation_job(project["id"], "storyboard", "checkpoint", input_payload=inputs)
    assert db.start_generation_job(job["id"]) is True
    assert db.start_generation_job(job["id"]) is False
    assert db.save_storyboard_checkpoint(job["id"], project["id"], "another-user", []) is False
    db.update_project(project["id"], project["user_id"], original_text=project["original_text"] + "新しい結末。")
    assert db.save_storyboard_checkpoint(job["id"], project["id"], project["user_id"], [page(1, project["analysis"])]) is False
    assert db.complete_storyboard_job(job["id"], project["id"], project["user_id"], project["characters"], []) is False
    db.fail_storyboard_job(job["id"], project["id"], project["user_id"], "停止しました", "timeout")
    assert db.start_generation_job(job["id"]) is False
    assert db.save_storyboard_checkpoint(job["id"], project["id"], project["user_id"], []) is False


def test_request_progress_preserves_checkpoint_and_has_owner_and_terminal_guards(tmp_path, monkeypatch):
    _client, project, _provider = project_fixture(tmp_path, monkeypatch)
    inputs = {**storyboard_project_inputs(project), "pages": [page(1, project["analysis"])]}
    job, _ = db.create_async_generation_job(project["id"], "storyboard", "progress", input_payload=inputs)
    db.start_generation_job(job["id"])
    details = {"page_start": 2, "page_end": 3, "phase": "repairing", "attempt": 2, "raw_source": "公開しない原稿内容"}
    assert not db.update_storyboard_progress(job["id"], project["id"], "another-user", details)
    assert db.update_storyboard_progress(job["id"], project["id"], project["user_id"], details)
    updated = db.get_generation_job(job["id"])
    public = public_storyboard_job(updated)
    assert public["completed_pages"] == 1 and public["progress"]["page_start"] == 2
    assert public["progress"]["phase"] == "repairing" and public["progress"]["started_at"]
    assert "公開しない" not in json.dumps(public, ensure_ascii=False)
    assert "input_json" not in public and "raw_source" not in public["progress"]
    assert json.loads(updated["input_json"])["pages"] == inputs["pages"]
    db.fail_storyboard_job(job["id"], project["id"], project["user_id"], "停止しました")
    assert not db.update_storyboard_progress(job["id"], project["id"], project["user_id"], details)


def test_navigation_does_not_reflow_old_name_or_invalidate_in_progress_checkpoint(tmp_path, monkeypatch):
    client, project, _provider = project_fixture(tmp_path, monkeypatch)
    inputs = storyboard_project_inputs(project)
    job, _ = db.create_async_generation_job(project["id"], "storyboard", "navigate", input_payload=inputs)
    db.start_generation_job(job["id"])
    with db.connection() as conn:
        before = conn.execute("SELECT storyboard_json FROM projects WHERE id = ?", (project["id"],)).fetchone()["storyboard_json"]
    response = client.patch(f"/api/projects/{project['id']}", json={"current_step": "analysis"})
    assert response.status_code == 200
    with db.connection() as conn:
        after = conn.execute("SELECT storyboard_json FROM projects WHERE id = ?", (project["id"],)).fetchone()["storyboard_json"]
    assert after == before
    assert db.save_storyboard_checkpoint(job["id"], project["id"], project["user_id"], [page(1, project["analysis"])])
    assert db.get_generation_job(job["id"])["status"] == "processing"


def test_duplicate_requests_queue_only_one_storyboard_worker(tmp_path, monkeypatch):
    client, project, provider = project_fixture(tmp_path, monkeypatch)
    tasks = []
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda _self, function, *args: tasks.append((function, args)))
    first = client.post(f"/api/projects/{project['id']}/storyboard")
    second = client.post(f"/api/projects/{project['id']}/storyboard")
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"] and len(tasks) == 1
    function, args = tasks[0]
    function(*args)
    function(*args)
    assert provider.generated == [1, 2]
