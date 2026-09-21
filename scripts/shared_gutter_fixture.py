"""共有ガターの3コマ段を、画像APIなしで再現する。"""

from copy import deepcopy
from app.services.dynamic_layout import apply_shared_geometry
from app.services.layout import reflow_page
from scripts.dynamic_layout_fixture import SETTINGS


def three_panel_row():
    panels = [
        dict(
            id=f"gutter-{i}",
            order=i + 1,
            dialogue=[],
            narration=[],
            sfx=[],
            importance="medium",
        )
        for i in range(3)
    ]
    page = reflow_page(
        dict(id="shared-gutter-row", page_number=1, panels=panels), SETTINGS
    )
    boxes = {
        2: dict(panel_id="gutter-2", x=0.025, y=0.02, width=0.38, height=0.35),
        1: dict(panel_id="gutter-1", x=0.419, y=0.02, width=0.25, height=0.35),
        0: dict(panel_id="gutter-0", x=0.683, y=0.02, width=0.292, height=0.35),
    }
    geometries, edges = apply_shared_geometry(
        boxes, panels, "conversation-asymmetric", "ja", 1, 0.014
    )
    composition = deepcopy(page["composition"])
    composition.update(
        shared_edges=edges,
        shared_gutter_version=2,
        outer_bounds=dict(left=0.025, right=0.975, top=0.02, bottom=0.37),
    )
    for g in composition["panels"]:
        index = int(g["panel_id"].split("-")[-1])
        g.update(geometries[index], row=1, protected_zones=[], text_safe_zones={})
    composition["effect_budget"]["angled_panels"] = 3
    for panel in page["panels"]:
        panel.pop("panel_direction", None)
    page["composition"] = composition
    return page


def export_fixture(destination):
    import json
    from pathlib import Path
    from app.services.export import render_page_png, export_pdf, export_zip
    from app.services.storage import LocalFileStorage
    from app.services.dynamic_layout import geometry_metrics

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    page = three_panel_row()
    project = dict(
        id="shared-gutter-qa",
        title="共有ガター検証",
        settings=SETTINGS,
        storyboard=[page],
    )
    storage = LocalFileStorage(root / "media")
    (root / "three-panel-row.png").write_bytes(render_page_png(project, page, storage))
    (root / "three-panel-row.pdf").write_bytes(export_pdf(project, storage))
    (root / "three-panel-row.zip").write_bytes(export_zip(project, storage))
    (root / "report.json").write_text(
        json.dumps(geometry_metrics(page["composition"]), indent=2)
    )
    (root / "page.json").write_text(json.dumps(page, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys

    export_fixture(sys.argv[1])
