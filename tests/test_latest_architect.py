"""最新版の版面・原作の出所・保存済み作品の互換性を実データで検証する。"""
from copy import deepcopy
from io import BytesIO
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from app.schemas import normalize_storyboard, SettingsPayload, normalize_characters
from app.services.artwork_geometry import artwork_aspect_ratio
from app.services.character_sheet import fixed_sheet_layout
from app.services.export import render_page_png, export_pdf, export_zip
from app.services.layout import reflow_page, repair_storyboard_page, ensure_page_layout
from app.services.manga_contract import creative_direction, rendering_modifiers
from app.services.manga_documents import name_snapshot, render_name_markdown, name_validation
from app.services.page_contract import A4_PAGE_SIZE, A4_EXPORT_SIZE
from app.services.storage import LocalFileStorage
from app.services.cover_layout import cover_artwork_panel, cover_text_regions


def sample(version=5):
    settings = {"composition_version": version, "language": "ja", "visual_style": "cinematic", "layout_policy": "content_driven", "color_mode": "bw"}
    page = reflow_page({"id": "p", "page_number": 1, "page_kind": "content", "title": "", "layout": "classic",
                       "panels": [{"id": "a", "description": "二人が公園で話す", "background": "公園", "characters": [],
                                   "dialogue": ["行こう。"], "narration": [], "sfx": [], "order": 1}]}, settings)
    return {"id": "qa", "title": "確認", "original_text": "二人は公園で出発を決めた。", "settings": settings, "storyboard": [page]}


def test_a4_geometry_survives_save_and_all_exports_agree(tmp_path):
    project = sample()
    page = project["storyboard"][0]
    comp = page["composition"]
    assert comp["size"] == list(A4_PAGE_SIZE)
    assert comp["canvas_contract"]["margin_contract"]["blank_outer_margin_percent"] == 3
    assert comp["page_number_design"]["text"] == "1"
    assert comp["page_number_design"]["renderer"] == "application"
    geometry = page["panels"][0]["geometry"]
    assert geometry["x"] == .03 and geometry["y"] == .03
    assert geometry["width"] == .94
    assert artwork_aspect_ratio(page["panels"][0]) == pytest.approx(geometry["width"] * 900 / (geometry["height"] * 1273))
    saved = normalize_storyboard(project["storyboard"], project["settings"])
    assert saved[0]["composition"]["size"] == list(A4_PAGE_SIZE)
    assert saved[0]["panels"][0]["geometry"]["page_size"] == list(A4_PAGE_SIZE)
    storage = LocalFileStorage(tmp_path)
    png = render_page_png(project, page, storage)
    image = Image.open(BytesIO(png)).convert("RGB")
    assert image.size == A4_EXPORT_SIZE
    assert image.getpixel((0, 0)) == (255, 255, 255)
    # 外周3%は白、下中央の予約領域には番号が存在する。
    assert image.crop((0, 0, image.width, int(image.height * .029))).getextrema() == ((255,255),) * 3
    assert image.crop((int(image.width*.45), int(image.height*.94), int(image.width*.55), int(image.height*.97))).getextrema()[0][0] < 100
    with zipfile.ZipFile(BytesIO(export_zip(project, storage))) as archive:
        assert archive.read("pages/page-001.png") == png
    pdf = PdfReader(BytesIO(export_pdf(project, storage)))
    assert float(pdf.pages[0].mediabox.width) / float(pdf.pages[0].mediabox.height) == pytest.approx(210/297)
    assert "行こう。" in pdf.pages[0].extract_text()


def test_old_page_is_kept_until_explicit_a4_repair(tmp_path):
    project = sample(4)
    page = deepcopy(project["storyboard"][0])
    project["settings"]["composition_version"] = 5
    assert ensure_page_layout(page, project["settings"])["composition"]["size"] == [900,1200]
    assert Image.open(BytesIO(render_page_png(project, page, LocalFileStorage(tmp_path)))).size == (900,1200)
    fixed = repair_storyboard_page([page], "p", project["settings"], composition_version=5)[0]
    assert fixed["composition"]["size"] == list(A4_PAGE_SIZE)
    assert fixed["panels"][0]["dialogue"] == page["panels"][0]["dialogue"]
    assert page == project["storyboard"][0]


def test_a4_sheet_has_same_51_slots_without_stretching():
    layout = fixed_sheet_layout("70:99")
    assert layout["id"] == "LAYOUT_A4_PORTRAIT"
    assert (layout["canvas"]["width"],layout["canvas"]["height"]) == A4_EXPORT_SIZE
    assert [s["nominal_rect"] for s in layout["sections"]] == [s["nominal_rect"] for s in fixed_sheet_layout("3:4")["sections"]]
    assert sum(s["count"] for s in layout["sections"]) == 51
    for section in layout["sections"]:
        for group in section["groups"]:
            for slot in group["slots"]:
                assert slot["rect"]["width"] > 0 and slot["rect"]["height"] > 0
    assert normalize_characters([{"name":"葵"}])[0]["sheet_ratio"] == "70:99"
    assert normalize_characters([{"name":"葵", "sheet_ratio":"3:4"}])[0]["sheet_ratio"] == "3:4"


def test_direction_and_original_description_remain_editable():
    settings = SettingsPayload(dialogue_mode="silent", panel_direction="restrained", reconstruction_policy="source_only").model_dump()
    assert creative_direction(settings)["dialogue_mode"] == "silent"
    assert len(rendering_modifiers(settings)["terms"]) == 7
    project = sample()
    page = project["storyboard"][0]
    page["creative_direction"] = creative_direction(settings)
    page["panels"][0]["dialogue_details"] = [{"speaker":"葵", "addressee":"凛", "source":"adaptation", "source_excerpt":project["original_text"]}]
    page = normalize_storyboard([page], project["settings"])[0]
    assert page["creative_direction"] == creative_direction(settings)
    project["storyboard"] = [page]
    snapshot = name_snapshot(project)
    markdown = render_name_markdown(snapshot, 1)
    assert "元説明：二人は公園で出発を決めた。" in markdown
    assert "ページ番号：『1』" in markdown and "A4縦" in markdown
    assert "サイレント / 落ち着いた演出 / 原文を保持" in markdown
    assert "minimal linework" in markdown


def test_a4_cover_reserves_safe_text_regions_without_body_margin(tmp_path):
    project = sample()
    cover = {**project["storyboard"][0], "page_kind": "cover", "page_number": 0}
    panel = cover_artwork_panel(cover, cover["panels"][0])
    assert artwork_aspect_ratio(panel) == pytest.approx(900/1273)
    assert panel["geometry"]["x"] == 0 and panel["geometry"]["width"] == 1
    for kind in ("cover", "back_cover"):
        for x, y, width, height in cover_text_regions(kind, a4=True).values():
            assert min(x, y) >= .05
            assert x + width <= .950001 and y + height <= .950001
    image = Image.open(BytesIO(render_page_png(project, cover, LocalFileStorage(tmp_path))))
    assert image.size == A4_EXPORT_SIZE


def test_a4_climax_keeps_footer_and_outer_margin():
    project = sample()
    page = project["storyboard"][0]
    page.update(scene_type="climax", climax=True, special_emphasis=True)
    page["panels"][0].update(panel_shape="large-bleed", shape_reason="決断を大きく描く")
    page = reflow_page(page, project["settings"])
    g = page["panels"][0]["geometry"]
    assert g["x"] >= .03 and g["y"] >= .03
    assert g["y"] + g["height"] <= .940001


def test_documentary_reconstruction_requires_verifiable_source_excerpt():
    project = sample()
    project["settings"].update(source_kind="documentary", reconstruction_policy="review_proposals", target_page_count=1)
    detail = {"source": "adaptation", "speaker": "葵"}
    project["storyboard"][0]["panels"][0]["dialogue_details"] = [detail]
    assert any("元説明" in message for message in name_validation(project, name_snapshot(project))["errors"])
    detail["source_excerpt"] = "本文には存在しない説明"
    assert any("原文と一致" in message for message in name_validation(project, name_snapshot(project))["errors"])
    detail["source_excerpt"] = project["original_text"]
    assert not any("元説明" in message for message in name_validation(project, name_snapshot(project))["errors"])
