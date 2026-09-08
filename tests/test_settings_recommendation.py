"""Story Analysis由来の漫画化設定推奨テスト。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services.ai_pipeline import AIProviderError, DemoAIProvider
from app.services.settings_recommendation import (
    fallback_recommendation,
    normalize_settings_recommendation,
)


def analysis_fixture(size: str) -> dict:
    if size == "short":
        return {
            "title": "短編",
            "synopsis": "二人が朝に再会する。",
            "genre": "日常",
            "tone": "穏やか",
            "world_setting": "小さな駅",
            "main_characters": ["蒼"],
            "supporting_characters": [],
            "locations": ["駅"],
            "major_events": ["再会"],
            "story_beats": ["導入", "結末"],
            "conflicts": [],
            "climax": "蒼が挨拶する",
            "ending": "二人は歩き出す",
            "important_objects": [],
        }
    if size == "standard":
        return {
            "title": "標準編",
            "synopsis": "蒼は町の秘密を知り、凛と相談しながら決断する。",
            "genre": "ミステリー",
            "tone": "静かな緊張",
            "world_setting": "霧の町",
            "main_characters": ["蒼", "凛"],
            "supporting_characters": ["町の人々"],
            "locations": ["駅", "灯台", "古い家"],
            "major_events": ["違和感", "調査", "再会", "決断"],
            "story_beats": ["導入", "会話", "調査", "転機", "結末"],
            "conflicts": ["秘密を話す怖さ"],
            "climax": "蒼が真実を伝える",
            "ending": "町に新しい朝が来る",
            "important_objects": ["鍵", "手紙"],
        }
    return {
        "title": "長編冒険譚",
        "synopsis": "複数の国を巡る一行が戦いと別れを越え、古代の災厄を止める。",
        "genre": "ファンタジー アクション",
        "tone": "壮大で感情的",
        "world_setting": "三つの王国と失われた文明がある世界",
        "main_characters": ["蒼", "凛", "レオ", "ミナ"],
        "supporting_characters": ["王", "敵将", "村人", "旅の仲間"],
        "locations": ["王都", "森", "砂漠", "港", "地下遺跡", "火山"],
        "major_events": [f"戦い{i}" for i in range(1, 11)],
        "story_beats": ["導入", "旅", "会話", "追跡", "戦闘", "別れ", "再会", "転機", "危機", "決戦", "余韻"],
        "conflicts": ["仲間の対立", "敵との戦い", "過去の罪", "世界の危機"],
        "climax": "仲間が大きな犠牲を越えて災厄と決戦する",
        "ending": "長い旅の結末と新しい時代の始まりを描く",
        "important_objects": ["剣", "鍵", "王冠", "古文書", "指輪"],
    }


def client_for(tmp_path: Path) -> TestClient:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    return TestClient(app)


def create_project_with_analysis(client: TestClient, analysis: dict) -> str:
    client.post(
        "/register",
        data={"email": "recommend@example.com", "password": "long-password"},
        follow_redirects=False,
    )
    created = client.post(
        "/api/projects",
        data={"title": analysis["title"], "story_text": analysis["synopsis"]},
    )
    project_id = created.json()["project"]["id"]
    patched = client.patch(f"/api/projects/{project_id}", json={"analysis": analysis})
    assert patched.status_code == 200
    return project_id


def test_page_recommendation_varies_with_story_complexity() -> None:
    short = fallback_recommendation(analysis_fixture("short"), {"language": "ja"})
    standard = fallback_recommendation(analysis_fixture("standard"), {"language": "ja"})
    complex_story = fallback_recommendation(analysis_fixture("complex"), {"language": "ja"})
    assert short["recommended_page_count"] < standard["recommended_page_count"]
    assert standard["recommended_page_count"] < complex_story["recommended_page_count"]
    assert short["recommended_page_count"] != 40
    assert sum(item["estimated_pages"] for item in complex_story["scene_page_budget"]) == complex_story["recommended_page_count"]


def test_structured_recommendation_is_validated_and_direction_is_not_ai_controlled() -> None:
    analysis = analysis_fixture("standard")
    result = normalize_settings_recommendation(
        {
            "recommended_page_count": 999,
            "recommended_visual_style": "not-supported",
            "recommended_color_mode": "bw",
            "recommended_pacing": "balanced",
            "recommended_dialogue_density": "medium",
            "recommended_target_audience": "一般読者",
            "recommendation_reason": "分析結果に基づく提案",
            "page_count_reason": "場面転換に余白を確保",
            "scene_page_budget": [],
        },
        analysis,
        {"language": "en", "reading_direction": "right_to_left"},
    )
    assert result["recommended_page_count"] == 120
    assert result["recommended_visual_style"] == "cinematic"
    assert result["recommended_color_mode"] == "bw"


def test_recommendation_api_persists_and_user_override_is_protected(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    project_id = create_project_with_analysis(client, analysis_fixture("standard"))

    first = client.post(f"/api/projects/{project_id}/settings/recommendation", json={})
    assert first.status_code == 200
    recommendation = first.json()["recommendation"]
    assert recommendation["recommended_page_count"] != 40
    assert recommendation["user_override"] is False
    assert first.json()["mode"] == "demo"

    cached = client.post(f"/api/projects/{project_id}/settings/recommendation", json={})
    assert cached.status_code == 200
    assert cached.json()["mode"] == "cached"

    saved = client.patch(
        f"/api/projects/{project_id}",
        json={"settings": {"target_page_count": 31, "visual_style": "minimal"}},
    )
    assert saved.status_code == 200
    assert saved.json()["project"]["settings"]["target_page_count"] == 31
    assert saved.json()["project"]["manga_settings_recommendation"]["user_override"] is True

    rerecommended = client.post(
        f"/api/projects/{project_id}/settings/recommendation", json={"force": True}
    )
    assert rerecommended.status_code == 200
    assert rerecommended.json()["project"]["settings"]["target_page_count"] == 31
    assert rerecommended.json()["recommendation"]["user_override"] is True


def test_analysis_update_marks_recommendation_stale_without_overwriting_settings(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    project_id = create_project_with_analysis(client, analysis_fixture("short"))
    assert client.post(f"/api/projects/{project_id}/settings/recommendation", json={}).status_code == 200
    updated = client.patch(
        f"/api/projects/{project_id}",
        json={"analysis": analysis_fixture("complex"), "settings": {"target_page_count": 12}},
    )
    assert updated.status_code == 200
    assert updated.json()["project"]["settings"]["target_page_count"] == 12
    assert updated.json()["project"]["manga_settings_recommendation"]["stale"] is True
    stale = client.post(f"/api/projects/{project_id}/settings/recommendation", json={})
    assert stale.status_code == 200
    assert stale.json()["mode"] == "stale"


def test_recommendation_uses_knowledge_context_and_falls_back_on_provider_failure(
    tmp_path: Path, monkeypatch
) -> None:
    client = client_for(tmp_path)
    project_id = create_project_with_analysis(client, analysis_fixture("standard"))
    captured: dict = {}

    class CapturingProvider(DemoAIProvider):
        def recommend_settings(self, analysis, settings, knowledge_context=None):
            captured["context"] = knowledge_context
            return super().recommend_settings(analysis, settings, knowledge_context)

    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: CapturingProvider())
    monkeypatch.setattr(
        "app.main.retrieve_knowledge_context",
        lambda *_args: {"prompt_text": "1ページ4コマを基本にする", "references": [], "chunks": []},
    )
    result = client.post(f"/api/projects/{project_id}/settings/recommendation", json={"force": True})
    assert result.status_code == 200
    assert captured["context"]["prompt_text"].startswith("1ページ")

    class FailingProvider(DemoAIProvider):
        def recommend_settings(self, *_args, **_kwargs):
            raise AIProviderError("provider unavailable")

    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: FailingProvider())
    fallback = client.post(f"/api/projects/{project_id}/settings/recommendation", json={"force": True})
    assert fallback.status_code == 200
    assert fallback.json()["fallback"] is True
    assert fallback.json()["recommendation"]["recommended_page_count"] > 0

