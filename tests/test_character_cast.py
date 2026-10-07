"""全編の人物抽出、別名照合、一覧と人物設定の欠落検出。"""

from __future__ import annotations

import pytest

from app.schemas import MAX_CHARACTERS, normalize_characters
from app.services.character_cast import (
    CHARACTER_SOURCE_CHUNK_SIZE,
    character_source_chunks,
    merge_cast,
    normalize_cast,
    normalize_character_batch,
)


def person(name: str, aliases: list[str] | None = None) -> dict:
    return {"name": name, "aliases": aliases or [], "role": "決断を支える人物",
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
