"""長い原稿を省略せず、人物の抽出根拠と全員分の設定を照合する。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List

from ..schemas import MAX_CHARACTERS, normalize_characters
from .story_profile import build_story_source_profile


CHARACTER_SOURCE_CHUNK_SIZE = 24_000
CHARACTER_PROFILE_BATCH_SIZE = 8
CAST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "cast": {
            "type": "array",
            "maxItems": MAX_CHARACTERS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                    "role": {"type": "string"},
                    "source_quotes": {"type": "array", "minItems": 1, "maxItems": 8,
                                      "items": {"type": "string"}},
                },
                "required": ["name", "aliases", "role", "source_quotes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cast"],
    "additionalProperties": False,
}


def character_source_chunks(text: str) -> List[str]:
    """本文を全文残して分割し、末尾の参考文献を登場人物と混同しない。"""

    source = str(text or "").replace("\r\n", "\n").strip()
    profile = build_story_source_profile(source)
    body = source[:profile["narrative_character_count"]]
    chunks: List[str] = []
    start = 0
    while start < len(body):
        end = min(len(body), start + CHARACTER_SOURCE_CHUNK_SIZE)
        if end < len(body):
            # 段落・文の途中で名前や人物関係を分断しない。
            boundary = body.rfind("\n\n", start + CHARACTER_SOURCE_CHUNK_SIZE // 2, end)
            if boundary < 0:
                boundary = body.rfind("。", start + CHARACTER_SOURCE_CHUNK_SIZE // 2, end)
            if boundary >= 0:
                end = boundary + 1
        chunks.append(body[start:end])
        start = end
    return chunks


def normalize_cast(value: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    """原文に存在する引用を持つ人物候補だけを受け入れる。"""

    raw = value.get("cast")
    if not isinstance(raw, list) or len(raw) > MAX_CHARACTERS:
        raise ValueError("人物一覧の形式または人数が不正です")
    result: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("人物候補の形式が不正です")
        name = str(item.get("name") or "").strip()
        role = str(item.get("role") or "").strip()
        quotes = item.get("source_quotes")
        aliases = item.get("aliases")
        if not name or len(name) > 80 or not role or len(role) > 2_000:
            raise ValueError("人物名または役割が不正です")
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 8:
            raise ValueError("人物の抽出根拠がありません")
        if any(not isinstance(quote, str) or not quote.strip() or quote.strip() not in source
               for quote in quotes):
            raise ValueError("人物の抽出根拠が原稿に存在しません")
        if not isinstance(aliases, list) or len(aliases) > 4 or any(
            not isinstance(alias, str) or not alias.strip() or len(alias) > 80 for alias in aliases
        ):
            raise ValueError("人物の別名が不正です")
        result.append({"name": name, "role": role,
                       "aliases": list(dict.fromkeys(alias.strip() for alias in aliases)),
                       "source_quotes": list(dict.fromkeys(quote.strip() for quote in quotes))})
    return result


def _identity_key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).casefold()


def merge_cast(roster: List[Dict[str, Any]], candidates: List[Dict[str, Any]]) -> None:
    """名前・明示された別名を照合し、共有する一般呼称だけでは統合しない。"""

    for candidate in candidates:
        name = _identity_key(candidate["name"])
        aliases = {_identity_key(alias) for alias in candidate["aliases"]}
        matches = [item for item in roster if name == _identity_key(item["name"])
                   or name in {_identity_key(alias) for alias in item["aliases"]}
                   or _identity_key(item["name"]) in aliases]
        if len(matches) > 1:
            raise ValueError("人物の別名が複数の人物に一致しています")
        if not matches:
            if len(roster) >= MAX_CHARACTERS:
                raise ValueError(f"人物が保存上限の{MAX_CHARACTERS}人を超えています。漫画化する範囲を分けてください")
            roster.append(dict(candidate))
            continue
        existing = matches[0]
        existing["aliases"] = list(dict.fromkeys(existing["aliases"] + candidate["aliases"]
                                                 + [candidate["name"]]))
        existing["source_quotes"] = list(dict.fromkeys(existing["source_quotes"]
                                                       + candidate["source_quotes"]))
        if candidate["role"] not in existing["role"]:
            existing["role"] = (existing["role"] + " / " + candidate["role"])[:2_000]


def normalize_character_batch(value: Dict[str, Any], roster: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """人物一覧の各人にちょうど一つの設定があることを、正規化前に確認する。"""

    raw = value.get("characters")
    if not isinstance(raw, list) or len(raw) != len(roster) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("人物一覧と人物設定の人数が一致しません")
    names = [str(item.get("name") or "").strip() for item in raw]
    expected = [item["name"] for item in roster]
    if len(set(names)) != len(names) or set(names) != set(expected):
        raise ValueError("人物一覧の誰かが省略または重複しています")
    if any(not str(item.get("appearance") or "").strip() for item in raw):
        raise ValueError("人物の外見設定がありません")
    lookup = {item["name"]: item for item in normalize_characters(raw)}
    result = []
    for person in roster:
        character = lookup[person["name"]]
        character["aliases"] = person["aliases"][:16]
        character["source_quotes"] = person["source_quotes"][:16]
        result.append(character)
    return result
