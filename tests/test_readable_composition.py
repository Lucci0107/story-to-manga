"""実画像の顔位置が不明な場合の保守的配置と描画一致を検証する。"""

import io
import zipfile
import pytest

from PIL import Image
from pypdf import PdfReader

from app.services.export import CompositionReadabilityError, render_page_png, export_pdf, export_zip
from app.services.layout import reflow_page
from app.services.storage import LocalFileStorage
from app.services.text_composition import separate_text_from_unverified_artwork, wrap_text, body_font


def test_text_is_measured_with_actual_glyph_width():
    lines = wrap_text("長い日本語とEnglish wordsを混ぜても、文字は枠から飛び出さない。", 135)
    assert len(lines) > 1
    assert all(body_font().getlength(line) <= 135 for line in lines)
    assert all(not line.startswith(tuple("、。ゃゅょっ」")) for line in wrap_text("うん、聞いているよ。ちゃんと伝えよう。", 153))


def test_unknown_faces_use_disjoint_artwork_and_text_regions():
    panel = {"image_url": "/media/existing.png", "dialogue": ["こんにちは"], "narration": ["翌朝"]}
    for language in ("ja", "en"):
        layout = separate_text_from_unverified_artwork(panel, {"width": .5, "height": .5}, language)
        assert not layout["warnings"]
        assert all(item["y"] + item["height"] < layout["artwork_viewport"]["y"] for item in layout["items"])
        assert all(item["font_size"] == 17 for item in layout["items"])


def test_existing_verified_protection_is_not_overwritten():
    assert separate_text_from_unverified_artwork({"image_url": "x.png", "protected_zones": [{"x": .1}]}, {}, "ja") is None


def test_dense_text_requires_more_space_instead_of_tiny_font():
    layout = separate_text_from_unverified_artwork({"image_url": "x.png", "dialogue": ["長文です。" * 60]}, {"width": .2, "height": .15}, "ja")
    assert layout["warnings"]
    assert layout["items"][0]["overflow"]
    assert layout["items"][0]["font_size"] == 17


@pytest.mark.parametrize("exporter", [export_pdf, export_zip])
def test_unreadable_page_is_not_exported_as_success(tmp_path, exporter):
    settings = {"language": "ja", "composition_version": 3}
    page = reflow_page({"id": "dense", "page_number": 1, "panels": [
        {"id": "a", "image_url": "/media/source.png", "dialogue": ["長文です。" * 500]}
    ]}, settings)
    with pytest.raises(CompositionReadabilityError, match="文字領域が不足"):
        exporter({"id": "dense", "settings": settings, "storyboard": [page]}, LocalFileStorage(tmp_path))


def test_artwork_viewport_is_shared_by_preview_and_zip(tmp_path):
    storage = LocalFileStorage(tmp_path)
    image = Image.new("RGB", (600, 400), (160, 190, 210))
    source = io.BytesIO()
    image.save(source, format="PNG")
    storage.put_bytes(storage.asset_key("readable", "source.png"), source.getvalue())
    settings = {"language": "ja", "composition_version": 3}
    page = reflow_page({"id": "p", "page_number": 1, "layout": "conversation", "panels": [
        {"id": "a", "image_url": "/media/source.png", "characters": ["A"], "dialogue": ["ここに顔を隠さないセリフ"], "narration": ["翌朝"], "sfx": []}
    ]}, settings)
    project = {"id": "readable", "settings": settings, "storyboard": [page]}
    composition_panel = page["composition"]["panels"][0]
    assert composition_panel["artwork_viewport"]
    rendered = render_page_png(project, page, storage)
    with zipfile.ZipFile(io.BytesIO(export_zip(project, storage))) as archive:
        assert archive.read("pages/page-001.png") == rendered
    output = Image.open(io.BytesIO(rendered))
    assert output.getpixel((450, 900)) == (160, 190, 210)
    pdf_page = PdfReader(io.BytesIO(export_pdf(project, storage))).pages[0]
    embedded = pdf_page.images[0].image.convert("RGB")
    assert embedded.size == output.size
    assert embedded.tobytes() == output.convert("RGB").tobytes()
    assert abs(float(pdf_page.mediabox.width) / float(pdf_page.mediabox.height) - .75) < .001
