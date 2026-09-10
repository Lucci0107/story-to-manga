"""PageComposition v3の意味的ポリシー回帰テスト。"""

from __future__ import annotations

import io
import json
import zipfile

from pypdf import PdfReader

from app.db import DEFAULT_SETTINGS
from app.services.ai_pipeline import compose_panel_prompt
from app.services.composition import (
    SEMANTIC_COMPOSITION_VERSION,
    composition_quality_issues,
    composition_quality_score,
    semantic_effect_budget,
    semantic_page_family,
    simplify_composition,
)
from app.services.export import export_pdf, export_zip, render_page_png
from app.services.knowledge import quality_check as knowledge_quality_check
from app.services.layout import ensure_page_layout, reflow_page, repair_storyboard_page, place_text_elements
from app.services.storage import LocalFileStorage


def test_explicit_dominant_controls_row_instead_of_importance_tie():
    from app.services.layout import _rows_for_panels, TEMPLATE_ACTION

    panels = [{"id": str(index), "importance": "high"} for index in range(6)]
    rows = _rows_for_panels(TEMPLATE_ACTION, panels, dominant_index=2)
    assert [2] in rows
    assert [index for row in rows for index in row] == list(range(6))


def _page(
    *,
    layout: str = "conversation",
    page_role: str = "dialogue",
    count: int = 5,
    **extra,
) -> dict:
    panels = []
    for index in range(count):
        panels.append(
            {
                "id": f"semantic-panel-{index + 1}",
                "order": index + 1,
                "description": "爆発の衝撃" if extra.get("climax") and index == 0 else "会話の場面",
                "panel_role": "クライマックス" if extra.get("climax") and index == 0 else "会話",
                "scene_type": "action" if extra.get("climax") and index == 0 else "dialogue",
                "importance": "critical" if extra.get("climax") and index == 0 else "medium",
                "characters": ["A"],
                "dialogue": ["ここにセリフ"] if index == 0 else [],
                "narration": [],
                "sfx": ["ズン"] if extra.get("climax") and index == 0 else [],
                "image_url": None,
                "generation_status": "not_started",
                **({"breakout_reason": "impact emphasis"} if extra.get("breakout_reason") and index == 0 else {}),
            }
        )
    return {
        "id": "semantic-page",
        "page_number": 1,
        "title": "Semantic Page",
        "layout": layout,
        "page_role": page_role,
        "panels": panels,
        **extra,
    }


def _v3(page: dict, language: str = "ja") -> dict:
    return ensure_page_layout(page, {"language": language, "composition_version": SEMANTIC_COMPOSITION_VERSION})


def test_dialogue_defaults_to_rectangles_and_keeps_breakouts_off() -> None:
    prepared = _v3(_page())
    composition = prepared["composition"]
    assert composition["composition_version"] == SEMANTIC_COMPOSITION_VERSION
    assert composition["semantic_family"] == "dialogue"
    assert all(item["shape"] in {"rectangle", "wide", "tall"} for item in composition["panels"])
    assert composition["breakouts"] == []
    assert composition["effect_budget"]["angled_panels"] == 0
    assert composition_quality_issues(prepared) == []


def test_quiet_page_budget_cannot_be_expanded_by_decorative_override() -> None:
    page = _page(composition_budget={"angled_panels": 3, "character_breakouts": 2, "bubble_breakouts": 2})
    budget = semantic_effect_budget(page)
    assert budget["angled_panels"] == 0
    assert budget["character_breakouts"] == 0
    assert budget["bubble_breakouts"] == 0
    action = _page(layout="action", page_role="action", climax=True, special_emphasis=True, composition_budget={"character_breakouts": 2})
    assert semantic_effect_budget(action)["character_breakouts"] == 2


def test_semantic_family_ignores_reaction_substring_and_generated_prompt() -> None:
    page = _page(page_role="余韻のページ", count=2)
    page["panels"][0]["scene_type"] = "reaction"
    page["panels"][0]["generation_prompt"] = "Semantic family: action; comedy emphasis"
    assert semantic_page_family(page) == "psychological"


def test_unequal_area_does_not_require_polygon() -> None:
    prepared = _v3(_page(count=6))
    panels = prepared["composition"]["panels"]
    areas = [float(item["width"]) * float(item["height"]) for item in panels]
    assert max(areas) / min(areas) > 1.2
    assert all(item["shape"] == "rectangle" for item in panels)


def test_action_uses_at_most_one_meaningful_angle() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=6))
    panels = prepared["composition"]["panels"]
    angled = [item for item in panels if item["shape"] not in {"rectangle", "wide", "tall"}]
    assert len(angled) <= 1
    assert angled and angled[0]["shape_reason"]
    assert prepared["composition"]["dominant_panel_id"] == angled[0]["panel_id"]


def test_shape_without_reason_falls_back_to_rectangle() -> None:
    page = _page()
    page["panels"][0]["panel_shape"] = "trapezoid"
    prepared = _v3(page)
    assert prepared["composition"]["panels"][0]["shape"] == "rectangle"


def test_repair_replaces_polygon_vertices_not_only_shape_label() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=6))
    prepared["composition"]["semantic_family"] = "dialogue"
    repaired = simplify_composition(prepared)
    for panel in repaired["composition"]["panels"]:
        assert len({point[0] for point in panel["polygon_points"]}) == 2
        assert len({point[1] for point in panel["polygon_points"]}) == 2


def test_explicit_v3_repair_preserves_other_pages_and_artwork() -> None:
    legacy = reflow_page(_page(), {"composition_version": 2})
    legacy["panels"][0]["image_url"] = "/media/original.png"
    other = {**legacy, "id": "untouched"}
    repaired = repair_storyboard_page([legacy, other], legacy["id"], {}, composition_version=3)
    assert repaired[0]["composition_version"] == 3
    assert repaired[0]["panels"][0]["image_url"] == "/media/original.png"
    assert repaired[0]["panels"][0]["dialogue"] == legacy["panels"][0]["dialogue"]
    assert repaired[1] == other
    assert legacy["composition_version"] == 2


def test_impossible_face_safe_placement_is_not_silently_accepted() -> None:
    layout = place_text_elements({"dialogue": ["隠してはいけない"]}, {"language": "ja"},
                                 protected_zones=[{"x": 0, "y": 0, "width": 1, "height": 1}])
    assert layout["items"][0]["overflow"]
    assert layout["warnings"]


def test_title_does_not_overlap_first_artwork_row() -> None:
    page = _v3(_page())
    title = next(item for item in page["composition"]["overlays"] if item["type"] == "title")
    assert min(panel["y"] for panel in page["composition"]["panels"]) > title["y"] + title["height"]


def test_text_rows_are_not_compressed_into_thin_strips() -> None:
    page = _page(count=7)
    for panel in page["panels"]:
        panel["dialogue"] = ["読みやすい文字を配置する"]
    prepared = _v3(page)
    assert min(panel["height"] for panel in prepared["composition"]["panels"]) >= 0.17


def test_breakout_reason_does_not_turn_full_artwork_into_fake_cutout() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=6, breakout_reason=True))
    breakouts = prepared["composition"]["breakouts"]
    assert breakouts == []
    noisy = {**prepared, "composition": {**prepared["composition"], "breakouts": [{"enabled": True, "reason": "impact"}] * 3}}
    assert any(issue["key"] == "composition-v3-breakout-budget-1" for issue in composition_quality_issues(noisy))


def test_overcomposition_budget_is_reported() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=3))
    noisy = {
        **prepared,
        "composition": {
            **prepared["composition"],
            "overlays": [
                {"id": "bubble-1", "type": "bubble", "breakout": True, "reason": "impact", "x": 0.06, "y": 0.06, "width": 0.16, "height": 0.08},
                {"id": "bubble-2", "type": "bubble", "breakout": True, "reason": "impact", "x": 0.74, "y": 0.06, "width": 0.16, "height": 0.08},
                {"id": "sfx-1", "type": "sfx", "breakout": True, "reason": "impact", "x": 0.52, "y": 0.40, "width": 0.16, "height": 0.08},
                {"id": "sfx-2", "type": "sfx", "breakout": True, "reason": "impact", "x": 0.52, "y": 0.52, "width": 0.16, "height": 0.08},
            ],
        },
    }
    keys = {issue["key"] for issue in composition_quality_issues(noisy)}
    assert "composition-v3-bubble-budget-1" in keys
    assert "composition-v3-overlay-budget-1" in keys


def test_bubble_stays_in_panel_unless_explicit_breakout() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=3))
    assert not any(item["type"] == "bubble" for item in prepared["composition"]["overlays"])
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=3, bubble_breakout=True))
    assert any(item["type"] == "bubble" and item.get("breakout") for item in prepared["composition"]["overlays"])


def test_face_protection_and_reserved_text_zone_are_persisted() -> None:
    page = _page(count=1)
    page["panels"][0]["face_position"] = "right"
    prepared = _v3(page)
    geometry = prepared["composition"]["panels"][0]
    assert geometry["text_safe_zones"]["bubble"]["x"] < 0.5
    assert geometry["protected_zones"]
    assert prepared["panels"][0]["text_layout"]["items"]


def test_prompt_contains_semantic_family_and_reserved_zone() -> None:
    prepared = _v3(_page(count=1))
    prompt = compose_panel_prompt(prepared["panels"][0], [], {"language": "ja"})
    assert "Semantic family" in prompt
    assert "Reserved text safe zones" in prompt
    assert "rectangle default" in prompt


def test_dominant_panel_is_meaningful_and_score_is_deterministic() -> None:
    prepared = _v3(_page(layout="action", page_role="action", climax=True, count=6))
    dominant = next(item for item in prepared["composition"]["panels"] if item["dominant"])
    total = sum(float(item["width"]) * float(item["height"]) for item in prepared["composition"]["panels"])
    assert 0.30 <= float(dominant["width"]) * float(dominant["height"]) / total <= 0.55
    score = composition_quality_score(prepared)
    assert score["readability"] == 100
    assert score["face_visibility"] == 100


def test_simplification_removes_quiet_page_effects() -> None:
    prepared = _v3(_page())
    noisy = {
        **prepared,
        "composition": {
            **prepared["composition"],
            "panels": [{**prepared["composition"]["panels"][0], "shape": "trapezoid", "shape_reason": "decorative"}],
            "breakouts": [{"enabled": True, "reason": "decorative", "x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1}],
        },
    }
    simplified = simplify_composition(noisy)
    assert simplified["composition"]["panels"][0]["shape"] == "rectangle"
    assert simplified["composition"]["breakouts"] == []


def test_v2_page_is_not_upgraded_implicitly() -> None:
    legacy = ensure_page_layout(_page(layout="action", page_role="action", climax=True, count=6), {"language": "ja"})
    assert legacy["composition"]["composition_version"] == 2
    assert all(item["shape"] == "rectangle" for item in legacy["composition"]["panels"]) is False


def test_semantic_metadata_change_reflows_existing_v3_page() -> None:
    prepared = _v3(_page(count=5))
    changed = {**prepared, "layout": "action", "page_role": "action", "climax": True}
    changed["panels"] = [{**changed["panels"][0], "importance": "critical"}, *changed["panels"][1:]]
    changed = ensure_page_layout(changed, {"language": "ja", "composition_version": 3})
    assert changed["composition"]["semantic_family"] == "climax"
    assert any(item["dominant"] for item in changed["composition"]["panels"])


def test_simplification_keeps_shared_geometry_in_sync() -> None:
    prepared = _v3(_page(count=1))
    panel = prepared["composition"]["panels"][0]
    noisy = {
        **prepared,
        "composition": {
            **prepared["composition"],
            "panels": [{**panel, "shape": "trapezoid", "shape_reason": "decorative"}],
        },
    }
    simplified = simplify_composition(noisy)
    assert simplified["composition"]["panels"][0]["shape"] == "rectangle"
    assert simplified["panels"][0]["geometry"]["shape"] == "rectangle"
    assert simplified["layout_geometry"]["panels"][0]["shape"] == "rectangle"


def test_quality_report_exposes_semantic_composition_score() -> None:
    page = _v3(_page(count=2))
    report = knowledge_quality_check(
        {"original_text": "本文", "characters": [{"name": "A"}], "settings": {"language": "ja", "composition_version": 3}, "storyboard": [page]},
        {"references": [], "resolution_status": "no_selection"},
    )
    assert report["composition_quality"]["pages"]
    assert report["composition_quality"]["average_readability"] is not None


def test_new_project_default_is_v3() -> None:
    assert DEFAULT_SETTINGS["composition_version"] == SEMANTIC_COMPOSITION_VERSION


def test_v3_preview_pdf_zip_share_same_png(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path)
    page = _v3(_page(count=3))
    project = {"id": "semantic-project", "title": "Semantic", "settings": {"language": "ja", "composition_version": 3}, "storyboard": [page]}
    preview = render_page_png(project, page, storage)
    archive = export_zip(project, storage)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert zipped.read("pages/page-001.png") == preview
        manifest = json.loads(zipped.read("project.json"))
        assert manifest["storyboard"][0]["composition"]["composition_version"] == 3
    pdf = export_pdf(project, storage)
    assert "Semantic" in "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def test_rtl_and_ltr_preserve_semantic_composition() -> None:
    for language in ("ja", "en"):
        prepared = _v3(_page(count=4), language)
        assert prepared["composition"]["composition_version"] == 3
        assert prepared["layout_geometry"]["reading_direction"] in {"right_to_left", "left_to_right"}
