"""全編の人物抽出、別名照合、一覧と人物設定の欠落検出。"""

from __future__ import annotations

import pytest

from app.schemas import MAX_CHARACTERS, normalize_characters
from app.services.character_cast import (
    CHARACTER_SOURCE_CHUNK_SIZE,
    CharacterValidationError,
    character_source_chunks,
    merge_cast,
    normalize_cast,
    normalize_character_batch,
)


def person(name: str, aliases: list[str] | None = None) -> dict:
    return {"name": name, "participation": "story_actor", "aliases": aliases or [], "role": "決断を支える人物",
            "source_quotes": [f"{name}が主人公を支えた。"]}


def test_long_source_chunks_preserve_every_character_and_remove_bibliography() -> None:
    body = "\n\n".join(f"## 第{i}章\n" + "出来事を詳しく振り返る。" * 200
                       + f"\n途中の人物{i}が登場する。\n" for i in range(1, 32))
    source = body + "\n\n## 参考文献\n著者名は登場人物ではない。"
    chunks = character_source_chunks(source)
    assert len(chunks) > 2
    assert all(len(chunk) <= CHARACTER_SOURCE_CHUNK_SIZE for chunk in chunks)
    assert "".join(chunks) == body.strip()
    assert all(f"途中の人物{i}" in "".join(chunks) for i in range(1, 32))
    assert "著者名" not in "".join(chunks)


def test_cast_requires_real_source_quotes() -> None:
    item = person("葵")
    assert normalize_cast({"cast": [item]}, "葵が主人公を支えた。") == [item]
    with pytest.raises(ValueError, match="原稿に存在しません"):
        normalize_cast({"cast": [item]}, "別の場面だけを記した。")
    assert normalize_cast({"cast": []}, "風景だけを描く。") == []


def test_references_and_generic_people_do_not_inflate_the_story_cast() -> None:
    actors = [person("葵"), person("千尋")]
    references = [{**person(f"引用著者{i}"), "participation": "reference_only"} for i in range(40)]
    other = [{**person("一般的な医師"), "participation": "generic_or_hypothetical"},
             {**person("学会の参加者"), "participation": "background_group"}]
    source = "\n".join(item["source_quotes"][0] for item in actors + references + other)
    roster: list[dict] = []
    for candidates in (references[:20] + actors, references[20:] + other):
        merge_cast(roster, normalize_cast({"cast": candidates}, source))
    assert [item["name"] for item in roster] == ["葵", "千尋"]


def test_source_quotes_preserve_original_after_typographic_normalization() -> None:
    source = "Ｍ教授は、\n　葵の決断を支えた。\nThe teacher helped 葵."
    item = person("M教授")
    item["source_quotes"] = ["M教授は、葵の決断を支えた。", "The\n  teacher helped 葵."]
    normalized = normalize_cast({"cast": [item]}, source)
    assert normalized[0]["source_quotes"] == ["Ｍ教授は、\n　葵の決断を支えた。", "The teacher helped 葵."]
    assert all(quote in source for quote in normalized[0]["source_quotes"])
    for quote in ["M教授は、葵を支えた。", "M教授は、…決断を支えた。", "Theteacher helped 葵."]:
        item["source_quotes"] = [quote]
        with pytest.raises(ValueError, match="原稿に存在しません"):
            normalize_cast({"cast": [item]}, source)


def test_profile_names_accept_typographic_changes_and_restore_canonical_name() -> None:
    roster = [person("M教授"), person("葵")]
    result = normalize_character_batch({"characters": [
        {"name": "Ｍ 教授", "appearance": "未設定"}, {"name": "葵", "appearance": "未設定"},
    ]}, roster)
    assert [item["name"] for item in result] == ["M教授", "葵"]


def test_source_quote_cannot_match_only_part_of_a_normalized_character() -> None:
    item = person("葵")
    item["source_quotes"] = ["I"]
    with pytest.raises(ValueError, match="原稿に存在しません"):
        normalize_cast({"cast": [item]}, "Ⅳ")


def test_aliases_merge_one_person_but_shared_generic_aliases_do_not_merge_people() -> None:
    roster: list[dict] = []
    merge_cast(roster, [person("葵", ["アオイ"]), person("千尋", ["先生"])])
    merge_cast(roster, [person("アオイ"), person("玲", ["先生"])])
    assert [item["name"] for item in roster] == ["葵", "千尋", "玲"]
    assert "アオイが主人公を支えた。" in roster[0]["source_quotes"]


def test_cast_over_storage_limit_is_rejected_without_silently_discarding_people() -> None:
    roster: list[dict] = []
    with pytest.raises(ValueError, match="64人"):
        merge_cast(roster, [person(f"人物{i}") for i in range(MAX_CHARACTERS + 1)])
    assert len(roster) == MAX_CHARACTERS


def test_every_roster_member_gets_exactly_one_profile() -> None:
    roster = [person(f"人物{i}") for i in range(1, 15)]
    raw = {"characters": [{"name": item["name"], "appearance": "外見は未設定"}
                          for item in reversed(roster)]}
    result = normalize_character_batch(raw, roster)
    assert len(result) == 14
    assert [item["name"] for item in result] == [item["name"] for item in roster]
    assert result[-1]["source_quotes"] == roster[-1]["source_quotes"]
    assert normalize_characters(result)[-1]["source_quotes"] == roster[-1]["source_quotes"]
    with pytest.raises(ValueError, match="人数"):
        normalize_character_batch({"characters": raw["characters"][:4]}, roster)
    raw["characters"][-1]["name"] = raw["characters"][0]["name"]
    with pytest.raises(ValueError, match="省略または重複"):
        normalize_character_batch(raw, roster)


@pytest.mark.parametrize("appearance", ["", " \n\t", None])
def test_unknown_appearance_keeps_the_person_without_inventing_attributes(appearance: str | None) -> None:
    roster = [person("氏名不明の協力者")]
    result = normalize_character_batch({"characters": [{
        "name": roster[0]["name"], "appearance": appearance, "age_range": "", "clothing": "",
    }]}, roster)
    assert len(result) == 1
    assert result[0]["appearance"].startswith("未設定")
    assert "確認" in result[0]["appearance"]
    assert result[0]["age_range"] == result[0]["clothing"] == ""
    assert result[0]["source_quotes"] == roster[0]["source_quotes"]


def test_profile_name_whitespace_does_not_truncate_the_canonical_name() -> None:
    name = "名" * 80
    result = normalize_character_batch({"characters": [{"name": f"  {name}  ", "appearance": "未設定"}]},
                                       [person(name)])
    assert result[0]["name"] == name


@pytest.mark.parametrize("field,value,category", [
    ("name", "", "character_name"),
    ("name", "長" * 81, "character_name"),
    ("role", "", "character_role"),
    ("participation", None, "character_participation"),
    ("participation", "unknown", "character_participation"),
    ("aliases", [""], "character_aliases"),
    ("source_quotes", [], "character_evidence"),
])
def test_cast_validation_has_safe_field_specific_repair_hints(field: str, value: object, category: str) -> None:
    item = person("非公開の人物名")
    item[field] = value
    with pytest.raises(CharacterValidationError) as raised:
        normalize_cast({"cast": [item]}, "非公開の人物名が主人公を支えた。")
    assert raised.value.error_category == category
    assert field in raised.value.repair_hint
    assert "非公開の人物名" not in str(raised.value) + raised.value.repair_hint
