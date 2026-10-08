"""AIモデル設定・推論強度・フォールバックの決定論的テスト。"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app
from app.services.ai_pipeline import AIProviderError, OpenAIProvider
from app.services.openai_client import OpenAIRequestError
from app.services.model_registry import (
    DEFAULT_AI_MODEL_SETTINGS,
    MODEL_TASKS,
    TEXT_MODEL_IDS,
    _AVAILABILITY_CACHE,
    get_model_availability,
    is_allowed_image_model,
    is_allowed_text_model,
    model_registry_view,
    reasoning_for_model,
    resolve_model_settings,
    validate_model_settings,
)


class FakeHTTPResponse:
    """urllibレスポンスの最小モック。"""

    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_provider="openai",
        openai_api_key="test-key",
        openai_responses_url="https://api.openai.com/v1/responses",
        openai_models_url="https://api.openai.com/v1/models",
        openai_image_url="https://api.openai.com/v1/images/generations",
        openai_text_model="gpt-5.6-luna",
        openai_image_model="gpt-image-2",
        openai_timeout_seconds=5.0,
        openai_max_retries=0,
        openai_max_output_tokens=2_000,
    )


def response_with_json(value: dict) -> dict:
    return {
        "model": "gpt-5.6-sol",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        ],
    }


def valid_analysis() -> dict:
    return {
        "title": "灯台",
        "synopsis": "蒼が決断を伝える。",
        "genre": "ドラマ",
        "tone": "静か",
        "world_setting": "霧の町",
        "main_characters": ["蒼"],
        "supporting_characters": [],
        "locations": ["灯台"],
        "major_events": ["再会"],
        "story_beats": ["導入", "決断"],
        "conflicts": ["迷い"],
        "climax": "決断を伝える",
        "ending": "歩き出す",
        "important_objects": ["鍵"],
    }


@pytest.mark.parametrize("configured,expected", [(None, 25_000), ("1", 512), ("999999", 32_000)])
def test_character_output_budget_is_separate_and_bounded(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: int,
) -> None:
    monkeypatch.setattr("app.config._load_local_env", lambda: None)
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "12000")
    monkeypatch.delenv("OPENAI_CHARACTER_TIMEOUT_SECONDS", raising=False)
    if configured is None:
        monkeypatch.delenv("OPENAI_CHARACTER_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("OPENAI_CHARACTER_MAX_OUTPUT_TOKENS", configured)
    settings = get_settings()
    assert settings.openai_character_max_output_tokens == expected
    assert settings.openai_character_timeout_seconds == 300.0
    assert settings.openai_max_output_tokens == 12_000


def test_registry_accepts_supported_models_and_rejects_arbitrary_ids() -> None:
    assert "gpt-6-astra" in TEXT_MODEL_IDS
    assert is_allowed_text_model("gpt-6-astra")
    assert is_allowed_text_model("gpt-6.1-sol")
    assert is_allowed_text_model("gpt-6-luna")
    assert is_allowed_text_model("auto", allow_auto=True)
    assert not is_allowed_text_model("gpt-unknown")
    assert is_allowed_image_model("gpt-image-2")
    assert is_allowed_image_model("gpt-image-2.5-sunburst")
    assert is_allowed_image_model("gpt-image-2.5-flare")
    assert not is_allowed_image_model("gpt-6-astra")
    with pytest.raises(ValueError):
        validate_model_settings({"story_analysis_model": "gpt-unknown"}, partial=True)
    with pytest.raises(ValueError):
        validate_model_settings({"image_model": "gpt-5.6-sol"}, partial=True)


def test_preset_and_project_override_resolution() -> None:
    highest = resolve_model_settings({"preset": "highest_quality"})
    assert highest["story_analysis_model"] == "gpt-6-astra"
    assert highest["settings_recommendation_model"] == "gpt-6-astra"
    assert highest["character_model"] == "gpt-6.1-sol"
    balanced = resolve_model_settings(
        {"preset": "balanced"}, {"storyboard_model": "gpt-6-astra"}
    )
    assert balanced["story_analysis_model"] == "gpt-6.1-sol"
    assert balanced["settings_recommendation_model"] == "gpt-6.1-sol"
    assert balanced["storyboard_model"] == "gpt-6-astra"
    legacy = resolve_model_settings(
        None, legacy_text_model="gpt-5.6-luna", legacy_image_model="gpt-image-2"
    )
    assert legacy["story_analysis_model"] == "gpt-5.6-luna"
    assert legacy["storyboard_model"] == "gpt-5.6-luna"


def test_invalid_saved_model_falls_back_without_breaking_and_reasoning_is_capability_aware() -> None:
    resolved = resolve_model_settings({"preset": "balanced", "story_analysis_model": "bad-model"})
    assert resolved["story_analysis_model"] == "gpt-6.1-sol"
    assert reasoning_for_model({"reasoning_effort": "high"}, "gpt-6-astra") == "high"
    assert reasoning_for_model({"reasoning_effort": "none"}, "gpt-6-astra") == "auto"
    assert DEFAULT_AI_MODEL_SETTINGS["image_model"] == "gpt-image-2.5-sunburst"


def test_latest_defaults_and_legacy_project_pins_coexist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config._load_local_env", lambda: None)
    for key in ("OPENAI_MODEL", "OPENAI_TEXT_MODEL", "OPENAI_IMAGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert get_settings().openai_text_model == "gpt-6-luna"
    assert get_settings().openai_image_model == "gpt-image-2.5-sunburst"
    settings = resolve_model_settings(DEFAULT_AI_MODEL_SETTINGS)
    assert settings["storyboard_model"] == "gpt-6.1-sol"
    assert settings["qa_model"] == "gpt-6-luna"
    assert settings["reasoning_effort"] == "auto"
    economy = resolve_model_settings({"preset": "economy"})
    assert all(economy[f"{task}_model"] == "gpt-6-luna" for task in MODEL_TASKS)
    pinned = resolve_model_settings(DEFAULT_AI_MODEL_SETTINGS, {
        "storyboard_model": "gpt-5.6-sol", "image_model": "gpt-image-2", "reasoning_effort": "high",
    })
    assert pinned["storyboard_model"] == "gpt-5.6-sol"
    assert pinned["image_model"] == "gpt-image-2"
    assert pinned["reasoning_effort"] == "high"


def test_registry_exposes_current_reasoning_without_unsupported_ultra() -> None:
    view = model_registry_view()
    assert [item["id"] for item in view["reasoning_levels"]] == ["auto", "low", "medium", "high", "xhigh", "max"]
    for model in view["text_models"]:
        assert model["ui_reasoning_levels"] == ["low", "medium", "high", "xhigh", "max"]
    sol = next(model for model in view["text_models"] if model["id"] == "gpt-6.1-sol")
    assert "none" not in sol["supported_reasoning_levels"]
    assert "minimal" not in sol["supported_reasoning_levels"]
    assert view["default_settings"] == DEFAULT_AI_MODEL_SETTINGS
    assert view["preset_policies"]["auto"]["adaptation"] == "gpt-6.1-sol"
    with pytest.raises(ValueError):
        validate_model_settings({"reasoning_effort": "ultra"})


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"])
@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_latest_reasoning_is_sent_in_responses_and_recorded(monkeypatch, model, effort) -> None:
    calls = []

    def fake_request(url, **kwargs):
        calls.append(kwargs["payload"])
        return {**response_with_json(valid_analysis()), "model": model}

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.ai_pipeline.request_json", fake_request)
    provider = OpenAIProvider({"story_analysis_model": model, "reasoning_effort": effort})
    provider.analyze("本文", "作品")
    assert len(calls) == 1
    assert calls[0]["model"] == model
    assert calls[0]["reasoning"] == {"effort": effort}
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["store"] is False
    assert not {"temperature", "top_p", "top_logprobs"} & calls[0].keys()
    assert provider.last_generation_metadata["reasoning_effort"] == effort
    assert provider.last_generation_metadata["actual_model"] == model


def test_storyboard_auto_request_uses_allowlisted_model_and_strict_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto設定のStoryboardがallowlist内モデルとResponses形式を使う。"""

    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(
            response_with_json(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "title": "霧の入口",
                            "layout": "classic",
                            "panels": [
                                {
                                    "description": "蒼が灯台を見る",
                                    "shot_type": "遠景",
                                    "characters": ["蒼"],
                                    "action": "立ち止まる",
                                    "expression": "迷い",
                                    "background": "霧の町",
                                    "dialogue": [],
                                    "narration": [],
                                    "sfx": [],
                                }
                            ],
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)

    result = OpenAIProvider({"preset": "auto"}).storyboard(
        "蒼は灯台へ向かった。",
        valid_analysis(),
        {"target_page_count": 1, "language": "ja"},
        [{"name": "蒼", "role": "主人公", "appearance": "短髪"}],
    )

    assert result and result[0]["panels"]
    assert calls[0]["model"] == "gpt-6.1-sol"
    assert calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[0]["text"]["format"]["strict"] is True


@pytest.mark.parametrize("requested,fallback", [
    ("gpt-6-astra", "gpt-6.1-sol"),
    ("gpt-6.1-sol", "gpt-5.6-sol"),
    ("gpt-6-luna", "gpt-5.6-luna"),
])
def test_model_unavailable_falls_back_once_and_records_requested_actual_models(
    monkeypatch: pytest.MonkeyPatch, requested: str, fallback: str,
) -> None:
    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                404,
                "not found",
                {},
                io.BytesIO(json.dumps({"error": {"code": "model_not_found"}}).encode()),
            )
        return FakeHTTPResponse(response_with_json(valid_analysis()))

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"story_analysis_model": requested, "reasoning_effort": "max"})
    result = provider.analyze("蒼は灯台へ向かった。", "灯台")

    assert result["title"] == "灯台"
    assert [call["model"] for call in calls] == [requested, fallback]
    assert all(call["reasoning"] == {"effort": "max"} for call in calls)
    assert provider.last_generation_metadata == {
        "task": "story_analysis",
        "requested_model": requested,
        "actual_model": fallback,
        "fallback": True,
        "reasoning_effort": "max",
        "provider": "openai",
        "created_at": provider.last_generation_metadata["created_at"],
    }


def test_astra_available_dispatches_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse({**response_with_json(valid_analysis()), "model": "gpt-6-astra"})

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIProvider({"story_analysis_model": "gpt-6-astra"})
    provider.analyze("本文", "作品")
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-astra"
    assert provider.last_generation_metadata["fallback"] is False


@pytest.mark.parametrize("category,code,expected_calls", [
    ("invalid_request", "model_not_found", 2),
    ("authentication", "invalid_api_key", 1),
    ("rate_limit", "rate_limit_exceeded", 1),
])
def test_new_model_fallback_is_bounded_and_only_for_access_errors(monkeypatch, category, code, expected_calls) -> None:
    calls = []

    def fail_request(url, **kwargs):
        calls.append(kwargs["payload"]["model"])
        raise OpenAIRequestError("利用できません", category=category, error_code=code)

    monkeypatch.setattr("app.services.ai_pipeline.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.ai_pipeline.request_json", fail_request)
    provider = OpenAIProvider({"story_analysis_model": "gpt-6.1-sol", "reasoning_effort": "max"})
    with pytest.raises(AIProviderError) as error:
        provider.analyze("本文", "作品")
    assert len(calls) == expected_calls
    assert error.value.retryable is False
    assert error.value.requested_model == "gpt-6.1-sol"
    assert error.value.actual_model == calls[-1]


def test_model_availability_is_cached_and_does_not_expose_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _AVAILABILITY_CACHE.clear()
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeHTTPResponse({"data": [{"id": "gpt-6-astra"}, {"id": "gpt-6.1-sol"}, {"id": "gpt-image-2.5-sunburst"}]})

    monkeypatch.setattr("app.services.model_registry.get_settings", runtime_settings)
    monkeypatch.setattr("app.services.openai_client.urllib.request.urlopen", fake_urlopen)
    first = get_model_availability()
    second = get_model_availability()
    assert first["gpt-6-astra"]["status"] == "available"
    assert first["gpt-6.1-sol"]["status"] == "available"
    assert first["gpt-6-luna"]["status"] == "unavailable"
    assert first["gpt-image-2.5-sunburst"]["status"] == "available"
    assert second == first
    assert len(calls) == 1
    assert "test-key" not in json.dumps(first)


def test_model_settings_api_persists_global_and_project_override(tmp_path: Path) -> None:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "models@example.com", "password": "long-password"}, follow_redirects=False)
    saved = client.put(
        "/api/settings/ai-models",
        json={
            "preset": "highest_quality",
            "story_analysis_model": "gpt-6-astra",
            "image_model": "gpt-image-2",
            "reasoning_effort": "high",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["global"]["story_analysis_model"] == "gpt-6-astra"
    assert saved.json()["settings"]["effective"]["story_analysis_model"] == "gpt-6-astra"
    project = client.post("/api/projects", data={"title": "モデル設定", "story_text": "本文"}).json()["project"]
    override = client.put(
        f"/api/projects/{project['id']}/ai-model-settings",
        json={"story_analysis_model": "gpt-5.6-terra"},
    )
    assert override.status_code == 200
    assert override.json()["settings"]["global"]["story_analysis_model"] == "gpt-6-astra"
    assert override.json()["settings"]["project"]["story_analysis_model"] == "gpt-5.6-terra"
    assert override.json()["settings"]["effective"]["story_analysis_model"] == "gpt-5.6-terra"
    latest = client.put("/api/settings/ai-models", json={
        **DEFAULT_AI_MODEL_SETTINGS,
        "story_analysis_model": "gpt-6.1-sol", "image_model": "gpt-image-2.5-sunburst", "reasoning_effort": "max",
    })
    assert latest.status_code == 200
    restored = client.get(f"/api/projects/{project['id']}/ai-model-settings").json()["settings"]
    assert restored["global"]["story_analysis_model"] == "gpt-6.1-sol"
    assert restored["effective"]["story_analysis_model"] == "gpt-5.6-terra"
    assert restored["effective"]["image_model"] == "gpt-image-2.5-sunburst"
    assert restored["effective"]["reasoning_effort"] == "max"
    assert client.put("/api/settings/ai-models", json={"reasoning_effort": "ultra"}).status_code == 422
    invalid = client.put(
        "/api/settings/ai-models", json={"story_analysis_model": "arbitrary-model"}
    )
    assert invalid.status_code == 422
    body = json.dumps(saved.json(), ensure_ascii=False)
    assert "OPENAI_API_KEY" not in body
    assert "test-key" not in body
