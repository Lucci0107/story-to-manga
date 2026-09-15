"""画像生成なしでページ構図の既知の失敗を再現する回帰テスト。"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from app.services.artwork_geometry import resolve_final_crop_window
from app.services.composition import (
    MIN_PANEL_AREA_RATIO,
    MIN_PANEL_HEIGHT_RATIO,
    MIN_PANEL_WIDTH_RATIO,
    composition_for_page,
    composition_quality_issues,
    composition_quality_score,
    is_explicit_elliptical_inset,
    shape_points,
)
from app.services.export import _crop_panel_source_for_box, export_pdf, export_zip, prepare_page_for_render, render_page_png
from app.services.layout import ensure_page_layout, place_text_elements
from app.services.storage import LocalFileStorage


def _page(count: int = 1, *, composition_version: int = 2) -> dict:
    return {
        "id": "composition-regression-page",
        "page_number": 1,
        "title": "構図QA",
        "layout": "conversation",
        "page_role": "dialogue",
        "composition_version": composition_version,
        "panels": [
            {
                "id": f"panel-{index + 1}",
                "order": index + 1,
                "importance": "high" if index == 0 else "medium",
                "panel_role": "dialogue",
                "characters": [f"character-{index + 1}"],
                "dialogue": ["セリフを配置する"] if index == 0 else [],
                "narration": [],
                "sfx": [],
                "image_url": None,
                "generation_status": "not_started",
            }
            for index in range(count)
        ],
    }


@pytest.mark.parametrize(
    "breakout",
    [
        {"clip_shape": "ellipse"},
        {"clip_shape": "circle"},
        {"clip_path": "ellipse(50% 50% at 50% 50%)"},
        {"border-radius": "50%"},
    ],
)
def test_legacy_elliptical_artwork_masks_are_suppressed_only_at_render_time(breakout):
    page = ensure_page_layout(_page(), {"language": "ja"})
    page["composition"]["breakouts"] = [{"id": "legacy-mask", "enabled": True, **breakout}]
    normalized = composition_for_page(page)
    assert normalized["breakouts"][0]["enabled"] is False
    assert normalized["breakouts"][0]["render_suppressed"] is True
    assert page["composition"]["breakouts"][0]["enabled"] is True
    assert composition_quality_score(page)["ellipse_mask_count"] == 1


def test_elliptical_inset_requires_semantics_and_crop_protection():
    inset = {
        "clip_shape": "ellipse",
        "semantic_effect": "memory",
        "explicitly_requested": True,
        "is_inset": True,
        "main_panel_retained": True,
        "crop_protection_validated": True,
        "semantic_reason": "回想を示す小さなInset",
    }
    assert is_explicit_elliptical_inset(inset)
    assert not is_explicit_elliptical_inset({**inset, "crop_protection_validated": False})
    assert not is_explicit_elliptical_inset({**inset, "semantic_reason": ""})


def test_breakout_css_cannot_round_artwork_into_an_ellipse():
    css = (Path(__file__).resolve().parents[1] / "static/css/app.css").read_text(encoding="utf-8")
    match = re.search(r"\.manga-breakout-character\s*\{([^}]*)\}", css)
    assert match
    assert "ellipse(" not in match.group(1)
    assert "border-radius: 0" in match.group(1)


def test_sliver_geometry_is_repaired_without_replacing_artwork_or_mutating_saved_page():
    page = ensure_page_layout(_page(3), {"language": "ja"})
    page["panels"][0]["image_url"] = "/media/existing-panel.png"
    geometry = page["composition"]["panels"][0]
    geometry.update(x=0.05, y=0.25, width=0.90, height=0.04)
    geometry["polygon_points"] = shape_points(geometry, "rectangle")
    original_geometry = dict(geometry)

    rendered = prepare_page_for_render(page, {"language": "ja"})

    assert rendered["panels"][0]["image_url"] == "/media/existing-panel.png"
    assert rendered["composition"]["panels"][0]["height"] >= 0.13
    assert page["composition"]["panels"][0] == original_geometry


@pytest.mark.parametrize("panel_count", [16, 20])
def test_dense_pages_reflow_to_five_readable_rows_before_creating_slivers(panel_count):
    page = ensure_page_layout(_page(panel_count), {"language": "ja"})
    geometries = page["composition"]["panels"]

    assert max(item["row"] for item in geometries) <= 5
    assert all(item["width"] >= MIN_PANEL_WIDTH_RATIO for item in geometries)
    assert all(item["height"] >= MIN_PANEL_HEIGHT_RATIO for item in geometries)
    assert all(item["area"] >= MIN_PANEL_AREA_RATIO for item in geometries)


def test_explicit_empty_text_detail_strip_is_allowed_but_dialogue_is_not():
    detail = ensure_page_layout(_page(), {"language": "ja"})
    detail["panels"][0]["panel_role"] = "eye detail"
    detail["panels"][0]["dialogue"] = []
    geometry = detail["composition"]["panels"][0]
    geometry.update(x=0.10, y=0.25, width=0.80, height=0.07)
    geometry["polygon_points"] = shape_points(geometry, "rectangle")
    assert not any(issue["key"].startswith("composition-sliver-") for issue in composition_quality_issues(detail))

    detail["panels"][0]["dialogue"] = ["detailには文字を押し込まない"]
    assert any(issue["key"].startswith("composition-sliver-") for issue in composition_quality_issues(detail))


@pytest.mark.parametrize(
    "protected",
    [
        {"x": 0.48, "y": 0.04, "width": 0.48, "height": 0.34},  # 顔・頭
        {"x": 0.04, "y": 0.42, "width": 0.28, "height": 0.28},  # 手・器具
    ],
)
def test_balloon_candidates_never_intersect_hard_subject_zones(protected):
    layout = place_text_elements(
        {"characters": ["A"], "dialogue": ["人物を隠さない短いセリフ"]},
        {"language": "ja"},
        protected_zones=[protected],
    )
    item = layout["items"][0]
    left = max(item["x"], protected["x"])
    top = max(item["y"], protected["y"])
    overlap = max(0, min(item["x"] + item["width"], protected["x"] + protected["width"]) - left) * max(
        0, min(item["y"] + item["height"], protected["y"] + protected["height"]) - top
    )
    assert overlap == 0
    assert not item["render_suppressed"]


def test_balloon_prefers_reserved_negative_space_and_speaker_opposite_side():
    reserved = {"x": 0.06, "y": 0.79, "width": 0.36, "height": 0.15}
    layout = place_text_elements(
        {"character_position": "right", "dialogue": ["短い台詞"]},
        {"language": "ja"},
        reserved_zones={"bubble": reserved},
        protected_zones=[],
    )
    item = layout["items"][0]
    assert {key: item[key] for key in reserved} == reserved
    assert item["x"] < 0.5


def test_unplaceable_long_dialogue_is_flagged_instead_of_shrunk_or_overlaid():
    layout = place_text_elements(
        {"characters": ["A"], "dialogue": ["長いセリフです。" * 80]},
        {"language": "ja"},
        protected_zones=[{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}],
    )
    assert layout["items"][0]["overflow"]
    assert layout["items"][0]["render_suppressed"]
    assert layout["warnings"]


def test_narration_uses_edge_caption_rules_and_bubbles_keep_reading_order():
    layout = place_text_elements(
        {"dialogue": ["一つ目", "二つ目"], "narration": ["翌朝"], "characters": []},
        {"language": "ja"},
    )
    bubbles = [item for item in layout["items"] if item["type"] == "bubble"]
    narration = next(item for item in layout["items"] if item["type"] == "narration")
    assert bubbles[0]["x"] > bubbles[1]["x"]
    assert narration["y"] > max(item["y"] for item in bubbles)
    for index, left in enumerate(layout["items"]):
        for right in layout["items"][index + 1 :]:
            assert not (
                left["x"] < right["x"] + right["width"]
                and right["x"] < left["x"] + left["width"]
                and left["y"] < right["y"] + right["height"]
                and right["y"] < left["y"] + left["height"]
            )


def test_page_quality_metrics_detect_face_collision_and_bad_reading_order():
    page = ensure_page_layout(_page(2, composition_version=3), {"language": "ja", "composition_version": 3})
    panel_geometry = page["composition"]["panels"][0]
    panel_geometry["reading_order"] = 2
    page["composition"]["panels"][1]["reading_order"] = 1
    page["panels"][0]["text_layout"]["items"][0].update(x=0.28, y=0.24, width=0.44, height=0.18)
    metrics = composition_quality_score(page)
    assert metrics["face_balloon_overlap_count"] >= 1
    assert metrics["important_subject_balloon_overlap_count"] >= 1
    assert metrics["ambiguous_reading_order_count"] == 1


def test_final_crop_adjusts_to_preserve_face_hands_and_props():
    plan = resolve_final_crop_window(
        (1200, 900),
        (600, 800),
        protected_regions=[
            {"x": 0.42, "y": 0.08, "width": 0.16, "height": 0.20},
            {"x": 0.53, "y": 0.66, "width": 0.13, "height": 0.16},
            {"x": 0.32, "y": 0.62, "width": 0.12, "height": 0.14},
        ],
        anchor_x="left",
        anchor_y="top",
    )
    assert plan["valid"]
    crop = plan["window"]
    assert crop["x"] <= 0.32
    assert crop["x"] + crop["width"] >= 0.66
    assert crop["y"] <= 0.08
    assert crop["y"] + crop["height"] >= 0.82


def test_unpreservable_crop_falls_back_to_contain_without_amputating_source():
    image = Image.new("RGB", (1200, 900), "white")
    image.paste((255, 0, 0), (0, 0, 300, 900))
    image.paste((0, 0, 255), (900, 0, 1200, 900))
    prepared, crop_valid = _crop_panel_source_for_box(
        image,
        {
            "panel_direction": {
                "face_safe_zone": {"x": 0.0, "y": 0.1, "width": 0.1, "height": 0.2},
                "important_hand_zone": {"x": 0.9, "y": 0.7, "width": 0.1, "height": 0.2},
            }
        },
        600,
        800,
        "center",
        "middle",
    )
    assert not crop_valid
    assert prepared.getpixel((15, 400))[0] > 200
    assert prepared.getpixel((585, 400))[2] > 200


def test_artwork_without_crop_safety_metadata_is_contained_not_guessed_crop():
    image = Image.new("RGB", (1200, 900), "white")
    image.paste((255, 0, 0), (0, 0, 300, 900))
    image.paste((0, 0, 255), (900, 0, 1200, 900))
    prepared, crop_valid = _crop_panel_source_for_box(image, {}, 600, 800, "center", "middle")
    assert not crop_valid
    assert prepared.getpixel((15, 400))[0] > 200
    assert prepared.getpixel((585, 400))[2] > 200
    assert prepared.getpixel((300, 20)) == (245, 243, 235)


def test_preview_pdf_and_zip_share_the_same_v2_composition_pixels(tmp_path):
    storage = LocalFileStorage(tmp_path)
    settings = {"language": "ja", "composition_version": 2}
    page = ensure_page_layout(_page(2), settings)
    project = {"id": "composition-parity", "title": "構図QA", "settings": settings, "storyboard": [page]}
    preview = render_page_png(project, page, storage)
    with zipfile.ZipFile(io.BytesIO(export_zip(project, storage))) as archive:
        assert archive.read("pages/page-001.png") == preview
    pdf_page = PdfReader(io.BytesIO(export_pdf(project, storage))).pages[0]
    embedded = pdf_page.images[0].image.convert("RGB")
    assert embedded.size == (900, 1200)
    assert embedded.tobytes() == Image.open(io.BytesIO(preview)).convert("RGB").tobytes()
    assert abs(float(pdf_page.mediabox.width) / float(pdf_page.mediabox.height) - 0.75) < 0.001


def test_panel_text_is_drawn_above_rectangular_breakout(tmp_path):
    storage = LocalFileStorage(tmp_path)
    project_id = "breakout-text-layer"
    filename = "solid-red-panel.png"
    source = Image.new("RGB", (900, 1200), (220, 20, 20))
    source_bytes = io.BytesIO()
    source.save(source_bytes, format="PNG")
    storage.put_bytes(storage.asset_key(project_id, filename), source_bytes.getvalue(), "image/png")

    page = ensure_page_layout(_page(), {"language": "ja"})
    panel = page["panels"][0]
    panel["image_url"] = f"/media/{filename}"
    composition = page["composition"]
    composition["breakouts"] = [{
        "id": "rectangular-foreground",
        "panel_id": panel["id"],
        "source_panel_id": panel["id"],
        "type": "character",
        "enabled": True,
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "clip_shape": "rectangle",
        "z_index": 3,
    }]
    project = {"id": project_id, "settings": {"language": "ja"}, "storyboard": [page]}

    rendered = Image.open(io.BytesIO(render_page_png(project, page, storage))).convert("RGB")
    geometry = composition["panels"][0]
    bubble = panel["text_layout"]["items"][0]
    x = round((geometry["x"] + (bubble["x"] + bubble["width"] * 0.20) * geometry["width"]) * 900)
    y = round((geometry["y"] + (bubble["y"] + bubble["height"] * 0.50) * geometry["height"]) * 1200)
    assert rendered.getpixel((x, y)) == (250, 248, 242)
