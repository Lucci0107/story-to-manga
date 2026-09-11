"""過密な横長Panelを生成前に安全な連続コマへ変換するテスト。"""

from copy import deepcopy

import pytest

from app.services.composition_fallback import (
    COMPLEXITY_INFEASIBLE,
    FALLBACK_GEOMETRY_CHANGE,
    FALLBACK_PANEL_SPLIT,
    FALLBACK_SHOT_RELAXATION,
    FALLBACK_TEXT_RELOCATION,
    panel_spatial_complexity,
    resolve_composition_fallback,
    split_overloaded_panel,
)
from app.services.layout import reflow_page
from app.services.layout import ensure_page_layout
from app.services.panel_direction import plan_panel_direction
from app.schemas import normalize_storyboard


def overloaded_panel(**overrides):
    panel = {
        "id": "overloaded",
        "characters": ["医師"],
        "description": "医師が器具を持って説明する",
        "action": "手で重要な器具を示す",
        "shot_type": "close_up",
        "hands_required": True,
        "props_required": True,
        "dialogue": ["ここを確認します"],
        "narration": [],
        "sfx": [],
        "geometry": {"x": 0.02, "y": 0.1, "width": 0.96, "height": 0.32},
        "importance": "critical",
    }
    panel.update(overrides)
    return panel


def test_overloaded_wide_panel_is_infeasible_before_generation():
    result = panel_spatial_complexity(overloaded_panel())
    assert result["level"] == COMPLEXITY_INFEASIBLE
    assert result["feasibility_status"] == "INFEASIBLE"
    assert result["aspect_ratio"] >= 1.8
    assert result["requirements"]["hands_required"]
    assert result["requirements"]["props_required"]
    assert result["reasons"]


def test_normal_wide_panel_remains_feasible():
    result = panel_spatial_complexity(overloaded_panel(hands_required=False, props_required=False, dialogue=[]))
    assert result["level"] != COMPLEXITY_INFEASIBLE
    assert result["feasibility_status"] != "INFEASIBLE"


def test_fallback_attempts_are_conservative_and_end_in_split():
    plan = resolve_composition_fallback(overloaded_panel())
    assert plan["required"]
    assert plan["fallback_type"] == FALLBACK_PANEL_SPLIT
    assert [item["type"] for item in plan["attempts"]] == [
        FALLBACK_SHOT_RELAXATION,
        FALLBACK_SHOT_RELAXATION,
        FALLBACK_SHOT_RELAXATION,
        FALLBACK_TEXT_RELOCATION,
        FALLBACK_GEOMETRY_CHANGE,
    ]


@pytest.mark.parametrize("language", ["ja", "en"])
def test_split_preserves_story_intent_and_logical_order(language):
    children = split_overloaded_panel(overloaded_panel(), language)
    assert [child["id"] for child in children] == ["overloaded--face-dialogue", "overloaded--hands-prop"]
    assert children[0]["dialogue"] == ["ここを確認します"]
    assert children[1]["dialogue"] == []
    assert children[0]["hands_required"] is False
    assert children[1]["hands_required"] is True
    assert children[1]["props_required"] is True
    assert children[1]["head_visible"] is False
    for child in children:
        assert child["fallback_type"] == FALLBACK_PANEL_SPLIT
        assert child["original_panel_id"] == "overloaded"
        assert child["generated_panel_ids"] == ["overloaded--face-dialogue", "overloaded--hands-prop"]
        assert child["original_story_intent"]["description"] == overloaded_panel()["description"]
        assert child["image_url"] is None


def test_v3_reflow_splits_only_overloaded_panel_and_shows_notice():
    panels = [overloaded_panel()]
    panels.extend(
        {
            "id": f"support-{index}",
            "characters": [],
            "description": "背景",
            "action": "",
            "shot_type": "medium",
            "dialogue": [],
            "narration": [],
            "sfx": [],
            "importance": "low",
        }
        for index in range(3)
    )
    page = reflow_page(
        {"id": "page", "page_number": 1, "layout": "action", "page_role": "action", "panels": panels},
        {"composition_version": 3, "language": "ja", "visual_style": "cinematic", "color_mode": "color"},
    )
    assert len(page["panels"]) == 5
    assert page["composition_fallbacks"][0]["fallback_type"] == FALLBACK_PANEL_SPLIT
    assert "分割しました" in page["composition_fallback_notice"]
    assert page["panels"][0]["dialogue"]
    assert page["panels"][1]["dialogue"] == []
    assert [panel["order"] for panel in page["panels"]] == [1, 2, 3, 4, 5]
    assert all(panel["panel_direction"]["status"] == "ready" for panel in page["panels"])


def test_existing_artwork_is_not_split_or_replaced():
    source = overloaded_panel()
    source["image_url"] = "/media/existing.png"
    page = reflow_page(
        {"id": "page", "page_number": 1, "layout": "action", "page_role": "action", "panels": [source]},
        {"composition_version": 3, "language": "ja", "visual_style": "cinematic", "color_mode": "color"},
    )
    assert len(page["panels"]) == 1
    assert page["panels"][0]["id"] == "overloaded"
    assert page["panels"][0]["image_url"] == "/media/existing.png"
    assert "composition_fallbacks" not in page


def test_saved_unrendered_overload_is_replanned_and_split():
    """保存済みdirectionがあっても、課金前の再読込で過密判定を逃がさない。"""
    source = overloaded_panel()
    source["panel_direction"] = plan_panel_direction(source, {"language": "ja", "composition_version": 3})
    page_source = {
        "id": "saved-overload",
        "page_number": 1,
        "layout": "action",
        "page_role": "action",
        "panels": [source],
    }
    # 1コマPageは縦長へ割り当てられるため、横長の元geometryを持つ
    # 保存済みPageを4コマ相当の構造で再読込する。
    page_source["panels"].extend(
        {"id": f"support-{index}", "characters": [], "dialogue": [], "narration": [], "sfx": []}
        for index in range(3)
    )
    page = reflow_page(
        page_source,
        {"language": "ja", "composition_version": 3},
    )
    assert len(page["panels"]) == 5
    # DB読込後のensureでも同じ保存構図を見て、元の過密Panelを残さない。
    loaded = ensure_page_layout(page, {"language": "ja", "composition_version": 3})
    assert [panel["id"] for panel in loaded["panels"]] == [panel["id"] for panel in page["panels"]]
    assert all(panel["fallback_applied"] for panel in loaded["panels"][:2])
    assert all(not panel.get("fallback_applied") for panel in loaded["panels"][2:])


def test_mixed_page_only_splits_unrendered_overload():
    source = overloaded_panel()
    preserved = {"id": "artwork", "characters": [], "image_url": "/media/kept.png", "dialogue": [], "narration": [], "sfx": []}
    page = reflow_page(
        {"id": "mixed", "page_number": 1, "layout": "action", "page_role": "action", "panels": [source, preserved]},
        {"language": "ja", "composition_version": 3},
    )
    assert len(page["panels"]) == 3
    assert page["panels"][-1]["id"] == "artwork"
    assert page["panels"][-1]["image_url"] == "/media/kept.png"


def test_fallback_lineage_roundtrip_is_persisted():
    source = overloaded_panel()
    source.update({"id": "source", "geometry": {"x": 0.02, "y": 0.1, "width": 0.96, "height": 0.32}})
    page = reflow_page(
        {"id": "page", "page_number": 1, "layout": "action", "page_role": "action", "panels": [source, {"id": "other", "characters": [], "dialogue": [], "narration": [], "sfx": []}]},
        {"composition_version": 3, "language": "ja", "visual_style": "cinematic", "color_mode": "color"},
    )
    normalized = normalize_storyboard([page], {"composition_version": 3, "language": "ja", "visual_style": "cinematic", "color_mode": "color"})[0]
    children = normalized["panels"][:2]
    assert children[0]["original_panel_id"] == "source"
    assert children[0]["fallback_lineage"]["original_panel_id"] == "source"
    assert children[0]["generated_panel_ids"] == ["source--face-dialogue", "source--hands-prop"]
    assert normalized["composition_fallbacks"][0]["original_panel_id"] == "source"


def test_planned_direction_exposes_complexity_debug_metadata():
    panel = overloaded_panel()
    direction = plan_panel_direction(panel, {"language": "ja", "visual_style": "cinematic", "color_mode": "color"})
    assert direction["composition_complexity_level"] == COMPLEXITY_INFEASIBLE
    assert direction["feasibility_status"] == "INFEASIBLE"
    assert direction["fallback_recommendation"] == FALLBACK_PANEL_SPLIT
    assert direction["composition_debug"]["fallback_required"] is True


def test_queue_blocks_untransformed_overload_without_creating_job(monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from app import db
    from app.main import queue_panels

    panel = overloaded_panel()
    panel["panel_direction"] = plan_panel_direction(panel, {"language": "ja", "composition_version": 3})
    project = {
        "id": "project",
        "settings": {"language": "ja", "composition_version": 3},
        "storyboard": [{"id": "page", "panels": [panel]}],
    }
    monkeypatch.setattr(db, "create_generation_job", lambda *args, **kwargs: pytest.fail("過密コマでJobを作成してはいけません"))
    with pytest.raises(HTTPException) as error:
        queue_panels(project, "user", [], False, False, BackgroundTasks())
    assert error.value.status_code == 422
    assert "画像生成は開始していません" in str(error.value.detail)
