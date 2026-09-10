"""画像生成前に人物と文字の空間を確定する。画像解析結果とは区別する。"""

from copy import deepcopy
import hashlib
import json

from .composition import PAGE_SIZE
from .text_composition import body_font, wrap_text
from .visual_style import resolve_visual_style, text_direction
from .in_world_text import resolve_in_world_text
from .reading_order import canonicalize_stored_settings


def direction_fingerprint(panel, settings):
    """構図に関係する入力のみ署名し、生成状態や画像URLでは無効化しない。"""
    values = {key: panel.get(key) for key in (
        "dialogue", "narration", "sfx", "dialogue_types", "sfx_types", "characters",
        "character_position", "subject_position", "shot_type", "action", "background",
        "expression", "protected_zones", "text_safe_zones")}
    # 未指定の旧コマの署名を変えず、明示された背景文字の変更だけを検出する。
    if panel.get("in_world_text_policy") not in {None, "abstract_only"} or panel.get("in_world_exact_text"):
        values["in_world_text"] = resolve_in_world_text(panel)
    geometry = panel.get("geometry") or {}
    values["geometry"] = {key: geometry.get(key) for key in ("x", "y", "width", "height", "shape", "polygon_points")}
    canonical = canonicalize_stored_settings(settings)
    values["settings"] = {"visual_style": canonical.get("visual_style", "cinematic"),
                          "color_mode": canonical.get("color_mode", "bw"), "language": canonical["language"]}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_panel_direction(panel, settings):
    """実測文字量から予約領域を作り、残りに人物を配置する。収まらなければ未確定。"""
    geometry = panel["geometry"]
    profile = resolve_visual_style(settings)
    width, height = geometry["width"] * PAGE_SIZE[0], geometry["height"] * PAGE_SIZE[1]
    language = settings.get("language", "ja")
    position = str(panel.get("character_position") or panel.get("subject_position") or "")
    character_right = "right" in position or "右" in position or not position and language == "en"
    text_x = 0.04 if character_right else 0.55
    character_zone = {"x": 0.50 if character_right else 0.04, "y": 0.06, "width": 0.46, "height": 0.88}
    if "center" in position or "中央" in position:
        character_zone["x"] = 0.27
    face_zone = {"x": character_zone["x"] + 0.04, "y": 0.12, "width": 0.36, "height": 0.38}
    prop_zone = {"x": character_zone["x"] + 0.03, "y": 0.56, "width": 0.38, "height": 0.30}
    raised_hand = any(word in str(panel.get("action", "")) for word in ("手を挙げ", "手を上げ", "raise", "wave"))
    hand_zone = {"x": character_zone["x"], "y": 0.08 if raised_hand else 0.56, "width": 0.16, "height": 0.25}
    protected = deepcopy(panel.get("protected_zones") or ([face_zone, prop_zone, hand_zone] if panel.get("characters") else []))
    items, zones = [], []
    cursor = 0.04
    for kind, key in (("bubble", "dialogue"), ("narration", "narration"), ("sfx", "sfx")):
        for index, text in enumerate(panel.get(key, []) or []):
            if not str(text).strip():
                continue
            item = {"id": f"{kind}-{index + 1}", "type": kind, "order": index + 1, "text": str(text)}
            types = panel.get("dialogue_types" if kind == "bubble" else "sfx_types") or []
            token = text_direction(item, profile, str(types[index]) if index < len(types) else "")
            size = max(14, round(17 * token["size_scale"]))
            box_width = 0.41
            padding = 0.18 * box_width * width if token["shape"] == "burst" else 12
            lines = wrap_text(str(text), max(1, box_width * width - 2 * padding), size)
            # 短いセリフを横長の空疎な帯へ引き伸ばさず、生成前に実幅を確定する。
            glyph_width = max(body_font(size).getlength(line) for line in lines)
            desired_width = glyph_width / 0.64 if token["shape"] == "burst" else glyph_width + 2 * padding
            box_width = min(box_width, max(0.12, desired_width / width))
            box_height = (len(lines) * (size + 5) + 24 + (8 if kind == "bubble" else 0)) / height
            if kind == "bubble":
                box_height = max(box_height, box_width * width / height * 0.36)
            actual_x = text_x if character_right else 0.96 - box_width
            rect = {"x": actual_x, "y": cursor, "width": box_width, "height": box_height}
            overlap = any(rect["x"] < zone["x"] + zone["width"] and rect["x"] + rect["width"] > zone["x"]
                          and rect["y"] < zone["y"] + zone["height"] and rect["y"] + rect["height"] > zone["y"] for zone in protected)
            effective_padding = box_width * width * 0.18 if token["shape"] == "burst" else padding
            overflow = cursor + box_height > 0.96 or overlap or any(body_font(size).getlength(line) > box_width * width - 2 * effective_padding + 0.01 for line in lines)
            item.update(rect, font_size=size, lines=lines, line_count=len(lines), direction=token,
                        overflow=overflow, font_scale=1, side="left" if character_right else "right")
            items.append(item)
            zones.append({**rect, "type": kind, "item_id": item["id"]})
            cursor += box_height + 0.025
    ready = not any(item["overflow"] for item in items)
    return {"version": 1, "status": "ready" if ready else "needs_revision",
            "in_world_text": resolve_in_world_text(panel),
            "source": "pre_generation_plan", "fingerprint": direction_fingerprint(panel, settings),
            "geometry": deepcopy(geometry), "character_zone": character_zone,
            "face_safe_zone": face_zone, "important_prop_zone": prop_zone, "important_hand_zone": hand_zone,
            "protected_zones": protected, "reserved_text_zones": zones,
            "crop_anchor": {"x": "right" if character_right else "left", "y": "middle"},
            "visual_style": profile, "breakout_policy": {"enabled": False, "reason": "専用前景素材と実画像の人物位置確認が必要"},
            "text_layout": {"version": 3, "placement_mode": "pre_generation_plan", "items": items,
                            "style_profile": profile, "warnings": [] if ready else ["生成前の文字領域が不足しています。コマ拡大・ページ分割または文字量を調整してください。"]}}


def direction_is_ready(panel, settings):
    """古い構図や不足した文字領域のまま課金生成しない。"""
    direction = panel.get("panel_direction") or {}
    return direction.get("status") == "ready" and direction.get("fingerprint") == direction_fingerprint(panel, settings)
