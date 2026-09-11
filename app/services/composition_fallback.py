"""生成前の過密なコマを安全な漫画構成へ変換する純粋ロジック。

画像生成後のcropやPDF補正ではなく、Storyboardを保存する前に
「一枚のコマへ詰め込みすぎていないか」を判定する。判定は意図的に
保守的で、既存の画像付きProjectには適用しない。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping


COMPLEXITY_LOW = "LOW"
COMPLEXITY_MEDIUM = "MEDIUM"
COMPLEXITY_HIGH = "HIGH"
COMPLEXITY_INFEASIBLE = "INFEASIBLE"

FALLBACK_SHOT_RELAXATION = "shot_relaxation"
FALLBACK_TEXT_RELOCATION = "text_relocation"
FALLBACK_GEOMETRY_CHANGE = "geometry_change"
FALLBACK_PANEL_SPLIT = "panel_split"

_WIDE_RATIO = 1.8
_TEXT_AREA_LIMIT = 0.16
_SHOT_TYPES = {"close_up", "medium_close", "medium", "medium_wide", "close-shot", "close_up"}


def _safe_ratio(panel: Mapping[str, Any]) -> float:
    """保存済みgeometryから横長度を取得する。"""

    geometry = panel.get("geometry")
    if isinstance(geometry, Mapping):
        try:
            width = float(geometry.get("width", 0))
            height = float(geometry.get("height", 0))
            if width > 0 and height > 0:
                return width / height
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    direction = panel.get("panel_direction")
    if isinstance(direction, Mapping):
        feasibility = direction.get("composition_feasibility")
        if isinstance(feasibility, Mapping):
            try:
                ratio = float(feasibility.get("aspect_ratio", 0))
                if ratio > 0:
                    return ratio
            except (TypeError, ValueError):
                pass
    return 1.0


def _text_area_ratio(panel: Mapping[str, Any]) -> float:
    """文字予約領域を優先し、まだ計画されていないコマは控えめに推定する。"""

    zones = panel.get("text_safe_zones")
    if isinstance(zones, Mapping):
        total = 0.0
        for value in zones.values():
            if not isinstance(value, Mapping):
                continue
            try:
                total += max(0.0, float(value.get("width", 0))) * max(0.0, float(value.get("height", 0)))
            except (TypeError, ValueError):
                continue
        if total > 0:
            return min(1.0, total)
    direction = panel.get("panel_direction")
    if isinstance(direction, Mapping):
        zones = direction.get("reserved_text_zones")
        if isinstance(zones, list):
            total = 0.0
            for value in zones:
                if not isinstance(value, Mapping):
                    continue
                try:
                    total += max(0.0, float(value.get("width", 0))) * max(0.0, float(value.get("height", 0)))
                except (TypeError, ValueError):
                    continue
            if total > 0:
                return min(1.0, total)
    dialogue = panel.get("dialogue") if isinstance(panel.get("dialogue"), list) else []
    narration = panel.get("narration") if isinstance(panel.get("narration"), list) else []
    # 文字量の厳密な組版はPanelDirection側が担当する。ここでは「予約領域が
    # ある」ことを検出するため、1〜2個の短文に相当する安全な下限だけ置く。
    count = len([item for item in [*dialogue, *narration] if str(item).strip()])
    return min(0.45, 0.10 + count * 0.07) if count else 0.0


def _requirements(panel: Mapping[str, Any]) -> Dict[str, Any]:
    direction = panel.get("panel_direction")
    direction = direction if isinstance(direction, Mapping) else {}
    framing = direction.get("camera_framing")
    framing = framing if isinstance(framing, Mapping) else {}
    characters = panel.get("characters")
    visible_character = bool(characters) and panel.get("head_visible") is not False and not panel.get("intentional_head_crop")
    requirements_text = " ".join(
        str(panel.get(key) or "") for key in ("description", "action", "panel_role", "scene_type")
    ).lower()
    # 明示Falseは旧ネーム／Fixtureの「手や器具を描かない」宣言として尊重し、
    # 未指定の新規ネームだけは内容から保守的に推定する。生成後のPanelDirection
    # がある場合は、その保存済み要求も参照する。
    hands_required = panel.get("hands_required") is True or (
        panel.get("hands_required") is None
        and framing.get("hands_required") is True
    ) or (
        panel.get("hands_required") is None
        and any(word in requirements_text for word in ("手", "hand", "instrument"))
    )
    props_required = panel.get("props_required") is True or (
        panel.get("props_required") is None
        and framing.get("props_required") is True
    ) or (
        panel.get("props_required") is None
        and any(word in requirements_text for word in ("器具", "小物", "instrument", "prop"))
    )
    shot = str(panel.get("shot_type") or framing.get("requested_shot_type") or "medium").lower().strip().replace("-", "_").replace(" ", "_")
    return {
        "visible_character": visible_character,
        "full_head_required": visible_character and panel.get("intentional_head_crop") is not True,
        "hands_required": hands_required,
        "props_required": props_required,
        "text_required": _text_area_ratio(panel) > 0,
        "text_area_ratio": round(_text_area_ratio(panel), 5),
        "shot_type": shot,
        "characters": len(characters) if isinstance(characters, list) else 0,
    }


def panel_spatial_complexity(panel: Mapping[str, Any]) -> Dict[str, Any]:
    """Panelの空間要求をLOW〜INFEASIBLEへ分類する。

    横長・全頭・明示的な手・明示的な器具・文字予約を同時に要求する
    組み合わせだけをINFEASIBLEとする。矩形中心の通常コマや、手元だけの
    コマを過剰に分割しない。
    """

    ratio = _safe_ratio(panel)
    requirements = _requirements(panel)
    score = 0
    reasons: List[str] = []
    if ratio >= _WIDE_RATIO:
        score += 25
        reasons.append("横長で縦方向の余裕が限られています")
    elif ratio >= 1.45:
        score += 12
    if requirements["visible_character"]:
        score += 15
    if requirements["full_head_required"]:
        score += 15
    if requirements["hands_required"]:
        score += 15
        reasons.append("重要な手を同じ画面に保持します")
    if requirements["props_required"]:
        score += 15
        reasons.append("重要な器具・小物を同じ画面に保持します")
    if requirements["text_required"]:
        score += 10
        reasons.append("セリフまたはナレーションの予約領域が必要です")
    if requirements["text_area_ratio"] >= _TEXT_AREA_LIMIT:
        score += 5

    overloaded = (
        ratio >= _WIDE_RATIO
        and requirements["visible_character"]
        and requirements["full_head_required"]
        and requirements["hands_required"]
        and requirements["props_required"]
        and requirements["text_required"]
        and requirements["shot_type"] in _SHOT_TYPES
    )
    if overloaded:
        level = COMPLEXITY_INFEASIBLE
        feasibility = "INFEASIBLE"
        reasons.append("頭・手・器具・文字を一枚の浅い横長コマへ同時に収めるのは不安定です")
    elif score >= 65:
        level = COMPLEXITY_HIGH
        feasibility = "TIGHT"
    elif score >= 35:
        level = COMPLEXITY_MEDIUM
        feasibility = "FEASIBLE"
    else:
        level = COMPLEXITY_LOW
        feasibility = "FEASIBLE"

    return {
        "score": min(100, score),
        "level": level,
        "feasibility_status": feasibility,
        "feasibility_status_normalized": feasibility.lower(),
        "reasons": reasons[:8],
        "aspect_ratio": round(ratio, 5),
        "requirements": requirements,
    }


def resolve_composition_fallback(panel: Mapping[str, Any], language: str = "ja") -> Dict[str, Any]:
    """過密Panelに対して、簡略化を試した履歴と最終変換を返す。"""

    complexity = panel_spatial_complexity(panel)
    if complexity["level"] != COMPLEXITY_INFEASIBLE:
        return {
            "required": False,
            "fallback_type": None,
            "fallback_reason": "このコマは保存済みgeometryで生成可能です",
            "attempts": [],
            "complexity": complexity,
        }

    attempts: List[Dict[str, Any]] = []
    original_shot = str(panel.get("shot_type") or "medium")
    for candidate in ("medium_close", "medium", "medium_wide"):
        candidate_panel = deepcopy(dict(panel))
        candidate_panel["shot_type"] = candidate
        candidate_result = panel_spatial_complexity(candidate_panel)
        attempts.append({
            "type": FALLBACK_SHOT_RELAXATION,
            "requested": original_shot,
            "effective": candidate,
            "status": "feasible" if candidate_result["level"] != COMPLEXITY_INFEASIBLE else "insufficient",
            "reason": "人物と必要要素を保持したままショットを広げて再評価",
        })
        if candidate_result["level"] != COMPLEXITY_INFEASIBLE:
            return {
                "required": True,
                "fallback_type": FALLBACK_SHOT_RELAXATION,
                "fallback_reason": "ショットを広げると必要要素が収まるため、画像生成前に緩和しました",
                "attempts": attempts,
                "complexity": complexity,
                "effective_shot_type": candidate,
            }

    # 文字を消すのではなく、予約側を移動した仮想候補を再評価する。
    relocated = deepcopy(dict(panel))
    relocated["text_safe_zones"] = {}
    relocated["dialogue"] = []
    relocated["narration"] = []
    relocated_result = panel_spatial_complexity(relocated)
    attempts.append({
        "type": FALLBACK_TEXT_RELOCATION,
        "status": "feasible" if relocated_result["level"] != COMPLEXITY_INFEASIBLE else "insufficient",
        "reason": "文字を人物と競合しない別領域へ移す仮想候補を再評価",
    })

    # Geometry変更はページ全体の再計算を伴うため、ここでは候補として記録し、
    # コマだけを無理に横長のまま残さない。次のsplitが局所的で安全な最終策。
    geometry_candidate = deepcopy(dict(panel))
    geometry = dict(geometry_candidate.get("geometry") or {})
    try:
        height = float(geometry.get("height", 0.1))
        geometry["width"] = min(float(geometry.get("width", 0.2)), height * 1.55)
    except (TypeError, ValueError):
        pass
    geometry_candidate["geometry"] = geometry
    geometry_result = panel_spatial_complexity(geometry_candidate)
    attempts.append({
        "type": FALLBACK_GEOMETRY_CHANGE,
        "status": "candidate",
        "would_be_feasible": geometry_result["level"] != COMPLEXITY_INFEASIBLE,
        "reason": "横長比率を下げる候補を再評価（ページ再配置が必要）",
    })
    return {
        "required": True,
        "fallback_type": FALLBACK_PANEL_SPLIT,
        "fallback_reason": "情報量が多く横長コマへ安全に収まらないため、顔/セリフと手/器具を連続2コマへ分割",
        "attempts": attempts,
        "complexity": complexity,
        "language": language,
    }


def _story_intent(panel: Mapping[str, Any]) -> Dict[str, Any]:
    """分割前の意味を追跡できる最小メタデータ。本文を増やさない。"""

    return {
        key: deepcopy(panel.get(key))
        for key in ("description", "action", "expression", "characters", "dialogue", "narration", "sfx", "shot_type")
        if panel.get(key) not in (None, "", [], {})
    }


def _child_panel(
    panel: Mapping[str, Any],
    child_id: str,
    *,
    role: str,
    dialogue: List[Any],
    narration: List[Any],
    sfx: List[Any],
    shot_type: str,
    hands_required: bool,
    props_required: bool,
    original_id: str,
    generated_ids: List[str],
    reason: str,
) -> Dict[str, Any]:
    child = deepcopy(dict(panel))
    child.update(
        {
            "id": child_id,
            "order": 0,
            "panel_role": role,
            "semantic_reason": reason,
            # 分割後は読解を優先し、元ページの斜め演出を子コマへ持ち込まない。
            "panel_shape": "rectangle",
            "shape_reason": "",
            "shot_type": shot_type,
            "head_visible": True,
            "dialogue": dialogue,
            "narration": narration,
            "sfx": sfx,
            "dialogue_types": list(panel.get("dialogue_types") or []) if dialogue else [],
            "sfx_types": list(panel.get("sfx_types") or []) if sfx else [],
            "hands_required": hands_required,
            "props_required": props_required,
            "fallback_applied": True,
            "fallback_type": FALLBACK_PANEL_SPLIT,
            "fallback_reason": reason,
            "original_panel_id": original_id,
            "generated_panel_ids": list(generated_ids),
            "original_story_intent": _story_intent(panel),
            "fallback_lineage": {"original_panel_id": original_id, "role": role},
            "image_url": None,
            "generation_status": "not_started",
            "generation_error": None,
            "generation_prompt": "",
            "revision": 0,
            "panel_direction": None,
        }
    )
    for key in ("geometry", "visual_position", "text_layout"):
        child.pop(key, None)
    return child


def split_overloaded_panel(panel: Mapping[str, Any], language: str = "ja") -> List[Dict[str, Any]]:
    """顔/セリフと手/器具へ分け、元Panelへのlineageを各子へ保存する。"""

    original_id = str(panel.get("id") or "panel")
    if panel.get("fallback_applied") or panel.get("image_url"):
        return [deepcopy(dict(panel))]
    face_id = f"{original_id}--face-dialogue"
    detail_id = f"{original_id}--hands-prop"
    generated_ids = [face_id, detail_id]
    reason = "横長コマの情報量を分散し、顔/セリフと手/器具を読みやすく分離"
    face = _child_panel(
        panel,
        face_id,
        role="fallback_face_dialogue",
        dialogue=list(panel.get("dialogue") or []),
        narration=list(panel.get("narration") or []),
        sfx=[],
        shot_type="medium_close",
        hands_required=False,
        props_required=False,
        original_id=original_id,
        generated_ids=generated_ids,
        reason=reason,
    )
    detail = _child_panel(
        panel,
        detail_id,
        role="fallback_hands_prop_detail",
        dialogue=[],
        narration=[],
        sfx=list(panel.get("sfx") or []),
        shot_type="close_up",
        hands_required=True,
        props_required=True,
        original_id=original_id,
        generated_ids=generated_ids,
        reason=reason,
    )
    # 手元・器具のディテールPanelは頭部を要求しない。これにより、元の
    # 「全頭＋手＋器具＋セリフ」という過密条件を子コマへ持ち越さず、
    # 直接生成の安全なdetail shotとして扱える。
    detail["head_visible"] = False
    # 子コマを同じ人物・動作の連続として扱い、片方にだけ本文を残す。
    detail["description"] = f"{panel.get('description', '')}（手元と器具のディテール）"[:2000]
    detail["action"] = f"{panel.get('action', '')} 手元と器具を明確に見せる"[:500]
    face["description"] = f"{panel.get('description', '')}（顔と反応）"[:2000]
    face["action"] = f"{panel.get('action', '')} 顔の反応を中心に見せる"[:500]
    return [face, detail]


def _apply_shot_relaxation(
    panel: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> Dict[str, Any]:
    """実際に適用できるショット緩和を保存し、構図を再計算させる。"""

    adjusted = deepcopy(dict(panel))
    requested = str(panel.get("shot_type") or "medium")
    effective = str(plan.get("effective_shot_type") or requested)
    adjusted.update(
        {
            "shot_type": effective,
            "requested_shot_type": requested,
            "effective_shot_type": effective,
            "fallback_applied": True,
            "fallback_type": FALLBACK_SHOT_RELAXATION,
            "fallback_reason": str(plan.get("fallback_reason") or "")[:240],
            "fallback_attempts": deepcopy(plan.get("attempts") or []),
            "generated_panel_ids": [str(panel.get("id") or "")],
            "panel_direction": None,
        }
    )
    for key in ("geometry", "visual_position", "text_layout"):
        adjusted.pop(key, None)
    return adjusted


def apply_fallbacks_to_panels(
    panels: List[Mapping[str, Any]],
    *,
    language: str = "ja",
    allow_split: bool = True,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """未生成Panelだけへ局所変換を適用し、ページ通知用記録も返す。"""

    result: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    for raw_panel in panels:
        panel = deepcopy(dict(raw_panel))
        # 既存ArtworkはそのPanelを分割・差し替えしない。一方、同じPageに
        # 未生成Panelが残っている場合は、そのPanelだけを再評価できるように
        # する。ページ全体の再配置は呼び出し側が一度だけ行う。
        if (
            panel.get("image_url")
            or panel.get("fallback_applied")
            or not allow_split
        ):
            result.append(panel)
            continue
        complexity = panel_spatial_complexity(panel)
        panel["composition_complexity"] = complexity["score"]
        panel["composition_complexity_level"] = complexity["level"]
        panel["feasibility_status"] = complexity["feasibility_status"]
        panel["feasibility_reasons"] = complexity["reasons"]
        if complexity["level"] != COMPLEXITY_INFEASIBLE:
            result.append(panel)
            continue
        plan = resolve_composition_fallback(panel, language)
        if plan["fallback_type"] == FALLBACK_PANEL_SPLIT:
            children = split_overloaded_panel(panel, language)
        elif plan["fallback_type"] == FALLBACK_SHOT_RELAXATION:
            children = [_apply_shot_relaxation(panel, plan)]
        else:
            # 文字移動／geometry変更は、実際の予約領域またはPage全体の
            # 再計算を伴うため、この純粋関数では仮候補として記録する。
            # 適用不能なまま生成へ進めず、最終的にsplitへ収束させる。
            children = split_overloaded_panel(panel, language)
        child_ids = [str(item.get("id")) for item in children]
        for child in children:
            child["composition_complexity"] = complexity["score"]
            child["composition_complexity_level"] = complexity["level"]
            child["feasibility_status"] = "FEASIBLE"
            child["feasibility_reasons"] = ["分割後の局所Panelとして再評価"]
            child["fallback_recommendation"] = plan["fallback_type"]
            child["fallback_attempts"] = deepcopy(plan["attempts"])
            child["generated_panel_ids"] = child_ids
            result.append(child)
        records.append(
            {
                "original_panel_id": str(panel.get("id") or ""),
                "fallback_applied": True,
                "fallback_type": plan["fallback_type"],
                "fallback_reason": plan["fallback_reason"],
                "generated_panel_ids": child_ids,
                "original_story_intent": _story_intent(panel),
                "attempts": deepcopy(plan["attempts"]),
                "complexity": deepcopy(complexity),
            }
        )
    for index, panel in enumerate(result, start=1):
        panel["order"] = index
    return result, records
