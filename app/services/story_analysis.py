"""原稿全文の解析範囲と、ネームに必要な原文の参照を管理する。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .story_profile import MAX_SOURCE_SECTIONS, build_story_source_profile, story_fingerprint


ANALYSIS_SOURCE_VERSION = 2
ANALYSIS_BATCH_CHARACTERS = 60_000
ANALYSIS_UNIT_CHARACTERS = 12_000
LEGACY_EXCERPT_THRESHOLD = 24_000


def story_analysis_units(text: str) -> list[dict]:
    """章の中間も残し、大きい章だけ分割する。章が多い場合も全文を保持する。"""

    sections = build_story_source_profile(text, include_section_text=True)["section_texts"]
    grouped = []
    count = min(MAX_SOURCE_SECTIONS, len(sections))
    for index in range(count):
        group = sections[index * len(sections) // count:(index + 1) * len(sections) // count]
        content = "\n\n".join(section["text"] for section in group)
        title = group[0]["title"] if len(group) == 1 else f'{group[0]["title"]} ～ {group[-1]["title"]}'
        parts = [content[start:start + ANALYSIS_UNIT_CHARACTERS]
                 for start in range(0, len(content), ANALYSIS_UNIT_CHARACTERS)]
        for part_index, part in enumerate(parts, 1):
            grouped.append({"number": len(grouped) + 1, "title": title[:240],
                            "part": part_index, "parts": len(parts), "text": part})
    return grouped


def analysis_batches(units: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    size = 0
    for unit in units:
        if not batches or size + len(unit["text"]) > ANALYSIS_BATCH_CHARACTERS:
            batches.append([])
            size = 0
        batches[-1].append(unit)
        size += len(unit["text"])
    return batches


def section_analysis_schema(base: dict, units: list[dict]) -> dict:
    schema = json.loads(json.dumps(base))
    schema["properties"]["source_sections"] = {
        "type": "array", "minItems": len(units), "maxItems": len(units),
        "items": {"type": "object", "properties": {
            "number": {"type": "integer", "enum": [unit["number"] for unit in units]},
            "summary": {"type": "string"},
            "events": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "string"}},
        }, "required": ["number", "summary", "events"], "additionalProperties": False},
    }
    schema["required"].append("source_sections")
    return schema


def validate_analysis_sections(value: dict, units: list[dict]) -> dict:
    sections = value.get("source_sections") or []
    if ([item.get("number") for item in sections] != [unit["number"] for unit in units]
            or any(not str(item.get("summary") or "").strip()
                   or not item.get("events")
                   or any(not str(event).strip() for event in item["events"]) for item in sections)):
        raise ValueError("原稿の解析区間が不足しています。全区間を順番どおりに含めてください")
    return value


def complete_source_analysis(value: dict, text: str, units: list[dict]) -> dict:
    validate_analysis_sections(value, units)
    source = build_story_source_profile(text)
    value["major_events"] = list(dict.fromkeys(
        event for section in value["source_sections"] for event in section["events"]
    ))
    value["source_coverage"] = {
        "version": ANALYSIS_SOURCE_VERSION, "source_fingerprint": source["source_fingerprint"],
        "chapter_count": source["chapter_count"], "section_count": source["section_count"],
        "unit_count": len(units),
    }
    return value


def analysis_content_fingerprint(analysis: dict) -> str:
    """生成時の解析を識別する。表示用の履歴・Knowledge更新だけでは変化させない。"""

    value = {key: item for key, item in analysis.items() if key not in {"knowledge_refs", "source_coverage"}}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def analysis_source_status(text: str, analysis: dict, pages: list | None = None) -> dict:
    profile = build_story_source_profile(text)
    coverage = analysis.get("source_coverage") or {}
    has_coverage = coverage.get("version") == ANALYSIS_SOURCE_VERSION
    requires = bool(analysis) and (
        (not has_coverage and len(text) > LEGACY_EXCERPT_THRESHOLD)
        or (has_coverage and coverage.get("source_fingerprint") != story_fingerprint(text))
    )
    if has_coverage and not requires:
        try:
            validate_analysis_sections(analysis, story_analysis_units(text))
        except (ValueError, TypeError, AttributeError):
            requires = True
    fingerprint = analysis_content_fingerprint(analysis)
    content = [page for page in pages or [] if page.get("page_kind") not in {"cover", "back_cover"}]
    origins = [page["source_analysis_fingerprint"] for page in content if page.get("source_analysis_fingerprint")]
    # 最新の解析で生成した本文に手動でページを追加しただけなら、作り直しを強制しない。
    needs_rebuild = bool(has_coverage and not requires and content and (
        not origins or any(origin != fingerprint for origin in origins)
    ))
    return {"requires_reanalysis": requires, "storyboard_needs_rebuild": needs_rebuild,
            "complete": has_coverage and not requires, "chapter_count": profile["chapter_count"],
            "section_count": profile["section_count"], "event_count": len(analysis.get("major_events") or []),
            "legacy_excerpt": bool(analysis and not has_coverage and len(text) > LEGACY_EXCERPT_THRESHOLD)}


def storyboard_source_reference(units: list[dict], analysis: dict, plan: list[dict], start: int, end: int) -> list[dict]:
    """今回のページで扱う出来事の原文を選ぶ。結末も対応する最終範囲に渡す。"""

    allowed = {event for page in plan[start - 1:end] for event in page["allowed_events"]}
    selected = {section["number"] for section in analysis.get("source_sections") or []
                if allowed.intersection(section.get("events") or [])}
    if selected:
        return [unit for unit in units if unit["number"] in selected]
    # 区間対応のない短い旧解析でも、抜粋だけへ戻さず担当範囲の全文を渡す。
    total = max(1, len(plan))
    content = "\n\n".join(unit["text"] for unit in units)
    first, last = (start - 1) * len(content) // total, end * len(content) // total
    return [{"number": start, "title": "生成範囲の原文", "text": content[first:last]}] if content else []
