"""生成サイズと実際のコマ寸法の整合性を検証する。"""

import pytest

from app.services.artwork_geometry import artwork_aspect_ratio, artwork_generation_size
from app.services.ai_pipeline import compose_panel_prompt


@pytest.mark.parametrize("width,height,ratio,size", [
    (0.8, 0.3, 2.0, "1536x1024"),
    (0.4, 0.6, 0.5, "1024x1536"),
    (0.4, 0.3, 1.0, "1024x1024"),
])
def test_physical_panel_ratio_shared_with_prompt(width, height, ratio, size):
    panel = {"geometry": {"width": width, "height": height}}
    assert artwork_aspect_ratio(panel) == pytest.approx(ratio)
    assert artwork_generation_size(panel, "gpt-image-1") == size
    assert f"target aspect ratio {ratio:.2f}:1" in compose_panel_prompt(panel, [], {})


def test_existing_artwork_viewport_ratio():
    panel = {"geometry": {"width": 0.4, "height": 0.6,
                          "artwork_viewport": {"width": 1, "height": 0.5}}}
    assert artwork_aspect_ratio(panel) == pytest.approx(1)


@pytest.mark.parametrize("geometry", [{}, None, {"width": "nan", "height": 1},
                                      {"width": 1, "height": 0}])
def test_invalid_geometry_is_safe(geometry):
    assert artwork_aspect_ratio({"geometry": geometry}) == 1


def test_nearest_supported_ratio_limits_crop():
    assert artwork_generation_size({"geometry": {"width": 0.52, "height": 0.3}}, "gpt-image-1") == "1536x1024"


@pytest.mark.parametrize("ratio", [0.34, 0.5, 0.75, 1, 1.3, 2.08, 2.8, 3])
def test_flexible_model_matches_panel_without_large_crop(ratio):
    panel = {"geometry": {"width": ratio / 3, "height": 0.25}}
    width, height = map(int, artwork_generation_size(panel, "gpt-image-2").split("x"))
    assert width % 16 == height % 16 == 0
    assert 900_000 <= width * height <= 1_200_000
    assert abs((width / height) / ratio - 1) < 0.01


def test_image_api_receives_flexible_size_and_confirmed_regions(monkeypatch, tmp_path):
    import base64
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from app.services import artwork
    from app.services.storage import LocalFileStorage
    from app.services.panel_direction import plan_panel_direction

    panel = {"geometry": {"width": 0.832, "height": 0.3}, "dialogue": ["会えたね"], "characters": ["A"]}
    panel["panel_direction"] = plan_panel_direction(panel, {})
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="PNG")
    captured = []

    def fake_request(url, **kwargs):
        captured.append(kwargs["payload"])
        return {"data": [{"b64_json": base64.b64encode(output.getvalue()).decode()}]}

    monkeypatch.setattr(artwork, "request_json", fake_request)
    runtime = SimpleNamespace(openai_image_model="gpt-image-2", openai_image_url="https://example.invalid", openai_api_key="test-only")
    artwork.save_openai_image(panel, runtime, LocalFileStorage(tmp_path), "test.png")
    assert len(captured) == 1
    assert captured[0]["size"] == artwork_generation_size(panel)
    assert captured[0]["size"] != "1536x1024"
    assert "face_safe_zone" in captured[0]["prompt"]
    assert panel["panel_direction"]["generation_canvas"]["generation_size"] == captured[0]["size"]
