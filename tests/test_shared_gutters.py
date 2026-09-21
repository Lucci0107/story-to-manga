"""段全体の共有境界・旧版互換・実際の書き出しを検証する。"""

from copy import deepcopy
import io
import zipfile
import pytest
from PIL import Image
from pypdf import PdfReader
from app.services.dynamic_layout import (
    geometry_metrics,
    normalize_legacy_gutters,
    polygons_overlap,
)
from app.services.composition import composition_quality_issues, shape_points
from app.services.export import render_page_png, export_pdf, export_zip
from app.services.storage import LocalFileStorage
from app.schemas import normalize_composition
from scripts.shared_gutter_fixture import three_panel_row
from scripts.dynamic_layout_fixture import SETTINGS


def test_three_panel_row_canonical_boundaries_and_straight_perimeter():
    page = three_panel_row()
    c = page["composition"]
    edges = c["shared_edges"]
    assert len(edges) == 2
    assert edges[0]["angle"] == edges[1]["angle"] != 0
    assert abs(edges[0]["angle"]) <= 12
    assert edges[0]["angle_family"] == edges[1]["angle_family"]
    assert len({g["base_box"]["width"] for g in c["panels"]}) == 3
    assert all(g["shape"] == "trapezoid" for g in c["panels"])
    for edge in edges:
        assert all(
            edge["id"] in g["shared_edge_ids"]
            for g in c["panels"]
            if g["panel_id"] in edge["panel_ids"]
        )
    metrics = geometry_metrics(c)
    for key, value in metrics.items():
        assert value == (2 if key == "meaningful_dynamic_boundary_count" else 0), (
            key,
            value,
        )
    assert not composition_quality_issues(page)
    assert all(
        not polygons_overlap(a["polygon_points"], b["polygon_points"])
        for i, a in enumerate(c["panels"])
        for b in c["panels"][i + 1 :]
    )
    assert normalize_composition(c)["shared_edges"] == edges


@pytest.mark.parametrize("damage", ["wedge", "missing", "angle", "overlap"])
def test_bad_shared_gutters_are_detected(damage):
    c = three_panel_row()["composition"]
    if damage == "missing":
        c["shared_edges"].pop()
    elif damage == "angle":
        c["shared_edges"][1]["center_line"][0][0] += 0.01
    else:
        c["panels"][1]["polygon_points"][1][0] += 0.04 if damage == "overlap" else -0.04
    m = geometry_metrics(c)
    assert m["shared_gutter_mismatch_count"] > 0
    if damage == "overlap":
        assert m["gutter_overlap_count"] > 0
    if damage == "angle":
        assert m["adjacent_edge_angle_delta"] > 0


def test_legacy_wedges_use_shared_geometry_without_mutating_artwork():
    c = three_panel_row()["composition"]
    c.pop("shared_gutter_version")
    c["shared_edges"] = []
    for g in c["panels"]:
        g.update(g.pop("base_box"))
        g["polygon_points"] = shape_points(g, "trapezoid")
        g["image_url"] = "/media/existing.png"
    original = deepcopy(c)
    fixed = normalize_legacy_gutters(c)
    assert c == original
    assert len(fixed["shared_edges"]) == 2
    assert geometry_metrics(fixed)["shared_gutter_mismatch_count"] == 0
    assert all(g["image_url"] == "/media/existing.png" for g in fixed["panels"])


def test_three_panel_preview_pdf_zip_parity(tmp_path):
    page = three_panel_row()
    project = dict(
        id="gutter", title="共有ガター", settings=SETTINGS, storyboard=[page]
    )
    storage = LocalFileStorage(tmp_path)
    png = render_page_png(project, page, storage)
    archive = zipfile.ZipFile(io.BytesIO(export_zip(project, storage)))
    assert (
        archive.read(next(n for n in archive.namelist() if n.endswith(".png"))) == png
    )
    pdf = PdfReader(io.BytesIO(export_pdf(project, storage)))
    assert (
        pdf.pages[0].images[0].image.convert("RGB").tobytes()
        == Image.open(io.BytesIO(png)).convert("RGB").tobytes()
    )


def test_band_fit_keeps_both_edges_when_artwork_requires_straight_gutters():
    from app.services.layout import _fit_shared_boundaries

    page = three_panel_row()
    c = page["composition"]
    c["panels"][0]["artwork_viewport"] = dict(x=0, y=0, width=1, height=1)
    for source in page["panels"]:
        source["image_url"] = "/media/preserved.png"
    c["shared_edges"] = _fit_shared_boundaries(
        c["panels"], page["panels"], c["shared_edges"], SETTINGS
    )
    assert len(c["shared_edges"]) == 2
    assert all(e["angle"] == 0 for e in c["shared_edges"])
    assert geometry_metrics(c)["shared_gutter_mismatch_count"] == 0
    assert all(p["image_url"] == "/media/preserved.png" for p in page["panels"])


def test_partial_t_junction_has_canonical_references_and_straight_spacing():
    from app.services.dynamic_layout import apply_shared_geometry

    boxes = {
        0: dict(panel_id="a", x=0.025, y=0.02, width=0.45, height=0.9),
        1: dict(panel_id="b", x=0.489, y=0.02, width=0.486, height=0.4),
        2: dict(panel_id="c", x=0.489, y=0.434, width=0.486, height=0.486),
    }
    geometries, edges = apply_shared_geometry(
        boxes, [{}, {}, {}], "conversation-asymmetric", "ja", 1, 0.014
    )
    c = dict(
        panels=list(geometries.values()), shared_edges=edges, shared_gutter_version=2
    )
    assert len(edges) == 3
    assert sum(not e["full_span"] for e in edges) == 2
    assert geometry_metrics(c)["shared_gutter_mismatch_count"] == 0
    assert all(len(g["shared_edge_ids"]) == 2 for g in geometries.values())
