"""漫画言語と読順の一貫性を確認するテスト。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import SettingsPayload, normalize_storyboard
from app.services.ai_pipeline import compose_panel_prompt
from app.services.export import _page_panel_boxes
from app.services.knowledge import quality_check
from app.services.reading_order import (
    LANGUAGE_DIRECTIONS,
    READING_LEFT_TO_RIGHT,
    READING_RIGHT_TO_LEFT,
    bubble_side,
    canonicalize_stored_settings,
    panel_visual_position,
    reading_order_issues,
)


def test_language_is_the_source_of_truth_for_direction() -> None:
    japanese = SettingsPayload(language="ja", reading_direction="ltr")
    english = SettingsPayload(language="en", reading_direction="rtl")
    legacy_japanese = SettingsPayload(reading_direction="rtl")
    legacy_english = SettingsPayload(reading_direction="ltr")

    assert japanese.reading_direction == READING_RIGHT_TO_LEFT
    assert english.reading_direction == READING_LEFT_TO_RIGHT
    assert legacy_japanese.language == "ja"
    assert legacy_english.language == "en"
    assert LANGUAGE_DIRECTIONS == {"ja": READING_RIGHT_TO_LEFT, "en": READING_LEFT_TO_RIGHT}


def test_existing_settings_are_migrated_without_losing_other_values() -> None:
    migrated = canonicalize_stored_settings(
        {"reading_direction": "ltr", "target_page_count": 12, "visual_style": "cinematic"}
    )
    assert migrated["language"] == "en"
    assert migrated["reading_direction"] == READING_LEFT_TO_RIGHT
    assert migrated["target_page_count"] == 12
    assert canonicalize_stored_settings(migrated) == migrated
    explicit_language_wins = canonicalize_stored_settings(
        {"language": "en", "reading_direction": "unknown", "visual_style": "cinematic"}
    )
    assert explicit_language_wins["language"] == "en"
    assert explicit_language_wins["reading_direction"] == READING_LEFT_TO_RIGHT


def test_panel_and_bubble_positions_follow_language() -> None:
    japanese = [panel_visual_position(index, 4, {"language": "ja"}) for index in range(4)]
    english = [panel_visual_position(index, 4, {"language": "en"}) for index in range(4)]
    assert [(item["row"], item["column"]) for item in japanese] == [(1, 2), (1, 1), (2, 2), (2, 1)]
    assert [(item["row"], item["column"]) for item in english] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert [bubble_side(index, {"language": "ja"}) for index in range(4)] == ["right", "left", "right", "left"]
    assert [bubble_side(index, {"language": "en"}) for index in range(4)] == ["left", "right", "left", "right"]


def test_normalized_storyboard_uses_one_based_reader_order() -> None:
    storyboard = normalize_storyboard(
        [
            {
                "title": "ページ",
                "panels": [
                    {"description": "右側", "dialogue": ["先", "後"]},
                    {"description": "左側", "dialogue": ["続き"]},
                ],
            }
        ]
    )
    panels = storyboard[0]["panels"]
    assert [panel["order"] for panel in panels] == [1, 2]
    assert panels[0]["bubble_order"] == [1, 2]
    assert panels[1]["bubble_order"] == [1]


def test_quality_check_detects_language_and_order_mismatch() -> None:
    project = {
        "original_text": "本文",
        "settings": {"language": "ja", "reading_direction": "ltr"},
        "characters": [{"name": "蒼"}],
        "storyboard": [
            {
                "page_number": 1,
                "panels": [
                    {"order": 2, "dialogue": ["後", "先"], "bubble_order": [2, 1]},
                    {"order": 1, "dialogue": [], "bubble_order": []},
                ],
            }
        ],
    }
    result = quality_check(project, {"selection_count": 0, "references": []})
    issue_keys = {issue["key"] for issue in result["issues"]}
    assert "language_direction" in issue_keys
    assert "panel-order-1" in issue_keys
    assert "bubble_order-1-1" in issue_keys
    assert next(check for check in result["checks"] if check["key"] == "language_direction")["status"] == "error"


def test_export_panel_boxes_use_explicit_visual_direction() -> None:
    page = {"panels": [{"order": 1}, {"order": 2}, {"order": 3}, {"order": 4}]}
    japanese = _page_panel_boxes(page, 595, 842, {"language": "ja"})
    english = _page_panel_boxes(page, 595, 842, {"language": "en"})
    assert japanese[0][0] > japanese[1][0]
    assert english[0][0] < english[1][0]
    assert japanese[2][0] > japanese[3][0]
    assert english[2][0] < english[3][0]


def test_panel_prompt_contains_project_language_rule() -> None:
    prompt = compose_panel_prompt(
        {"characters": ["蒼"], "shot_type": "寄り", "background": "駅", "action": "立つ", "expression": "決意"},
        [{"name": "蒼", "appearance": "短髪", "clothing": "ジャケット"}],
        {"language": "en", "reading_direction": "right_to_left", "color_mode": "bw", "visual_style": "cinematic"},
    )
    assert "English" in prompt
    assert "left_to_right" in prompt


def test_language_change_preserves_artwork_and_dialogue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    assert client.post(
        "/register",
        data={"email": "language@example.com", "password": "long-password"},
        follow_redirects=False,
    ).status_code == 303
    created = client.post(
        "/api/projects", data={"title": "読順", "story_text": "本文"}
    ).json()["project"]
    project_id = created["id"]
    storyboard = [
        {
            "id": "page-1",
            "layout": "grid",
            "panels": [
                {
                    "id": "panel-1",
                    "description": "既存画像",
                    "dialogue": ["既存のセリフ"],
                    "image_url": "/static/assets/keep.png",
                    "generation_status": "completed",
                    "revision": 1,
                },
                {"id": "panel-2", "description": "二つ目"},
            ],
        }
    ]
    saved = client.patch(f"/api/projects/{project_id}", json={"storyboard": storyboard})
    assert saved.status_code == 200
    configured = client.patch(
        f"/api/projects/{project_id}", json={"settings": {"visual_style": "minimal"}}
    )
    assert configured.status_code == 200
    changed = client.patch(f"/api/projects/{project_id}", json={"settings": {"language": "en"}})
    assert changed.status_code == 200
    project = changed.json()["project"]
    assert project["settings"]["language"] == "en"
    assert project["settings"]["reading_direction"] == READING_LEFT_TO_RIGHT
    assert project["settings"]["visual_style"] == "minimal"
    panel = project["storyboard"][0]["panels"][0]
    assert panel["image_url"] == "/static/assets/keep.png"
    assert panel["dialogue"] == ["既存のセリフ"]
    reloaded = client.get(f"/api/projects/{project_id}").json()["project"]
    assert reloaded["settings"]["language"] == "en"
    assert reloaded["storyboard"][0]["panels"][0]["order"] == 1
