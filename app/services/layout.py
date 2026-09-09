"""漫画ページの不均等コマ割りと文字要素配置を決定する純粋ロジック。"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .reading_order import LANGUAGE_EN, canonicalize_stored_settings


LAYOUT_VERSION = 2
PAGE_SIDE_MARGIN = 0.025
PAGE_TOP_MARGIN = 0.02
PAGE_BOTTOM_MARGIN = 0.06
PANEL_GAP = 0.014
TEXT_SAFE_MARGIN = 0.06
TEXT_GAP = 0.012

TEMPLATE_DRAMA = "template_a"
TEMPLATE_CONVERSATION = "template_b"
TEMPLATE_ACTION = "template_c"
TEMPLATE_PSYCHOLOGICAL = "template_d"
TEMPLATE_FOUR_PANEL = "four_panel"
TEMPLATES = {
    TEMPLATE_DRAMA,
    TEMPLATE_CONVERSATION,
    TEMPLATE_ACTION,
    TEMPLATE_PSYCHOLOGICAL,
    TEMPLATE_FOUR_PANEL,
}

IMPORTANCE_SCORES = {
    "low": 1.0,
    "medium": 2.0,
    "high": 3.2,
    "critical": 4.2,
}

_HIGH_IMPORTANCE_WORDS = (
    "クライマックス",
    "決着",
    "衝撃",
    "告白",
    "真相",
    "正体",
    "決定的",
    "転機",
    "大きな反応",
    "climax",
    "reveal",
    "impact",
    "decisive",
    "emotional peak",
)
_ACTION_WORDS = (
    "アクション",
    "戦闘",
    "走",
    "飛",
    "殴",
    "斬",
    "爆発",
    "追跡",
    "action",
    "fight",
    "chase",
    "impact",
)
_PSYCHOLOGICAL_WORDS = (
    "心理",
    "葛藤",
    "余韻",
    "沈黙",
    "涙",
    "決意",
    "目のアップ",
    "顔アップ",
    "クローズアップ",
    "emotion",
    "silence",
    "close-up",
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round(value: float) -> float:
    return round(float(value), 5)


def _text_from_panel(panel: Mapping[str, Any]) -> str:
    values: List[str] = []
    for key in ("panel_role", "role", "scene_type", "description", "shot_type", "action", "expression"):
        values.append(str(panel.get(key, "")))
    values.extend(str(item) for item in panel.get("sfx", []) if item)
    return " ".join(values).lower()


def panel_importance(panel: Mapping[str, Any]) -> float:
    """明示重要度を優先し、旧データは内容から安全に推定する。"""

    raw = panel.get("importance")
    if isinstance(raw, (int, float)):
        return _clamp(float(raw), 1.0, 4.2)
    normalized = str(raw or "").strip().lower()
    if normalized in IMPORTANCE_SCORES:
        return IMPORTANCE_SCORES[normalized]
    text = _text_from_panel(panel)
    if any(word in text for word in _HIGH_IMPORTANCE_WORDS):
        return IMPORTANCE_SCORES["critical"]
    if any(word in text for word in _ACTION_WORDS + _PSYCHOLOGICAL_WORDS):
        return IMPORTANCE_SCORES["high"]
    if panel.get("dialogue") or panel.get("narration"):
        return IMPORTANCE_SCORES["medium"]
    return IMPORTANCE_SCORES["low"]


def normalize_importance(panel: Mapping[str, Any]) -> str:
    score = panel_importance(panel)
    if score >= 4:
        return "critical"
    if score >= 3:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def _page_text(page: Mapping[str, Any]) -> str:
    values = [str(page.get("page_role", "")), str(page.get("title", ""))]
    values.extend(_text_from_panel(panel) for panel in page.get("panels", []) if isinstance(panel, Mapping))
    return " ".join(values).lower()


def select_layout_template(page: Mapping[str, Any]) -> str:
    """明示指定、場面種別、内容の順でテンプレート系統を決定する。"""

    layout = str(page.get("layout", "classic")).strip().lower()
    explicit = {
        "drama": TEMPLATE_DRAMA,
        "template_a": TEMPLATE_DRAMA,
        "conversation": TEMPLATE_CONVERSATION,
        "template_b": TEMPLATE_CONVERSATION,
        "action": TEMPLATE_ACTION,
        "template_c": TEMPLATE_ACTION,
        "psychological": TEMPLATE_PSYCHOLOGICAL,
        "template_d": TEMPLATE_PSYCHOLOGICAL,
        "four_panel": TEMPLATE_FOUR_PANEL,
        "wide": TEMPLATE_DRAMA,
        "hero": TEMPLATE_PSYCHOLOGICAL,
    }
    if layout in explicit:
        return explicit[layout]

    panels = [panel for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    text = _page_text(page)
    action_score = sum(text.count(word) for word in _ACTION_WORDS)
    psychological_score = sum(text.count(word) for word in _PSYCHOLOGICAL_WORDS)
    dialogue_count = sum(len(panel.get("dialogue", [])) for panel in panels)
    if action_score >= 2 or (len(panels) >= 5 and action_score):
        return TEMPLATE_ACTION
    if psychological_score >= 2 or (len(panels) <= 4 and psychological_score):
        return TEMPLATE_PSYCHOLOGICAL
    if dialogue_count >= max(3, len(panels)):
        return TEMPLATE_CONVERSATION
    if layout == "grid":
        return TEMPLATE_CONVERSATION
    return TEMPLATE_DRAMA


def _base_pattern(template: str, count: int) -> List[int]:
    if count <= 1:
        return [1]
    if template == TEMPLATE_FOUR_PANEL and count == 4:
        return [2, 2]
    patterns = {
        TEMPLATE_DRAMA: {
            2: [1, 1], 3: [1, 2], 4: [2, 2], 5: [1, 2, 2],
            6: [1, 2, 2, 1], 7: [1, 2, 2, 2], 8: [1, 2, 2, 2, 1],
        },
        TEMPLATE_CONVERSATION: {
            2: [2], 3: [2, 1], 4: [2, 2], 5: [2, 2, 1],
            6: [2, 2, 2], 7: [2, 2, 2, 1], 8: [2, 2, 2, 2],
        },
        TEMPLATE_ACTION: {
            2: [1, 1], 3: [1, 2], 4: [1, 2, 1], 5: [1, 2, 2],
            6: [1, 3, 2], 7: [1, 3, 2, 1], 8: [1, 3, 2, 2],
        },
        TEMPLATE_PSYCHOLOGICAL: {
            2: [1, 1], 3: [1, 1, 1], 4: [1, 2, 1], 5: [1, 2, 1, 1],
            6: [1, 2, 2, 1], 7: [1, 2, 2, 1, 1], 8: [1, 2, 2, 2, 1],
        },
    }
    if count in patterns.get(template, {}):
        return list(patterns[template][count])
    pattern: List[int] = []
    remaining = count
    while remaining:
        size = min(2, remaining)
        pattern.append(size)
        remaining -= size
    return pattern


def _rows_for_panels(template: str, panels: Sequence[Mapping[str, Any]]) -> List[List[int]]:
    count = len(panels)
    pattern = _base_pattern(template, count)
    rows: List[List[int]] = []
    cursor = 0
    for size in pattern:
        rows.append(list(range(cursor, min(count, cursor + size))))
        cursor += size
    if cursor < count:
        rows.append(list(range(cursor, count)))

    if template == TEMPLATE_FOUR_PANEL or count < 3:
        return rows
    scores = [panel_importance(panel) for panel in panels]
    highlight = max(range(count), key=lambda index: (scores[index], index))
    if scores[highlight] < IMPORTANCE_SCORES["high"]:
        return rows
    for row_index, row in enumerate(rows):
        if highlight not in row or len(row) == 1:
            continue
        before_rows = [list(indices) for indices in rows[:row_index]]
        after_rows = [list(indices) for indices in rows[row_index + 1 :]]
        before = [index for index in row if index < highlight]
        after = [index for index in row if index > highlight]
        if before:
            if before_rows and len(before_rows[-1]) + len(before) <= 3:
                before_rows[-1].extend(before)
            else:
                before_rows.append(before)
        replacement = before_rows + [[highlight]]
        if after:
            if after_rows and len(after) + len(after_rows[0]) <= 3:
                after_rows[0] = after + after_rows[0]
            else:
                replacement.append(after)
        return replacement + after_rows
    return rows


def _row_base_weights(template: str, row_count: int) -> List[float]:
    defaults = {
        TEMPLATE_DRAMA: [0.95, 0.82, 0.78, 1.45, 0.9],
        TEMPLATE_CONVERSATION: [0.9, 1.05, 1.28, 0.95],
        TEMPLATE_ACTION: [0.72, 0.78, 1.5, 1.25],
        TEMPLATE_PSYCHOLOGICAL: [0.78, 0.68, 1.5, 1.18, 0.9],
        TEMPLATE_FOUR_PANEL: [1.0, 1.0],
    }
    values = list(defaults.get(template, defaults[TEMPLATE_DRAMA]))
    while len(values) < row_count:
        values.append(0.9)
    return values[:row_count]


def _physical_boxes_for_row(
    indices: Sequence[int],
    panels: Sequence[Mapping[str, Any]],
    language: str,
    y: float,
    height: float,
    uniform: bool,
) -> Dict[int, Dict[str, float]]:
    available_width = 1 - PAGE_SIDE_MARGIN * 2 - PANEL_GAP * max(0, len(indices) - 1)
    if uniform:
        raw_weights = [1.0] * len(indices)
    else:
        raw_weights = [1.0 + (panel_importance(panels[index]) - 2.0) * 0.18 for index in indices]
        if len(indices) > 1 and max(raw_weights) - min(raw_weights) < 0.08:
            raw_weights[0] *= 1.08
            raw_weights[-1] *= 0.92
    total_weight = sum(raw_weights) or 1.0
    widths = [available_width * weight / total_weight for weight in raw_weights]
    physical_order = list(indices) if language == LANGUAGE_EN else list(reversed(indices))
    width_by_index = dict(zip(indices, widths))
    x = PAGE_SIDE_MARGIN
    result: Dict[int, Dict[str, float]] = {}
    for physical_column, panel_index in enumerate(physical_order, start=1):
        width = width_by_index[panel_index]
        result[panel_index] = {
            "x": _round(x),
            "y": _round(y),
            "width": _round(width),
            "height": _round(height),
            "column": physical_column,
        }
        x += width + PANEL_GAP
    return result


def _page_signature(page: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    payload = {
        "layout": page.get("layout"),
        "page_role": page.get("page_role"),
        "language": canonicalize_stored_settings(settings).get("language"),
        "panels": [
            {
                "id": panel.get("id"),
                "order": panel.get("order"),
                "importance": panel.get("importance"),
                "panel_role": panel.get("panel_role"),
                "scene_type": panel.get("scene_type"),
                "description": panel.get("description"),
                "shot_type": panel.get("shot_type"),
                "action": panel.get("action"),
                "expression": panel.get("expression"),
                "dialogue": panel.get("dialogue"),
                "narration": panel.get("narration"),
                "sfx": panel.get("sfx"),
            }
            for panel in page.get("panels", [])
            if isinstance(panel, Mapping)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _rect_intersects(left: Mapping[str, float], right: Mapping[str, float], padding: float = 0.0) -> bool:
    return not (
        left["x"] + left["width"] + padding <= right["x"]
        or right["x"] + right["width"] + padding <= left["x"]
        or left["y"] + left["height"] + padding <= right["y"]
        or right["y"] + right["height"] + padding <= left["y"]
    )


def _intersection_area(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    width = max(0.0, min(left["x"] + left["width"], right["x"] + right["width"]) - max(left["x"], right["x"]))
    height = max(0.0, min(left["y"] + left["height"], right["y"] + right["height"]) - max(left["y"], right["y"]))
    return width * height


def _text_box_size(text: str, item_type: str) -> tuple[float, float, int]:
    length = max(1, len(text.strip()))
    if item_type == "narration":
        width = _clamp(0.46 + min(length, 80) * 0.0015, 0.46, 0.58)
        chars_per_line = max(10, int(width * 31))
        lines = max(1, math.ceil(length / chars_per_line))
        return width, _clamp(0.07 + lines * 0.045, 0.11, 0.23), lines
    if item_type == "sfx":
        width = _clamp(0.20 + min(length, 30) * 0.006, 0.22, 0.36)
        return width, _clamp(0.08 + math.ceil(length / 10) * 0.035, 0.11, 0.20), max(1, math.ceil(length / 10))
    width = _clamp(0.34 + min(length, 70) * 0.0026, 0.36, 0.52)
    chars_per_line = max(8, int(width * 27))
    lines = max(1, math.ceil(length / chars_per_line))
    return width, _clamp(0.075 + lines * 0.052, 0.18, 0.46), lines


def _candidate_rects(item_type: str, width: float, height: float, language: str) -> Iterable[Dict[str, float]]:
    start_side = "left" if language == LANGUAGE_EN else "right"
    sides = [start_side, "right" if start_side == "left" else "left", "center"]
    if item_type == "narration":
        y_values = [1 - TEXT_SAFE_MARGIN - height, 0.64, 0.46, 0.28, TEXT_SAFE_MARGIN]
    elif item_type == "sfx":
        y_values = [0.48, 0.68, 0.28, TEXT_SAFE_MARGIN, 1 - TEXT_SAFE_MARGIN - height]
    else:
        y_values = [TEXT_SAFE_MARGIN, 0.28, 0.50, 1 - TEXT_SAFE_MARGIN - height]
    for y in y_values:
        for side in sides:
            if side == "left":
                x = TEXT_SAFE_MARGIN
            elif side == "right":
                x = 1 - TEXT_SAFE_MARGIN - width
            else:
                x = (1 - width) / 2
            yield {"x": _round(x), "y": _round(y), "width": _round(width), "height": _round(height)}


def _protected_zones(panel: Mapping[str, Any]) -> List[Dict[str, float]]:
    supplied = panel.get("protected_zones")
    zones: List[Dict[str, float]] = []
    if isinstance(supplied, list):
        for item in supplied[:8]:
            if not isinstance(item, Mapping):
                continue
            try:
                zone = {key: float(item[key]) for key in ("x", "y", "width", "height")}
            except (KeyError, TypeError, ValueError):
                continue
            if all(0 <= zone[key] <= 1 for key in zone):
                zones.append(zone)
    if zones:
        return zones
    if panel.get("characters"):
        text = _text_from_panel(panel)
        if "アップ" in text or "close-up" in text:
            return [{"x": 0.22, "y": 0.20, "width": 0.56, "height": 0.62}]
        return [{"x": 0.27, "y": 0.24, "width": 0.46, "height": 0.58}]
    return []


def place_text_elements(panel: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    """吹き出し・ナレーション・SFXを同じ衝突判定で配置する。"""

    language = canonicalize_stored_settings(settings).get("language")
    elements: List[tuple[str, int, str]] = []
    for item_type, source_key in (("bubble", "dialogue"), ("narration", "narration"), ("sfx", "sfx")):
        source = panel.get(source_key, [])
        if isinstance(source, list):
            elements.extend((item_type, index + 1, str(text).strip()) for index, text in enumerate(source) if str(text).strip())

    placed: List[Dict[str, Any]] = []
    warnings: List[str] = []
    protected = _protected_zones(panel)
    for item_type, order, text in elements:
        width, height, lines = _text_box_size(text, item_type)
        valid_candidates: List[tuple[float, int, Dict[str, float]]] = []
        for candidate_index, candidate in enumerate(_candidate_rects(item_type, width, height, language)):
            if any(_rect_intersects(candidate, item, TEXT_GAP) for item in placed):
                continue
            protected_overlap = sum(_intersection_area(candidate, zone) for zone in protected)
            valid_candidates.append((protected_overlap, candidate_index, candidate))
        if valid_candidates:
            _score, _candidate_index, rect = min(valid_candidates, key=lambda value: (value[0], value[1]))
        else:
            rect = {}
            for scale in (0.9, 0.8):
                scaled_width = max(0.28, width * scale)
                scaled_height = max(0.10, height * scale)
                candidates = list(_candidate_rects(item_type, scaled_width, scaled_height, language))
                candidate = next(
                    (item for item in candidates if not any(_rect_intersects(item, existing, TEXT_GAP / 2) for existing in placed)),
                    None,
                )
                if candidate:
                    rect = candidate
                    width, height = scaled_width, scaled_height
                    break
            if not rect:
                rect = min(
                    _candidate_rects(item_type, width, height, language),
                    key=lambda candidate: sum(_intersection_area(candidate, existing) for existing in placed),
                )
                warnings.append(f"{item_type}-{order}:配置領域が不足しています")
        side = "left" if rect["x"] + rect["width"] / 2 < 0.5 else "right"
        placed.append(
            {
                "id": f"{item_type}-{order}",
                "type": item_type,
                "order": order,
                "text": text,
                **{key: _round(rect[key]) for key in ("x", "y", "width", "height")},
                "side": side,
                "line_count": lines,
                "font_scale": _round(
                    _clamp(
                        (rect["height"] - 0.055) / max(0.052 * lines, 0.052),
                        0.62,
                        1.0,
                    )
                ),
            }
        )
    return {
        "version": LAYOUT_VERSION,
        "safe_margin": TEXT_SAFE_MARGIN,
        "items": placed,
        "warnings": warnings,
    }


def reflow_page(page: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    """一つのPageだけを再計算し、Artwork情報はそのまま維持する。"""

    next_page = deepcopy(dict(page))
    panels = [deepcopy(dict(panel)) for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    for panel_index, panel in enumerate(panels, start=1):
        if not str(panel.get("id") or "").strip():
            panel["id"] = f"layout-panel-{panel_index}"
    next_page["panels"] = panels
    template = select_layout_template(next_page)
    language = canonicalize_stored_settings(settings).get("language")
    rows = _rows_for_panels(template, panels)
    row_weights = _row_base_weights(template, len(rows))
    for row_index, indices in enumerate(rows):
        importance = max((panel_importance(panels[index]) for index in indices), default=2.0)
        row_weights[row_index] *= 1 + max(0.0, importance - 2.0) * (0.24 if len(indices) == 1 else 0.10)
    highlighted_rows = [
        row_index
        for row_index, indices in enumerate(rows)
        if len(indices) == 1 and panel_importance(panels[indices[0]]) >= IMPORTANCE_SCORES["high"]
    ]
    for row_index in highlighted_rows:
        other_weights = [weight for index, weight in enumerate(row_weights) if index != row_index]
        if other_weights:
            row_weights[row_index] = max(row_weights[row_index], max(other_weights) * 1.4)
    total_height = 1 - PAGE_TOP_MARGIN - PAGE_BOTTOM_MARGIN - PANEL_GAP * max(0, len(rows) - 1)
    weight_total = sum(row_weights) or 1.0
    row_heights = [total_height * weight / weight_total for weight in row_weights]

    geometries: List[Dict[str, Any]] = []
    y = PAGE_TOP_MARGIN
    for row_index, (indices, row_height) in enumerate(zip(rows, row_heights), start=1):
        boxes = _physical_boxes_for_row(
            indices,
            panels,
            str(language),
            y,
            row_height,
            template == TEMPLATE_FOUR_PANEL,
        )
        for panel_index in indices:
            box = boxes[panel_index]
            importance = normalize_importance(panels[panel_index])
            geometry = {
                "panel_id": str(panels[panel_index].get("id", "")),
                **box,
                "row": row_index,
                "row_span": max(1, round(box["height"] * 12)),
                "column_span": max(1, round(box["width"] * 12)),
                "importance": importance,
                "visual_weight": panel_importance(panels[panel_index]),
                "area": _round(box["width"] * box["height"]),
            }
            panels[panel_index]["importance"] = importance
            panels[panel_index]["visual_position"] = {
                "row": row_index,
                "column": box["column"],
                "column_count": len(indices),
            }
            panels[panel_index]["geometry"] = geometry
            panels[panel_index]["text_layout"] = place_text_elements(panels[panel_index], settings)
            geometries.append(geometry)
        y += row_height + PANEL_GAP

    signature = _page_signature(next_page, settings)
    next_page["layout_version"] = LAYOUT_VERSION
    next_page["layout_geometry"] = {
        "version": LAYOUT_VERSION,
        "signature": signature,
        "template": template,
        "reading_direction": canonicalize_stored_settings(settings).get("reading_direction"),
        "panel_count": len(panels),
        # rowsと各rowのindicesは論理読順のままなので、DOM・Export共通の順序を維持できる。
        "panels": geometries,
    }
    return next_page


def _stored_geometry_is_current(page: Mapping[str, Any], settings: Mapping[str, Any]) -> bool:
    layout = page.get("layout_geometry")
    if not isinstance(layout, Mapping) or layout.get("version") != LAYOUT_VERSION:
        return False
    if layout.get("signature") != _page_signature(page, settings):
        return False
    panels = page.get("panels", [])
    return bool(panels) and all(
        isinstance(panel, Mapping)
        and isinstance(panel.get("geometry"), Mapping)
        and isinstance(panel.get("text_layout"), Mapping)
        for panel in panels
    )


def ensure_page_layout(page: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    if _stored_geometry_is_current(page, settings):
        return deepcopy(dict(page))
    return reflow_page(page, settings)


def ensure_storyboard_layout(storyboard: Any, settings: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(storyboard, list):
        return []
    canonical_settings = canonicalize_stored_settings(settings)
    return [
        ensure_page_layout(page, canonical_settings)
        for page in storyboard
        if isinstance(page, Mapping)
    ]


def repair_storyboard_page(
    storyboard: Any,
    page_id: str,
    settings: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    """指定Pageだけを再配置し、他Pageと画像情報は変更しない。"""

    if not isinstance(storyboard, list):
        return []
    canonical_settings = canonicalize_stored_settings(settings)
    result: List[Dict[str, Any]] = []
    for page in storyboard:
        if not isinstance(page, Mapping):
            continue
        result.append(reflow_page(page, canonical_settings) if str(page.get("id")) == str(page_id) else deepcopy(dict(page)))
    return result


def _inside_text_safe_area(item: Mapping[str, Any], margin: float) -> bool:
    try:
        x, y = float(item["x"]), float(item["y"])
        width, height = float(item["width"]), float(item["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return x >= margin and y >= margin and x + width <= 1 - margin and y + height <= 1 - margin


def page_layout_issues(page: Mapping[str, Any], settings: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    """均等タイル、境界超過、文字要素の衝突を決定的に検出する。"""

    issues: List[Dict[str, str]] = []
    page_number = str(page.get("page_number", ""))
    layout = page.get("layout_geometry")
    geometries = layout.get("panels", []) if isinstance(layout, Mapping) else []
    template = str(layout.get("template", "")) if isinstance(layout, Mapping) else ""
    panels = [panel for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    if len(geometries) != len(panels):
        return [{"key": f"layout-missing-{page_number}", "label": f"ページ{page_number}のレイアウト", "detail": "保存済みのPanel geometryが不足しています。"}]
    areas = [float(item.get("area", 0)) for item in geometries if isinstance(item, Mapping)]
    if len(panels) >= 4 and template != TEMPLATE_FOUR_PANEL and areas and min(areas) > 0:
        if max(areas) / min(areas) < 1.12:
            issues.append({"key": f"layout-uniform-{page_number}", "label": f"ページ{page_number}のコマ面積", "detail": "通常漫画ページが均等タイルに近すぎます。重要度に応じて面積差を付けてください。"})

    for panel_index, panel in enumerate(panels, start=1):
        text_layout = panel.get("text_layout")
        items = text_layout.get("items", []) if isinstance(text_layout, Mapping) else []
        margin = float(text_layout.get("safe_margin", TEXT_SAFE_MARGIN)) if isinstance(text_layout, Mapping) else TEXT_SAFE_MARGIN
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if not _inside_text_safe_area(item, margin - 0.002):
                issues.append({"key": f"text-boundary-{page_number}-{panel_index}-{item.get('id', '')}", "label": f"ページ{page_number} コマ{panel_index}の文字配置", "detail": "吹き出し・ナレーション・SFXがセーフマージンまたはPanel境界を越えています。"})
            if item.get("overflow"):
                issues.append({"key": f"text-overflow-{page_number}-{panel_index}-{item.get('id', '')}", "label": f"ページ{page_number} コマ{panel_index}の文字量", "detail": "文字が吹き出しまたは文字枠に収まっていません。文面の分割か配置の再計算が必要です。"})
        for left_index, left in enumerate(items):
            if not isinstance(left, Mapping):
                continue
            for right in items[left_index + 1 :]:
                if isinstance(right, Mapping) and _rect_intersects(left, right):
                    issues.append({"key": f"text-collision-{page_number}-{panel_index}-{left.get('id', '')}-{right.get('id', '')}", "label": f"ページ{page_number} コマ{panel_index}の文字衝突", "detail": "吹き出し、ナレーション、またはSFXが重なっています。"})
    return issues


def storyboard_layout_issues(project: Mapping[str, Any]) -> List[Dict[str, str]]:
    settings = project.get("settings") if isinstance(project.get("settings"), Mapping) else {}
    issues: List[Dict[str, str]] = []
    for page in project.get("storyboard", []) or []:
        if isinstance(page, Mapping):
            issues.extend(page_layout_issues(page, settings))
    return issues
