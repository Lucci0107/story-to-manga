"""漫画ページを一枚のCompositionとして扱うための決定的なモデル。"""

from __future__ import annotations

from .dynamic_layout import DYNAMIC_COMPOSITION_VERSION, geometry_metrics, polygons_overlap, lock_outer_edges, polygon_safety_metrics

import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence


# v2は既存Projectの保存形式として維持する。v3は新規Projectで使う意味的な
# ポリシー付きCompositionで、既存v2を暗黙に書き換えない。
COMPOSITION_VERSION = 2
SEMANTIC_COMPOSITION_VERSION = 3
CURRENT_COMPOSITION_VERSION = DYNAMIC_COMPOSITION_VERSION
LEGACY_COMPOSITION_VERSION = 1
PAGE_SIZE = (900, 1200)
PAGE_SAFE_MARGIN = 0.04
ARTWORK_COVERAGE_TARGET = 0.95

# 通常コマの下限は900×1200pxの3:4ページで、幅162px・高さ156px・
# 面積約3.2%に相当する。役割が明示された無文字のdetailだけ緩和する。
MIN_PANEL_WIDTH_RATIO = 0.18
MIN_PANEL_HEIGHT_RATIO = 0.13
MIN_PANEL_AREA_RATIO = 0.032
MAX_PANEL_ASPECT_RATIO = 5.5
MIN_DETAIL_WIDTH_RATIO = 0.08
MIN_DETAIL_HEIGHT_RATIO = 0.07
MIN_DETAIL_AREA_RATIO = 0.02
MAX_DETAIL_ASPECT_RATIO = 10.0

ELLIPTICAL_CLIP_SHAPES = {"ellipse", "oval", "circle"}
_DETAIL_ROLE_WORDS = (
    "detail", "insert", "eye", "hand", "instrument", "close-up",
    "目のアップ", "手元", "器具", "小物", "詳細", "つなぎ", "トランジション",
)


def panel_allows_detail_geometry(panel: Mapping[str, Any]) -> bool:
    """明示された無文字のdetailコマだけ、通常の細片寸法制約を緩和する。"""

    if any(panel.get(key) for key in ("dialogue", "narration", "sfx")):
        return False
    role_text = " ".join(
        str(panel.get(key) or "")
        for key in ("panel_role", "role", "panel_type", "scene_type", "shot_type", "action", "description")
    ).lower()
    return any(word in role_text for word in _DETAIL_ROLE_WORDS)


def is_explicit_elliptical_inset(breakout: Mapping[str, Any]) -> bool:
    """楕円を許す唯一の例外。曖昧な旧clip_shapeだけでは許可しない。"""

    clip_shape = str(breakout.get("clip_shape") or breakout.get("mask_shape") or "").strip().lower()
    effect = str(breakout.get("semantic_effect") or "").strip().lower()
    return bool(
        clip_shape in ELLIPTICAL_CLIP_SHAPES
        and effect in {"inset", "memory", "pov"}
        and breakout.get("explicitly_requested") is True
        and breakout.get("is_inset") is True
        and breakout.get("main_panel_retained") is True
        and breakout.get("crop_protection_validated") is True
        and str(breakout.get("semantic_reason") or "").strip()
    )


def breakout_uses_elliptical_mask(breakout: Mapping[str, Any]) -> bool:
    values = [
        str(breakout.get(key) or "").strip().lower()
        for key in ("clip_shape", "mask_shape", "shape", "clip_path", "mask")
    ]
    if any(
        value in ELLIPTICAL_CLIP_SHAPES
        or any(f"{shape}(" in value for shape in ELLIPTICAL_CLIP_SHAPES)
        for value in values
    ):
        return True
    radius = str(breakout.get("border_radius") or breakout.get("border-radius") or "").strip().lower()
    return radius in {"50%", "9999px", "999px", "50vw", "50vh"} or "ellipse(" in radius

SEMANTIC_FAMILIES = {
    "dialogue",
    "action",
    "comedy",
    "psychological",
    "establishing",
    "climax",
}

SEMANTIC_DEFAULT_BUDGET = {
    "angled_panels": 0,
    "character_breakouts": 0,
    "bubble_breakouts": 0,
    "large_page_overlays": 0,
}

SEMANTIC_FAMILY_BUDGETS = {
    "dialogue": {**SEMANTIC_DEFAULT_BUDGET},
    "psychological": {**SEMANTIC_DEFAULT_BUDGET},
    "establishing": {**SEMANTIC_DEFAULT_BUDGET},
    "action": {
        "angled_panels": 1,
        "character_breakouts": 1,
        "bubble_breakouts": 1,
        "large_page_overlays": 0,
    },
    "comedy": {
        "angled_panels": 0,
        "character_breakouts": 1,
        "bubble_breakouts": 1,
        "large_page_overlays": 1,
    },
    "climax": {
        "angled_panels": 1,
        "character_breakouts": 1,
        "bubble_breakouts": 1,
        "large_page_overlays": 1,
    },
}

_SEMANTIC_ACTION_WORDS = (
    "action", "fight", "chase", "impact", "rapid", "movement", "戦闘", "バトル",
    "追跡", "走", "飛", "殴", "斬", "爆発", "衝突", "動き",
)
_SEMANTIC_COMEDY_WORDS = (
    "comedy", "comic", "punchline", "gag", "comedy beat", "コメディ", "ギャグ",
    "オチ", "笑", "おかし", "冗談",
)
_SEMANTIC_PSYCHOLOGICAL_WORDS = (
    "psychological", "emotion", "emotional", "silence", "close-up", "quiet",
    "心理", "葛藤", "余韻", "沈黙", "涙", "決意", "顔アップ", "目のアップ",
)
_SEMANTIC_ESTABLISHING_WORDS = (
    "establishing", "location", "landscape", "setting", "導入", "風景", "場所", "背景",
)
_SEMANTIC_CLIMAX_WORDS = (
    "climax", "reveal", "decisive", "emotional peak", "クライマックス", "真相", "正体",
    "決着", "決定的", "告白", "衝撃", "大事件",
)


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


def _semantic_text(value: Any) -> str:
    """意味判定に使う短いテキストを作る。本文や画像データは含めない。"""

    if isinstance(value, Mapping):
        return " ".join(_semantic_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_semantic_text(item) for item in value)
    return str(value or "").strip().lower()


def _semantic_contains(text: str, words: Sequence[str]) -> bool:
    """英単語の部分一致（reaction内のaction等）を避けて意味語を探す。"""

    normalized = str(text or "").lower()
    for word in words:
        token = str(word or "").strip().lower()
        if not token:
            continue
        if all(ord(char) < 128 for char in token):
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", normalized):
                return True
        elif token in normalized:
            return True
    return False


def _semantic_panel_text(panel: Mapping[str, Any]) -> str:
    """意味分類に使うStoryboard項目だけを抽出する（Prompt／人物全文は除外）。"""

    values: list[Any] = [
        panel.get("panel_role"),
        panel.get("role"),
        panel.get("scene_type"),
        panel.get("description"),
        panel.get("shot_type"),
        panel.get("action"),
        panel.get("expression"),
        panel.get("sfx"),
    ]
    return " ".join(_semantic_text(value) for value in values if value is not None)


def semantic_page_family(page: Mapping[str, Any]) -> str:
    """Storyboardの意味メタデータからレイアウト系統を決める。

    明示されたscene_type/page_roleを優先し、未指定の旧データは内容から安全に
    推定する。特殊効果を増やすための判定ではなく、抑制するための分類である。
    """

    explicit = _semantic_text(
        {
            "scene_type": page.get("scene_type"),
            "page_role": page.get("page_role"),
            "layout_family": page.get("layout_family"),
            "layout": page.get("layout"),
        }
    )
    panel_text = " ".join(
        _semantic_panel_text(panel)
        for panel in page.get("panels", [])
        if isinstance(panel, Mapping)
    )
    all_text = " ".join(
        [
            explicit,
            _semantic_text(page.get("emotion")),
            _semantic_text(page.get("action_intensity")),
            _semantic_text(page.get("reveal")),
            _semantic_text(page.get("comedy")),
            _semantic_text(page.get("climax")),
            _semantic_text(page.get("title")),
            panel_text,
        ]
    )
    if page.get("climax") or page.get("reveal") or _semantic_contains(all_text, _SEMANTIC_CLIMAX_WORDS):
        return "climax"
    if page.get("comedy") or _semantic_contains(all_text, _SEMANTIC_COMEDY_WORDS):
        return "comedy"
    if _semantic_contains(explicit, ("action", "アクション")) or _semantic_contains(all_text, _SEMANTIC_ACTION_WORDS):
        return "action"
    if _semantic_contains(explicit, ("psychological", "心理")) or _semantic_contains(all_text, _SEMANTIC_PSYCHOLOGICAL_WORDS):
        return "psychological"
    if _semantic_contains(explicit, ("establishing", "導入")) or _semantic_contains(all_text, _SEMANTIC_ESTABLISHING_WORDS):
        return "establishing"
    panels = [item for item in page.get("panels", []) if isinstance(item, Mapping)]
    dialogue_count = sum(len(item.get("dialogue", [])) for item in panels)
    if explicit in {"conversation", "dialogue"} or dialogue_count >= max(3, len(panels)):
        return "dialogue"
    return "dialogue"


def semantic_effect_budget(page: Mapping[str, Any]) -> Dict[str, int]:
    """ページの意味に応じた装飾上限を返す。未指定は保守的に0とする。"""

    family = semantic_page_family(page)
    budget = dict(SEMANTIC_FAMILY_BUDGETS.get(family, SEMANTIC_DEFAULT_BUDGET))
    raw = page.get("composition_budget") or page.get("effect_budget")
    if isinstance(raw, Mapping):
        # ユーザー／AIの指定は上限を緩めるためではなく、各ページの文脈に
        # 合わせて同じか少ない演出へ調整する場合だけ受け付ける。
        special_emphasis = bool(page.get("special_emphasis")) and family in {"action", "comedy", "climax"}
        for key, default in budget.items():
            try:
                requested = int(raw.get(key, default))
            except (TypeError, ValueError):
                requested = default
            # 静かな会話／心理ページへ指定値で斜めコマや越境を持ち込まない。
            # 特別演出を明示したAction/Comedy/ClimaxだけBreakoutを最大2件に
            # 拡張し、それ以外は意味的ファミリーの既定値を上限とする。
            upper = 2 if key == "character_breakouts" and special_emphasis else default
            budget[key] = max(0, min(requested, upper))
    return budget


def semantic_dominant_panel_index(page: Mapping[str, Any]) -> int | None:
    """意味上の主役コマを一つだけ返す。会話ページでは強制しない。"""

    family = semantic_page_family(page)
    panels = [item for item in page.get("panels", []) if isinstance(item, Mapping)]
    if not panels or family in {"dialogue", "psychological"} and not (page.get("climax") or page.get("reveal")):
        return None
    if family not in {"action", "comedy", "climax", "establishing"} and not page.get("dominant_panel_id"):
        return None
    explicit_id = str(page.get("dominant_panel_id") or "").strip()
    if explicit_id:
        for index, panel in enumerate(panels):
            if str(panel.get("id")) == explicit_id:
                return index
    ranked = sorted(
        enumerate(panels),
        key=lambda item: (_importance_rank(item[1].get("importance")), -item[0]),
        reverse=True,
    )
    index, panel = ranked[0]
    if _importance_rank(panel.get("importance")) < 3:
        return None
    return index


def semantic_text_safe_zones(panel: Mapping[str, Any], language: str = "ja") -> Dict[str, Dict[str, float]]:
    """Panel内の文字予約領域を返す（すべてPanel相対座標）。"""

    supplied = panel.get("text_safe_zones")
    result: Dict[str, Dict[str, float]] = {}
    if isinstance(supplied, Mapping):
        for key in ("bubble", "narration", "sfx"):
            raw = supplied.get(key)
            if isinstance(raw, Mapping):
                try:
                    zone = {field: _clamp(float(raw[field]), 0.0, 1.0) for field in ("x", "y", "width", "height")}
                except (KeyError, TypeError, ValueError):
                    continue
                if zone["width"] > 0 and zone["height"] > 0:
                    result[key] = zone
    character_side = str(panel.get("character_position") or panel.get("subject_position") or panel.get("face_position") or "").lower()
    character_on_right = any(word in character_side for word in ("right", "右"))
    character_on_left = any(word in character_side for word in ("left", "左"))
    if "bubble" not in result:
        # 顔の反対側に短い吹き出し領域を予約し、画像生成Promptと配置計算で共有する。
        result["bubble"] = {
            "x": 0.06 if character_on_right else 0.56 if character_on_left else (0.06 if language == "en" else 0.54),
            "y": 0.06,
            "width": 0.36,
            "height": 0.24,
        }
    result.setdefault("narration", {"x": 0.06, "y": 0.72, "width": 0.38, "height": 0.16})
    result.setdefault("sfx", {"x": 0.06 if language == "en" else 0.52, "y": 0.40, "width": 0.30, "height": 0.16})
    return result


def semantic_protected_zones(panel: Mapping[str, Any]) -> List[Dict[str, float]]:
    """顔・重要な手／小物を保護するPanel相対領域。"""

    supplied = panel.get("protected_zones")
    if isinstance(supplied, list):
        zones: List[Dict[str, float]] = []
        for raw in supplied[:8]:
            if not isinstance(raw, Mapping):
                continue
            try:
                zone = {field: _clamp(float(raw[field]), 0.0, 1.0) for field in ("x", "y", "width", "height")}
            except (KeyError, TypeError, ValueError):
                continue
            if zone["width"] > 0 and zone["height"] > 0:
                zones.append(zone)
        if zones:
            return zones
    position = str(panel.get("face_position") or panel.get("subject_position") or "").lower()
    if any(word in position for word in ("left", "左")):
        return [{"x": 0.08, "y": 0.22, "width": 0.42, "height": 0.38}]
    if any(word in position for word in ("right", "右")):
        return [{"x": 0.50, "y": 0.22, "width": 0.42, "height": 0.38}]
    if panel.get("characters"):
        return [{"x": 0.30, "y": 0.22, "width": 0.40, "height": 0.38}]
    return []


def semantic_shape_plan(
    page: Mapping[str, Any],
    panel: Mapping[str, Any],
    index: int,
    count: int,
    dominant_index: int | None,
    angled_used: int,
) -> tuple[str, str, int]:
    """v3の形状を決める。特殊形状には必ず意味的な理由を添える。"""

    explicit = str(panel.get("panel_shape") or panel.get("shape") or "").strip().lower()
    reason = str(panel.get("shape_reason") or panel.get("semantic_reason") or "").strip()[:160]
    budget = semantic_effect_budget(page)
    family = semantic_page_family(page)
    if explicit in {"trapezoid", "slanted-left", "slanted-right", "polygon", "large-bleed"}:
        if explicit == "large-bleed" and family != "climax" and not page.get("special_emphasis"):
            return "rectangle", "", angled_used
        if not reason or (angled_used >= budget["angled_panels"] and explicit != "large-bleed"):
            return "rectangle", "", angled_used
        return ("polygon" if explicit == "large-bleed" else explicit), reason, angled_used + (0 if explicit == "large-bleed" else 1)
    if explicit in {"wide", "tall", "rectangle"}:
        return explicit, "", angled_used
    # 意味が明確なページでも、斜め要素は1つまで。面積差を主役にする。
    if family in {"action", "climax"} and dominant_index == index and angled_used < budget["angled_panels"]:
        return "slanted-left", "impact transition" if family == "action" else "climax emphasis", angled_used + 1
    return "rectangle", "", angled_used


def _rect_intersects(left: Mapping[str, Any], right: Mapping[str, Any], padding: float = 0.0) -> bool:
    return not (
        _float(left.get("x")) + _float(left.get("width")) + padding <= _float(right.get("x"))
        or _float(right.get("x")) + _float(right.get("width")) + padding <= _float(left.get("x"))
        or _float(left.get("y")) + _float(left.get("height")) + padding <= _float(right.get("y"))
        or _float(right.get("y")) + _float(right.get("height")) + padding <= _float(left.get("y"))
    )


def _intersection_area(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    width = max(0.0, min(_float(left.get("x")) + _float(left.get("width")), _float(right.get("x")) + _float(right.get("width"))) - max(_float(left.get("x")), _float(right.get("x"))))
    height = max(0.0, min(_float(left.get("y")) + _float(left.get("height")), _float(right.get("y")) + _float(right.get("height"))) - max(_float(left.get("y")), _float(right.get("y"))))
    return width * height


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


def _page_protected_zones(
    page_panels: Sequence[Mapping[str, Any]],
    composition_panels: Sequence[Mapping[str, Any]],
) -> List[Dict[str, float]]:
    """すべてのPanelの重要領域をページ座標へ写す。"""

    panel_by_id = {str(panel.get("id")): panel for panel in page_panels if isinstance(panel, Mapping)}
    result: List[Dict[str, float]] = []
    for geometry in composition_panels:
        if not isinstance(geometry, Mapping):
            continue
        source = panel_by_id.get(str(geometry.get("panel_id")), {})
        local_zones = geometry.get("protected_zones")
        if not isinstance(local_zones, list) or not local_zones:
            local_zones = semantic_protected_zones(source)
        for zone in local_zones:
            if not isinstance(zone, Mapping):
                continue
            result.append(
                {
                    "x": _float(geometry.get("x")) + _float(zone.get("x")) * _float(geometry.get("width")),
                    "y": _float(geometry.get("y")) + _float(zone.get("y")) * _float(geometry.get("height")),
                    "width": _float(zone.get("width")) * _float(geometry.get("width")),
                    "height": _float(zone.get("height")) * _float(geometry.get("height")),
                }
            )
    return result


def _page_text_rect(candidate: Mapping[str, Any], geometry: Mapping[str, Any]) -> Dict[str, float]:
    return {
        "x": _float(geometry.get("x")) + _float(candidate.get("x")) * _float(geometry.get("width")),
        "y": _float(geometry.get("y")) + _float(candidate.get("y")) * _float(geometry.get("height")),
        "width": _float(candidate.get("width")) * _float(geometry.get("width")),
        "height": _float(candidate.get("height")) * _float(geometry.get("height")),
    }


def _page_text_rect_is_safe(
    rect: Mapping[str, Any],
    protected_zones: Sequence[Mapping[str, Any]],
    overlays: Sequence[Mapping[str, Any]],
) -> bool:
    if (
        _float(rect.get("x")) < PAGE_SAFE_MARGIN
        or _float(rect.get("y")) < PAGE_SAFE_MARGIN
        or _float(rect.get("x")) + _float(rect.get("width")) > 1 - PAGE_SAFE_MARGIN
        or _float(rect.get("y")) + _float(rect.get("height")) > 1 - PAGE_SAFE_MARGIN
    ):
        return False
    if any(_intersection_area(rect, zone) > 0.00001 for zone in protected_zones):
        return False
    return not any(
        str(item.get("type")) != "title" and _rect_intersects(rect, item)
        for item in overlays
        if isinstance(item, Mapping)
    )


def _build_v2_page_composition(
    page: Mapping[str, Any],
    geometries: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """保存用のPageComposition v2を作る。Artwork自体は変更しない。"""

    panels = [panel for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    geometry_by_id = {str(item.get("panel_id")): item for item in geometries if isinstance(item, Mapping)}
    composition_panels: List[Dict[str, Any]] = []
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
            "area": _round(box["width"] * box["height"]),
            "bounding_box": dict(box),
            "polygon_points": points,
            "shape": shape,
            "z_index": int(geometry.get("z_index", 1)),
            "row": int(geometry.get("row", 1) or 1),
            "column": int(geometry.get("column", index + 1) or index + 1),
            "bleed": bool(geometry.get("bleed", False)),
            "gutter": deepcopy(geometry.get("gutter") or {"type": "normal", "width": 0.014}),
            "reading_order": int(panel.get("order", index + 1) or index + 1),
            "importance": importance,
            "allow_breakout": bool(geometry.get("allow_breakout", False)),
            "crop_anchor_x": anchor["x"],
            "crop_anchor_y": anchor["y"],
            "protected_zones": deepcopy(geometry.get("protected_zones") or semantic_protected_zones(panel)),
            "artwork_viewport": deepcopy(geometry.get("artwork_viewport")),
            "artwork_coverage_target": ARTWORK_COVERAGE_TARGET,
        }
        composition_panels.append(item)

    # 生成済みArtworkを切り抜いた人物画像として扱うことはできない。
    # v2の自動Breakoutは楕円マスクと誤った重ね合わせを作るため、新規には作らない。
    breakouts: List[Dict[str, Any]] = []

    overlays: List[Dict[str, Any]] = []
    page_number = int(page.get("page_number", 1) or 1)
    if (page_number == 1 or page.get("page_kind") == "cover") and page.get("show_title", True) and str(page.get("title", "")).strip():
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

    # コマ外へ移すのは、ページ意味情報で明示された要素だけにする。
    moved_items: set[tuple[str, str]] = set()
    protected_global = _page_protected_zones(panels, composition_panels)
    for geometry in composition_panels:
        panel = next((item for item in panels if str(item.get("id")) == geometry["panel_id"]), {})
        if _importance_rank(geometry.get("importance")) < 3:
            continue
        text_layout = panel.get("text_layout") if isinstance(panel, Mapping) else None
        text_items = text_layout.get("items", []) if isinstance(text_layout, Mapping) else []
        candidate = next((item for item in text_items if isinstance(item, Mapping) and item.get("type") == "bubble"), None)
        bubble_reason = str(page.get("bubble_breakout_reason") or "").strip()
        if candidate and page.get("bubble_breakout") is True and bubble_reason:
            rect = _page_text_rect(candidate, geometry)
            if _page_text_rect_is_safe(rect, protected_global, overlays):
                overlays.append(
                    {
                        "id": f"overlay-{geometry['panel_id']}-{candidate.get('id', 'bubble-1')}",
                        "type": "bubble",
                        "text": str(candidate.get("text", "")),
                        "source_panel_id": geometry["panel_id"],
                        "source_item_id": str(candidate.get("id", "")),
                        **{key: _round(value) for key, value in rect.items()},
                        "rotation": 0,
                        "z_index": 4,
                        "reading_priority": int(candidate.get("order", 1) or 1),
                        "breakout": True,
                        "reason": bubble_reason[:160],
                    }
                )
                moved_items.add((geometry["panel_id"], str(candidate.get("id", ""))))
        sfx = next(
            (
                item for item in text_items
                if isinstance(item, Mapping)
                and item.get("type") == "sfx"
                and item.get("breakout") is True
                and str(item.get("breakout_reason") or "").strip()
            ),
            None,
        )
        if sfx:
            rect = _page_text_rect(sfx, geometry)
            if _page_text_rect_is_safe(rect, protected_global, overlays):
                overlays.append(
                    {
                        "id": f"overlay-{geometry['panel_id']}-{sfx.get('id', 'sfx-1')}",
                        "type": "sfx",
                        "text": str(sfx.get("text", "")),
                        "source_panel_id": geometry["panel_id"],
                        "source_item_id": str(sfx.get("id", "")),
                        **{key: _round(value) for key, value in rect.items()},
                        "rotation": -8,
                        "z_index": 5,
                        "reading_priority": int(sfx.get("order", 1) or 1),
                        "breakout": True,
                        "reason": str(sfx.get("breakout_reason"))[:160],
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


def _build_v3_page_composition(
    page: Mapping[str, Any],
    geometries: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """意味的ポリシーを適用したPageComposition v3を作る。"""

    settings = settings or {}
    language = str(settings.get("language") or "ja").lower()
    panels = [panel for panel in page.get("panels", []) if isinstance(panel, Mapping)]
    geometry_by_id = {str(item.get("panel_id")): item for item in geometries if isinstance(item, Mapping)}
    family = semantic_page_family(page)
    budget = semantic_effect_budget(page)
    dominant_index = next((i for i, g in enumerate(geometries) if g.get('dominant')), None) if page.get('dynamic_layout') else semantic_dominant_panel_index(page)
    composition_panels: List[Dict[str, Any]] = []
    for index, panel in enumerate(panels):
        panel_id = str(panel.get("id", ""))
        geometry = geometry_by_id.get(panel_id) or panel.get("geometry") or {}
        box = {
            "x": _round(_float(geometry.get("x"))),
            "y": _round(_float(geometry.get("y"))),
            "width": _round(_float(geometry.get("width"))),
            "height": _round(_float(geometry.get("height"))),
        }
        # 形状はreflow_pageで決定済み。理由のない特殊形状はここでも矩形へ戻す。
        shape = str(geometry.get("shape") or "rectangle")
        shape_reason = str(geometry.get("shape_reason") or panel.get("shape_reason") or "").strip()[:160]
        if shape not in {"rectangle", "wide", "tall"} and not shape_reason:
            shape = "rectangle"
        points = shape_points(box, shape) if shape == "rectangle" else normalize_polygon(geometry.get("polygon_points"), {**box, "shape": shape})
        importance = str(panel.get("importance") or geometry.get("importance") or "medium")
        text_zones = semantic_text_safe_zones(panel, language)
        protected_zones = geometry.get("protected_zones") or semantic_protected_zones(panel)
        item = {
            "panel_id": panel_id,
            "row": geometry.get("row"),
            "base_box": deepcopy(geometry.get("base_box")),
            "full_bleed_effect": bool(geometry.get("full_bleed_effect")),
            "shared_edge_ids": deepcopy(geometry.get("shared_edge_ids", [])),
            **box,
            "area": _round(box["width"] * box["height"]),
            "bounding_box": dict(box),
            "polygon_points": points,
            "shape": shape,
            "shape_reason": shape_reason,
            "semantic_family": family,
            "dominant": dominant_index == index,
            "z_index": int(geometry.get("z_index", 1)),
            "bleed": bool(geometry.get("bleed", False)),
            "gutter": deepcopy(geometry.get("gutter") or {"type": "normal", "width": 0.014}),
            "reading_order": int(panel.get("order", index + 1) or index + 1),
            "importance": importance,
            "allow_breakout": bool(geometry.get("allow_breakout", False)),
            "crop_anchor_x": str(geometry.get("crop_anchor_x") or "center"),
            "crop_anchor_y": str(geometry.get("crop_anchor_y") or "middle"),
            "text_safe_zones": text_zones,
            "protected_zones": protected_zones,
            "artwork_viewport": deepcopy(geometry.get("artwork_viewport")),
            "intentional_whitespace": bool(geometry.get("artwork_viewport")),
            "whitespace_reason": "reserved_text_band" if geometry.get("artwork_viewport") else "",
            "artwork_coverage_target": ARTWORK_COVERAGE_TARGET,
        }
        composition_panels.append(item)

    # 元画像を楕円で複製しても人物切り抜きにはならない。
    # 専用前景素材のない自動再配置ではBreakoutを作らず、元Artworkを保つ。
    breakouts: List[Dict[str, Any]] = []

    overlays: List[Dict[str, Any]] = []
    page_number = int(page.get("page_number", 1) or 1)
    # タイトルは1ページ目の明示タイトルだけ。大きなPage overlayは意味的イベント時のみ。
    if (page_number == 1 or page.get("page_kind") == "cover") and page.get("show_title", True) and str(page.get("title", "")).strip():
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
                "semantic_family": family,
            }
        )

    # 吹き出しはPanel内を優先し、v3では明示されたemphasis時のみgutterへ移す。
    moved_items: set[tuple[str, str]] = set()
    bubble_budget = budget["bubble_breakouts"]
    if bubble_budget > 0 and family in {"action", "comedy", "climax"}:
        dominant_geometry = next((item for item in composition_panels if item.get("dominant")), None)
        dominant_panel = next((item for item in panels if str(item.get("id")) == str((dominant_geometry or {}).get("panel_id"))), None)
        text_layout = dominant_panel.get("text_layout") if isinstance(dominant_panel, Mapping) else None
        text_items = text_layout.get("items", []) if isinstance(text_layout, Mapping) else []
        candidate = next((item for item in text_items if isinstance(item, Mapping) and item.get("type") == "bubble"), None)
        bubble_reason = str(page.get("bubble_breakout_reason") or "").strip()
        if candidate and dominant_geometry and not dominant_geometry.get("artwork_viewport") and page.get("bubble_breakout") is True and bubble_reason:
            rect = _page_text_rect(candidate, dominant_geometry)
            protected_global = _page_protected_zones(panels, composition_panels)
            if not candidate.get("overflow") and _page_text_rect_is_safe(rect, protected_global, overlays):
                overlays.append(
                    {
                        "id": f"overlay-{dominant_geometry['panel_id']}-{candidate.get('id', 'bubble-1')}",
                        "type": "bubble",
                        "text": str(candidate.get("text", "")),
                        "source_panel_id": dominant_geometry["panel_id"],
                        "source_item_id": str(candidate.get("id", "")),
                        **{key: _round(value) for key, value in rect.items()},
                        "rotation": 0,
                        "z_index": 4,
                        "reading_priority": int(candidate.get("order", 1) or 1),
                        "breakout": True,
                        "reason": bubble_reason[:160],
                    }
                )
                moved_items.add((dominant_geometry["panel_id"], str(candidate.get("id", ""))))

    # 大きなTypographyはclimax/comedyで明示された場合だけ許可する。
    if budget["large_page_overlays"] > 0 and family in {"comedy", "climax"} and page.get("page_overlay_text") and not any(item.get("artwork_viewport") for item in composition_panels):
        overlays.append(
            {
                "id": "semantic-page-overlay",
                "type": "sfx",
                "text": str(page.get("page_overlay_text"))[:80],
                "x": 0.52,
                "y": 0.40,
                "width": 0.34,
                "height": 0.14,
                "rotation": -6,
                "z_index": 5,
                "reading_priority": 1,
                "breakout": True,
                "reason": str(page.get("page_overlay_reason") or "semantic emphasis")[:160],
            }
        )

    for overlay in overlays:
        overlay["x"] = _round(_clamp(_float(overlay.get("x")), 0.0, 1.0))
        overlay["y"] = _round(_clamp(_float(overlay.get("y")), 0.0, 1.0))
        overlay["width"] = _round(_clamp(_float(overlay.get("width"), 0.2), 0.01, 1.0 - overlay["x"]))
        overlay["height"] = _round(_clamp(_float(overlay.get("height"), 0.1), 0.01, 1.0 - overlay["y"]))

    return {
        "composition_version": SEMANTIC_COMPOSITION_VERSION,
        "policy": "semantic",
        "semantic_family": family,
        "effect_budget": budget,
        "dominant_panel_id": next((item.get("panel_id") for item in composition_panels if item.get("dominant")), None),
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


def build_page_composition(
    page: Mapping[str, Any],
    geometries: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
    *,
    composition_version: int = COMPOSITION_VERSION,
) -> Dict[str, Any]:
    """指定Versionに応じてv2互換または意味的v3を作る。"""

    if composition_version >= SEMANTIC_COMPOSITION_VERSION:
        from .visual_style import resolve_visual_style

        composition = _build_v3_page_composition(page, geometries, settings)
        if composition_version >= DYNAMIC_COMPOSITION_VERSION:
            composition['composition_version'] = DYNAMIC_COMPOSITION_VERSION
            composition.update(deepcopy(page.get('dynamic_layout') or {}))
            composition['effect_budget']['angled_panels'] = 2 if composition.get('shared_edges') else 0
        composition["style_profile"] = resolve_visual_style(settings or {})
        composition["page_direction"] = {
            "page_role": str(page.get("page_role") or composition["semantic_family"]),
            "dominant_panel": composition["dominant_panel_id"],
            "panel_count": len(geometries),
            "reading_order": [item.get("panel_id") for item in geometries],
            "panel_area_weights": {str(item.get("panel_id")): _round(_float(item.get("width")) * _float(item.get("height"))) for item in geometries},
            "status": "needs_revision" if any((panel.get("panel_direction") or {}).get("status") == "needs_revision" for panel in page.get("panels", [])) else "ready",
        }
        return composition
    return _build_v2_page_composition(page, geometries, settings)


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
            result = deepcopy(dict(composition))
            safe_breakouts = []
            for raw in result.get("breakouts", []):
                if not isinstance(raw, Mapping):
                    continue
                breakout = deepcopy(dict(raw))
                if breakout_uses_elliptical_mask(breakout) and not is_explicit_elliptical_inset(breakout):
                    # Compatibility is render-time only: keep the persisted project untouched.
                    breakout["enabled"] = False
                    breakout["render_suppressed"] = True
                    breakout["render_suppressed_reason"] = "ellipse_without_valid_semantic_inset"
                safe_breakouts.append(breakout)
            result["breakouts"] = safe_breakouts
            if version < DYNAMIC_COMPOSITION_VERSION and result.get('panels'):
                panels = result['panels']
                safe = dict(left=min(p['x'] for p in panels), right=max(p['x']+p['width'] for p in panels),
                            top=min(p['y'] for p in panels), bottom=max(p['y']+p['height'] for p in panels))
                for panel in panels:
                    points = panel.get('polygon_points', [])
                    if len(points) == 4:
                        panel['polygon_points'] = lock_outer_edges(points, panel, safe)
            return result
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
    # 目視で確認した背景文字だけを報告する。未確認を自動検出済みと偽らない。
    for source in page.get("panels", []):
        review = source.get("in_world_text_review") or {}
        if review.get("incidental_generated_text") is True:
            issues.append({"key": f"INCIDENTAL_GENERATED_TEXT-{source.get('id')}",
                           "label": "背景に意図しない生成文字があります",
                           "detail": "看板・ラベル等の文字を目視で確認しました。生成前の背景文字方針を見直してください。"})
    if len(panels) != len([item for item in page.get("panels", []) if isinstance(item, Mapping)]):
        issues.append({"key": f"composition-panel-count-{page_number}", "label": f"ページ{page_number}のComposition", "detail": "PanelとCompositionの件数が一致しません。"})
        return issues
    template = str((page.get("layout_geometry") or {}).get("template", "")) if isinstance(page.get("layout_geometry"), Mapping) else ""
    areas = []
    source_panels = [item for item in page.get("panels", []) if isinstance(item, Mapping)]
    source_by_id = {str(item.get("id")): item for item in source_panels}
    geometry_rects: List[Dict[str, float]] = []
    for index, panel in enumerate(panels, start=1):
        points = normalize_polygon(panel.get("polygon_points"), panel)
        area = polygon_area(points)
        areas.append(area)
        rect = {key: _float(panel.get(key)) for key in ("x", "y", "width", "height")}
        geometry_rects.append(rect)
        if len(points) < 3 or area <= 0.0005:
            issues.append({"key": f"composition-polygon-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の形状", "detail": "有効なpolygon geometryがありません。"})
        if not panel.get("bleed") and any(point[0] < PAGE_SAFE_MARGIN * 0.5 or point[0] > 1 - PAGE_SAFE_MARGIN * 0.5 or point[1] < PAGE_SAFE_MARGIN * 0.5 or point[1] > 1 - PAGE_SAFE_MARGIN * 0.5 for point in points):
            issues.append({"key": f"composition-safe-margin-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}のセーフ領域", "detail": "Panelがページのセーフマージンを越えています。"})
        if _float(panel.get("artwork_coverage", 1.0), 1.0) < ARTWORK_COVERAGE_TARGET and not panel.get("intentional_whitespace"):
            issues.append({"key": f"composition-coverage-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の画像密度", "detail": "通常コマのArtwork coverageが95%未満です。cover cropまたは焦点の再計算が必要です。"})
        source_panel = source_by_id.get(str(panel.get("panel_id")), {})
        detail_exception = panel_allows_detail_geometry(source_panel)
        min_width = MIN_DETAIL_WIDTH_RATIO if detail_exception else MIN_PANEL_WIDTH_RATIO
        min_height = MIN_DETAIL_HEIGHT_RATIO if detail_exception else MIN_PANEL_HEIGHT_RATIO
        min_area = MIN_DETAIL_AREA_RATIO if detail_exception else MIN_PANEL_AREA_RATIO
        max_aspect = MAX_DETAIL_ASPECT_RATIO if detail_exception else MAX_PANEL_ASPECT_RATIO
        physical_width = rect["width"] * PAGE_SIZE[0]
        physical_height = rect["height"] * PAGE_SIZE[1]
        aspect = physical_width / physical_height if physical_height > 0 else float("inf")
        if (
            rect["width"] < min_width
            or rect["height"] < min_height
            or area < min_area
            or aspect > max_aspect
        ):
            issues.append({"key": f"composition-sliver-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の最小寸法", "detail": "Panelがページ寸法に対する読みやすさの下限を満たしません。detailコマは役割を明示し、文字を含めないでください。"})

    for left_index, left in enumerate(geometry_rects):
        for right_index, right in enumerate(geometry_rects[left_index + 1 :], start=left_index + 2):
            if (_intersection_area(left, right) > 0.0001 and polygons_overlap(normalize_polygon(panels[left_index].get("polygon_points"), left), normalize_polygon(panels[right_index-1].get("polygon_points"), right))):
                issues.append({"key": f"composition-panel-overlap-{page_number}-{left_index + 1}-{right_index}", "label": f"ページ{page_number}のコマ重複", "detail": "Panel領域同士が重なっています。"})

    if version >= DYNAMIC_COMPOSITION_VERSION:
        metrics = {**geometry_metrics(composition), **polygon_safety_metrics(page)}
        for key in ('outer_edge_slant_count', 'boundary_alignment_error', 'gutter_consistency_error', 'polygon_protected_clip_count', 'polygon_text_clip_count'):
            if metrics[key]:
                issues.append({'key': f'composition-boundary-{key}-{page_number}', 'label': f'ページ{page_number}の共有境界', 'detail': '外周または共有ガターが一致していません。配置を再計算してください。'})
    orders = [int(_float(panel.get("reading_order"), 0)) for panel in panels]
    expected_ids = [str(item.get("panel_id")) for item in sorted(panels, key=lambda item: int(_float(item.get("reading_order"), 0)))]
    layout_geometry = page.get("layout_geometry") if isinstance(page.get("layout_geometry"), Mapping) else {}
    direction = str(layout_geometry.get("reading_direction") or "right_to_left")
    rtl = direction != "left_to_right"
    geometric_ids = [
        str(item.get("panel_id"))
        for item in sorted(
            panels,
            key=lambda item: (
                int(_float(item.get("row"), round(_float(item.get("y")) / 0.02))),
                -_float(item.get("x")) if rtl else _float(item.get("x")),
                int(_float(item.get("reading_order"), 0)),
            ),
        )
    ]
    if (
        len(set(orders)) != len(orders)
        or any(order <= 0 for order in orders)
        or geometric_ids != expected_ids
    ):
        issues.append({"key": f"composition-reading-order-{page_number}", "label": f"ページ{page_number}の読順", "detail": "Panel位置と保存された読順が一致しないか、一意に決められません。"})

    ellipse_masks = [
        item for item in composition.get("breakouts", [])
        if isinstance(item, Mapping)
        and item.get("enabled", True)
        and breakout_uses_elliptical_mask(item)
    ]
    invalid_ellipse_masks = [item for item in ellipse_masks if not is_explicit_elliptical_inset(item)]
    for index, _item in enumerate(invalid_ellipse_masks, start=1):
        issues.append({"key": f"composition-ellipse-mask-{page_number}-{index}", "label": f"ページ{page_number}の楕円画像マスク", "detail": "通常Breakoutの楕円マスクは無効です。意味的理由と安全確認を持つ小さなInset以外は矩形・polygonへ変更してください。"})
    if len(ellipse_masks) > 1:
        issues.append({"key": f"composition-ellipse-inset-budget-{page_number}", "label": f"ページ{page_number}の楕円Inset密度", "detail": "楕円Insetは1ページに1件までです。"})
    if version < SEMANTIC_COMPOSITION_VERSION and len(panels) >= 4 and template != "four_panel" and areas and min(areas) > 0 and max(areas) / min(areas) < 1.12:
        issues.append({"key": f"composition-uniform-{page_number}", "label": f"ページ{page_number}の面積階層", "detail": "通常ページのコマ面積が均等すぎます。重要コマに30〜50%程度の面積を与えてください。"})
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
    protected_global = _page_protected_zones(source_panels, panels)
    for panel_index, source_panel in enumerate(source_panels, start=1):
        geometry = next((item for item in panels if str(item.get("panel_id")) == str(source_panel.get("id"))), None)
        if geometry is None:
            continue
        layout = source_panel.get("text_layout") if isinstance(source_panel.get("text_layout"), Mapping) else {}
        for item in layout.get("items", []) if isinstance(layout.get("items"), list) else []:
            if not isinstance(item, Mapping):
                continue
            global_item = _page_text_rect(item, geometry)
            collision = next(
                (zone_index for zone_index, zone in enumerate(protected_global, start=1)
                 if _intersection_area(global_item, zone) > 0.00001),
                None,
            )
            if collision is not None:
                prefix = "composition-v3-face-collision" if version >= SEMANTIC_COMPOSITION_VERSION else "composition-protected-collision"
                issues.append({"key": f"{prefix}-{page_number}-{panel_index}-{item.get('id', collision)}", "label": f"ページ{page_number} コマ{panel_index}の人物保護", "detail": "文字要素が顔・頭・重要な手や小物の保護領域に重なっています。"})
    for overlay_index, overlay in enumerate(overlays, start=1):
        if str(overlay.get("type")) not in {"bubble", "narration", "sfx"}:
            continue
        collision = next(
            (zone_index for zone_index, zone in enumerate(protected_global, start=1)
             if _intersection_area(overlay, zone) > 0.00001),
            None,
        )
        if collision is not None:
            prefix = "composition-v3-overlay-face-collision" if version >= SEMANTIC_COMPOSITION_VERSION else "composition-overlay-subject-collision"
            issues.append({"key": f"{prefix}-{page_number}-{overlay_index}-{collision}", "label": f"ページ{page_number}の文字と人物", "detail": "ページレベルの文字が顔・頭・重要な手や小物を覆っています。Panel内へ戻すか、安全な位置を選んでください。"})
    breakouts = [item for item in composition.get("breakouts", []) if isinstance(item, Mapping) and item.get("enabled", True)]
    for left_index, left in enumerate(breakouts):
        for right in breakouts[left_index + 1 :]:
            if _rect_intersects(left, right, 0.0):
                issues.append({"key": f"composition-breakout-collision-{page_number}-{left.get('id', left_index)}-{right.get('id', 'breakout')}", "label": f"ページ{page_number}のBreakout衝突", "detail": "前景Breakout同士が重なっています。"})
    if page_number != "1" and page.get("page_kind") != "cover" and any(str(item.get("type")) == "title" for item in composition.get("overlays", []) if isinstance(item, Mapping)):
        issues.append({"key": f"composition-title-repeat-{page_number}", "label": f"ページ{page_number}のタイトル", "detail": "2ページ目以降に大きなタイトルを繰り返さないでください。"})

    if version >= SEMANTIC_COMPOSITION_VERSION:
        family = str(composition.get("semantic_family") or semantic_page_family(page))
        budget = composition.get("effect_budget") if isinstance(composition.get("effect_budget"), Mapping) else semantic_effect_budget(page)
        non_rectangles = [item for item in panels if str(item.get("shape", "rectangle")) not in {"rectangle", "wide", "tall"}]
        allowed_angles = max(0, int(budget.get("angled_panels", 0) or 0))
        if len(non_rectangles) > allowed_angles:
            issues.append({"key": f"composition-v3-shape-budget-{page_number}", "label": f"ページ{page_number}の変形コマ密度", "detail": "意味的な上限を超える台形・斜めコマがあります。矩形へ戻してください。"})
        for index, panel in enumerate(panels, start=1):
            shape = str(panel.get("shape", "rectangle"))
            if shape not in {"rectangle", "wide", "tall"} and not str(panel.get("shape_reason", "")).strip():
                issues.append({"key": f"composition-v3-shape-reason-{page_number}-{index}", "label": f"ページ{page_number} コマ{index}の形状理由", "detail": "特殊形状には場面上の理由が必要です。矩形へ戻してください。"})
            if panel.get("dominant"):
                panel_area = polygon_area(panel.get("polygon_points"))
                total_area = sum(areas) or 1.0
                ratio = panel_area / total_area
                if len(panels) > 1 and family in {"action", "comedy", "climax", "establishing"} and not 0.30 <= ratio <= 0.55:
                    issues.append({"key": f"composition-v3-dominant-area-{page_number}-{index}", "label": f"ページ{page_number}の主役コマ面積", "detail": "主役コマはページ面積の30〜50%を目安にしてください。"})
        breakouts_v3 = [item for item in composition.get("breakouts", []) if isinstance(item, Mapping) and item.get("enabled", True)]
        allowed_breakouts = max(0, int(budget.get("character_breakouts", 0) or 0))
        if len(breakouts_v3) > allowed_breakouts:
            issues.append({"key": f"composition-v3-breakout-budget-{page_number}", "label": f"ページ{page_number}のBreakout密度", "detail": "人物Breakoutがページ予算を超えています。不要な越境を無効にしてください。"})
        bubble_breakouts = [
            item for item in overlays
            if str(item.get("type")) == "bubble" and item.get("breakout")
        ]
        allowed_bubble_breakouts = max(0, int(budget.get("bubble_breakouts", 0) or 0))
        if len(bubble_breakouts) > allowed_bubble_breakouts:
            issues.append({"key": f"composition-v3-bubble-budget-{page_number}", "label": f"ページ{page_number}の吹き出し越境密度", "detail": "吹き出しのPanel越境がページ予算を超えています。まずPanel内へ戻してください。"})
        large_overlays = [
            item for item in overlays
            if str(item.get("type")) == "sfx" and item.get("breakout")
        ]
        allowed_large_overlays = max(0, int(budget.get("large_page_overlays", 0) or 0))
        if len(large_overlays) > allowed_large_overlays:
            issues.append({"key": f"composition-v3-overlay-budget-{page_number}", "label": f"ページ{page_number}の大きな文字密度", "detail": "ページレベルのSFX／Typographyが多すぎます。意味的に必要な要素だけを残してください。"})
        for breakout_index, breakout in enumerate(breakouts_v3, start=1):
            if not str(breakout.get("reason", "")).strip():
                issues.append({"key": f"composition-v3-breakout-reason-{page_number}-{breakout_index}", "label": f"ページ{page_number}のBreakout理由", "detail": "Breakoutには意味的な理由が必要です。"})
            source = next((item for item in panels if str(item.get("panel_id")) == str(breakout.get("source_panel_id") or breakout.get("panel_id"))), None)
            if source:
                if _float(breakout.get("width")) > _float(source.get("width"), 1.0) * 1.2 or _float(breakout.get("height")) > _float(source.get("height"), 1.0) * 1.2:
                    issues.append({"key": f"composition-v3-breakout-extension-{page_number}-{breakout_index}", "label": f"ページ{page_number}のBreakout越境量", "detail": "Breakoutが元コマの許容越境量を超えています。"})
        # Breakoutが文字や顔の前へ被らないよう、Panel相対のtext_layoutをページ座標へ変換して確認する。
        for breakout_index, breakout in enumerate(breakouts_v3, start=1):
            source_id = str(breakout.get("source_panel_id") or breakout.get("panel_id"))
            source_geometry = next((item for item in panels if str(item.get("panel_id")) == source_id), None)
            source_panel = next((item for item in page.get("panels", []) if isinstance(item, Mapping) and str(item.get("id")) == source_id), None)
            if not source_geometry or not isinstance(source_panel, Mapping):
                continue
            source_layout = source_panel.get("text_layout") if isinstance(source_panel.get("text_layout"), Mapping) else {}
            for text_item in source_layout.get("items", []) if isinstance(source_layout.get("items"), list) else []:
                if not isinstance(text_item, Mapping):
                    continue
                global_text = {
                    "x": _float(source_geometry.get("x")) + _float(text_item.get("x")) * _float(source_geometry.get("width")),
                    "y": _float(source_geometry.get("y")) + _float(text_item.get("y")) * _float(source_geometry.get("height")),
                    "width": _float(text_item.get("width")) * _float(source_geometry.get("width")),
                    "height": _float(text_item.get("height")) * _float(source_geometry.get("height")),
                }
                if _rect_intersects(breakout, global_text, 0.0):
                    issues.append({"key": f"composition-v3-breakout-text-collision-{page_number}-{breakout_index}-{text_item.get('id', '')}", "label": f"ページ{page_number}のBreakoutと文字", "detail": "人物Breakoutが吹き出し・ナレーション・SFXを覆っています。"})
            # 越境した人物が隣接コマの重要な顔を隠さないようにする。
            for adjacent_geometry in panels:
                if str(adjacent_geometry.get("panel_id")) == source_id:
                    continue
                for zone_index, zone in enumerate(
                    adjacent_geometry.get("protected_zones", [])
                    if isinstance(adjacent_geometry.get("protected_zones"), list)
                    else [],
                    start=1,
                ):
                    if not isinstance(zone, Mapping):
                        continue
                    global_zone = {
                        "x": _float(adjacent_geometry.get("x")) + _float(zone.get("x")) * _float(adjacent_geometry.get("width")),
                        "y": _float(adjacent_geometry.get("y")) + _float(zone.get("y")) * _float(adjacent_geometry.get("height")),
                        "width": _float(zone.get("width")) * _float(adjacent_geometry.get("width")),
                        "height": _float(zone.get("height")) * _float(adjacent_geometry.get("height")),
                    }
                    if _rect_intersects(breakout, global_zone, 0.0):
                        issues.append({"key": f"composition-v3-breakout-face-collision-{page_number}-{breakout_index}-{zone_index}", "label": f"ページ{page_number}のBreakoutと隣接人物", "detail": "人物Breakoutが隣接コマの顔または重要領域を覆っています。"})
        # v3のページレベル文字要素は最後の手段だけにし、理由のない越境を拒否する。
        for overlay_index, overlay in enumerate(overlays, start=1):
            if str(overlay.get("type")) in {"bubble", "narration", "sfx"} and overlay.get("breakout") and not str(overlay.get("reason", "")).strip():
                issues.append({"key": f"composition-v3-overlay-reason-{page_number}-{overlay_index}", "label": f"ページ{page_number}の文字越境理由", "detail": "Panel外へ出す文字要素には意味的な理由が必要です。"})
        if family in {"dialogue", "psychological", "establishing"}:
            if non_rectangles and version < DYNAMIC_COMPOSITION_VERSION:
                issues.append({"key": f"composition-v3-quiet-shape-{page_number}", "label": f"ページ{page_number}の静かな形状", "detail": "会話・心理・導入ページでは特殊形状を抑制してください。"})
            if breakouts_v3:
                issues.append({"key": f"composition-v3-quiet-breakout-{page_number}", "label": f"ページ{page_number}の静かなBreakout", "detail": "会話・心理・導入ページでは人物Breakoutを原則使いません。"})
            if any(item.get("breakout") for item in overlays if isinstance(item, Mapping)):
                issues.append({"key": f"composition-v3-quiet-overlay-{page_number}", "label": f"ページ{page_number}の静かな越境文字", "detail": "会話・心理・導入ページでは文字のPanel越境を原則使いません。"})
    return issues


def composition_quality_metrics(page: Mapping[str, Any]) -> Dict[str, Any]:
    """描画結果へ結び付く、決定的なページ品質メトリクス。"""

    composition = page.get("composition") if isinstance(page.get("composition"), Mapping) else {}
    panels = [item for item in composition.get("panels", []) if isinstance(item, Mapping)]
    source_panels = [item for item in page.get("panels", []) if isinstance(item, Mapping)]
    source_by_id = {str(item.get("id")): item for item in source_panels}
    geometry_by_id = {str(item.get("panel_id")): item for item in panels}
    moved = moved_text_item_set(composition)
    face_balloon_overlap_count = 0
    important_subject_balloon_overlap_count = 0
    text_overflow_count = 0

    global_face_zones: List[Dict[str, float]] = []
    global_subject_zones: List[Dict[str, float]] = []
    for panel_id, geometry in geometry_by_id.items():
        source = source_by_id.get(panel_id, {})
        direction = source.get("panel_direction") if isinstance(source.get("panel_direction"), Mapping) else {}
        face_zones = [
            direction[key] for key in ("face_safe_zone", "head_safe_zone")
            if isinstance(direction.get(key), Mapping)
        ]
        if not face_zones and source.get("characters"):
            face_zones = semantic_protected_zones(source)
        subject_zones = [
            direction[key] for key in ("face_safe_zone", "head_safe_zone", "important_hand_zone", "important_prop_zone")
            if isinstance(direction.get(key), Mapping)
        ]
        supplied = geometry.get("protected_zones")
        if isinstance(supplied, list):
            subject_zones.extend(zone for zone in supplied if isinstance(zone, Mapping))
        if not subject_zones:
            subject_zones = semantic_protected_zones(source)
        for target, zones in ((global_face_zones, face_zones), (global_subject_zones, subject_zones)):
            for zone in zones:
                target.append(
                    {
                        "x": _float(geometry.get("x")) + _float(zone.get("x")) * _float(geometry.get("width")),
                        "y": _float(geometry.get("y")) + _float(zone.get("y")) * _float(geometry.get("height")),
                        "width": _float(zone.get("width")) * _float(geometry.get("width")),
                        "height": _float(zone.get("height")) * _float(geometry.get("height")),
                    }
                )
        layout = source.get("text_layout") if isinstance(source.get("text_layout"), Mapping) else {}
        for item in layout.get("items", []) if isinstance(layout.get("items"), list) else []:
            if not isinstance(item, Mapping):
                continue
            text_overflow_count += int(bool(item.get("overflow")))
            if str(item.get("type")) != "bubble" or (panel_id, str(item.get("id", ""))) in moved:
                continue
            if any(_intersection_area(item, zone) > 0.00001 for zone in face_zones):
                face_balloon_overlap_count += 1
            if any(_intersection_area(item, zone) > 0.00001 for zone in subject_zones):
                important_subject_balloon_overlap_count += 1

    for overlay in composition.get("overlays", []) if isinstance(composition.get("overlays"), list) else []:
        if not isinstance(overlay, Mapping):
            continue
        text_overflow_count += int(bool(overlay.get("overflow")))
        if str(overlay.get("type")) != "bubble":
            continue
        if any(_intersection_area(overlay, zone) > 0.00001 for zone in global_face_zones):
            face_balloon_overlap_count += 1
        if any(_intersection_area(overlay, zone) > 0.00001 for zone in global_subject_zones):
            important_subject_balloon_overlap_count += 1

    ellipse_mask_count = sum(
        1 for item in composition.get("breakouts", [])
        if isinstance(item, Mapping)
        and item.get("enabled", True)
        and breakout_uses_elliptical_mask(item)
    )
    issues = composition_quality_issues(page)
    invalid_crop_count = 0
    try:
        from .artwork_geometry import resolve_final_crop_window

        for panel_id, geometry in geometry_by_id.items():
            source = source_by_id.get(panel_id, {})
            direction = source.get("panel_direction") if isinstance(source.get("panel_direction"), Mapping) else {}
            canvas = direction.get("generation_canvas") if isinstance(direction.get("generation_canvas"), Mapping) else None
            if not isinstance(canvas, Mapping):
                continue
            source_size = canvas.get("source_size")
            if not isinstance(source_size, (list, tuple)) or len(source_size) != 2:
                continue
            crop_bounds = canvas.get("safe_crop") or canvas.get("final_crop_window") if canvas.get("strategy") == "overscan_safe_crop" else None
            stored_regions = canvas.get("regions") if isinstance(canvas.get("regions"), Mapping) else {}
            required_regions = [
                stored_regions[key] for key in ("face_safe_zone", "head_safe_zone", "important_hand_zone", "important_prop_zone")
                if isinstance(stored_regions.get(key), Mapping)
            ]
            plan = resolve_final_crop_window(
                source_size,
                (max(1, round(_float(geometry.get("width")) * PAGE_SIZE[0])), max(1, round(_float(geometry.get("height")) * PAGE_SIZE[1]))),
                crop_bounds=crop_bounds if isinstance(crop_bounds, Mapping) else None,
                protected_regions=required_regions,
                anchor_x=str(geometry.get("crop_anchor_x") or "center"),
                anchor_y=str(geometry.get("crop_anchor_y") or "middle"),
            )
            invalid_crop_count += int(not plan.get("valid"))
    except (ImportError, TypeError, ValueError, OverflowError):
        invalid_crop_count = 0

    return {
        **geometry_metrics(composition),
        **polygon_safety_metrics(page),
        "circle_mask_count": sum(1 for item in composition.get("breakouts", []) if isinstance(item, Mapping) and item.get("enabled", True) and str(item.get("clip_shape", item.get("mask_shape", ""))).lower() == "circle"),
        "face_balloon_overlap_count": face_balloon_overlap_count,
        "important_subject_balloon_overlap_count": important_subject_balloon_overlap_count,
        "extreme_sliver_panel_count": sum(issue["key"].startswith("composition-sliver-") for issue in issues),
        "meaningless_sliver_count": sum(issue["key"].startswith("composition-sliver-") for issue in issues),
        "ellipse_mask_count": ellipse_mask_count,
        "ambiguous_reading_order_count": sum(issue["key"].startswith("composition-reading-order-") for issue in issues),
        "text_overflow_count": text_overflow_count,
        "panel_overlap_count": sum(issue["key"].startswith("composition-panel-overlap-") for issue in issues),
        "invalid_crop_count": invalid_crop_count,
    }


def composition_quality_score(page: Mapping[str, Any]) -> Dict[str, Any]:
    """v3互換の決定的なスコアと、v2/v3共通の機械判定指標を返す。"""

    composition = page.get("composition") if isinstance(page.get("composition"), Mapping) else {}
    panels = [item for item in composition.get("panels", []) if isinstance(item, Mapping)]
    issues = composition_quality_issues(page)
    metrics = composition_quality_metrics(page)
    areas = [polygon_area(item.get("polygon_points")) for item in panels]
    total = sum(areas) or 1.0
    dominant = next((item for item in panels if item.get("dominant")), None)
    readability = max(0, 100 - sum(1 for issue in issues if "collision" in issue["key"] or "overflow" in issue["key"]) * 20)
    hierarchy = 100 if not dominant or 0.28 <= polygon_area(dominant.get("polygon_points")) / total <= 0.55 else 65
    face_visibility = max(0, 100 - metrics["face_balloon_overlap_count"] * 35)
    decoration = max(0, 100 - sum(1 for issue in issues if "budget" in issue["key"] or "quiet-" in issue["key"]) * 25)
    return {
        "outer_edge_alignment_score": max(0, 100 - 50*metrics["outer_edge_slant_count"]),
        "gutter_consistency_score": max(0, 100 - 50*metrics["gutter_consistency_error"]),
        "dynamic_layout_score": min(100, metrics["meaningful_dynamic_boundary_count"]*100),
        "panel_size_variation_score": min(100, round((max(areas)/min(areas)-1)*100)) if areas and min(areas)>0 else 0,
        "semantic_area_match_score": hierarchy,
        "reading_order_score": max(0, 100-50*metrics['ambiguous_reading_order_count']),
        "balloon_fit_score": readability,
        "subject_safety_score": max(0, 100-35*(metrics['important_subject_balloon_overlap_count']+metrics['polygon_protected_clip_count'])),
        "readability": readability,
        "visual_hierarchy": hierarchy,
        "face_visibility": face_visibility,
        "text_collision": not any("collision" in issue["key"] for issue in issues),
        "panel_area_variation": round(max(areas) / min(areas), 3) if areas and min(areas) > 0 else 0.0,
        "decoration_density": decoration,
        "issue_count": len(issues),
        "composition_version": int(composition.get("composition_version", 1) or 1),
        **metrics,
        "metrics": metrics,
    }


def simplify_composition(page: Mapping[str, Any]) -> Dict[str, Any]:
    """QA失敗時に演出を増やさず、不要な越境・変形だけを減らす。"""

    result = deepcopy(dict(page))
    composition = result.get("composition")
    if not isinstance(composition, Mapping) or int(composition.get("composition_version", 1) or 1) < SEMANTIC_COMPOSITION_VERSION:
        return result
    if int(composition.get("composition_version", 0)) >= DYNAMIC_COMPOSITION_VERSION:
        return result
    next_composition = deepcopy(dict(composition))
    family = str(next_composition.get("semantic_family") or "dialogue")
    budget = next_composition.get("effect_budget") if isinstance(next_composition.get("effect_budget"), Mapping) else SEMANTIC_DEFAULT_BUDGET
    if family in {"dialogue", "psychological", "establishing"}:
        for panel in next_composition.get("panels", []):
            if isinstance(panel, Mapping):
                panel["shape"] = "rectangle"
                panel["shape_reason"] = ""
                panel["polygon_points"] = shape_points(panel, "rectangle")
        next_composition["breakouts"] = []
        next_composition["overlays"] = [
            item for item in next_composition.get("overlays", [])
            if isinstance(item, Mapping) and str(item.get("type")) == "title"
        ]
        next_composition["moved_text_items"] = []
    else:
        max_breakouts = max(0, int(budget.get("character_breakouts", 0) or 0))
        next_composition["breakouts"] = [
            item for item in next_composition.get("breakouts", [])
            if isinstance(item, Mapping) and item.get("enabled", True) and str(item.get("reason", "")).strip()
        ][:max_breakouts]
        max_angles = max(0, int(budget.get("angled_panels", 0) or 0))
        used = 0
        for panel in next_composition.get("panels", []):
            if not isinstance(panel, Mapping) or str(panel.get("shape", "rectangle")) in {"rectangle", "wide", "tall"}:
                continue
            if not str(panel.get("shape_reason", "")).strip() or used >= max_angles:
                panel["shape"] = "rectangle"
                panel["shape_reason"] = ""
                panel["polygon_points"] = shape_points(panel, "rectangle")
            else:
                used += 1
        next_composition["overlays"] = [
            item for item in next_composition.get("overlays", [])
            if isinstance(item, Mapping) and (not item.get("breakout") or str(item.get("reason", "")).strip())
        ]
    # Compositionだけを簡略化してlayout_geometryを古いまま残すと、Preview/PDF/ZIP
    # のレンダラーごとに形状が食い違うため、共有されるgeometryにも同じ修正を反映する。
    composition_panels = {
        str(item.get("panel_id")): item
        for item in next_composition.get("panels", [])
        if isinstance(item, Mapping) and item.get("panel_id") is not None
    }
    for panel in result.get("panels", []) if isinstance(result.get("panels"), list) else []:
        if not isinstance(panel, dict):
            continue
        panel_id = str(panel.get("id", ""))
        composition_panel = composition_panels.get(panel_id)
        geometry = panel.get("geometry")
        if isinstance(composition_panel, Mapping) and isinstance(geometry, dict):
            for key in ("shape", "shape_reason", "polygon_points", "allow_breakout"):
                if key in composition_panel:
                    geometry[key] = deepcopy(composition_panel[key])
    layout_geometry = result.get("layout_geometry")
    if isinstance(layout_geometry, dict) and isinstance(layout_geometry.get("panels"), list):
        for geometry in layout_geometry["panels"]:
            if not isinstance(geometry, dict):
                continue
            composition_panel = composition_panels.get(str(geometry.get("panel_id", "")))
            if isinstance(composition_panel, Mapping):
                for key in ("shape", "shape_reason", "polygon_points", "allow_breakout"):
                    if key in composition_panel:
                        geometry[key] = deepcopy(composition_panel[key])
    result["composition"] = next_composition
    return result


repair_composition = simplify_composition
