"""生成プロンプトと画像APIが共有する、実寸ベースのコマ比率。"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .composition import PAGE_SIZE


def artwork_aspect_ratio(panel: Mapping[str, Any]) -> float:
    """ページ相対座標を画素比へ変換する。未確定コマは正方形とする。"""
    geometry = panel.get("geometry")
    if not isinstance(geometry, dict) or not geometry:
        return 1.0
    viewport = geometry.get("artwork_viewport")
    viewport = viewport if isinstance(viewport, dict) else {}
    try:
        width = float(geometry.get("width", 0)) * PAGE_SIZE[0] * float(viewport.get("width", 1))
        height = float(geometry.get("height", 0)) * PAGE_SIZE[1] * float(viewport.get("height", 1))
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            return 1.0
        return width / height
    except (TypeError, ValueError, OverflowError):
        return 1.0


def artwork_generation_size(panel: Mapping[str, Any], model_id: str | None = None) -> str:
    """対応サイズのうちcover時の切り落とし面積が最小になるものを選ぶ。"""
    ratio = artwork_aspect_ratio(panel)
    model = model_id or panel.get("generation_image_model") or "gpt-image-2"
    if model in {"gpt-image-2", "gpt-image-2-2026-04-21"}:
        # 公式の16px刻み・最大3:1に従い、約1MPで比率誤差を抑える。
        # 旧モデルの3サイズ制約を新モデルへ持ち込まない。
        bounded_ratio = max(1 / 3, min(3, ratio))
        candidates = []
        for width in range(592, 1777, 16):
            height = max(592, min(1776, round(width / bounded_ratio / 16) * 16))
            pixels = width * height
            if 900_000 <= pixels <= 1_200_000 and 1 / 3 <= width / height <= 3:
                score = abs(math.log((width / height) / bounded_ratio)) + abs(math.log(pixels / 1_048_576)) * 0.01
                candidates.append((score, width, height))
        _, width, height = min(candidates)
        return f"{width}x{height}"
    sizes = (("1024x1024", 1.0), ("1536x1024", 1.5), ("1024x1536", 2 / 3))
    return min(sizes, key=lambda item: abs(math.log(ratio / item[1])))[0]


def generation_crop_window(panel: Mapping[str, Any], model_id: str | None = None) -> dict:
    """生成キャンバス上で最終コマに残る領域。予約領域を同じ座標へ変換する。"""
    target_ratio = artwork_aspect_ratio(panel)
    size = artwork_generation_size(panel, model_id)
    width, height = (float(value) for value in size.split("x"))
    source_ratio = width / height
    geometry = panel.get("geometry") or {}
    anchor = (panel.get("panel_direction") or {}).get("crop_anchor") or {}
    ax = {"left": 0, "center": 0.5, "right": 1}.get(anchor.get("x", geometry.get("crop_anchor_x")), 0.5)
    ay = {"top": 0, "middle": 0.5, "bottom": 1}.get(anchor.get("y", geometry.get("crop_anchor_y")), 0.5)
    crop_width = min(1.0, target_ratio / source_ratio)
    crop_height = min(1.0, source_ratio / target_ratio)
    return {"x": (1 - crop_width) * ax, "y": (1 - crop_height) * ay, "width": crop_width, "height": crop_height}


def generation_canvas_zones(panel: Mapping[str, Any], model_id: str | None = None) -> dict:
    """文字と顔の予約位置を、近似生成サイズで失われない位置へ写す。"""
    direction = panel.get("panel_direction") or {}
    crop = generation_crop_window(panel, model_id)

    def transform(zone):
        return {"x": round(crop["x"] + float(zone["x"]) * crop["width"], 5),
                "y": round(crop["y"] + float(zone["y"]) * crop["height"], 5),
                "width": round(float(zone["width"]) * crop["width"], 5),
                "height": round(float(zone["height"]) * crop["height"], 5)}

    regions = {key: transform(direction[key]) for key in ("character_zone", "face_safe_zone", "important_prop_zone", "important_hand_zone") if direction.get(key)}
    regions["text_reserved_zones"] = [transform(zone) for zone in direction.get("reserved_text_zones", [])]
    return {"generation_size": artwork_generation_size(panel, model_id), "final_crop_window": crop, "regions": regions}
