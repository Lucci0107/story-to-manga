"""全編の人物候補を照合し、固定する人物の選択・統合・除外を検証する。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..schemas import CHARACTER_TEXT_MAX_LENGTH, MAX_CHARACTERS
from .character_cast import CharacterValidationError


EXCLUSION_REASONS = {"incidental_person", "reference_only", "generic_or_hypothetical", "background_group"}


def cast_review_schema(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = [item["name"] for item in candidates]
    name_schema = {"type": "string", "enum": names}
    return {
        "type": "object",
        "properties": {
            "groups": {"type": "array", "minItems": 1, "maxItems": len(names), "items": {
                "type": "object",
                "properties": {
                    "name": name_schema,
                    "members": {"type": "array", "minItems": 1, "maxItems": len(names), "items": name_schema},
                },
                "required": ["name", "members"], "additionalProperties": False,
            }},
            "excluded": {"type": "array", "maxItems": len(names), "items": {
                "type": "object",
                "properties": {"name": name_schema, "reason": {"type": "string", "enum": sorted(EXCLUSION_REASONS)}},
                "required": ["name", "reason"], "additionalProperties": False,
            }},
        },
        "required": ["groups", "excluded"], "additionalProperties": False,
    }


def _coverage_error() -> CharacterValidationError:
    return CharacterValidationError(
        "人物候補の整理に省略・重複・対象外の名前があります", category="character_coverage",
        repair_hint="candidatesの全nameをgroups.membersまたはexcludedへちょうど1回ずつ入れてください。"
                    "groups.nameはそのgroups.members内の名前を使い、新しい人物を作らないでください。"
                    "除外する人物にも指定されたreasonを付け、重要な人物を人数合わせで除外しないでください。",
    )


def normalize_cast_review(value: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """全候補の処遇を確認してから、原文の根拠を残して同一人物をまとめる。"""

    groups, excluded = value.get("groups"), value.get("excluded")
    if not isinstance(groups, list) or not groups or not isinstance(excluded, list):
        raise _coverage_error()
    if len(groups) > MAX_CHARACTERS:
        raise CharacterValidationError(
            f"固定する人物が保存上限の{MAX_CHARACTERS}人を超えています。漫画化する範囲を分けてください",
            category="character_limit", repair_hint="同一人物の重複を整理し、重要な人物を人数合わせで除外しないでください。",
        )
    lookup = {item["name"]: item for item in candidates}
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise _coverage_error()
        name, members = group.get("name"), group.get("members")
        if (not isinstance(name, str) or not isinstance(members, list) or not members or name not in members
                or any(not isinstance(member, str) or member not in lookup or member in seen for member in members)
                or len(set(members)) != len(members)):
            raise _coverage_error()
        seen.update(members)
    for item in excluded:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or item["name"] not in lookup or item["name"] in seen
                or not isinstance(item.get("reason"), str) or item["reason"] not in EXCLUSION_REASONS):
            raise _coverage_error()
        seen.add(item["name"])
    if seen != set(lookup):
        raise _coverage_error()
    result: List[Dict[str, Any]] = []
    order = {item["name"]: index for index, item in enumerate(candidates)}
    for group in sorted(groups, key=lambda item: min(order[name] for name in item["members"])):
        canonical = lookup[group["name"]]
        members = [canonical] + [lookup[name] for name in group["members"] if name != canonical["name"]]
        aliases = list(dict.fromkeys(alias for member in members for alias in [member["name"]] + member["aliases"]
                                    if alias != canonical["name"]))
        result.append({
            **canonical, "aliases": aliases,
            "role": " / ".join(dict.fromkeys(member["role"] for member in members))[:CHARACTER_TEXT_MAX_LENGTH],
            "source_quotes": list(dict.fromkeys(quote for member in members for quote in member["source_quotes"])),
            "source_parts": sorted({part for member in members for part in member.get("source_parts", [])}),
        })
    return result
