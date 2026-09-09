"""PageComposition v2のgeometry・layer・出力同一性テスト。"""

from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from app.services.composition import (
    COMPOSITION_VERSION,
    composition_quality_issues,
    composition_for_page,
    polygon_area,
    shape_points,
)
from app.services.export import _crop_image_to_box, export_pdf, export_zip, render_page_png
from app.services.layout import (
    TEMPLATE_DYNAMIC_7,
    ensure_page_layout,
    repair_storyboard_page,
)
from app.services.reading_order import reading_order_issues
from app.services.storage import LocalFileStorage


def _page(count: int = 7, layout: str = "dynamic_7", *, language: str = "ja") -> dict:
    return {
        "id": "composition-page",
        "page_number": 1,
        "title": "オープニング",
        "layout": layout,
        "page_role": "action",
        "panels": [
            {
                "id": f"panel-{index + 1}",
                "order": index + 1,
                "description": "決定的な瞬間" if index == 0 else "場面",
                "panel_role": "クライマックス" if index == 0 else "会話",
                "scene_type": "action" if index == 0 else "dialogue",
                "importance": "critical" if index == 0 else "medium",
                "characters": ["蒼"],
                "dialogue": ["ここから先は、自分で決める。"] if index == 0 else [],
                "narration": [],
                "sfx": ["ズン"] if index == 0 else [],
                "image_url": None,
                "generation_status": "not_started",
            }
            for index in range(count)
        ],
    }


def test_dynamic_page_has_polygon_and_unequal_geometry() -> None:
    prepared = ensure_page_layout(_page(), {"language": "ja"})
    assert prepared["layout_geometry"]["template"] == TEMPLATE_DYNAMIC_7
    composition = prepared["composition"]
    assert composition["composition_version"] == COMPOSITION_VERSION
    assert any(item["shape"] in {"trapezoid", "slanted-left", "slanted-right", "polygon"} for item in composition["panels"])
    areas = [polygon_area(item["polygon_points"]) for item in composition["panels"]]
    assert max(areas) / min(areas) > 1.3


def test_shape_points_and_diagonal_gutter_are_persisted() -> None:
    prepared = ensure_page_layout(_page(6, "action"), {"language": "ja"})
    angled = [item for item in prepared["composition"]["panels"] if item["shape"] != "rectangle"]
    assert angled
    assert all(len(item["polygon_points"]) >= 4 for item in angled)
    assert all(item["gutter"]["type"] == "diagonal" for item in angled)
    assert len(shape_points({"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.3}, "trapezoid")) == 4


def test_important_panel_gets_dominant_area_and_breakout_layer() -> None:
    prepared = ensure_page_layout(_page(6, "action"), {"language": "ja"})
    panels = prepared["composition"]["panels"]
    critical = next(item for item in panels if item["panel_id"] == "panel-1")
    total = sum(polygon_area(item["polygon_points"]) for item in panels)
    assert 0.20 < polygon_area(critical["polygon_points"]) / total < 0.55
    assert prepared["composition"]["breakouts"]
    assert all(0.04 <= item["x"] <= 0.74 and 0.04 <= item["y"] <= 0.62 for item in prepared["composition"]["breakouts"])


def test_bubble_and_sfx_can_move_to_page_level_without_duplicate_panel_text() -> None:
    prepared = ensure_page_layout(_page(3, "action"), {"language": "ja"})
    overlays = prepared["composition"]["overlays"]
    assert any(item["type"] == "bubble" and item.get("breakout") for item in overlays)
    assert any(item["type"] == "sfx" and item.get("breakout") for item in overlays)
    assert {tuple(item.values()) for item in prepared["composition"]["moved_text_items"]}


def test_first_page_title_is_small_and_later_pages_do_not_repeat_it() -> None:
    first = ensure_page_layout(_page(2, "action"), {"language": "ja"})
    second = ensure_page_layout({**_page(2, "action"), "id": "page-2", "page_number": 2}, {"language": "ja"})
    assert any(item["type"] == "title" for item in first["composition"]["overlays"])
    assert not any(item["type"] == "title" for item in second["composition"]["overlays"])
    title = next(item for item in first["composition"]["overlays"] if item["type"] == "title")
    assert title["height"] <= 0.05


def test_cover_crop_anchor_is_forwarded_to_generation_prompt() -> None:
    from app.services.ai_pipeline import compose_panel_prompt

    prepared = ensure_page_layout(_page(1, "action"), {"language": "en"})
    prompt = compose_panel_prompt(prepared["panels"][0], [], {"language": "en"})
    assert "Panel geometry" in prompt
    assert "target aspect ratio" in prompt
    assert "crop anchor" in prompt


def test_composition_qa_has_no_issues_for_standard_dynamic_page() -> None:
    prepared = ensure_page_layout(_page(7), {"language": "ja"})
    assert composition_quality_issues(prepared) == []


def test_cover_crop_uses_focus_anchor_without_letterbox() -> None:
    source = Image.new("RGB", (400, 100), "white")
    for x in range(200):
        for y in range(100):
            source.putpixel((x, y), (220, 30, 30))
    left = _crop_image_to_box(source, 100, 100, "fit", "left", "middle")
    right = _crop_image_to_box(source, 100, 100, "fit", "right", "middle")
    assert left.size == (100, 100)
    assert right.size == (100, 100)
    assert left.getpixel((50, 50))[1] < right.getpixel((50, 50))[1]


def test_japanese_and_english_geometry_keep_reading_order() -> None:
    for language in ("ja", "en"):
        prepared = ensure_page_layout(_page(7), {"language": language})
        project = {"settings": {"language": language}, "storyboard": [prepared]}
        assert not any(item["key"].startswith("panel-position-") for item in reading_order_issues(project))
        assert prepared["composition"]["panels"]


def test_composition_persists_and_reflow_keeps_artwork() -> None:
    prepared = ensure_page_layout(_page(5), {"language": "ja"})
    prepared["panels"][0]["image_url"] = "/media/composition/panel.png"
    reloaded = ensure_page_layout(prepared, {"language": "ja"})
    assert reloaded["composition"] == prepared["composition"]
    repaired = repair_storyboard_page([prepared, {**_page(2), "id": "other", "page_number": 2}], prepared["id"], {"language": "ja"})
    assert repaired[0]["panels"][0]["image_url"] == "/media/composition/panel.png"


def test_legacy_page_is_read_without_automatic_upgrade() -> None:
    modern = ensure_page_layout(_page(1), {"language": "ja"})
    legacy = deepcopy(modern)
    legacy.pop("composition", None)
    assert composition_for_page(legacy)["composition_version"] == 1
    assert "composition" not in ensure_page_layout(legacy, {"language": "ja"})


def test_composition_quality_detects_uniform_and_coverage_failure() -> None:
    prepared = ensure_page_layout(_page(4, "conversation"), {"language": "ja"})
    for panel in prepared["composition"]["panels"]:
        panel["shape"] = "rectangle"
        panel["polygon_points"] = [[panel["x"], panel["y"]], [panel["x"] + panel["width"], panel["y"]], [panel["x"] + panel["width"], panel["y"] + panel["height"]], [panel["x"], panel["y"] + panel["height"]]]
    prepared["composition"]["panels"][0]["artwork_coverage"] = 0.8
    keys = {issue["key"] for issue in composition_quality_issues(prepared)}
    assert "composition-shape-uniform-1" in keys
    assert "composition-coverage-1-1" in keys


def test_page_level_bubble_collision_is_reported() -> None:
    prepared = ensure_page_layout(_page(3, "action"), {"language": "ja"})
    bubbles = [item for item in prepared["composition"]["overlays"] if item["type"] == "bubble"]
    assert bubbles
    prepared["composition"]["overlays"].append({**bubbles[0], "id": "overlap-bubble", "x": bubbles[0]["x"], "y": bubbles[0]["y"]})
    assert any(item["key"].startswith("composition-overlay-collision-") for item in composition_quality_issues(prepared))


def test_preview_zip_share_same_composition_png(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    prepared = ensure_page_layout(_page(3, "action"), {"language": "ja"})
    project = {"id": "composition-project", "title": "構成", "settings": {"language": "ja"}, "storyboard": [prepared]}
    preview_png = render_page_png(project, prepared, storage)
    archive_bytes = export_zip(project, storage)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.read("pages/page-001.png") == preview_png
        manifest = json.loads(archive.read("project.json"))
        assert manifest["storyboard"][0]["composition"]["composition_version"] == COMPOSITION_VERSION


def test_pdf_composition_is_raster_parity_and_text_searchable(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    prepared = ensure_page_layout(_page(2, "action"), {"language": "ja"})
    project = {"id": "composition-pdf", "title": "構成", "settings": {"language": "ja"}, "storyboard": [prepared]}
    content = export_pdf(project, storage)
    assert content.startswith(b"%PDF")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    assert "構成" in text
    assert "ここから先は、自分で決める。" in text
