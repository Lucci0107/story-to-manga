"""主要人物の提案・選択を扱い、確認前の詳細生成を防ぐ。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from ..schemas import MAX_CHARACTERS
from .story_profile import build_story_source_profile

RECOMMENDED_CHARACTER_LIMIT = 12
PROPOSAL_VERSION = 1


def character_input_fingerprint(project: dict) -> str:
    value = {"text": project.get("original_text", ""), "analysis": project.get("analysis") or {},
             "version": PROPOSAL_VERSION}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def proposal_is_stale(proposal: Any, project: dict) -> bool:
    return not isinstance(proposal, dict) or proposal.get("input_fingerprint") != character_input_fingerprint(project)


def _identity(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def existing_character(candidate: dict, characters: list[dict]) -> dict | None:
    names = {_identity(name) for name in [candidate["name"], *candidate.get("aliases", [])]}
    canonical = _identity(candidate["name"])
    matches = [item for item in characters if _identity(item.get("name", "")) in names
               or canonical in {_identity(alias) for alias in item.get("aliases", [])}]
    return matches[0] if len(matches) == 1 else None


def candidate_frequency(candidate: dict, sections: list[dict]) -> dict:
    names = list(dict.fromkeys([candidate["name"], *candidate.get("aliases", [])]))
    # 長い別名を先に数えて重複を避け、英字名を別の単語内へ一致させない。
    terms = [rf"(?<!\w){re.escape(name)}(?!\w)" if name.isascii() and name.isalnum()
             else re.escape(name) for name in sorted(names, key=len, reverse=True) if name]
    pattern = re.compile("|".join(terms))
    quotes = candidate.get("source_quotes", [])
    appearing = [section for section in sections if pattern.search(section["text"])
                 or any(quote and quote in section["text"] for quote in quotes)]
    return {
        "mention_count": sum(len(pattern.findall(section["text"])) for section in sections),
        "appearance_section_count": len(appearing),
        "appearance_rate": round(100 * len(appearing) / max(1, len(sections)), 1),
        "appearing_sections": [{"number": section["number"], "title": section["title"]} for section in appearing],
    }


def build_character_proposal(candidates: list[dict], project: dict, *,
                             metadata: dict | None = None, knowledge_refs: list | None = None) -> dict:
    profile = build_story_source_profile(project["original_text"], include_section_text=True)
    sections = profile["section_texts"]
    result = []
    for candidate in candidates:
        result.append({
            **candidate, "id": uuid.uuid5(uuid.NAMESPACE_OID, _identity(candidate["name"])).hex,
            **candidate_frequency(candidate, sections),
            "importance": candidate.get("importance", 3),
            "recommendation_reason": candidate.get("recommendation_reason", "原稿上の役割と登場頻度から確認してください。"),
        })
    result.sort(key=lambda item: (-item["importance"], -item["appearance_section_count"], -item["mention_count"]))
    recommended = [item for item in result if item["importance"] >= 4][:RECOMMENDED_CHARACTER_LIMIT]
    if not recommended:
        recommended = result[:min(3, len(result))]
    recommended_ids = {item["id"] for item in recommended}
    for item in result:
        item["recommended"] = item["id"] in recommended_ids
    return {
        "id": str(uuid.uuid4()), "version": PROPOSAL_VERSION,
        "input_fingerprint": character_input_fingerprint(project), "status": "awaiting_confirmation",
        "candidates": result, "selected_candidate_ids": [item["id"] for item in recommended],
        "section_count": len(sections), "section_label": "章" if profile["chapter_count"] else "区間",
        "recommendation_limit": RECOMMENDED_CHARACTER_LIMIT, "selection_limit": MAX_CHARACTERS,
        "frequency_note": "出現率は、名前・別名・抽出根拠を確認できた章／区間の割合です。代名詞だけの登場は反映しきれない推定値です。",
        "generation_metadata": metadata, "knowledge_refs": knowledge_refs or [],
        "created_at": datetime.now(timezone.utc).isoformat(), "confirmed_at": None,
    }


def validate_selection(project: dict, proposal_id: str, selected_ids: list[str], *,
                       allow_empty: bool = False) -> list[dict]:
    proposal = project.get("character_proposal")
    if proposal_is_stale(proposal, project) or proposal.get("id") != proposal_id:
        raise ValueError("原稿・解析または人物候補が更新されています。主要人物を再提案して選び直してください")
    if (not allow_empty and not selected_ids) or len(selected_ids) > MAX_CHARACTERS or len(set(selected_ids)) != len(selected_ids):
        raise ValueError(f"人物設定の対象を1〜{MAX_CHARACTERS}人の範囲で選択してください")
    lookup = {item["id"]: item for item in proposal["candidates"]}
    if any(item not in lookup for item in selected_ids):
        raise ValueError("提案に存在しない人物が選択されています。候補を確認し直してください")
    targets = [item for item in proposal["candidates"] if item["id"] in set(selected_ids)]
    existing = project.get("characters") or []
    new_count = sum(existing_character(item, existing) is None for item in targets)
    if len(existing) + new_count > MAX_CHARACTERS:
        raise ValueError(f"保存済みの人物と合わせて{MAX_CHARACTERS}人を超えます。生成する対象を減らしてください")
    return targets


def merge_generated_characters(existing: list[dict], generated: list[dict]) -> list[dict]:
    result = list(existing)
    for item in generated:
        if existing_character(item, result) is None:
            result.append(item)
    if len(result) > MAX_CHARACTERS:
        raise ValueError("人物設定の保存上限を超えています")
    return result


def proposal_view(project: dict) -> dict | None:
    proposal = project.get("character_proposal")
    if not isinstance(proposal, dict):
        return None
    return {**proposal, "stale": proposal_is_stale(proposal, project),
            "candidates": [{**item, "existing_character_id": (existing_character(item, project.get("characters") or []) or {}).get("id")}
                           for item in proposal.get("candidates", [])]}


def active_characters(project: dict) -> list[dict]:
    """保管した設定を削除せず、確認済みの人物だけを新しい制作へ使う。"""

    stored = project.get("characters") or []
    proposal = project.get("character_proposal")
    if not proposal:
        return stored
    if proposal_is_stale(proposal, project) or not proposal.get("confirmed_at"):
        return []
    selected_ids = set(proposal.get("selected_candidate_ids", []))
    result = []
    for item in proposal["candidates"]:
        if item["id"] in selected_ids:
            character = existing_character(item, stored)
            if character is None:
                return []
            if character not in result:
                result.append(character)
    return result


def panel_characters(project: dict, panel: dict) -> list[dict]:
    """コマに実際に登場する人物の設定だけを渡す。既存のコマの人物も保持する。"""

    names = {_identity(name) for name in panel.get("characters") or []}
    return [item for item in project.get("characters") or []
            if names.intersection({_identity(name) for name in [item["name"], *item.get("aliases", [])]})]
