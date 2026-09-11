"""生成プロンプトと画像APIが共有する、実寸ベースのコマ比率。"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .composition import PAGE_SIZE


DIRECT_GENERATION = "direct"
OVERSCAN_SAFE_CROP = "overscan_safe_crop"
"""画像生成段階の構図戦略名。保存値としても利用する。"""


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


def _direction_from_panel(panel: Mapping[str, Any]) -> Mapping[str, Any]:
    direction = panel.get("panel_direction")
    return direction if isinstance(direction, Mapping) else panel


def _direction_ratio(panel: Mapping[str, Any], direction: Mapping[str, Any]) -> float:
    feasibility = direction.get("composition_feasibility")
    if isinstance(feasibility, Mapping):
        try:
            ratio = float(feasibility.get("aspect_ratio", 0))
            if math.isfinite(ratio) and ratio > 0:
                return ratio
        except (TypeError, ValueError):
            pass
    return artwork_aspect_ratio(panel)


def _wide_multi_element(panel: Mapping[str, Any], direction: Mapping[str, Any], ratio: float) -> bool:
    framing = direction.get("camera_framing")
    framing = framing if isinstance(framing, Mapping) else direction
    mode = str(direction.get("composition_mode") or framing.get("composition_mode") or "").lower()
    if mode != "wide_multi_element" or ratio < 1.6:
        return False
    if framing.get("head_visible") is False or framing.get("preserve_entire_head") is False:
        return False
    hands = framing.get("hands_required") is True
    props = framing.get("props_required") is True
    if not (hands and props):
        return False
    # 過去の目視結果をPanelDirectionへ付与する場合も、同じ戦略へ収束させる。
    observed = str(direction.get("headroom_status") or framing.get("headroom_status") or "").upper()
    feasibility = direction.get("composition_feasibility")
    feasibility = feasibility if isinstance(feasibility, Mapping) else {}
    return bool(
        observed in {"TOUCHING", "CRAMPED", "FAIL_CROP_RISK"}
        or direction.get("wide_multi_requirement") is True
        or framing.get("wide_multi_requirement") is True
        or feasibility.get("wide_multi_requirement") is True
    )


def _preferred_overscan_source_ratio(final_ratio: float) -> float:
    """最終コマより縦に余裕のある、APIが扱える現実的なsource比率を返す。"""
    # 2:1の代表ケースでは3:2 sourceとする。5:4まで縦を増やすと、
    # top-anchorのfinal crop下端が手・器具を切りやすくなるため、sourceの
    # 縦余裕と必要なbody extentのバランスを優先する。source上端は捨てず、
    # 余分な高さは下側のoverscanへ回す。
    return round(max(1.35, min(1.5, final_ratio * 0.75)), 4)


def _safe_crop_for_ratio(final_ratio: float, source_ratio: float, head_target: float) -> dict:
    """source内のfinal cropを決める。source/finalとも正規化座標で表す。"""
    if source_ratio <= 0 or final_ratio <= 0 or source_ratio >= final_ratio:
        return {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    crop_height = max(0.01, min(1.0, source_ratio / final_ratio))
    # 直前の実画像では、モデルがsource上端付近へ髪を配置したため、
    # top overscanをfinal cropから捨てる方式が頭頂cropを起こした。
    # 横長多要素のsafe cropは上端をfinal cropへ含め、余分な高さを下側へ
    # 残す。head_targetはprompt/zone設計で使い、危険な固定top offsetには
    # 変換しない。
    top = 0.0
    return {"x": 0.0, "y": top, "width": 1.0, "height": round(crop_height, 5)}


def resolve_generation_strategy(panel: Mapping[str, Any]) -> dict:
    """PanelDirectionからDIRECT/OVERSCANを一元的に解決する。

    生成前に保存される計画であり、Preview/PDF/ZIPが独自に再計算するための
    レイアウト規則ではない。通常コマは従来どおりDIRECTを返す。
    """
    direction = _direction_from_panel(panel)
    ratio = _direction_ratio(panel, direction)
    if not _wide_multi_element(panel, direction, ratio):
        return {
            "strategy": DIRECT_GENERATION,
            "source_aspect_ratio": round(ratio, 4),
            "final_panel_aspect_ratio": round(ratio, 4),
            "overscan": {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0},
            "safe_crop": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "headroom_target": None,
            "required_zones_in_final_crop": {},
            "crop_reason": "通常コマはPanel比率へ直接生成",
        }
    framing = direction.get("camera_framing")
    framing = framing if isinstance(framing, Mapping) else direction
    head_clearance = direction.get("head_clearance") or framing.get("head_clearance") or {}
    # head_clearance.targetはPanel内のhead zone開始位置であり、画像上端からの
    # 余白率とは別物。safe-crop/promptへはshot-awareなheadroom targetを渡す。
    head_target = float(
        direction.get("headroom_target")
        or framing.get("headroom_target")
        or ((framing.get("headroom_target_min", .06) + framing.get("headroom_target_max", .10)) / 2)
    )
    source_ratio = _preferred_overscan_source_ratio(ratio)
    crop = _safe_crop_for_ratio(ratio, source_ratio, head_target)
    return {
        "strategy": OVERSCAN_SAFE_CROP,
        "source_aspect_ratio": source_ratio,
        "final_panel_aspect_ratio": round(ratio, 4),
        "overscan": {
            "top": round(crop["y"], 5),
            "bottom": round(max(0.0, 1.0 - crop["y"] - crop["height"]), 5),
            "left": round(crop["x"], 5),
            "right": round(max(0.0, 1.0 - crop["x"] - crop["width"]), 5),
        },
        "safe_crop": crop,
        "safe_crop_anchor": "top",
        "headroom_target": round(head_target, 5),
        "required_zones_in_final_crop": {
            key: direction.get(key)
            for key in (
                "head_safe_zone", "face_safe_zone", "important_hand_zone",
                "important_prop_zone", "reserved_text_zones",
            )
            if direction.get(key)
        },
        "crop_reason": "横長多要素の直接生成で頭頂がCRAMPEDになったため、生成時に上下余白を含むsourceを使い保存済みsafe cropを適用",
    }


def _generation_size_for_ratio(ratio: float, model_id: str | None = None) -> str:
    model = model_id or "gpt-image-2"
    if model in {"gpt-image-2", "gpt-image-2-2026-04-21"}:
        bounded_ratio = max(1 / 3, min(3, ratio))
        candidates = []
        for width in range(592, 1777, 16):
            height = max(592, min(1776, round(width / bounded_ratio / 16) * 16))
            pixels = width * height
            if 900_000 <= pixels <= 1_200_000 and 1 / 3 <= width / height <= 3:
                score = abs(math.log((width / height) / bounded_ratio)) + abs(math.log(pixels / 1_048_576)) * 0.01
                candidates.append((score, width, height))
        if candidates:
            _, width, height = min(candidates)
            return f"{width}x{height}"
    sizes = (("1024x1024", 1.0), ("1536x1024", 1.5), ("1024x1536", 2 / 3))
    return min(sizes, key=lambda item: abs(math.log(max(ratio, 1e-6) / item[1])))[0]


def artwork_generation_size(panel: Mapping[str, Any], model_id: str | None = None) -> str:
    """対応サイズのうちcover時の切り落とし面積が最小になるものを選ぶ。"""
    ratio = artwork_aspect_ratio(panel)
    model = model_id or panel.get("generation_image_model") or "gpt-image-2"
    direction = _direction_from_panel(panel)
    stored_canvas = direction.get("generation_canvas") if isinstance(direction, Mapping) else None
    stored_size = None
    if isinstance(stored_canvas, Mapping):
        # source_sizeはoverscanの新形式、generation_sizeは旧direct形式。
        stored_size = stored_canvas.get("source_size") or stored_canvas.get("generation_size")
    if isinstance(stored_size, str) and "x" in stored_size:
        stored_size = stored_size.split("x", 1)
    if isinstance(stored_size, (list, tuple)) and len(stored_size) == 2:
        try:
            stored_width, stored_height = (int(stored_size[0]), int(stored_size[1]))
            if stored_width > 0 and stored_height > 0:
                return f"{stored_width}x{stored_height}"
        except (TypeError, ValueError):
            pass
    strategy = resolve_generation_strategy(panel)
    source_ratio = float(strategy.get("source_aspect_ratio", ratio) or ratio)
    return _generation_size_for_ratio(source_ratio, model)


def generation_crop_window(panel: Mapping[str, Any], model_id: str | None = None) -> dict:
    """生成キャンバス上で最終コマに残る領域。予約領域を同じ座標へ変換する。"""
    target_ratio = artwork_aspect_ratio(panel)
    size = artwork_generation_size(panel, model_id)
    width, height = (float(value) for value in size.split("x"))
    source_ratio = width / height
    direction = _direction_from_panel(panel)
    stored_canvas = direction.get("generation_canvas") if isinstance(direction, Mapping) else None
    if isinstance(stored_canvas, Mapping):
        stored_crop = stored_canvas.get("safe_crop") or stored_canvas.get("final_crop_window")
        if isinstance(stored_crop, Mapping):
            try:
                values = {key: float(stored_crop[key]) for key in ("x", "y", "width", "height")}
                if (all(math.isfinite(value) for value in values.values()) and values["width"] > 0 and values["height"] > 0
                        and values["x"] >= 0 and values["y"] >= 0
                        and values["x"] + values["width"] <= 1 and values["y"] + values["height"] <= 1):
                    return values
            except (KeyError, TypeError, ValueError):
                pass
    strategy = resolve_generation_strategy(panel)
    if strategy.get("strategy") == OVERSCAN_SAFE_CROP:
        crop = _safe_crop_for_ratio(target_ratio, source_ratio, float((direction.get("head_clearance") or {}).get("target", 0.24)))
        return crop
    geometry = panel.get("geometry") or {}
    anchor = direction.get("crop_anchor") or {}
    ax = {"left": 0, "center": 0.5, "right": 1}.get(anchor.get("x", geometry.get("crop_anchor_x")), 0.5)
    ay = {"top": 0, "middle": 0.5, "bottom": 1}.get(anchor.get("y", geometry.get("crop_anchor_y")), 0.5)
    crop_width = min(1.0, target_ratio / source_ratio)
    crop_height = min(1.0, source_ratio / target_ratio)
    return {"x": (1 - crop_width) * ax, "y": (1 - crop_height) * ay, "width": crop_width, "height": crop_height}


def generation_canvas_zones(panel: Mapping[str, Any], model_id: str | None = None) -> dict:
    """文字と顔の予約位置を、近似生成サイズで失われない位置へ写す。"""
    direction = panel.get("panel_direction") or {}
    crop = generation_crop_window(panel, model_id)
    stored_canvas = direction.get("generation_canvas") if isinstance(direction, Mapping) else None
    if isinstance(stored_canvas, Mapping) and not stored_canvas.get("strategy"):
        # 旧保存形式は直接生成として読み取り、既存画像をoverscanへ暗黙移行しない。
        strategy = {"strategy": DIRECT_GENERATION, "crop_reason": "旧direct生成の保存canvas"}
    else:
        strategy = resolve_generation_strategy(panel)
    size = artwork_generation_size(panel, model_id)
    source_width, source_height = (float(value) for value in size.split("x"))
    source_ratio = source_width / source_height

    def transform(zone):
        return {"x": round(crop["x"] + float(zone["x"]) * crop["width"], 5),
                "y": round(crop["y"] + float(zone["y"]) * crop["height"], 5),
                "width": round(float(zone["width"]) * crop["width"], 5),
                "height": round(float(zone["height"]) * crop["height"], 5)}

    regions = {key: transform(direction[key]) for key in ("character_zone", "face_safe_zone", "head_safe_zone", "important_prop_zone", "important_hand_zone") if direction.get(key)}
    regions["text_reserved_zones"] = [transform(zone) for zone in direction.get("reserved_text_zones", [])]
    overscan = (strategy.get("overscan") if strategy.get("strategy") == OVERSCAN_SAFE_CROP else
                {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0})
    required = strategy.get("required_zones_in_final_crop") or {}
    return {
        "strategy": strategy.get("strategy", DIRECT_GENERATION),
        "generation_size": size,
        "source_size": [int(source_width), int(source_height)],
        "source_aspect_ratio": round(source_ratio, 4),
        "final_panel_aspect_ratio": round(artwork_aspect_ratio(panel), 4),
        "overscan": overscan,
        "safe_crop": crop,
        "final_crop_window": crop,
        "safe_crop_anchor": strategy.get("safe_crop_anchor", "center"),
        "headroom_target": strategy.get("headroom_target"),
        "headroom_reference": "final_crop",
        "required_zones_in_final_crop": required,
        "crop_reason": strategy.get("crop_reason", ""),
        "regions": regions,
    }


def generation_canvas_is_feasible(panel: Mapping[str, Any], model_id: str | None = None) -> bool:
    """課金前にsource/final cropと必須領域の包含関係を検査する。"""
    canvas = generation_canvas_zones(panel, model_id)
    crop = canvas.get("safe_crop") or {}
    try:
        source_width, source_height = (float(value) for value in canvas["source_size"])
        target_ratio = float(canvas["final_panel_aspect_ratio"])
        crop_ratio = (source_width * float(crop["width"])) / (source_height * float(crop["height"]))
        bounds_ok = (
            0 <= float(crop["x"]) <= 1 and 0 <= float(crop["y"]) <= 1
            and 0 < float(crop["width"]) <= 1 and 0 < float(crop["height"]) <= 1
            and float(crop["x"]) + float(crop["width"]) <= 1
            and float(crop["y"]) + float(crop["height"]) <= 1
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    if not bounds_ok or not math.isfinite(crop_ratio) or abs(crop_ratio - target_ratio) > .02:
        return False
    if canvas.get("strategy") == OVERSCAN_SAFE_CROP:
        overscan = canvas.get("overscan") or {}
        total_overscan = sum(float(overscan.get(key, 0) or 0) for key in ("top", "bottom", "left", "right"))
        if not (float(canvas.get("source_aspect_ratio", 0)) < target_ratio and total_overscan >= .05):
            return False
    # generation_canvas_zonesの領域はsource座標。すべてfinal crop内にある必要がある。
    right = float(crop["x"]) + float(crop["width"])
    bottom = float(crop["y"]) + float(crop["height"])
    for region in (canvas.get("regions") or {}).values():
        values = region if isinstance(region, list) else [region]
        for item in values:
            if not isinstance(item, Mapping) or not {"x", "y", "width", "height"}.issubset(item):
                continue
            if (float(item["x"]) < float(crop["x"]) - .0001
                    or float(item["y"]) < float(crop["y"]) - .0001
                    or float(item["x"]) + float(item["width"]) > right + .0001
                    or float(item["y"]) + float(item["height"]) > bottom + .0001):
                return False
    return True
