"""漫画ページの不均等コマ割りと文字要素配置を決定する純粋ロジック。"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .composition import (
    SEMANTIC_COMPOSITION_VERSION,
    build_page_composition,
    composition_quality_issues,
    semantic_dominant_panel_index,
    semantic_page_family,
    semantic_protected_zones,
    semantic_shape_plan,
    semantic_text_safe_zones,
    simplify_composition,
    panel_shape_for,
    shape_points,
)
from .reading_order import LANGUAGE_EN, canonicalize_stored_settings
from .text_composition import separate_text_from_unverified_artwork
from .visual_style import apply_text_direction, resolve_visual_style
from .panel_direction import plan_panel_direction, direction_is_ready


LAYOUT_VERSION = 2
SEMANTIC_POLICY_VERSION = 2
PAGE_SIDE_MARGIN = 0.025
PAGE_TOP_MARGIN = 0.02
PAGE_BOTTOM_MARGIN = 0.06
PANEL_GAP = 0.014
TEXT_SAFE_MARGIN = 0.06
TEXT_GAP = 0.012


def _requested_composition_version(page: Mapping[str, Any], settings: Mapping[str, Any] | None) -> int:
    """新規Projectのv3指定と既存v2保存を区別する。"""

    raw_page = page.get("composition_version")
    raw_composition = page.get("composition") if isinstance(page.get("composition"), Mapping) else {}
    raw_settings = settings if isinstance(settings, Mapping) else {}
    for raw in (raw_page, raw_composition.get("composition_version"), raw_settings.get("composition_version")):
        try:
            version = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if version >= SEMANTIC_COMPOSITION_VERSION:
            return SEMANTIC_COMPOSITION_VERSION
        if version == 2:
            return 2
    return 2

TEMPLATE_DRAMA = "template_a"
TEMPLATE_CONVERSATION = "template_b"
TEMPLATE_ACTION = "template_c"
TEMPLATE_PSYCHOLOGICAL = "template_d"
TEMPLATE_FOUR_PANEL = "four_panel"
TEMPLATE_DYNAMIC_7 = "template_dynamic_7"
TEMPLATES = {
    TEMPLATE_DRAMA,
    TEMPLATE_CONVERSATION,
    TEMPLATE_ACTION,
    TEMPLATE_PSYCHOLOGICAL,
    TEMPLATE_FOUR_PANEL,
    TEMPLATE_DYNAMIC_7,
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
        "dynamic_7": TEMPLATE_DYNAMIC_7,
        TEMPLATE_DYNAMIC_7: TEMPLATE_DYNAMIC_7,
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


def _rows_for_panels(
    template: str,
    panels: Sequence[Mapping[str, Any]],
    *,
    force_dominant: bool = True,
    dominant_index: int | None = None,
) -> List[List[int]]:
    count = len(panels)
    pattern = _base_pattern(template, count)
    rows: List[List[int]] = []
    cursor = 0
    for size in pattern:
        rows.append(list(range(cursor, min(count, cursor + size))))
        cursor += size
    if cursor < count:
        rows.append(list(range(cursor, count)))

    if template == TEMPLATE_FOUR_PANEL or count < 3 or not force_dominant:
        return rows
    scores = [panel_importance(panel) for panel in panels]
    highlight = dominant_index if dominant_index is not None else max(range(count), key=lambda index: (scores[index], index))
    if dominant_index is None and scores[highlight] < IMPORTANCE_SCORES["high"]:
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
        TEMPLATE_DYNAMIC_7: [0.72, 0.88, 0.76, 1.35, 1.08],
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
    gap: float = PANEL_GAP,
) -> Dict[int, Dict[str, float]]:
    available_width = 1 - PAGE_SIDE_MARGIN * 2 - gap * max(0, len(indices) - 1)
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
        x += width + gap
    return result


def _page_signature(page: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    payload = {
        "layout": page.get("layout"),
        "page_role": page.get("page_role"),
        "scene_type": page.get("scene_type"),
        "emotion": page.get("emotion"),
        "action_intensity": page.get("action_intensity"),
        "reveal": page.get("reveal"),
        "comedy": page.get("comedy"),
        "climax": page.get("climax"),
        "layout_family": page.get("layout_family"),
        "dominant_panel_id": page.get("dominant_panel_id"),
        "composition_budget": page.get("composition_budget"),
        "special_emphasis": page.get("special_emphasis"),
        "bubble_breakout": page.get("bubble_breakout"),
        "bubble_breakout_reason": page.get("bubble_breakout_reason"),
        "page_overlay_text": page.get("page_overlay_text"),
        "page_overlay_reason": page.get("page_overlay_reason"),
        "language": canonicalize_stored_settings(settings).get("language"),
        "composition_version": settings.get("composition_version") if isinstance(settings, Mapping) else None,
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
                "panel_shape": panel.get("panel_shape"),
                "shape_reason": panel.get("shape_reason"),
                "breakout_reason": panel.get("breakout_reason"),
                "semantic_reason": panel.get("semantic_reason"),
                "character_position": panel.get("character_position"),
                "subject_position": panel.get("subject_position"),
                "face_position": panel.get("face_position"),
                "text_safe_zones": panel.get("text_safe_zones"),
                "protected_zones": panel.get("protected_zones"),
            }
            for panel in page.get("panels", [])
            if isinstance(panel, Mapping)
        ],
    }
    if any(panel.get("dialogue_types") or panel.get("sfx_types") for panel in page.get("panels", [])):
        payload["text_semantics"] = [{"dialogue_types": panel.get("dialogue_types"), "sfx_types": panel.get("sfx_types")} for panel in page.get("panels", [])]
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
            if x < TEXT_SAFE_MARGIN or y < TEXT_SAFE_MARGIN or x + width > 1 - TEXT_SAFE_MARGIN + 0.000001 or y + height > 1 - TEXT_SAFE_MARGIN + 0.000001:
                continue
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


def place_text_elements(
    panel: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    reserved_zones: Mapping[str, Mapping[str, float]] | None = None,
    protected_zones: Sequence[Mapping[str, float]] | None = None,
) -> Dict[str, Any]:
    """吹き出し・ナレーション・SFXを同じ衝突判定で配置する。"""

    language = canonicalize_stored_settings(settings).get("language")
    elements: List[tuple[str, int, str]] = []
    for item_type, source_key in (("bubble", "dialogue"), ("narration", "narration"), ("sfx", "sfx")):
        source = panel.get(source_key, [])
        if isinstance(source, list):
            elements.extend((item_type, index + 1, str(text).strip()) for index, text in enumerate(source) if str(text).strip())

    placed: List[Dict[str, Any]] = []
    warnings: List[str] = []
    protected = list(protected_zones) if protected_zones is not None else _protected_zones(panel)
    reserved = reserved_zones or {}
    for item_type, order, text in elements:
        width, height, lines = _text_box_size(text, item_type)
        valid_candidates: List[tuple[float, float, int, Dict[str, float]]] = []
        for candidate_index, candidate in enumerate(_candidate_rects(item_type, width, height, language)):
            if any(_rect_intersects(candidate, item, TEXT_GAP) for item in placed):
                continue
            protected_overlap = sum(_intersection_area(candidate, zone) for zone in protected)
            if protected_zones is not None and protected_overlap > 0.00001:
                continue
            reserved_zone = reserved.get(item_type)
            reserved_overlap = _intersection_area(candidate, reserved_zone) if isinstance(reserved_zone, Mapping) else 0.0
            # 顔保護を第一にしつつ、予約領域に多く重なる候補を優先する。
            valid_candidates.append((protected_overlap, -reserved_overlap, candidate_index, candidate))
        if valid_candidates:
            _score, _reserved_score, _candidate_index, rect = min(valid_candidates, key=lambda value: (value[0], value[1], value[2]))
        else:
            rect = {}
            for scale in (0.9, 0.8):
                scaled_width = max(0.28, width * scale)
                scaled_height = max(0.10, height * scale)
                candidates = list(_candidate_rects(item_type, scaled_width, scaled_height, language))
                candidate = next(
                    (item for item in candidates if not any(_rect_intersects(item, existing, TEXT_GAP / 2) for existing in placed)
                     and (protected_zones is None or not any(_intersection_area(item, zone) > 0.00001 for zone in protected))),
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
                "overflow": (protected_zones is not None and any(_intersection_area(rect, zone) > 0.00001 for zone in protected))
                or any(_rect_intersects(rect, existing) for existing in placed),
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


def reflow_page(
    page: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    enable_composition: bool = True,
) -> Dict[str, Any]:
    """一つのPageだけを再計算し、Artwork情報はそのまま維持する。"""

    next_page = deepcopy(dict(page))
    panels = [deepcopy(dict(panel)) for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    for panel_index, panel in enumerate(panels, start=1):
        if not str(panel.get("id") or "").strip():
            panel["id"] = f"layout-panel-{panel_index}"
    next_page["panels"] = panels
    template = select_layout_template(next_page)
    language = canonicalize_stored_settings(settings).get("language")
    composition_version = _requested_composition_version(next_page, settings) if enable_composition else 2
    semantic_mode = composition_version >= SEMANTIC_COMPOSITION_VERSION
    panel_gap = resolve_visual_style(settings or {})["gutter_width"] if semantic_mode else PANEL_GAP
    family = semantic_page_family(next_page) if semantic_mode else ""
    dominant_index = semantic_dominant_panel_index(next_page) if semantic_mode else None
    rows = _rows_for_panels(
        template,
        panels,
        force_dominant=not semantic_mode or dominant_index is not None,
        dominant_index=dominant_index,
    )
    row_weights = _row_base_weights(template, len(rows))
    for row_index, indices in enumerate(rows):
        importance = max((panel_importance(panels[index]) for index in indices), default=2.0)
        row_weights[row_index] *= 1 + max(0.0, importance - 2.0) * (0.24 if len(indices) == 1 else 0.10)
    highlighted_rows = [
        row_index
        for row_index, indices in enumerate(rows)
        if len(indices) == 1
        and (
            not semantic_mode
            and panel_importance(panels[indices[0]]) >= IMPORTANCE_SCORES["high"]
            or semantic_mode and dominant_index is not None and dominant_index in indices
        )
    ]
    for row_index in highlighted_rows:
        other_weights = [weight for index, weight in enumerate(row_weights) if index != row_index]
        if other_weights:
            row_weights[row_index] = max(row_weights[row_index], max(other_weights) * 1.4)
            if semantic_mode and dominant_index is not None and len(panels) <= 3:
                # 少コマページで主役が半ページを超えないよう、面積差を保ったまま上限を置く。
                row_weights[row_index] = min(row_weights[row_index], max(other_weights) * 1.15)
    top_margin = 0.075 if semantic_mode and int(page.get("page_number", 1) or 1) == 1 and page.get("title") else PAGE_TOP_MARGIN
    total_height = 1 - top_margin - PAGE_BOTTOM_MARGIN - panel_gap * max(0, len(rows) - 1)
    weight_total = sum(row_weights) or 1.0
    row_heights = [total_height * weight / weight_total for weight in row_weights]
    if semantic_mode and rows:
        # 文字のある段を細い横帯にしない。主役の面積を増やす前に最低高さを配分する。
        floors = [0.17 if any(panels[index].get("dialogue") or panels[index].get("narration") for index in indices) else 0.10 for indices in rows]
        floor_total = sum(floors)
        if floor_total < total_height:
            remaining = total_height - floor_total
            row_heights = [floor + remaining * weight / weight_total for floor, weight in zip(floors, row_weights)]

    geometries: List[Dict[str, Any]] = []
    angled_used = 0
    y = top_margin
    for row_index, (indices, row_height) in enumerate(zip(rows, row_heights), start=1):
        boxes = _physical_boxes_for_row(
            indices,
            panels,
            str(language),
            y,
            row_height,
            template == TEMPLATE_FOUR_PANEL,
            gap=panel_gap,
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
            if enable_composition:
                if semantic_mode:
                    shape, shape_reason, angled_used = semantic_shape_plan(
                        next_page,
                        panels[panel_index],
                        panel_index,
                        len(panels),
                        dominant_index,
                        angled_used,
                    )
                else:
                    shape = panel_shape_for(template, panels[panel_index], row_index, int(box["column"]), len(panels))
                    shape_reason = ""
                geometry["shape"] = shape
                geometry["polygon_points"] = shape_points(box, shape)
                geometry["shape_reason"] = shape_reason
                geometry["z_index"] = 1 + max(0, round(panel_importance(panels[panel_index]) - 2.0))
                geometry["bleed"] = bool(next_page.get("page_number", 1) == 1 and len(panels) == 1)
                geometry["gutter"] = {
                    "type": "diagonal" if shape != "rectangle" else "normal",
                    "width": _round(panel_gap),
                }
                geometry["semantic_family"] = family if semantic_mode else ""
                geometry["dominant"] = bool(semantic_mode and dominant_index == panel_index)
                if semantic_mode:
                    geometry["text_safe_zones"] = semantic_text_safe_zones(panels[panel_index], str(language))
                    geometry["protected_zones"] = semantic_protected_zones(panels[panel_index])
                    # v3は明示的な理由がある場合だけBreakout候補にする。
                    geometry["allow_breakout"] = bool(
                        (geometry["dominant"] or bool(next_page.get("special_emphasis")))
                        and str(
                            panels[panel_index].get("breakout_reason")
                            or panels[panel_index].get("semantic_reason")
                            or ""
                        ).strip()
                        and panel_importance(panels[panel_index]) >= IMPORTANCE_SCORES["high"]
                    )
                else:
                    geometry["allow_breakout"] = bool(
                        panel_importance(panels[panel_index]) >= IMPORTANCE_SCORES["high"]
                        and template in {TEMPLATE_ACTION, TEMPLATE_PSYCHOLOGICAL, TEMPLATE_DRAMA, TEMPLATE_DYNAMIC_7}
                    )
            panels[panel_index]["importance"] = importance
            panels[panel_index]["visual_position"] = {
                "row": row_index,
                "column": box["column"],
                "column_count": len(indices),
            }
            panels[panel_index]["geometry"] = geometry
            panels[panel_index]["text_layout"] = place_text_elements(
                panels[panel_index],
                settings,
                reserved_zones=geometry.get("text_safe_zones") if semantic_mode else None,
                protected_zones=geometry.get("protected_zones") if semantic_mode else None,
            )
            if semantic_mode:
                current_panel = panels[panel_index]
                planned = current_panel.get("panel_direction")
                if not current_panel.get("image_url"):
                    planned = plan_panel_direction(current_panel, settings or {})
                    current_panel["panel_direction"] = planned
                if planned and (not current_panel.get("image_url") or direction_is_ready(current_panel, settings or {})):
                    current_panel["text_layout"] = deepcopy(planned["text_layout"])
                    geometry["protected_zones"] = deepcopy(planned["protected_zones"])
                    geometry["text_safe_zones"] = {item["item_id"]: item for item in planned["reserved_text_zones"]}
                    geometry["crop_anchor_x"] = planned["crop_anchor"]["x"]
                    geometry["crop_anchor_y"] = planned["crop_anchor"]["y"]
                    geometry["allow_breakout"] = False
                elif planned:
                    # 生成済み画像の旧構図と新しい本文を無理に合わせない。
                    current_panel["panel_direction"] = {**planned, "status": "needs_revision"}
                    current_panel["text_layout"]["warnings"].append("生成時の構図と現在の設定が異なります。人物位置を確認して再設計してください。")
                    for item in current_panel["text_layout"]["items"]:
                        item["overflow"] = True
                separate_layout = None if planned else separate_text_from_unverified_artwork(current_panel, geometry, str(language))
                if separate_layout:
                    panels[panel_index]["text_layout"] = separate_layout
                    geometry["artwork_viewport"] = separate_layout["artwork_viewport"]
                    geometry["protected_zones"] = [separate_layout["artwork_viewport"]]
                    # 文字帯に斜線が入らないよう、保守的な再配置は矩形にする。
                    geometry["shape"] = "rectangle"
                    geometry["shape_reason"] = ""
                    geometry["polygon_points"] = shape_points(box, "rectangle")
                apply_text_direction(panels[panel_index], settings or {})
            geometries.append(geometry)
        y += row_height + panel_gap

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
    if semantic_mode:
        next_page["layout_geometry"]["semantic_policy_version"] = SEMANTIC_POLICY_VERSION
    if enable_composition:
        next_page["composition"] = build_page_composition(
            next_page,
            geometries,
            settings,
            composition_version=composition_version,
        )
        next_page["composition_version"] = composition_version
    else:
        next_page.pop("composition", None)
        next_page.pop("composition_version", None)
    return next_page


def _stored_geometry_is_current(page: Mapping[str, Any], settings: Mapping[str, Any]) -> bool:
    layout = page.get("layout_geometry")
    if not isinstance(layout, Mapping) or layout.get("version") != LAYOUT_VERSION:
        return False
    if layout.get("signature") != _page_signature(page, settings):
        return False
    requested_version = _requested_composition_version(page, settings)
    stored_composition = page.get("composition") if isinstance(page.get("composition"), Mapping) else {}
    try:
        stored_version = int(stored_composition.get("composition_version", page.get("composition_version", 0)) or 0)
    except (TypeError, ValueError):
        stored_version = 0
    # 新規Projectでv3を指定した場合だけ、未作成のCompositionを計算する。
    # 既存v2はsettings更新やProject読込だけでは自動変更しない。
    if requested_version >= SEMANTIC_COMPOSITION_VERSION and stored_version not in {SEMANTIC_COMPOSITION_VERSION}:
        return False
    if requested_version >= SEMANTIC_COMPOSITION_VERSION and layout.get("semantic_policy_version") != SEMANTIC_POLICY_VERSION:
        return False
    panels = page.get("panels", [])
    return bool(panels) and all(
        isinstance(panel, Mapping)
        and isinstance(panel.get("geometry"), Mapping)
        and isinstance(panel.get("text_layout"), Mapping)
        for panel in panels
    )


def ensure_page_layout(
    page: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    enable_composition: bool = True,
) -> Dict[str, Any]:
    if _stored_geometry_is_current(page, settings):
        return deepcopy(dict(page))
    return reflow_page(page, settings, enable_composition=enable_composition)


def ensure_storyboard_layout(
    storyboard: Any,
    settings: Mapping[str, Any] | None,
    *,
    enable_composition: bool = True,
) -> List[Dict[str, Any]]:
    if not isinstance(storyboard, list):
        return []
    canonical_settings = canonicalize_stored_settings(settings)
    if not enable_composition:
        # DB読込・migrationでは保存済みv1/v2/v3をそのまま返す。ここで再flowすると、
        # 旧形式のsignature差異だけで既存ArtworkやCompositionが消えるため。
        return [deepcopy(dict(page)) for page in storyboard if isinstance(page, Mapping)]
    return [
        ensure_page_layout(page, canonical_settings, enable_composition=enable_composition)
        for page in storyboard
        if isinstance(page, Mapping)
    ]


def repair_storyboard_page(
    storyboard: Any,
    page_id: str,
    settings: Mapping[str, Any] | None,
    *,
    composition_version: int | None = None,
) -> List[Dict[str, Any]]:
    """指定Pageだけを再配置し、他Pageと画像情報は変更しない。"""

    if not isinstance(storyboard, list):
        return []
    canonical_settings = canonicalize_stored_settings(settings)
    result: List[Dict[str, Any]] = []
    for page in storyboard:
        if not isinstance(page, Mapping):
            continue
        if str(page.get("id")) == str(page_id):
            if composition_version == SEMANTIC_COMPOSITION_VERSION:
                page = {**page, "composition_version": SEMANTIC_COMPOSITION_VERSION}
            repaired = reflow_page(page, canonical_settings)
            if int(repaired.get("composition_version", 0) or 0) >= SEMANTIC_COMPOSITION_VERSION and composition_quality_issues(repaired):
                repaired = simplify_composition(repaired)
            result.append(repaired)
        else:
            result.append(deepcopy(dict(page)))
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
    issues.extend(composition_quality_issues(page))
    return issues


def storyboard_layout_issues(project: Mapping[str, Any]) -> List[Dict[str, str]]:
    settings = project.get("settings") if isinstance(project.get("settings"), Mapping) else {}
    issues: List[Dict[str, str]] = []
    for page in project.get("storyboard", []) or []:
        if isinstance(page, Mapping):
            issues.extend(page_layout_issues(page, settings))
    return issues
