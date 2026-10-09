"""登録人物と匿名の脇役を区別し、一般的な役名を同一人物へ固定しない。"""

from __future__ import annotations

import re
import unicodedata


ANONYMOUS_ROLES = frozenset({
    "患者", "医師", "看護師", "病院職員", "通行人", "店員", "警備員", "観客", "群衆", "乗客",
})


def identity(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def reference_matches(characters: list[dict], name: str) -> list[dict]:
    key = identity(name)
    exact = [item for item in characters if identity(item.get("name", "")) == key]
    if exact:
        return exact
    return [item for item in characters if key in {identity(alias) for alias in item.get("aliases") or []}]


def registered_character(characters: list[dict], name: str) -> dict | None:
    matches = reference_matches(characters, name)
    return matches[0] if len(matches) == 1 else None


def anonymous_characters(panel: dict, characters: list[dict]) -> list[str]:
    """既存の人物設定がある場合は、一般的な役名でもその設定を優先する。"""

    return list(dict.fromkeys(name for name in panel.get("characters") or []
                             if identity(name) in ANONYMOUS_ROLES and not reference_matches(characters, name)))


def unresolved_characters(panel: dict, characters: list[dict]) -> list[str]:
    anonymous = set(anonymous_characters(panel, characters))
    return [name for name in panel.get("characters") or []
            if name not in anonymous and registered_character(characters, name) is None]
