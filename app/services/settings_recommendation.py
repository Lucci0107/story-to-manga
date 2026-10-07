"""Story Analysisと原稿の規模から漫画化設定の推奨値を作るサービス。

AIの出力はこのモジュールで正規化し、AIが利用できない環境でも同じ契約で
決定論的なフォールバックを返す。Projectの言語と読み方向は推奨対象にせず、
既存のサーバー側canonical設定を常に正とする。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from ..schemas import (
    ALLOWED_COLOR_MODES,
    ALLOWED_DIALOGUE_DENSITY,
    ALLOWED_PACING,
    ALLOWED_STYLES,
    normalize_analysis,
)
from .reading_order import canonicalize_stored_settings
from .story_profile import story_fingerprint


MAX_RECOMMENDED_PAGE_COUNT = 120
MAX_SCENE_BUDGET_ENTRIES = 64
ESTIMATOR_VERSION = 2
RECOMMENDATION_KEYS = (
    "recommended_page_count",
    "recommended_visual_style",
    "recommended_color_mode",
    "recommended_pacing",
    "recommended_dialogue_density",
    "recommended_target_audience",
)
SCENE_TYPES = {
    "establishing",
    "dialogue",
    "action",
    "emotional",
    "exposition",
    "climax",
    "transition",
}
IMPORTANCE_LEVELS = {"low", "medium", "high"}


class RecommendationCoverageError(ValueError):
    """全編向けの推奨ページ数が、原稿の規模に対して不足している。"""


def _language(settings: Optional[Dict[str, Any]]) -> str:
    return canonicalize_stored_settings(settings or {}).get("language", "ja")


def _text(value: Any, limit: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _items(analysis: Dict[str, Any], key: str) -> List[str]:
    value = analysis.get(key, [])
    if isinstance(value, str):
        value = [line.strip() for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        return []
    return [_text(item, 500) for item in value[:32] if _text(item, 500)]


def analysis_fingerprint(analysis: Any) -> str:
    """推奨の再利用・stale判定に使うAnalysisの安定した指紋を返す。"""

    normalized = normalize_analysis(analysis)
    relevant = {
        key: normalized.get(key)
        for key in (
            "title",
            "synopsis",
            "genre",
            "tone",
            "world_setting",
            "main_characters",
            "supporting_characters",
            "locations",
            "major_events",
            "story_beats",
            "conflicts",
            "climax",
            "ending",
            "important_objects",
        )
    }
    # ユーザー編集で追加された補助分析値も、存在する場合だけ指紋に含める。
    raw = analysis if isinstance(analysis, dict) else {}
    for key in (
        "scenes",
        "dialogue_volume",
        "narration_density",
        "action_intensity",
        "emotional_beats",
        "scene_transitions",
        "timeline_complexity",
    ):
        if key in raw:
            relevant[key] = raw[key]
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _keyword_count(values: Iterable[str], keywords: Iterable[str]) -> int:
    text = " ".join(values).lower()
    return sum(text.count(keyword.lower()) for keyword in keywords)


def _complexity(analysis: Dict[str, Any]) -> Dict[str, float]:
    """文字数だけに依存しない、説明可能なページ見積もり用指標を作る。"""

    scene_values = _items(analysis, "scenes")
    beats = _items(analysis, "story_beats")
    events = _items(analysis, "major_events")
    conflicts = _items(analysis, "conflicts")
    locations = _items(analysis, "locations")
    characters = _items(analysis, "main_characters") + _items(analysis, "supporting_characters")
    objects = _items(analysis, "important_objects")
    scene_count = max(len(scene_values), len(beats), len(events), 1)
    action_count = _keyword_count(
        events + beats + conflicts + [_text(analysis.get("climax"))],
        ("戦", "追", "逃", "攻", "action", "fight", "chase", "battle", "爆発", "危機"),
    )
    emotional_count = _keyword_count(
        conflicts + beats + [_text(analysis.get("tone")), _text(analysis.get("climax"))],
        ("感情", "決断", "別れ", "再会", "涙", "希望", "迷い", "emotional", "choice", "loss"),
    )
    dialogue_count = _keyword_count(
        events + beats + [_text(analysis.get("synopsis")), _text(analysis.get("tone"))],
        ("会話", "対話", "相談", "告白", "dialogue", "conversation", "interview"),
    )
    text_fields = (
        _text(analysis.get("synopsis"), 4_000),
        _text(analysis.get("world_setting"), 2_000),
        _text(analysis.get("climax"), 2_000),
        _text(analysis.get("ending"), 2_000),
    )
    narrative_weight = min(8.0, sum(len(value) for value in text_fields) / 650.0)
    timeline = _text(analysis.get("timeline_complexity"), 120).lower()
    if timeline in {"high", "複雑", "complex"}:
        timeline_weight = 5.0
    elif timeline in {"medium", "中", "moderate"}:
        timeline_weight = 2.0
    else:
        timeline_weight = 0.0
    return {
        "scene_count": float(scene_count),
        "event_count": float(len(events)),
        "beat_count": float(len(beats)),
        "conflict_count": float(len(conflicts)),
        "location_count": float(len(locations)),
        "character_count": float(len(characters)),
        "object_count": float(len(objects)),
        "action_count": float(action_count),
        "emotional_count": float(emotional_count),
        "dialogue_count": float(dialogue_count),
        "narrative_weight": narrative_weight,
        "timeline_weight": timeline_weight,
    }


def _scene_type(label: str, index: int, total: int, analysis: Dict[str, Any]) -> str:
    lowered = label.lower()
    if index == total - 1 or any(word in lowered for word in ("climax", "決着", "最終", "決断")):
        return "climax"
    if any(word in lowered for word in ("戦", "追", "action", "fight", "battle", "危機")):
        return "action"
    if any(word in lowered for word in ("会話", "対話", "再会", "dialogue", "conversation")):
        return "dialogue"
    if any(word in lowered for word in ("感情", "別れ", "涙", "希望", "emotional")):
        return "emotional"
    if index == 0:
        return "establishing"
    if any(word in lowered for word in ("説明", "世界", "exposition")):
        return "exposition"
    if index == total - 2 or "転" in lowered:
        return "transition"
    return "dialogue" if _items(analysis, "conflicts") else "exposition"


def _group_labels(labels: List[str], count: int) -> List[str]:
    if len(labels) <= count:
        return labels
    result = []
    for index in range(count):
        group = labels[index * len(labels) // count:(index + 1) * len(labels) // count]
        result.append(f"{group[0][:45]} ～ {group[-1][:45]}（{len(group)}場面）")
    return result


def _scene_budget(
    analysis: Dict[str, Any], page_count: int, language: str,
    source_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    labels = _items(analysis, "scenes") or _items(analysis, "story_beats") or _items(analysis, "major_events")
    if source_profile and (source_profile.get("chapter_count") or source_profile.get("section_count", 0) > 1):
        labels = [item["title"] for item in source_profile.get("sections", [])]
    if not labels:
        labels = ["導入", "展開", "結末"] if language == "ja" else ["Opening", "Development", "Ending"]
    labels = _group_labels(labels, min(MAX_SCENE_BUDGET_ENTRIES, max(1, page_count)))
    weights: List[float] = []
    types: List[str] = []
    source_sections = (source_profile or {}).get("sections", [])
    average_characters = sum(item["characters"] for item in source_sections) / len(source_sections) if source_sections else 0
    for index, label in enumerate(labels):
        scene_type = _scene_type(label, index, len(labels), analysis)
        types.append(scene_type)
        weights.append({"climax": 2.4, "action": 1.8, "emotional": 1.6, "dialogue": 1.2, "establishing": 0.9, "transition": 0.8, "exposition": 1.0}.get(scene_type, 1.0))
        if len(source_sections) == len(labels) and average_characters:
            weights[-1] *= max(0.5, min(2.5, source_sections[index]["characters"] / average_characters))
    total_weight = sum(weights) or 1.0
    allocations = [max(1, round(page_count * weight / total_weight)) for weight in weights]
    # 端数調整は最重要シーンから行い、合計を推奨ページ数に合わせる。
    while sum(allocations) > page_count and max(allocations) > 1:
        index = max(range(len(allocations)), key=lambda item: (allocations[item], weights[item]))
        allocations[index] -= 1
    while sum(allocations) < page_count:
        index = max(range(len(allocations)), key=lambda item: (weights[item], -item))
        allocations[index] += 1
    reasons_ja = {
        "establishing": "舞台と人物を理解するための導入",
        "dialogue": "会話の間とリアクションを確保",
        "action": "動きと視線の連続性を確保",
        "emotional": "感情の変化を急がず見せる",
        "exposition": "設定を絵で説明する余白を確保",
        "climax": "クライマックスの盛り上がりを保つ",
        "transition": "場面転換を読みやすくする",
    }
    reasons_en = {
        "establishing": "space to establish the setting and characters",
        "dialogue": "room for dialogue beats and reactions",
        "action": "room for action and visual continuity",
        "emotional": "time for the emotional change to land",
        "exposition": "space to explain the world visually",
        "climax": "enough room for the climax to build",
        "transition": "a clear transition between scenes",
    }
    reasons = reasons_ja if language == "ja" else reasons_en
    return [
        {
            "scene": label[:120],
            "scene_type": types[index],
            "importance": "high" if types[index] == "climax" else ("medium" if allocations[index] > 1 else "low"),
            "estimated_pages": allocations[index],
            "reason": reasons[types[index]],
        }
        for index, label in enumerate(labels)
    ]


def _fit_scene_budget(budget: List[Dict[str, Any]], page_count: int) -> List[Dict[str, Any]]:
    """AIが返した配分の合計を推奨ページ数へ合わせる。"""

    if not budget:
        return budget
    count = min(MAX_SCENE_BUDGET_ENTRIES, max(1, page_count))
    if len(budget) > count:
        result = []
        for index in range(count):
            group = budget[index * len(budget) // count:(index + 1) * len(budget) // count]
            item = dict(group[0])
            item["scene"] = _group_labels([str(value["scene"]) for value in group], 1)[0]
            item["estimated_pages"] = sum(int(value.get("estimated_pages", 1)) for value in group)
            item["importance"] = "high" if any(value.get("importance") == "high" for value in group) else "medium"
            result.append(item)
    else:
        result = [dict(item) for item in budget]
    while sum(int(item.get("estimated_pages", 1)) for item in result) > page_count:
        candidates = [index for index, item in enumerate(result) if int(item.get("estimated_pages", 1)) > 1]
        if not candidates:
            result = result[: max(1, page_count)]
            break
        index = max(candidates, key=lambda item: int(result[item].get("estimated_pages", 1)))
        result[index]["estimated_pages"] = int(result[index].get("estimated_pages", 1)) - 1
    while sum(int(item.get("estimated_pages", 1)) for item in result) < page_count:
        index = max(range(len(result)), key=lambda item: (result[item].get("importance") == "high", -item))
        result[index]["estimated_pages"] = int(result[index].get("estimated_pages", 1)) + 1
    return result


def fallback_recommendation(
    analysis: Any, settings: Optional[Dict[str, Any]] = None,
    source_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """解析と原稿の規模から、明示的な参考推定を作る。"""

    raw_analysis = analysis if isinstance(analysis, dict) else {}
    analysis = {**raw_analysis, **normalize_analysis(analysis)}
    language = _language(settings)
    metrics = _complexity(analysis)
    score = (
        5.5
        + metrics["scene_count"] * 1.7
        + metrics["event_count"] * 1.4
        + metrics["beat_count"] * 0.8
        + metrics["conflict_count"] * 2.4
        + metrics["location_count"] * 1.1
        + metrics["character_count"] * 0.65
        + metrics["object_count"] * 0.35
        + metrics["narrative_weight"]
        + metrics["timeline_weight"]
        + min(8.0, metrics["action_count"] * 1.5)
        + min(6.0, metrics["emotional_count"] * 1.0)
    )
    source_pages = int((source_profile or {}).get("reference_page_count", 0))
    page_count = max(8, min(MAX_RECOMMENDED_PAGE_COUNT, max(int(round(score)), source_pages)))
    all_text = " ".join(
        _items(analysis, "major_events")
        + _items(analysis, "story_beats")
        + [_text(analysis.get("genre")), _text(analysis.get("tone")), _text(analysis.get("climax"))]
    ).lower()
    if any(word in all_text for word in ("恋", "恋愛", "romance", "感情", "別れ")):
        style, color, pacing = "elegant", "color", "slow"
    elif any(word in all_text for word in ("コメディ", "喜劇", "comedy", "笑")):
        style, color, pacing = "comedy", "color", "fast"
    elif metrics["action_count"] or any(word in all_text for word in ("冒険", "ファンタジー", "fantasy", "action")):
        style, color, pacing = "dynamic", "color", "fast"
    elif any(word in all_text for word in ("ミステリー", "謎", "SF", "サスペンス", "serious", "cinematic")):
        style, color, pacing = "cinematic", "bw", "balanced"
    else:
        style, color, pacing = "cinematic", "bw", "balanced"
    dialogue = "high" if metrics["dialogue_count"] >= 2 or metrics["conflict_count"] >= 3 else "medium"
    audience = "一般読者" if language == "ja" else "General readers"
    scene_count = int(metrics["scene_count"])
    event_count = int(metrics["event_count"])
    if language == "ja":
        reason = f"主要シーン{scene_count}件、主要展開{event_count}件を基に、会話・感情・場面転換の余白を確保します。"
        page_reason = f"{scene_count}シーンの導入と展開に加え、クライマックスと結末を急がず描くため{page_count}ページを推奨します。"
    else:
        reason = f"Based on {scene_count} key scenes and {event_count} major events, this leaves room for dialogue, emotion, and transitions."
        page_reason = f"{page_count} pages leave enough room for the setup, climax, and ending without compressing key beats."
    if source_profile and source_profile.get("narrative_character_count"):
        characters = int(source_profile["narrative_character_count"])
        chapters = int(source_profile.get("chapter_count", 0))
        sections = int(source_profile.get("section_count", 0))
        if language == "ja":
            structure = f"{chapters}章" if chapters else f"{sections}区間"
            page_reason = f"原稿本文約{characters:,}文字・{structure}と解析結果から、全編の導入・会話・転換を描く参考値として{page_count}ページを見込みます。ネームで調整してください。"
        else:
            page_reason = f"Reference estimate: {page_count} pages for {characters:,} source characters across {sections} sections. Refine this during script review."
    return {
        "recommended_page_count": page_count,
        "recommended_visual_style": style,
        "recommended_color_mode": color,
        "recommended_pacing": pacing,
        "recommended_dialogue_density": dialogue,
        "recommended_target_audience": audience,
        "recommendation_reason": reason[:240],
        "page_count_reason": page_reason[:240],
        "scene_page_budget": _scene_budget(analysis, page_count, language, source_profile),
    }


def normalize_settings_recommendation(
    value: Any,
    analysis: Any,
    settings: Optional[Dict[str, Any]] = None,
    source_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Structured Outputをサーバー側で検証・補完する。"""

    fallback = fallback_recommendation(analysis, settings, source_profile)
    raw = value if isinstance(value, dict) else {}
    page_value = raw.get("recommended_page_count", fallback["recommended_page_count"])
    try:
        page_count = max(1, min(MAX_RECOMMENDED_PAGE_COUNT, int(page_value)))
    except (TypeError, ValueError):
        page_count = fallback["recommended_page_count"]
    minimum = min(MAX_RECOMMENDED_PAGE_COUNT, int((source_profile or {}).get("minimum_page_count", 1)))
    if page_count < minimum:
        raise RecommendationCoverageError(f"原稿全体の規模に対して{page_count}ページは不足しています（最低目安{minimum}ページ）。")
    style = str(raw.get("recommended_visual_style", ""))
    color = str(raw.get("recommended_color_mode", ""))
    pacing = str(raw.get("recommended_pacing", ""))
    dialogue = str(raw.get("recommended_dialogue_density", ""))
    audience = _text(raw.get("recommended_target_audience"), 80)
    budget = raw.get("scene_page_budget")
    if not isinstance(budget, list) or not budget:
        budget = _scene_budget(normalize_analysis(analysis), page_count, _language(settings), source_profile)
    normalized_budget: List[Dict[str, Any]] = []
    for item in budget:
        if not isinstance(item, dict):
            continue
        try:
            estimated = max(1, min(MAX_RECOMMENDED_PAGE_COUNT, int(item.get("estimated_pages", 1))))
        except (TypeError, ValueError):
            estimated = 1
        scene_type = str(item.get("scene_type", "transition"))
        importance = str(item.get("importance", "medium"))
        normalized_budget.append(
            {
                "scene": _text(item.get("scene"), 120) or "Scene",
                "scene_type": scene_type if scene_type in SCENE_TYPES else "transition",
                "importance": importance if importance in IMPORTANCE_LEVELS else "medium",
                "estimated_pages": estimated,
                "reason": _text(item.get("reason"), 240),
            }
        )
    if not normalized_budget:
        normalized_budget = _scene_budget(normalize_analysis(analysis), page_count, _language(settings), source_profile)
    normalized_budget = _fit_scene_budget(normalized_budget, page_count)
    normalized = {
        "recommended_page_count": page_count,
        "recommended_visual_style": style if style in ALLOWED_STYLES else fallback["recommended_visual_style"],
        "recommended_color_mode": color if color in ALLOWED_COLOR_MODES else fallback["recommended_color_mode"],
        "recommended_pacing": pacing if pacing in ALLOWED_PACING else fallback["recommended_pacing"],
        "recommended_dialogue_density": dialogue if dialogue in ALLOWED_DIALOGUE_DENSITY else fallback["recommended_dialogue_density"],
        "recommended_target_audience": audience or fallback["recommended_target_audience"],
        "recommendation_reason": _text(raw.get("recommendation_reason"), 240) or fallback["recommendation_reason"],
        "page_count_reason": _text(raw.get("page_count_reason"), 240) or fallback["page_count_reason"],
        "scene_page_budget": normalized_budget,
    }
    return normalized


def enrich_recommendation(
    value: Dict[str, Any],
    analysis: Any,
    *,
    generated_at: Optional[str] = None,
    fallback: bool = False,
    user_override: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    source_profile: Optional[Dict[str, Any]] = None,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    result = dict(value)
    result.update(
        {
            "analysis_fingerprint": analysis_fingerprint(analysis),
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "fallback": bool(fallback),
            "user_override": bool(user_override),
            "estimator_version": ESTIMATOR_VERSION,
            "source_fingerprint": (source_profile or {}).get("source_fingerprint", story_fingerprint("")),
        }
    )
    if source_profile:
        result["source_metrics"] = {key: source_profile[key] for key in (
            "source_character_count", "narrative_character_count", "reference_character_count",
            "chapter_count", "section_count", "paragraph_count", "quoted_passage_count",
        ) if key in source_profile}
        result["page_count_limit_reached"] = int(source_profile.get("reference_page_count", 0)) > MAX_RECOMMENDED_PAGE_COUNT
    if fallback_reason:
        result["fallback_reason"] = fallback_reason
    if isinstance(metadata, dict):
        result["generation_metadata"] = {
            key: metadata[key]
            for key in ("task", "requested_model", "actual_model", "fallback", "reasoning_effort", "provider", "created_at")
            if key in metadata
        }
    return result


def recommendation_settings(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """UI/API向けにSettingsPayloadへ適用できる項目だけを取り出す。"""

    if not isinstance(value, dict):
        return {}
    return {
        "target_page_count": value.get("recommended_page_count"),
        "color_mode": value.get("recommended_color_mode"),
        "visual_style": value.get("recommended_visual_style"),
        "pacing": value.get("recommended_pacing"),
        "dialogue_density": value.get("recommended_dialogue_density"),
        "target_audience": value.get("recommended_target_audience"),
    }


def recommendation_is_stale(value: Any, analysis: Any, source_text: Optional[str] = None) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        value.get("estimator_version") != ESTIMATOR_VERSION
        or str(value.get("analysis_fingerprint")) != analysis_fingerprint(analysis)
        or (source_text is not None and value.get("source_fingerprint") != story_fingerprint(source_text))
    )
