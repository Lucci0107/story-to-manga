"""版面の版付き仕様。旧作品の3:4と、新規作品のA4を分けて扱う。"""

from __future__ import annotations

from typing import Mapping

A4_COMPOSITION_VERSION = 5
LEGACY_PAGE_SIZE = (900, 1200)
A4_PAGE_SIZE = (900, 1273)
A4_EXPORT_SIZE = (2480, 3508)


def uses_a4(page: Mapping) -> bool:
    composition = page.get("composition") or {}
    return int(composition.get("composition_version") or page.get("composition_version") or 0) >= A4_COMPOSITION_VERSION


def page_size(page: Mapping) -> tuple[int, int]:
    return A4_PAGE_SIZE if uses_a4(page) else LEGACY_PAGE_SIZE


def geometry_page_size(geometry: Mapping) -> tuple[int, int]:
    return A4_PAGE_SIZE if geometry.get("page_size") == list(A4_PAGE_SIZE) else LEGACY_PAGE_SIZE


def canvas_contract(page: Mapping) -> dict:
    cover = page.get("page_kind") in {"cover", "back_cover"}
    return {
        "revision": "1.0.0", "format": "A4", "orientation": "portrait",
        "physical_mm": [210, 297], "ratio": "70:99", "layout_size": list(A4_PAGE_SIZE),
        "export_size": list(A4_EXPORT_SIZE), "target_dpi": 300,
        "resolution_note": "組版の出力寸法。原画のネイティブ解像度や印刷品質を保証しません。",
        "margin_contract": {
            "basis": "canvas_width_and_height", "target": "cover" if cover else "body",
            "blank_outer_margin_percent": 0 if cover else 3,
            "critical_safe_inset_percent": 5 if cover else 3,
            "background_to_edge_allowed": cover, "additive": False,
        },
        "frame_style": {"type": "single", "width_ratio": .00125, "max_width_ratio": .002,
                        "scope": "panel_frames_only"},
    }


def page_number_design(page: Mapping) -> dict:
    required = page.get("page_kind") not in {"cover", "back_cover"}
    return {"required": required, "text": str(page.get("page_number", "")) if required else "",
            "role": "page_number", "occurrences": 1 if required else 0,
            "x": .45, "y": .94, "width": .10, "height": .03,
            "center": [.5, .955], "font_height_ratio": .01,
            "renderer": "application", "revision": "1.0.0"}
