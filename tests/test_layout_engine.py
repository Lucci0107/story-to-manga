"""不均等コマ割りと文字要素collision passの回帰テスト。"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import db
from app.main import app
from app.services.export import PDF_FONT, _page_panel_boxes, export_pdf, export_zip
from app.services.layout import (
    TEMPLATE_ACTION,
    TEMPLATE_CONVERSATION,
    TEMPLATE_DRAMA,
    TEMPLATE_FOUR_PANEL,
    TEMPLATE_PSYCHOLOGICAL,
    ensure_page_layout,
    page_layout_issues,
    repair_storyboard_page,
)
from app.services.storage import LocalFileStorage


def _panels(count: int, *, important: int | None = None, long_text: bool = False) -> list[dict]:
    result = []
    for index in range(count):
        dialogue = ["これは長いセリフですが、意味を変えずに読みやすく折り返して配置します。" * 2] if long_text and index == 0 else [f"セリフ{index + 1}"]
        result.append(
            {
                "id": f"panel-{index + 1}",
                "order": index + 1,
                "description": f"場面{index + 1}",
                "panel_role": "決定的な瞬間" if important == index else "会話",
                "scene_type": "climax" if important == index else "dialogue",
                "importance": "critical" if important == index else "medium",
                "characters": ["蒼"],
                "dialogue": dialogue,
                "narration": ["時間が静かに流れる。"] if index == 0 else [],
                "sfx": ["ざわ…"] if index == 1 else [],
            }
        )
    return result


def _page(layout: str, count: int, *, important: int | None = None, long_text: bool = False) -> dict:
    return {
        "id": f"page-{layout}",
        "page_number": 1,
        "title": "テストページ",
        "layout": layout,
        "panels": _panels(count, important=important, long_text=long_text),
    }


def _rows(prepared: dict) -> list[int]:
    geometries = prepared["layout_geometry"]["panels"]
    return [sum(1 for item in geometries if item["row"] == row) for row in sorted({item["row"] for item in geometries})]


def _intersects(left: dict, right: dict) -> bool:
    return not (
        left["x"] + left["width"] <= right["x"]
        or right["x"] + right["width"] <= left["x"]
        or left["y"] + left["height"] <= right["y"]
        or right["y"] + right["height"] <= left["y"]
    )


def test_normal_page_is_not_uniform_but_four_panel_may_be_uniform() -> None:
    normal = ensure_page_layout(_page("conversation", 4), {"language": "ja"})
    areas = [item["area"] for item in normal["layout_geometry"]["panels"]]
    assert max(areas) / min(areas) >= 1.12
    assert not page_layout_issues(normal, {"language": "ja"})

    four_panel = ensure_page_layout(_page("four_panel", 4), {"language": "ja"})
    assert four_panel["layout_geometry"]["template"] == TEMPLATE_FOUR_PANEL
    assert len({item["area"] for item in four_panel["layout_geometry"]["panels"]}) == 1
    assert not page_layout_issues(four_panel, {"language": "ja"})


def test_important_panel_receives_largest_area() -> None:
    prepared = ensure_page_layout(_page("drama", 6, important=3), {"language": "ja"})
    by_id = {item["panel_id"]: item for item in prepared["layout_geometry"]["panels"]}
    assert by_id["panel-4"]["area"] == max(item["area"] for item in by_id.values())
    assert by_id["panel-4"]["area"] / min(item["area"] for item in by_id.values()) > 1.5


def test_template_a_b_c_d_geometry_patterns() -> None:
    drama = ensure_page_layout(_page("drama", 6), {"language": "ja"})
    conversation = ensure_page_layout(_page("conversation", 5), {"language": "ja"})
    action = ensure_page_layout(_page("action", 6), {"language": "ja"})
    psychological = ensure_page_layout(_page("psychological", 4), {"language": "ja"})
    assert drama["layout_geometry"]["template"] == TEMPLATE_DRAMA
    assert conversation["layout_geometry"]["template"] == TEMPLATE_CONVERSATION
    assert action["layout_geometry"]["template"] == TEMPLATE_ACTION
    assert psychological["layout_geometry"]["template"] == TEMPLATE_PSYCHOLOGICAL
    assert _rows(drama) == [1, 2, 2, 1]
    assert _rows(conversation) == [2, 2, 1]
    assert _rows(action) == [1, 3, 2]
    assert _rows(psychological) == [1, 2, 1]


def test_geometry_respects_japanese_and_english_reading_order() -> None:
    japanese = ensure_page_layout(_page("conversation", 4), {"language": "ja"})
    english = ensure_page_layout(_page("conversation", 4), {"language": "en"})
    ja = japanese["layout_geometry"]["panels"]
    en = english["layout_geometry"]["panels"]
    assert ja[0]["x"] > ja[1]["x"]
    assert ja[2]["x"] > ja[3]["x"]
    assert en[0]["x"] < en[1]["x"]
    assert en[2]["x"] < en[3]["x"]


def test_text_layout_prevents_bubble_and_narration_collision() -> None:
    page = _page("conversation", 4)
    page["panels"][0]["dialogue"] = ["最初の発言", "次の発言", "最後の発言"]
    page["panels"][0]["narration"] = ["同じコマのナレーション"]
    prepared = ensure_page_layout(page, {"language": "ja"})
    items = prepared["panels"][0]["text_layout"]["items"]
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            assert not _intersects(left, right)
    assert not [issue for issue in page_layout_issues(prepared, {"language": "ja"}) if "collision" in issue["key"]]


def test_text_safe_margin_and_language_order() -> None:
    japanese = ensure_page_layout(_page("conversation", 4), {"language": "ja"})
    english = ensure_page_layout(_page("conversation", 4), {"language": "en"})
    ja_items = [item for item in japanese["panels"][0]["text_layout"]["items"] if item["type"] == "bubble"]
    en_items = [item for item in english["panels"][0]["text_layout"]["items"] if item["type"] == "bubble"]
    assert ja_items[0]["side"] == "right"
    assert en_items[0]["side"] == "left"
    for prepared in (japanese, english):
        for panel in prepared["panels"]:
            for item in panel["text_layout"]["items"]:
                assert item["x"] >= 0.06
                assert item["y"] >= 0.06
                assert item["x"] + item["width"] <= 0.94
                assert item["y"] + item["height"] <= 0.94


def test_long_text_wraps_without_overlap() -> None:
    prepared = ensure_page_layout(_page("conversation", 4, long_text=True), {"language": "ja"})
    items = prepared["panels"][0]["text_layout"]["items"]
    bubble = next(item for item in items if item["type"] == "bubble")
    assert bubble["line_count"] > 1
    assert bubble["height"] > 0.31
    assert not page_layout_issues(prepared, {"language": "ja"})


def test_layout_qa_detects_text_overflow() -> None:
    prepared = ensure_page_layout(_page("conversation", 4), {"language": "ja"})
    prepared["panels"][0]["text_layout"]["items"][0]["overflow"] = True
    keys = {issue["key"] for issue in page_layout_issues(prepared, {"language": "ja"})}
    assert any(key.startswith("text-overflow-") for key in keys)


def test_preview_and_export_use_same_persisted_geometry() -> None:
    prepared = ensure_page_layout(_page("drama", 6, important=5), {"language": "ja"})
    boxes = _page_panel_boxes(prepared, 595, 842, {"language": "ja"})
    geometries = prepared["layout_geometry"]["panels"]
    largest_geometry = max(range(len(geometries)), key=lambda index: geometries[index]["area"])
    largest_export = max(range(len(boxes)), key=lambda index: boxes[index][2] * boxes[index][3])
    assert largest_geometry == largest_export
    assert prepared["layout_version"] == 2


def test_reload_preserves_layout_signature() -> None:
    first = ensure_page_layout(_page("drama", 6), {"language": "ja"})
    second = ensure_page_layout(first, {"language": "ja"})
    assert second["layout_geometry"] == first["layout_geometry"]
    assert [panel["text_layout"] for panel in second["panels"]] == [panel["text_layout"] for panel in first["panels"]]


def test_layout_qa_detects_uniform_tiles_and_collision() -> None:
    prepared = ensure_page_layout(_page("conversation", 4), {"language": "ja"})
    for geometry in prepared["layout_geometry"]["panels"]:
        geometry["area"] = 0.2
    items = prepared["panels"][0]["text_layout"]["items"]
    items.append({**items[0], "id": "bubble-overlap", "order": 2})
    keys = {issue["key"] for issue in page_layout_issues(prepared, {"language": "ja"})}
    assert any(key.startswith("layout-uniform-") for key in keys)
    assert any(key.startswith("text-collision-") for key in keys)


def test_auto_repair_changes_only_target_page_and_preserves_artwork() -> None:
    first = ensure_page_layout(_page("drama", 6), {"language": "ja"})
    second = ensure_page_layout({**_page("conversation", 5), "id": "page-2", "page_number": 2}, {"language": "ja"})
    first["panels"][0]["image_url"] = "/static/assets/existing.png"
    first["layout_geometry"]["signature"] = "stale"
    repaired = repair_storyboard_page([first, second], first["id"], {"language": "ja"})
    assert repaired[0]["layout_geometry"]["signature"] != "stale"
    assert repaired[0]["panels"][0]["image_url"] == "/static/assets/existing.png"
    assert repaired[1] == second


def test_zip_contains_composed_page_and_geometry_manifest(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    page = ensure_page_layout(_page("drama", 6), {"language": "ja"})
    project = {"id": "project-layout", "title": "Layout", "settings": {"language": "ja"}, "storyboard": [page]}
    content = export_zip(project, storage)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert "pages/page-001.png" in archive.namelist()
        manifest = json.loads(archive.read("project.json"))
        assert manifest["storyboard"][0]["layout_geometry"] == page["layout_geometry"]


def test_pdf_embeds_portable_japanese_font(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    page = ensure_page_layout(_page("drama", 6), {"language": "ja"})
    project = {"id": "project-pdf", "title": "日本語漫画", "settings": {"language": "ja"}, "storyboard": [page]}
    content = export_pdf(project, storage)
    assert content.startswith(b"%PDF")
    assert PDF_FONT == "MPlus1p"
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    assert "日本語漫画" in extracted
    assert "セリフ1" in extracted


def test_page_repair_api_is_page_scoped_and_persistent(tmp_path: Path) -> None:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "layout@example.com", "password": "long-password"}, follow_redirects=False)
    created = client.post("/api/projects", data={"title": "配置", "story_text": "短い物語"}).json()["project"]
    page_one = _page("drama", 6)
    page_two = {**_page("conversation", 5), "id": "page-2", "page_number": 2}
    saved = client.patch(f"/api/projects/{created['id']}", json={"storyboard": [page_one, page_two]})
    assert saved.status_code == 200
    before = saved.json()["project"]
    panel = before["storyboard"][0]["panels"][0]
    panel["image_url"] = "/static/assets/existing.png"
    client.patch(f"/api/projects/{created['id']}", json={"storyboard": before["storyboard"]})
    response = client.post(f"/api/projects/{created['id']}/pages/{page_one['id']}/layout/repair", json={})
    assert response.status_code == 200
    project = response.json()["project"]
    assert project["storyboard"][0]["panels"][0]["image_url"] == "/static/assets/existing.png"
    reloaded = client.get(f"/api/projects/{created['id']}").json()["project"]
    assert reloaded["storyboard"][0]["layout_geometry"] == project["storyboard"][0]["layout_geometry"]
