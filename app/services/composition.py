"""漫画ページを一枚のCompositionとして扱うための決定的なモデル。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence


COMPOSITION_VERSION = 2
LEGACY_COMPOSITION_VERSION = 1
PAGE_SIZE = (900, 1200)
PAGE_SAFE_MARGIN = 0.04
ARTWORK_COVERAGE_TARGET = 0.95


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round(value: float) -> float:
    return round(float(value), 5)


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_polygon(points: Any, fallback: Mapping[str, Any] | None = None) -> List[List[float]]:
    """任意入力をページ相対の安全なpolygonへそろえる。"""

    normalized: List[List[float]] = []
    if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
        for point in points[:12]:
            if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) < 2:
                continue
            normalized.append([_round(_clamp(_float(point[0]), 0.0, 1.0)), _round(_clamp(_float(point[1]), 0.0, 1.0))])
    if len(normalized) >= 3:
        return normalized
    box = fallback or {}
    x = _clamp(_float(box.get("x"), 0.0), 0.0, 1.0)
    y = _clamp(_float(box.get("y"), 0.0), 0.0, 1.0)
    width = _clamp(_float(box.get("width"), 1.0), 0.001, 1.0 - x)
    height = _clamp(_float(box.get("height"), 1.0), 0.001, 1.0 - y)
    return [
        [_round(x), _round(y)],
        [_round(x + width), _round(y)],
        [_round(x + width), _round(y + height)],
        [_round(x), _round(y + height)],
    ]


def polygon_bounding_box(points: Any) -> Dict[str, float]:
    normalized = normalize_polygon(points)
    xs = [point[0] for point in normalized]
    ys = [point[1] for point in normalized]
    if not xs or not ys:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return {
        "x": _round(left),
        "y": _round(top),
        "width": _round(max(0.0, right - left)),
        "height": _round(max(0.0, bottom - top)),
    }


def polygon_area(points: Any) -> float:
    normalized = normalize_polygon(points)
    if len(normalized) < 3:
        return 0.0
    return abs(
        sum(
            normalized[index][0] * normalized[(index + 1) % len(normalized)][1]
            - normalized[(index + 1) % len(normalized)][0] * normalized[index][1]
            for index in range(len(normalized))
        )
    ) / 2


def _rectangle_points(box: Mapping[str, Any]) -> List[List[float]]:
    x, y = _float(box.get("x")), _float(box.get("y"))
    width, height = _float(box.get("width")), _float(box.get("height"))
    return [
        [_round(x), _round(y)],
        [_round(x + width), _round(y)],
        [_round(x + width), _round(y + height)],
        [_round(x), _round(y + height)],
    ]


def shape_points(box: Mapping[str, Any], shape: str = "rectangle") -> List[List[float]]:
    """矩形・台形・斜めコマの頂点を生成する。"""

    x, y = _float(box.get("x")), _float(box.get("y"))
    width, height = _float(box.get("width")), _float(box.get("height"))
    skew = min(width * 0.13, 0.035)
    if shape in {"trapezoid", "slanted-left"}:
        return [
            [_round(x + skew), _round(y)],
            [_round(x + width), _round(y)],
            [_round(x + width - skew * 0.35), _round(y + height)],
            [_round(x), _round(y + height)],
        ]
    if shape == "slanted-right":
        return [
            [_round(x), _round(y)],
            [_round(x + width - skew), _round(y)],
            [_round(x + width), _round(y + height)],
            [_round(x + skew * 0.35), _round(y + height)],
        ]
    if shape == "polygon":
        return [
            [_round(x + skew), _round(y)],
            [_round(x + width), _round(y + height * 0.08)],
            [_round(x + width - skew), _round(y + height)],
            [_round(x), _round(y + height * 0.90)],
        ]
    return _rectangle_points(box)


def _anchor(panel: Mapping[str, Any], key: str, fallback: str) -> str:
    value = str(panel.get(key, fallback)).strip().lower()
    return value if value in {"left", "center", "right", "top", "middle", "bottom"} else fallback


def crop_anchor_for_panel(panel: Mapping[str, Any], index: int = 0) -> Dict[str, str]:
    """Storyboardの視線・動作情報からcover cropの焦点を決める。"""

    text = " ".join(str(panel.get(key, "")) for key in ("action", "shot_type", "description")).lower()
    if any(word in text for word in ("左", "left", "逃げ", "走り去")):
        default_x = "left"
    elif any(word in text for word in ("右", "right", "進む", "振り返")):
        default_x = "right"
    else:
        default_x = ("right", "center", "left")[index % 3]
    if any(word in text for word in ("空", "上", "飛", "jump", "sky")):
        default_y = "top"
    elif any(word in text for word in ("足元", "地面", "下", "ground")):
        default_y = "bottom"
    else:
        default_y = "middle"
    return {
        "x": _anchor(panel, "crop_anchor_x", default_x),
        "y": _anchor(panel, "crop_anchor_y", default_y),
    }


def _importance_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(str(value).lower(), 2)


def _rect_intersects(left: Mapping[str, Any], right: Mapping[str, Any], padding: float = 0.0) -> bool:
    return not (
        _float(left.get("x")) + _float(left.get("width")) + padding <= _float(right.get("x"))
        or _float(right.get("x")) + _float(right.get("width")) + padding <= _float(left.get("x"))
        or _float(left.get("y")) + _float(left.get("height")) + padding <= _float(right.get("y"))
        or _float(right.get("y")) + _float(right.get("height")) + padding <= _float(left.get("y"))
    )


def panel_shape_for(template: str, panel: Mapping[str, Any], row: int, column: int, count: int) -> str:
    """場面系統に応じて斜めコマを限定的に混ぜる。"""

    explicit = str(panel.get("panel_shape") or panel.get("shape") or "").strip().lower()
    if explicit in {"rectangle", "wide", "tall", "trapezoid", "slanted-left", "slanted-right", "polygon", "large-bleed"}:
        return "polygon" if explicit == "large-bleed" else explicit
    rank = _importance_rank(panel.get("importance"))
    if template in {"template_c", "action"} and (row == 2 or rank >= 3):
        return "slanted-left" if (row + column) % 2 else "trapezoid"
    if template in {"template_a", "template_dynamic_7"} and count >= 5 and row >= 2 and column == 1:
        return "trapezoid"
    if template == "template_b" and count >= 4 and row == 1 and column == 1:
        return "slanted-right"
    if template in {"template_d", "psychological"} and rank >= 4:
        return "slanted-right"
    return "rectangle"


def _safe_breakout_position(box: Mapping[str, Any], index: int, width: float = 0.22, height: float = 0.34) -> Dict[str, float]:
    margin = PAGE_SAFE_MARGIN
    right_side = index % 2 == 0
    x = _float(box.get("x")) + _float(box.get("width")) * (0.55 if right_side else 0.05)
    y = _float(box.get("y")) - height * 0.20
    return {
        "x": _round(_clamp(x, margin, 1 - margin - width)),
        "y": _round(_clamp(y, margin, 1 - margin - height)),
        "width": _round(width),
        "height": _round(height),
    }


def build_page_composition(
    page: Mapping[str, Any],
    geometries: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """保存用のPageComposition v2を作る。Artwork自体は変更しない。"""

    panels = [panel for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    geometry_by_id = {str(item.get("panel_id")): item for item in geometries if isinstance(item, Mapping)}
    composition_panels: List[Dict[str, Any]] = []
    candidates: List[tuple[int, int, Dict[str, Any], Mapping[str, Any]]] = []
    for index, panel in enumerate(panels):
        panel_id = str(panel.get("id", ""))
        geometry = geometry_by_id.get(panel_id) or panel.get("geometry") or {}
        shape = str(geometry.get("shape") or "rectangle")
        points = normalize_polygon(geometry.get("polygon_points"), geometry)
        box = {
            "x": _round(_float(geometry.get("x"))),
            "y": _round(_float(geometry.get("y"))),
            "width": _round(_float(geometry.get("width"))),
            "height": _round(_float(geometry.get("height"))),
        }
        importance = str(panel.get("importance") or geometry.get("importance") or "medium")
        anchor = crop_anchor_for_panel(panel, index)
        item = {
            "panel_id": panel_id,
            **box,
            "bounding_box": dict(box),
            "polygon_points": points,
            "shape": shape,
            "z_index": int(geometry.get("z_index", 1)),
            "bleed": bool(geometry.get("bleed", False)),
            "gutter": deepcopy(geometry.get("gutter") or {"type": "normal", "width": 0.014}),
            "reading_order": int(panel.get("order", index + 1) or index + 1),
            "importance": importance,
            "allow_breakout": bool(geometry.get("allow_breakout", False)),
            "crop_anchor_x": anchor["x"],
            "crop_anchor_y": anchor["y"],
            "artwork_coverage_target": ARTWORK_COVERAGE_TARGET,
        }
        composition_panels.append(item)
        if item["allow_breakout"] and _importance_rank(importance) >= 3:
            candidates.append((_importance_rank(importance), -index, item, panel))

    candidates.sort(reverse=True, key=lambda value: (value[0], value[1]))
    breakouts: List[Dict[str, Any]] = []
    # 1ページ0〜2個を上限にし、既存Artworkから同じソースを安全に切り出せる形で保存する。
    for breakout_index, (_rank, _order, geometry, panel) in enumerate(candidates[:2]):
        position = _safe_breakout_position(geometry, breakout_index)
        breakouts.append(
            {
                "id": f"breakout-{geometry['panel_id']}",
                "panel_id": geometry["panel_id"],
                "source_panel_id": geometry["panel_id"],
                "type": "character",
                "subject_id": str((panel.get("characters") or [""])[0]),
                "enabled": True,
                "source_asset": panel.get("image_url"),
                **position,
                "z_index": 3,
                "clip_shape": "ellipse",
                "allowed_overlap_regions": ["gutter", "adjacent_panel"],
            }
        )

    overlays: List[Dict[str, Any]] = []
    page_number = int(page.get("page_number", 1) or 1)
    if page_number == 1 and str(page.get("title", "")).strip():
        overlays.append(
            {
                "id": "page-title",
                "type": "title",
                "text": str(page.get("title"))[:120],
                "x": PAGE_SAFE_MARGIN,
                "y": 0.012,
                "width": 1 - PAGE_SAFE_MARGIN * 2,
                "height": 0.045,
                "rotation": 0,
                "z_index": 6,
                "reading_priority": 0,
            }
        )

    # 主役コマの最初の吹き出し/SFXは、ページ全体のレイヤーへ出せる。
    moved_items: set[tuple[str, str]] = set()
    for geometry in composition_panels:
        panel = next((item for item in panels if str(item.get("id")) == geometry["panel_id"]), {})
        if not geometry.get("allow_breakout") or _importance_rank(geometry.get("importance")) < 3:
            continue
        text_layout = panel.get("text_layout") if isinstance(panel, Mapping) else None
        text_items = text_layout.get("items", []) if isinstance(text_layout, Mapping) else []
        candidate = next((item for item in text_items if isinstance(item, Mapping) and item.get("type") == "bubble"), None)
        if candidate:
            box = geometry["bounding_box"]
            width = _clamp(_float(candidate.get("width"), 0.34) * _float(box.get("width"), 0.3), 0.16, 0.34)
            height = _clamp(_float(candidate.get("height"), 0.18) * _float(box.get("height"), 0.3), 0.08, 0.16)
            overlays.append(
                {
                    "id": f"overlay-{geometry['panel_id']}-{candidate.get('id', 'bubble-1')}",
                    "type": "bubble",
                    "text": str(candidate.get("text", "")),
                    "source_panel_id": geometry["panel_id"],
                    "source_item_id": str(candidate.get("id", "")),
                    "x": _round(_clamp(_float(box.get("x")) + _float(box.get("width")) * 0.56, PAGE_SAFE_MARGIN, 1 - PAGE_SAFE_MARGIN - width)),
                    "y": _round(_clamp(_float(box.get("y")) - height * 0.15, PAGE_SAFE_MARGIN, 1 - PAGE_SAFE_MARGIN - height)),
                    "width": _round(width),
                    "height": _round(height),
                    "rotation": 0,
                    "z_index": 4,
                    "reading_priority": int(candidate.get("order", 1) or 1),
                    "breakout": True,
                }
            )
            moved_items.add((geometry["panel_id"], str(candidate.get("id", ""))))
        sfx = next((item for item in text_items if isinstance(item, Mapping) and item.get("type") == "sfx"), None)
        if sfx:
            box = geometry["bounding_box"]
            overlays.append(
                {
                    "id": f"overlay-{geometry['panel_id']}-{sfx.get('id', 'sfx-1')}",
                    "type": "sfx",
                    "text": str(sfx.get("text", "")),
                    "source_panel_id": geometry["panel_id"],
                    "source_item_id": str(sfx.get("id", "")),
                    "x": _round(_clamp(_float(box.get("x")) + _float(box.get("width")) * 0.32, PAGE_SAFE_MARGIN, 1 - PAGE_SAFE_MARGIN - 0.24)),
                    "y": _round(_clamp(_float(box.get("y")) + _float(box.get("height")) * 0.42, PAGE_SAFE_MARGIN, 1 - PAGE_SAFE_MARGIN - 0.10)),
                    "width": 0.24,
                    "height": 0.11,
                    "rotation": -8,
                    "z_index": 5,
                    "reading_priority": int(sfx.get("order", 1) or 1),
                    "breakout": True,
                }
            )
            moved_items.add((geometry["panel_id"], str(sfx.get("id", ""))))
        break

    for overlay in overlays:
        overlay["x"] = _round(_clamp(_float(overlay.get("x")), 0.0, 1.0))
        overlay["y"] = _round(_clamp(_float(overlay.get("y")), 0.0, 1.0))
        overlay["width"] = _round(_clamp(_float(overlay.get("width"), 0.2), 0.01, 1.0 - overlay["x"]))
        overlay["height"] = _round(_clamp(_float(overlay.get("height"), 0.1), 0.01, 1.0 - overlay["y"]))

    return {
        "composition_version": COMPOSITION_VERSION,
        "size": list(PAGE_SIZE),
        "safe_margin": PAGE_SAFE_MARGIN,
        "background": {"color": "#f5f3eb"},
        "panels": composition_panels,
        "breakouts": breakouts,
        "overlays": sorted(overlays, key=lambda item: (int(item.get("z_index", 1)), int(item.get("reading_priority", 1)))),
        "moved_text_items": [
            {"panel_id": panel_id, "item_id": item_id}
            for panel_id, item_id in sorted(moved_items)
        ],
    }


def build_legacy_composition(page: Mapping[str, Any]) -> Dict[str, Any]:
    """v1保存データを読み取り専用Compositionへ変換する。"""

    layout = page.get("layout_geometry") if isinstance(page.get("layout_geometry"), Mapping) else {}
    geometries = layout.get("panels", []) if isinstance(layout, Mapping) else []
    panels = []
    for index, geometry in enumerate(geometries):
        if not isinstance(geometry, Mapping):
            continue
        box = {key: _float(geometry.get(key)) for key in ("x", "y", "width", "height")}
        panels.append(
            {
                "panel_id": str(geometry.get("panel_id", index + 1)),
                **box,
                "bounding_box": box,
                "polygon_points": _rectangle_points(box),
                "shape": "rectangle",
                "z_index": 1,
                "bleed": False,
                "gutter": {"type": "normal", "width": 0.014},
                "reading_order": index + 1,
                "importance": str(geometry.get("importance", "medium")),
                "allow_breakout": False,
                "crop_anchor_x": "center",
                "crop_anchor_y": "middle",
                "artwork_coverage_target": ARTWORK_COVERAGE_TARGET,
            }
        )
    return {
        "composition_version": LEGACY_COMPOSITION_VERSION,
        "size": list(PAGE_SIZE),
        "safe_margin": PAGE_SAFE_MARGIN,
        "background": {"color": "#f5f3eb"},
        "panels": panels,
        "breakouts": [],
        "overlays": [],
        "moved_text_items": [],
    }


def composition_for_page(page: Mapping[str, Any]) -> Dict[str, Any]:
    composition = page.get("composition")
    if isinstance(composition, Mapping):
        try:
            version = int(composition.get("composition_version", 0) or 0)
        except (TypeError, ValueError):
            version = 0
        if version >= COMPOSITION_VERSION:
            return deepcopy(dict(composition))
    return build_legacy_composition(page)


def moved_text_item_set(composition: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(item.get("panel_id")), str(item.get("item_id")))
        for item in composition.get("moved_text_items", [])
        if isinstance(item, Mapping)
    }


def composition_quality_issues(page: Mapping[str, Any]) -> List[Dict[str, str]]:
    """漫画らしさを壊すgeometry・余白・breakoutを決定的に検査する。"""

    composition = page.get("composition")
    if not isinstance(composition, Mapping):
        return []
    try:
        version = int(composition.get("composition_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version < COMPOSITION_VERSION:
        return []
    page_number = str(page.get("page_number", ""))
    panels = [item for item in composition.get("panels", []) if isinstance(item, Mapping)]
    issues: List[Dict[str, str]] = []
    if len(panels) != len([item for item in page.get("panels", []) if isinstance(item, Mapping)]):
        issues.append({"key": f"composition-panel-count-{page_number}", "label": f"ページ{page_number}のComposition", "detail": "PanelとCompositionの件数が一致しません。"})
        return issues
    template = str((page.get("layout_geometry") or {}).get("template", "")) if isinstance(page.get("layout_geometry"), Mapping) else ""
    areas = []
    shapes = set()
    for index, panel in enumerate(panels, start=1):
        points = normalize_polygon(panel.get("polygon_points"), panel)
        area = polygon_area(points)
        areas.append(area)
        shapes.add(str(panel.get("shape", "rectangle")))
        if len(points) < 3 or area <= 0.0005:
            issues.append({"key": f"composition-polygon-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の形状", "detail": "有効なpolygon geometryがありません。"})
        if not panel.get("bleed") and any(point[0] < PAGE_SAFE_MARGIN * 0.5 or point[0] > 1 - PAGE_SAFE_MARGIN * 0.5 or point[1] < PAGE_SAFE_MARGIN * 0.5 or point[1] > 1 - PAGE_SAFE_MARGIN * 0.5 for point in points):
            issues.append({"key": f"composition-safe-margin-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}のセーフ領域", "detail": "Panelがページのセーフマージンを越えています。"})
        if _float(panel.get("artwork_coverage", 1.0), 1.0) < ARTWORK_COVERAGE_TARGET and not panel.get("intentional_whitespace"):
            issues.append({"key": f"composition-coverage-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の画像密度", "detail": "通常コマのArtwork coverageが95%未満です。cover cropまたは焦点の再計算が必要です。"})
    if len(panels) >= 4 and template != "four_panel" and areas and min(areas) > 0 and max(areas) / min(areas) < 1.12:
        issues.append({"key": f"composition-uniform-{page_number}", "label": f"ページ{page_number}の面積階層", "detail": "通常ページのコマ面積が均等すぎます。重要コマに30〜50%程度の面積を与えてください。"})
    if len(panels) >= 4 and template != "four_panel" and len(shapes) == 1:
        issues.append({"key": f"composition-shape-uniform-{page_number}", "label": f"ページ{page_number}の形状多様性", "detail": "通常ページがすべて同じ矩形です。場面に応じて台形または斜め境界を混ぜてください。"})
    for breakout_index, breakout in enumerate(composition.get("breakouts", []), start=1):
        if not isinstance(breakout, Mapping) or not breakout.get("enabled", True):
            continue
        x, y = _float(breakout.get("x")), _float(breakout.get("y"))
        width, height = _float(breakout.get("width")), _float(breakout.get("height"))
        if x < PAGE_SAFE_MARGIN or y < PAGE_SAFE_MARGIN or x + width > 1 - PAGE_SAFE_MARGIN or y + height > 1 - PAGE_SAFE_MARGIN:
            issues.append({"key": f"composition-breakout-safe-{page_number}-{breakout_index}", "label": f"ページ{page_number}のBreakout", "detail": "Breakoutがページセーフ領域の外へ出ています。"})
    for overlay_index, overlay in enumerate(composition.get("overlays", []), start=1):
        if not isinstance(overlay, Mapping):
            continue
        x, y = _float(overlay.get("x")), _float(overlay.get("y"))
        width, height = _float(overlay.get("width")), _float(overlay.get("height"))
        title_exception = page_number == "1" and str(overlay.get("type")) == "title"
        if not title_exception and (x < PAGE_SAFE_MARGIN or y < PAGE_SAFE_MARGIN or x + width > 1 - PAGE_SAFE_MARGIN or y + height > 1 - PAGE_SAFE_MARGIN):
            issues.append({"key": f"composition-overlay-safe-{page_number}-{overlay_index}", "label": f"ページ{page_number}のオーバーレイ", "detail": "吹き出しまたはTypographyがページ外へ出ています。"})
    overlays = [item for item in composition.get("overlays", []) if isinstance(item, Mapping)]
    for left_index, left in enumerate(overlays):
        if str(left.get("type")) == "title":
            continue
        for right in overlays[left_index + 1 :]:
            if str(right.get("type")) == "title":
                continue
            if _rect_intersects(left, right, 0.006):
                issues.append({"key": f"composition-overlay-collision-{page_number}-{left.get('id', left_index)}-{right.get('id', 'overlay')}", "label": f"ページ{page_number}の文字衝突", "detail": "ページレベルの吹き出し・ナレーション・SFXが重なっています。"})
    breakouts = [item for item in composition.get("breakouts", []) if isinstance(item, Mapping) and item.get("enabled", True)]
    for left_index, left in enumerate(breakouts):
        for right in breakouts[left_index + 1 :]:
            if _rect_intersects(left, right, 0.0):
                issues.append({"key": f"composition-breakout-collision-{page_number}-{left.get('id', left_index)}-{right.get('id', 'breakout')}", "label": f"ページ{page_number}のBreakout衝突", "detail": "前景Breakout同士が重なっています。"})
    if page_number != "1" and any(str(item.get("type")) == "title" for item in composition.get("overlays", []) if isinstance(item, Mapping)):
        issues.append({"key": f"composition-title-repeat-{page_number}", "label": f"ページ{page_number}のタイトル", "detail": "2ページ目以降に大きなタイトルを繰り返さないでください。"})
    return issues
