"""変形コマ割りを画像APIなしで再現する六ページ。"""

from __future__ import annotations

SETTINGS = {
    "language": "ja",
    "composition_version": 4,
    "visual_style": "cinematic",
    "color_mode": "color",
}


def fixture_pages():
    from app.services.layout import reflow_page

    cases = [
        ("dialogue", 5, None),
        ("action", 5, 0),
        ("psychological", 3, None),
        ("climax", 5, 4),
        ("establishing", 4, 0),
        ("dialogue", 6, None),
    ]
    pages = []
    for number, (family, count, dominant) in enumerate(cases, 1):
        panels = []
        for i in range(count):
            panels.append(
                dict(
                    id=f"dynamic-{number}-{i}",
                    order=i + 1,
                    description="人物が状況を確認する",
                    panel_role="会話",
                    scene_type=family,
                    characters=["旅人"],
                    dialogue=[
                        "ここから始めよう。" if i == dominant else "確認したよ。"
                    ],
                    narration=[],
                    sfx=[],
                    importance="critical" if i == dominant else "medium",
                    image_url=None,
                    generation_status="not_started",
                )
            )
        page = dict(
            id=f"dynamic-page-{number}",
            page_number=number,
            title="",
            scene_type=family,
            page_role=family,
            layout="psychological" if family == "psychological" else "classic",
            climax=family == "climax",
            panels=panels,
        )
        if number == 6:
            page["layout_family"] = "vertical-anchor"
        pages.append(reflow_page(page, SETTINGS))
    return pages


def export_fixtures(destination):
    """色面・保護領域入りの設計図をPNG/PDF/ZIPで出力する。"""
    from pathlib import Path
    import json
    from app.services.composition import (
        composition_quality_score,
        composition_quality_issues,
    )
    from app.services.export import render_page_png, export_pdf, export_zip
    from app.services.storage import LocalFileStorage

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    storage = LocalFileStorage(root / "media")
    pages = fixture_pages()
    project = dict(
        id="dynamic-layout-qa",
        title="変形コマ割り検証",
        settings=SETTINGS,
        storyboard=pages,
    )
    for page in pages:
        (root / f"page-{page['page_number']}.png").write_bytes(
            render_page_png(project, page, storage)
        )
    (root / "pages.pdf").write_bytes(export_pdf(project, storage))
    (root / "pages.zip").write_bytes(export_zip(project, storage))
    report = [
        {
            "page": p["page_number"],
            "family": p["composition"]["family"],
            "score": composition_quality_score(p),
            "issues": composition_quality_issues(p),
        }
        for p in pages
    ]
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    import sys
    import json

    print(json.dumps(export_fixtures(sys.argv[1]), ensure_ascii=False, indent=2))
