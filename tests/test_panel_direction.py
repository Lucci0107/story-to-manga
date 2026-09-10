"""画像生成前の空間計画と意味別演出を検証する。外部APIは呼ばない。"""

from copy import deepcopy

import pytest

from app.services.panel_direction import plan_panel_direction, direction_is_ready
from app.services.visual_style import resolve_visual_style, text_direction, dialogue_type, sfx_type
from app.services.ai_pipeline import compose_panel_prompt
from app.services.layout import reflow_page, ensure_page_layout


def panel():
    return {"id": "planned", "characters": ["A"], "dialogue": ["待って。"],
            "geometry": {"x": 0.04, "y": 0.05, "width": 0.9, "height": 0.4, "shape": "rectangle"}}


def test_plan_precedes_artwork_and_reserves_real_text():
    item = panel()
    item["panel_direction"] = plan_panel_direction(item, {})
    assert direction_is_ready(item, {})
    direction = item["panel_direction"]
    assert direction["source"] == "pre_generation_plan"
    assert direction["text_layout"]["items"][0]["lines"]
    assert "artwork_viewport" not in direction["geometry"]
    assert direction["reserved_text_zones"][0]["x"] > direction["face_safe_zone"]["x"] + direction["face_safe_zone"]["width"]
    assert not direction["breakout_policy"]["enabled"]


@pytest.mark.parametrize("field,value", [("dialogue", ["変更"]), ("shot_type", "close-up")])
def test_changed_inputs_invalidate_generation(field, value):
    item = panel()
    item["panel_direction"] = plan_panel_direction(item, {})
    item[field] = value
    assert not direction_is_ready(item, {})


def test_style_change_invalidates_but_artwork_completion_does_not():
    item = panel()
    item["panel_direction"] = plan_panel_direction(item, {"visual_style": "comedy"})
    item["image_url"] = "/existing.png"
    assert direction_is_ready(item, {"visual_style": "comedy"})
    assert not direction_is_ready(item, {"visual_style": "minimal"})


def test_text_shortage_blocks_plan_not_squeezes_text():
    item = panel()
    item["dialogue"] = ["長いセリフ" * 100]
    item["panel_direction"] = plan_panel_direction(item, {})
    assert not direction_is_ready(item, {})
    assert item["panel_direction"]["text_layout"]["items"][0]["font_size"] >= 14


def test_explicit_face_collision_is_not_accepted():
    item = panel()
    item["protected_zones"] = [{"x": 0, "y": 0, "width": 1, "height": 1}]
    assert plan_panel_direction(item, {})["status"] == "needs_revision"


def test_prompt_uses_plan_regions():
    item = panel()
    item["panel_direction"] = plan_panel_direction(item, {})
    prompt = compose_panel_prompt(item, [], {})
    for key in ("character_zone", "face_safe_zone", "reserved_text_zones", "important_prop_zone", "crop_anchor"):
        assert key in prompt


@pytest.mark.parametrize("kind", ["normal", "thought", "shout", "whisper", "weak", "comedic_reaction", "announcement"])
def test_explicit_dialogue_semantics(kind):
    profile = resolve_visual_style({"visual_style": "comedy"})
    assert text_direction({"type": "bubble", "text": "セリフ"}, profile, kind)["family"] == kind


def test_no_punctuation_based_fake_shout():
    assert dialogue_type("こんにちは！") == "normal"


def test_restrained_style_does_not_force_burst():
    item = {"type": "bubble", "text": "叫び"}
    assert text_direction(item, resolve_visual_style({"visual_style": "minimal"}), "shout")["shape"] == "round"
    assert text_direction(item, resolve_visual_style({"visual_style": "comedy"}), "shout")["shape"] == "burst"


@pytest.mark.parametrize("text,kind", [("テクテク", "footstep"), ("ドン", "impact"), ("シーン", "ambient"), ("ピタッ", "stop")])
def test_sfx_semantics(text, kind):
    assert sfx_type(text) == kind


def test_sfx_strength_and_monochrome():
    profile = resolve_visual_style({"visual_style": "comedy", "color_mode": "bw"})
    foot = text_direction({"type": "sfx", "text": "テクテク"}, profile)
    impact = text_direction({"type": "sfx", "text": "ドン"}, profile)
    assert foot["size_scale"] < impact["size_scale"]
    assert foot["repetition"]
    assert foot["fill"] == "#222222"


def test_existing_saved_layout_not_recomputed_on_image_completion():
    settings = {"composition_version": 3, "visual_style": "comedy", "language": "ja"}
    page = reflow_page({"id": "page", "page_number": 1, "panels": [panel()]}, settings)
    assert page["panels"][0]["panel_direction"]["status"] == "ready"
    assert page["panels"][0]["text_layout"]["placement_mode"] == "pre_generation_plan"
    before = deepcopy(page)
    page["panels"][0]["image_url"] = "/unchanged.png"
    rendered = ensure_page_layout(page, settings)
    assert rendered["panels"][0]["text_layout"] == before["panels"][0]["text_layout"]
    assert rendered["panels"][0]["geometry"] == before["panels"][0]["geometry"]


def test_queue_rejects_shortage_before_any_job_or_paid_call(monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from app.main import queue_panels
    from app import db

    item = panel()
    item["dialogue"] = ["長いセリフ" * 200]
    item["panel_direction"] = plan_panel_direction(item, {})
    project = {"id": "test", "settings": {}, "storyboard": [{"panels": [item]}]}
    monkeypatch.setattr(db, "create_generation_job", lambda *args, **kwargs: pytest.fail("未確定の構図でJobを作成してはいけません"))
    with pytest.raises(HTTPException) as exc:
        queue_panels(project, "user", [], False, False, BackgroundTasks())
    assert exc.value.status_code == 422


def test_generation_canvas_mapping_survives_crop():
    from app.services.artwork_geometry import generation_canvas_zones

    item = panel()
    direction = plan_panel_direction(item, {})
    item["panel_direction"] = direction
    mapping = generation_canvas_zones(item)
    crop = mapping["final_crop_window"]
    for key in ("face_safe_zone", "important_prop_zone", "important_hand_zone"):
        mapped = mapping["regions"][key]
        assert (mapped["x"] - crop["x"]) / crop["width"] == pytest.approx(direction[key]["x"], abs=0.0001)
        assert (mapped["y"] - crop["y"]) / crop["height"] == pytest.approx(direction[key]["y"], abs=0.0001)


def test_short_dialogue_not_stretched_to_full_reserved_column():
    item = panel()
    layout = plan_panel_direction(item, {})["text_layout"]
    assert layout["items"][0]["width"] < 0.3


def test_style_tokens_change_narration_and_palette():
    elegant = resolve_visual_style({"visual_style": "elegant", "color_mode": "color"})
    dynamic = resolve_visual_style({"visual_style": "dynamic", "color_mode": "color"})
    assert text_direction({"type": "narration"}, elegant) != text_direction({"type": "narration"}, dynamic)
    assert elegant["accent"] != dynamic["accent"]


def test_style_changes_new_layout_gutters():
    page = {"id": "p", "page_number": 1, "page_role": "dialogue", "panels": [panel(), {**panel(), "id": "second"}]}
    elegant = reflow_page(page, {"composition_version": 3, "visual_style": "elegant"})
    dynamic = reflow_page(page, {"composition_version": 3, "visual_style": "dynamic"})
    assert elegant["panels"][0]["geometry"]["gutter"]["width"] > dynamic["panels"][0]["geometry"]["gutter"]["width"]


def test_storyboard_schema_requests_semantics_before_image_generation():
    from app.services.ai_pipeline import PANEL_SCHEMA

    for field in ("dialogue_types", "sfx_types", "character_position"):
        assert field in PANEL_SCHEMA["properties"]
        assert field in PANEL_SCHEMA["required"]


def test_planned_page_preview_pdf_zip_share_pixels(tmp_path):
    import io
    import zipfile
    from PIL import Image
    from pypdf import PdfReader
    from app.services.export import render_page_png, export_pdf, export_zip
    from app.services.storage import LocalFileStorage

    settings = {"composition_version": 3, "visual_style": "comedy", "color_mode": "color"}
    page = reflow_page({"id": "p", "page_number": 1, "panels": [panel()]}, settings)
    project = {"id": "p", "settings": settings, "storyboard": [page]}
    storage = LocalFileStorage(tmp_path)
    png = render_page_png(project, page, storage)
    with zipfile.ZipFile(io.BytesIO(export_zip(project, storage))) as archive:
        assert archive.read("pages/page-001.png") == png
    embedded = PdfReader(io.BytesIO(export_pdf(project, storage))).pages[0].images[0].image.convert("RGB")
    expected = Image.open(io.BytesIO(png)).convert("RGB")
    assert embedded.size == expected.size
    assert embedded.tobytes() == expected.tobytes()
