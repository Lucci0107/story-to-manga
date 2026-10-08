"""長い原稿を省略せず、人物の抽出根拠と全員分の設定を照合する。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List

from ..schemas import MAX_CHARACTERS, normalize_characters
from .story_profile import build_story_source_profile


CHARACTER_SOURCE_CHUNK_SIZE = 24_000
CHARACTER_PROFILE_BATCH_SIZE = 4
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


class CharacterValidationError(ValueError):
    """原稿や人物名を含まない、修復要求にも使える検証エラー。"""

    def __init__(self, message: str, *, category: str, repair_hint: str) -> None:
        super().__init__(message)
        self.error_category = category
        self.repair_hint = repair_hint


def _evidence_text(text: str) -> tuple[str, List[int]]:
    """組版上の空白・全半角だけを正規化し、原文の位置を保持する。"""

    characters: List[str] = []
    positions: List[int] = []
    for match in re.finditer(r"\s+|\S", text):
        character = match.group()
        index = match.start()
        if character[0].isspace():
            # 英単語間の空白は意味を持つため残し、連続する空白は一つにする。
            before = text[index - 1] if index else ""
            after = text[match.end():match.end() + 1]
            before, after = unicodedata.normalize("NFKC", before), unicodedata.normalize("NFKC", after)
            if before.isascii() and before.isalnum() and after.isascii() and after.isalnum():
                characters.append(" ")
                positions.append(index)
            continue
        normalized = unicodedata.normalize("NFKC", character)
        characters.extend(normalized)
        positions.extend([index] * len(normalized))
    return "".join(characters), positions


def _source_quote(quote: str, source: str, normalized_source: str, positions: List[int]) -> str:
    """引用を連続した原文へ照合する。言い換え・省略・曖昧一致は受け入れない。"""

    candidate = quote.strip()
    if candidate and candidate in source:
        return candidate
    normalized, _ = _evidence_text(candidate)
    start = normalized_source.find(normalized) if normalized else -1
    end = start + len(normalized)
    partial_character = start >= 0 and (
        (start > 0 and positions[start - 1] == positions[start])
        or (end < len(positions) and positions[end - 1] == positions[end])
    )
    if start < 0 or partial_character:
        raise CharacterValidationError(
            "人物の抽出根拠が原稿に存在しません",
            category="character_evidence",
            repair_hint="source_quotesの引用がstory_contentの原文と一致しません。"
                        "人物を省略せず、各人の根拠を原文から連続した短い一文としてそのままコピーしてください。"
                        "要約・言い換え・省略記号を使わないでください。",
        )
    return source[positions[start]:positions[end - 1] + 1].strip()


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
    normalized_source, positions = _evidence_text(source)
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
        if any(not isinstance(quote, str) or not quote.strip() for quote in quotes):
            raise ValueError("人物の抽出根拠の形式が不正です")
        verified_quotes = [_source_quote(quote, source, normalized_source, positions) for quote in quotes]
        if not isinstance(aliases, list) or len(aliases) > 4 or any(
            not isinstance(alias, str) or not alias.strip() or len(alias) > 80 for alias in aliases
        ):
            raise ValueError("人物の別名が不正です")
        result.append({"name": name, "role": role,
                       "aliases": list(dict.fromkeys(alias.strip() for alias in aliases)),
                       "source_quotes": list(dict.fromkeys(verified_quotes))})
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
            raise CharacterValidationError(
                "人物の別名が複数の人物に一致しています",
                category="character_identity",
                repair_hint="複数の人物に一致する別名があります。known_castの正式なnameを再利用し、"
                            "先生・父・母などの共有呼称や、別の人物のnameをaliasesへ入れないでください。",
            )
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
        observed = f"{len(raw)}人" if isinstance(raw, list) else "形式不正"
        raise CharacterValidationError(
            "人物一覧と人物設定の人数が一致しません"
            f"（対象{len(roster)}人／応答{observed}）",
            category="character_count",
            repair_hint=f"charactersの人数がtarget_castと一致しません。指定された{len(roster)}人全員に"
                        "1人につき1設定を作成し、人物を追加・省略・統合しないでください。",
        )
    names = [str(item.get("name") or "").strip() for item in raw]
    expected = [item["name"] for item in roster]
    keys = [_identity_key(name) for name in names]
    expected_keys = [_identity_key(name) for name in expected]
    if len(set(keys)) != len(keys) or set(keys) != set(expected_keys):
        raise CharacterValidationError(
            "人物一覧の誰かが省略または重複しています",
            category="character_identity",
            repair_hint="charactersのnameがtarget_castと一致しないか重複しています。"
                        "target_castのnameをそのままコピーし、全員に1設定ずつ作成してください。",
        )
    if any(not str(item.get("appearance") or "").strip() for item in raw):
        raise ValueError("人物の外見設定がありません")
    lookup = {_identity_key(item["name"]): item for item in normalize_characters(raw)}
    result = []
    for person in roster:
        character = lookup[_identity_key(person["name"])]
        character["name"] = person["name"]
        character["aliases"] = person["aliases"][:16]
        character["source_quotes"] = person["source_quotes"][:16]
        result.append(character)
    return result
