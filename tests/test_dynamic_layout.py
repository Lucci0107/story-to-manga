"""外周固定・共有境界・書き出し一致の回帰検証。画像APIは使わない。"""

from copy import deepcopy
import io
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from app.services.dynamic_layout import (
    lock_outer_edges,
    shared_diagonal,
    geometry_metrics,
    polygons_overlap,
    rectangle,
)
from app.services.composition import (
    composition_quality_issues,
    composition_quality_score,
    polygon_area,
    composition_for_page,
)
from app.services.layout import reflow_page, ensure_page_layout, repair_storyboard_page
from app.services.export import (
    render_page_png,
    export_pdf,
    export_zip,
    prepare_page_for_render,
)
from app.services.storage import LocalFileStorage
from app.services.panel_direction import direction_is_ready
from app.schemas import normalize_composition
from scripts.dynamic_layout_fixture import fixture_pages, SETTINGS


@pytest.mark.parametrize(
    "side,indices,axis",
    [
        ("left", (0, 3), 0),
        ("right", (1, 2), 0),
        ("top", (0, 1), 1),
        ("bottom", (2, 3), 1),
    ],
)
def test_outer_edge_lock_preserves_internal_angles(side, indices, axis):
    safe = dict(left=0.025, right=0.975, top=0.02, bottom=0.94)
    box = dict(x=0.2, y=0.2, width=0.3, height=0.3)
    box[
        "x"
        if side == "left"
        else "y"
        if side == "top"
        else "width"
        if side == "right"
        else "height"
    ] = safe[side] if side in ("left", "top") else safe[side] - 0.2
    points = rectangle(box)
    points[0][0] += 0.025
    points[1][1] += 0.025
    points[2][0] -= 0.025
    points[3][1] -= 0.025
    result = lock_outer_edges(points, box, safe)
    assert all(result[i][axis] == safe[side] for i in indices)
    assert result != rectangle(box)
    assert points != result


@pytest.mark.parametrize("axis", ["vertical", "horizontal"])
@pytest.mark.parametrize("amount", [-0.03, 0.03])
def test_canonical_gutter_has_constant_width_and_no_overlap(axis, amount):
    a = dict(panel_id="a", x=0.025, y=0.02, width=0.45, height=0.44)
    b = (
        dict(panel_id="b", x=0.489, y=0.02, width=0.486, height=0.44)
        if axis == "vertical"
        else dict(panel_id="b", x=0.025, y=0.474, width=0.45, height=0.466)
    )
    ap, bp, edge = shared_diagonal(a, b, axis, amount, 0.014, "shared")
    c = dict(
        panels=[dict(a, polygon_points=ap), dict(b, polygon_points=bp)],
        shared_edges=[edge],
        outer_bounds=dict(left=0.025, right=0.975, top=0.02, bottom=0.94),
    )
    assert not polygons_overlap(ap, bp)
    assert geometry_metrics(c)["gutter_consistency_error"] == 0
    assert geometry_metrics(c)["meaningful_dynamic_boundary_count"] == 1
    assert polygon_area(ap) > 0 and polygon_area(bp) > 0


@pytest.mark.parametrize("index", range(6))
def test_six_semantic_pages_are_safe_and_keep_direction_ready(index):
    page = fixture_pages()[index]
    assert not composition_quality_issues(page)
    score = composition_quality_score(page)
    for key in (
        "outer_edge_slant_count",
        "boundary_alignment_error",
        "gutter_consistency_error",
        "panel_overlap_count",
        "face_balloon_overlap_count",
        "text_overflow_count",
        "ellipse_mask_count",
        "invalid_crop_count",
        "extreme_sliver_panel_count",
    ):
        assert score[key] == 0, (key, score)
    assert all(direction_is_ready(p, SETTINGS) for p in page["panels"])
    assert prepare_page_for_render(page, SETTINGS)["composition"] == page["composition"]
    assert max(p["row"] for p in page["composition"]["panels"]) <= 5


def test_manga_variation_and_dominant_area():
    pages = fixture_pages()
    assert len({p["composition"]["family"] for p in pages}) >= 4
    assert (
        sum(
            composition_quality_score(p)["meaningful_dynamic_boundary_count"]
            for p in pages
        )
        >= 3
    )
    for page in pages:
        areas = [
            polygon_area(g["polygon_points"]) for g in page["composition"]["panels"]
        ]
        assert max(areas) / min(areas) > 1.2
        dominant = next(
            (g for g in page["composition"]["panels"] if g["dominant"]), None
        )
        if dominant:
            assert 0.30 <= polygon_area(dominant["polygon_points"]) / sum(areas) <= 0.55


@pytest.mark.parametrize("language", ["ja", "en"])
def test_reading_order_for_asymmetric_and_vertical_pages(language):
    for page in fixture_pages():
        result = reflow_page(page, dict(SETTINGS, language=language))
        assert not any(
            "reading-order" in i["key"] for i in composition_quality_issues(result)
        )
        assert [p["id"] for p in result["panels"]] == [p["id"] for p in page["panels"]]


def test_long_dialogue_prefers_regular_geometry():
    page = fixture_pages()[0]
    for panel in page["panels"]:
        panel["dialogue"] = ["言葉を確かめて、落ち着いて進めよう。" * 6]
    result = reflow_page(page, SETTINGS)
    assert not result["composition"]["shared_edges"]
    assert all(g["shape"] == "rectangle" for g in result["composition"]["panels"])


def test_psychological_does_not_force_diagonal():
    assert fixture_pages()[2]["composition"]["shared_edges"] == []


def test_explicit_full_bleed_requires_climax_reason_and_one_panel():
    page = fixture_pages()[3]
    page["panels"] = page["panels"][-1:]
    page["panels"][0].update(
        order=1, panel_shape="large-bleed", shape_reason="決着を大きく見せる"
    )
    page["special_emphasis"] = True
    result = reflow_page(page, SETTINGS)
    panel = result["composition"]["panels"][0]
    assert panel["full_bleed_effect"] and panel["x"] == 0 and panel["width"] == 1
    page["special_emphasis"] = False
    assert reflow_page(page, SETTINGS)["composition"]["panels"][0]["x"] > 0.0


def test_saved_legacy_is_not_upgraded_or_regenerated():
    page = reflow_page(
        dict(fixture_pages()[0], composition_version=3),
        dict(SETTINGS, composition_version=3),
    )
    page["panels"][0]["image_url"] = "/media/existing.png"
    original = deepcopy(page)
    assert ensure_page_layout(page, dict(SETTINGS, composition_version=3)) == original
    upgraded = repair_storyboard_page(
        [page], page["id"], SETTINGS, composition_version=4
    )[0]
    assert upgraded["composition_version"] == 4
    assert upgraded["panels"][0]["image_url"] == "/media/existing.png"
    assert page == original


def test_legacy_outer_slant_normalizes_without_mutation():
    page = reflow_page(
        dict(fixture_pages()[1], composition_version=3),
        dict(SETTINGS, composition_version=3),
    )
    original = deepcopy(page)
    render = composition_for_page(page)
    left = min(g["x"] for g in render["panels"])
    for g in render["panels"]:
        if g["x"] == left:
            assert g["polygon_points"][0][0] == left == g["polygon_points"][3][0]
    assert page == original


def test_v4_schema_retains_shared_edges_and_outer_bounds():
    composition = fixture_pages()[0]["composition"]
    result = normalize_composition(composition)
    assert result["composition_version"] == 4
    assert result["shared_edges"] == composition["shared_edges"]
    assert result["outer_bounds"] == composition["outer_bounds"]
    assert geometry_metrics(result) == geometry_metrics(composition)


def test_boundary_damage_is_reported():
    page = fixture_pages()[0]
    page["composition"]["panels"][0]["polygon_points"][1][0] -= 0.04
    assert any("boundary" in i["key"] for i in composition_quality_issues(page))


def test_preview_pdf_zip_use_identical_page_raster(tmp_path):
    pages = fixture_pages()
    project = dict(
        id="dynamic", title="変形コマ割り", settings=SETTINGS, storyboard=pages
    )
    storage = LocalFileStorage(tmp_path)
    archive = zipfile.ZipFile(io.BytesIO(export_zip(project, storage)))
    pdf = PdfReader(io.BytesIO(export_pdf(project, storage)))
    assert len(pdf.pages) == 6
    png_names = sorted(n for n in archive.namelist() if n.endswith(".png"))
    assert len(png_names) == 6
    for index, page in enumerate(pages):
        preview = render_page_png(project, page, storage)
        assert archive.read(png_names[index]) == preview
        expected = Image.open(io.BytesIO(preview)).convert("RGB")
        embedded = pdf.pages[index].images[0].image.convert("RGB")
        assert embedded.size == expected.size == (900, 1200)
        assert embedded.tobytes() == expected.tobytes()


@pytest.mark.parametrize("language", ["ja", "en"])
def test_three_to_eight_panels_keep_safe_reading_and_margins(language):
    for count in range(3, 9):
        for number in range(1, 4):
            page = dict(
                page_number=number,
                panels=[
                    dict(
                        id=str(i),
                        order=i + 1,
                        characters=["A"],
                        dialogue=["確認"],
                        importance="medium",
                    )
                    for i in range(count)
                ],
            )
            result = reflow_page(page, dict(SETTINGS, language=language))
            assert not composition_quality_issues(result)


def test_geometry_audit_blocks_broken_shared_edge():
    from app.services.generation_design import audit_design

    page = fixture_pages()[0]
    page["composition"]["shared_edges"][0]["width"] *= 2
    assert any(
        "ガター" in error
        for error in audit_design({"settings": SETTINGS}, page, page["panels"][0])
    )


def test_polygon_protects_face_and_text_after_manual_edit():
    page = fixture_pages()[0]
    g = page["composition"]["panels"][0]
    g["polygon_points"][3][0] += g["width"] * 0.8
    score = composition_quality_score(page)
    assert score["polygon_protected_clip_count"] > 0
    assert any("boundary" in issue["key"] for issue in composition_quality_issues(page))


def test_explicit_four_panel_grid_is_retained():
    page = fixture_pages()[0]
    page.update(layout="four_panel", panels=page["panels"][:4])
    result = reflow_page(page, SETTINGS)
    assert result["composition"]["shared_edges"] == []
    areas = [polygon_area(g["polygon_points"]) for g in result["composition"]["panels"]]
    assert max(areas) / min(areas) < 1.001


def test_layout_reflow_does_not_call_image_service(monkeypatch):
    from app.services import artwork

    def forbidden(*args, **kwargs):
        raise AssertionError("レイアウト計算が画像APIを呼びました")

    monkeypatch.setattr(artwork, "save_openai_image", forbidden)
    monkeypatch.setattr(artwork, "save_panel_artwork", forbidden)
    for page in fixture_pages():
        assert reflow_page(page, SETTINGS)["composition_version"] == 4


def test_invalid_geometry_overlap_is_detected():
    page = fixture_pages()[0]
    page["composition"]["panels"][1] = dict(
        page["composition"]["panels"][0],
        panel_id=page["panels"][1]["id"],
        reading_order=2,
    )
    assert composition_quality_score(page)["panel_overlap_count"] > 0


def test_unedited_v4_layout_is_deterministic():
    page = fixture_pages()[0]
    assert reflow_page(page, SETTINGS)["composition"] == page["composition"]
    assert ensure_page_layout(page, SETTINGS) == page
