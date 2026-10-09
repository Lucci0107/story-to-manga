"""主要人物の同一性を保ち、設定のない脇役も追加のAI処理なしで扱う。"""

from __future__ import annotations

import re
import unicodedata


FAMILY_ROLES = frozenset({
    "父", "父親", "母", "母親", "兄", "姉", "弟", "妹", "祖父", "祖母",
    "夫", "妻", "息子", "娘", "長男", "次男", "長女", "次女",
})
ANONYMOUS_ROLES = FAMILY_ROLES | frozenset({
    "患者", "医師", "看護師", "病院職員", "通行人", "店員", "警備員", "観客", "群衆", "乗客",
    "先生", "教師", "学生", "同級生", "友人", "同僚", "上司", "救急隊員", "運転手", "受付", "職員",
})
LIFE_STAGE_SUFFIX = re.compile(
    r"^(.+?)\((幼少期|幼年期|幼児期|少年期|少女期|児童期|青年期|壮年期|老年期|成人|"
    r"幼少時|幼少時代|小学生(?:時代)?|中学生(?:時代)?|高校生(?:時代)?|大学生(?:時代)?|"
    r"学生時代|研修医時代|[0-9]{1,3}歳(?:頃|時)?)\)$"
)
RENDERING_NOTE_SUFFIX = re.compile(
    r"^(.+?)\((声のみ|回想の声のみ|回想の引用のみ|引用音声のみ|手元のみ|手の一部のみ|"
    r"前腕のみ|処置部周辺のみ|覆われた体の一部|個人識別しない遠景|"
    r"匿名・外見未設定|匿名・身体の断片のみ)\)$"
)


def identity(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def character_life_stage(name: str) -> str:
    key = identity(name)
    note = RENDERING_NOTE_SUFFIX.fullmatch(key)
    if note:
        return character_life_stage(note.group(1))
    match = LIFE_STAGE_SUFFIX.fullmatch(key)
    return match.group(2) if match else ""


def reference_matches(characters: list[dict], name: str, *, allow_family_role: bool = True) -> list[dict]:
    key = identity(name)
    exact = [item for item in characters if identity(item.get("name", "")) == key]
    if exact:
        return exact
    aliases = [item for item in characters if key in {identity(alias) for alias in item.get("aliases") or []}]
    if aliases:
        return aliases
    note = RENDERING_NOTE_SUFFIX.fullmatch(key)
    if note:
        return reference_matches(characters, note.group(1), allow_family_role=allow_family_role)
    stage = LIFE_STAGE_SUFFIX.fullmatch(key)
    if stage:
        return reference_matches(characters, stage.group(1), allow_family_role=allow_family_role)
    if allow_family_role and key in FAMILY_ROLES:
        return [item for item in characters if identity(item.get("name", "")).endswith("の" + key)]
    return []


def registered_character(characters: list[dict], name: str) -> dict | None:
    matches = reference_matches(characters, name)
    return matches[0] if len(matches) == 1 else None


def character_reference(name: str, characters: list[dict], cast: list[dict] | None = None) -> dict:
    """完全一致・別名を優先し、曖昧な同名人物を勝手に統合しない。"""

    matches = reference_matches(characters, name)
    candidate_matches = reference_matches(cast or [], name)
    explicit_matches = reference_matches(characters, name, allow_family_role=False)
    if len(candidate_matches) > 1 and len(matches) <= 1 and not explicit_matches:
        matches = []
    if not matches and len(candidate_matches) == 1:
        matches = reference_matches(characters, candidate_matches[0]["name"])
    kind = "supporting"
    if len(matches) == 1:
        kind = "registered"
    elif len(matches) > 1:
        kind = "ambiguous"
    elif len(candidate_matches) == 1:
        kind = "missing_profile" if candidate_matches[0].get("requires_profile") else "supporting"
    elif identity(name) in ANONYMOUS_ROLES or (
        LIFE_STAGE_SUFFIX.fullmatch(identity(name))
        and LIFE_STAGE_SUFFIX.fullmatch(identity(name)).group(1) in ANONYMOUS_ROLES
    ):
        kind = "anonymous"
    candidate = candidate_matches[0] if len(candidate_matches) == 1 else {"name": name, "role": ""}
    return {"name": name, "kind": kind, "life_stage": character_life_stage(name),
            "character": matches[0] if kind == "registered" else None,
            "reference_source": "source_cast" if len(candidate_matches) == 1 else "storyboard",
            "candidate": candidate if kind == "supporting" else None}


def anonymous_characters(panel: dict, characters: list[dict], cast: list[dict] | None = None) -> list[str]:
    return list(dict.fromkeys(name for name in panel.get("characters") or []
                             if character_reference(name, characters, cast)["kind"] == "anonymous"))


def unresolved_characters(panel: dict, characters: list[dict], cast: list[dict] | None = None) -> list[str]:
    return [name for name in panel.get("characters") or []
            if character_reference(name, characters, cast)["kind"] in {"missing_profile", "ambiguous"}]


def scene_character(reference: dict) -> dict:
    """保存済み設定を変更せず、年齢違いには成人の容貌・服装を押し付けない。"""

    person = reference.get("character")
    stage = reference["life_stage"]
    if person:
        if not stage or character_life_stage(person["name"]) == stage:
            return person
        return {"id": person.get("id"), "name": person["name"], "aliases": person.get("aliases", []),
                "life_stage": stage, "role": f"登録済み人物の{stage}。このコマの年代と原稿の描写を優先する。"}
    candidate = reference["candidate"]
    return {"name": reference["name"], "aliases": [], "supporting_role": True,
            "source_name": candidate["name"], "role": candidate.get("role", ""),
            "reference_source": reference["reference_source"]}
