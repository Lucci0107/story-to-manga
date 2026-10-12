"""添付仕様との一致、全枠の境界、人物データに依存しない組版を検証する。"""

from copy import deepcopy
import hashlib
from itertools import combinations
from pathlib import Path
import re
from xml.etree import ElementTree

import pytest

from app.services.character_sheet import fixed_sheet_layout, render_sheet_svg, source_metadata
from app.services.manga_documents import character_sheet_design, render_sheet_markdown

ROOT = Path(__file__).resolve().parents[1]


def intersects(a, b):
    return (min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]) > 1e-8
            and min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]) > 1e-8)


def inside(inner, outer):
    assert inner["width"] > 0 and inner["height"] > 0
    for axis, length in (("x", "width"), ("y", "height")):
        assert inner[axis] >= outer[axis] - 1e-8
        assert inner[axis] + inner[length] <= outer[axis] + outer[length] + 1e-8


@pytest.mark.parametrize("ratio", ["3:4", "4:3", "3:2"])
def test_layout_matches_reference_tables_and_every_cell_fits(ratio):
    source = (ROOT / "docs/knowledge/character-sheet/1.0.0/knowledge.md").read_text()
    layout = fixed_sheet_layout(ratio)
    chapter = source.split("### " + layout["id"])[1].split("###")[0]
    expected = re.findall(r"\| ([A-I]) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|", chapter)
    assert len(expected) == 9
    assert [s["id"] for s in layout["sections"]] == list("ABCDEFGHI")
    for section, row in zip(layout["sections"], expected):
        assert section["nominal_rect"] == dict(zip(("x", "y", "width", "height"), map(int, row[1:])))
        inside(section["rect"], layout["body"])
    assert sum(s["nominal_rect"]["width"] * s["nominal_rect"]["height"] for s in layout["sections"]) == 10000
    for a, b in combinations(layout["sections"], 2):
        assert not intersects(a["nominal_rect"], b["nominal_rect"])
        assert not intersects(a["rect"], b["rect"])
    grids = re.findall(r"\| [A-I] [^|]+ \| (\d+) \| (\d+)×(\d+) \| (\d+)×(\d+) \| (\d+)×(\d+) \|", source)
    groups = [g for s in layout["sections"] for g in s["groups"]]
    offset = ("3:4", "4:3", "3:2").index(ratio) * 2 + 1
    assert len(groups) == len(grids) == 10
    for group, row in zip(groups, grids):
        assert (len(group["slots"]), group["columns"], group["rows"]) == tuple(map(int, (row[0], row[offset], row[offset + 1])))
        positions = [(s["rect"]["y"], s["rect"]["x"]) for s in group["slots"]]
        assert positions == sorted(positions)
    all_slots = []
    for section in layout["sections"]:
        for group in section["groups"]:
            for slot in group["slots"]:
                inside(slot["rect"], section["rect"])
                assert slot["rect"]["y"] >= section["rect"]["y"] + layout["title_height"]
                all_slots.append(slot)
    assert len({s["id"] for s in all_slots}) == 51
    for a, b in combinations(all_slots, 2):
        assert not intersects(a["rect"], b["rect"])
    colors, materials = layout["sections"][6]["groups"]
    color_height = colors["slots"][0]["rect"]["height"] * colors["rows"]
    material_height = materials["slots"][0]["rect"]["height"] * materials["rows"]
    assert color_height / (color_height + material_height) == pytest.approx(.4)


@pytest.mark.parametrize("ratio", ["3:4", "4:3", "3:2"])
def test_character_style_unknown_fields_and_long_text_never_move_slots(ratio):
    character = {"id": "a", "name": "未設定の人物", "sheet_ratio": ratio}
    project = {"characters": [character], "settings": {"color_mode": "bw"}}
    initial = character_sheet_design(project, character)
    changed = deepcopy(project)
    changed["characters"][0].update(name="別の人物" * 20, appearance="白い服" * 500, height="未設定", palette_notes="色は未確定")
    changed["settings"].update(color_mode="color", rendering_style_id="MASTER:STYLE-047/PRESET-26")
    updated = character_sheet_design(changed, changed["characters"][0])
    assert initial["fixed_layout"] == updated["fixed_layout"]
    assert initial["character"] != updated["character"]
    for design in (initial, updated):
        svg = ElementTree.fromstring(render_sheet_svg(design))
        rects = [node for node in svg.iter() if "data-slot" in node.attrib]
        assert len(rects) == 51
        by_id = {r.attrib["data-slot"]: r.attrib for r in rects}
        for section in design["sections"]:
            for group in section["groups"]:
                for slot in group["slots"]:
                    for key, value in slot["rect"].items():
                        assert float(by_id[slot["id"]][key]) == pytest.approx(value, abs=.0001)
        markdown = render_sheet_markdown(design, 1)
        assert markdown.count("| G-C") == 6 and markdown.count("| G-M") == 8
        assert "未確定" in markdown and "Approx." in markdown


def test_reference_versions_hashes_svg_escape_and_invalid_ratio():
    refs = source_metadata()
    assert len(refs) == 2
    for ref in refs:
        assert ref["version"] == "1.0.0"
        assert hashlib.sha256((ROOT / ref["path"]).read_bytes()).hexdigest() == ref["sha256"]
    with pytest.raises(ValueError, match="比率"):
        fixed_sheet_layout("16:9")
    character = {"id": "a", "name": '<script>alert("x")</script>'}
    svg = render_sheet_svg(character_sheet_design({"characters": [character]}, character))
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    ElementTree.fromstring(svg)
