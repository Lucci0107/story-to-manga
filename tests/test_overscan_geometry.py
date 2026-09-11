"""横長多要素コマのoverscan/safe-cropを課金なしで検証する。"""

from copy import deepcopy
from io import BytesIO

from PIL import Image

from app.services.artwork_geometry import (
    DIRECT_GENERATION,
    OVERSCAN_SAFE_CROP,
    artwork_generation_size,
    generation_canvas_is_feasible,
    generation_canvas_zones,
    resolve_generation_strategy,
)
from app.services.ai_pipeline import compose_panel_prompt
from app.services.export import _pil_panel_image
from app.services.panel_direction import plan_panel_direction
from app.services.storage import LocalFileStorage
from scripts.manga_validation_fixture import SETTINGS, validation_pages


def wide_panel():
    panel = deepcopy(validation_pages()[2]["panels"][2])
    panel.update(shot_type="close_up", hands_required=True, props_required=True)
    panel["panel_direction"] = plan_panel_direction(panel, SETTINGS)
    return panel


def test_wide_multi_element_uses_overscan_source_and_persists_crop_plan():
    panel = wide_panel()
    strategy = resolve_generation_strategy(panel)
    canvas = generation_canvas_zones(panel, "gpt-image-2")

    assert strategy["strategy"] == OVERSCAN_SAFE_CROP
    assert canvas["strategy"] == OVERSCAN_SAFE_CROP
    assert canvas["source_aspect_ratio"] < canvas["final_panel_aspect_ratio"]
    assert canvas["overscan"]["top"] >= .05
    assert canvas["safe_crop"] == canvas["final_crop_window"]
    assert canvas["headroom_reference"] == "final_crop"
    assert canvas["headroom_target"] == strategy["headroom_target"]
    assert canvas["safe_crop"]["y"] + canvas["safe_crop"]["height"] <= 1
    assert generation_canvas_is_feasible(panel)
    # 2:1のPanelへ3:2程度のsourceを生成し、上下のsafe marginを確保する。
    source_width, source_height = map(int, canvas["generation_size"].split("x"))
    assert source_width / source_height < panel["geometry"]["width"] / panel["geometry"]["height"]
    assert artwork_generation_size(panel, "gpt-image-2") == canvas["generation_size"]


def test_ordinary_panel_remains_direct_generation():
    panel = deepcopy(validation_pages()[0]["panels"][0])
    panel["panel_direction"] = plan_panel_direction(panel, SETTINGS)
    strategy = resolve_generation_strategy(panel)
    canvas = generation_canvas_zones(panel, "gpt-image-2")
    assert strategy["strategy"] == DIRECT_GENERATION
    assert canvas["strategy"] == DIRECT_GENERATION
    assert canvas["overscan"] == {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}


def test_required_zones_are_inside_persisted_final_crop():
    panel = wide_panel()
    canvas = generation_canvas_zones(panel)
    crop = canvas["safe_crop"]
    for item in canvas["regions"]["text_reserved_zones"]:
        assert crop["x"] <= item["x"]
        assert crop["y"] <= item["y"]
        assert item["x"] + item["width"] <= crop["x"] + crop["width"]
        assert item["y"] + item["height"] <= crop["y"] + crop["height"]
    for key in ("head_safe_zone", "face_safe_zone", "important_hand_zone", "important_prop_zone"):
        item = canvas["regions"][key]
        assert crop["x"] <= item["x"]
        assert crop["y"] <= item["y"]
        assert item["x"] + item["width"] <= crop["x"] + crop["width"]
        assert item["y"] + item["height"] <= crop["y"] + crop["height"]


def test_saved_crop_is_used_by_preview_pdf_zip_panel_renderer(tmp_path):
    panel = wide_panel()
    canvas = generation_canvas_zones(panel)
    panel["panel_direction"]["generation_canvas"] = {**canvas, "source_asset": "assets/p/panel.png"}
    panel["image_url"] = "/media/panel.png"
    storage = LocalFileStorage(tmp_path)
    key = storage.asset_key("p", "panel.png")
    source = Image.new("RGB", (120, 80), "white")
    pixels = source.load()
    for y in range(80):
        for x in range(120):
            pixels[x, y] = (255, 0, 0) if y < 5 else (0, 0, 255) if y >= 75 else (0, 255, 0)
    output = BytesIO()
    source.save(output, format="PNG")
    storage.put_bytes(key, output.getvalue(), content_type="image/png")
    rendered = _pil_panel_image({"id": "p"}, panel, 200, 100, storage)
    assert rendered.size == (200, 100)
    # 保存cropの上端を使うため、source最上段の赤いoverscanは出力に残らない。
    assert rendered.getpixel((100, 0)) != (255, 0, 0)


def test_generation_strategy_is_in_saved_panel_direction():
    panel = wide_panel()
    assert panel["panel_direction"]["generation_strategy"]["strategy"] == OVERSCAN_SAFE_CROP
    assert panel["panel_direction"]["composition_debug"]["generation_strategy"]["safe_crop"]
    prompt = compose_panel_prompt(panel, [], SETTINGS)
    assert "Generation strategy: overscan_safe_crop" in prompt
    assert "saved crop, not the source border" in prompt


def test_legacy_generation_canvas_stays_direct_and_is_not_migrated():
    panel = wide_panel()
    panel["panel_direction"]["generation_canvas"] = {
        "generation_size": "1440x720",
        "final_crop_window": {"x": 0, "y": 0, "width": 1, "height": 1},
    }
    canvas = generation_canvas_zones(panel)
    assert canvas["strategy"] == DIRECT_GENERATION
    assert canvas["generation_size"] == "1440x720"
