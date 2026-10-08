"""候補全員の処遇、同一人物の根拠、保存人数を全編照合で検証する。"""

from __future__ import annotations

import pytest

from app.schemas import MAX_CHARACTERS
from app.services.character_cast import CAST_CANDIDATE_LIMIT, CharacterValidationError, merge_cast
from app.services.character_cast_review import cast_review_schema, normalize_cast_review


def candidate(name: str) -> dict:
    return {"name": name, "aliases": [], "role": "原稿にある役割", "source_quotes": [f"{name}が登場した。"],
            "source_parts": [1]}


def test_review_merges_aliases_preserves_evidence_and_accounts_for_incidental_people() -> None:
    candidates = [candidate("葵"), {**candidate("アオイ"), "source_parts": [2]},
                  candidate("相談相手"), candidate("窓口で挨拶した担当者")]
    value = {"groups": [{"name": "葵", "members": ["葵", "アオイ"]},
                        {"name": "相談相手", "members": ["相談相手"]}],
             "excluded": [{"name": "窓口で挨拶した担当者", "reason": "incidental_person"}]}
    result = normalize_cast_review(value, candidates)
    assert [item["name"] for item in result] == ["葵", "相談相手"]
    assert result[0]["aliases"] == ["アオイ"]
    assert result[0]["source_quotes"] == ["葵が登場した。", "アオイが登場した。"]
    assert result[0]["source_parts"] == [1, 2]
    assert candidates[0]["aliases"] == []


@pytest.mark.parametrize("groups,excluded", [
    ([{"name": "葵", "members": ["葵"]}], []),
    ([{"name": "葵", "members": ["葵", "葵"]}], [{"name": "相談相手", "reason": "incidental_person"}]),
    ([{"name": "葵", "members": ["葵"]}], [{"name": "葵", "reason": "incidental_person"}]),
    ([{"name": "葵", "members": ["相談相手"]}], [{"name": "葵", "reason": "incidental_person"}]),
    ([{"name": "葵", "members": ["葵"]}], [{"name": "別人", "reason": "incidental_person"}]),
    ([{"name": "葵", "members": ["葵"]}], [{"name": "相談相手", "reason": "人数を減らすため"}]),
])
def test_review_cannot_silently_drop_duplicate_or_invent_candidates(groups: list, excluded: list) -> None:
    with pytest.raises(CharacterValidationError) as raised:
        normalize_cast_review({"groups": groups, "excluded": excluded}, [candidate("葵"), candidate("相談相手")])
    assert raised.value.error_category == "character_coverage"
    assert "葵" not in str(raised.value) + raised.value.repair_hint


def test_working_candidates_may_exceed_storage_limit_but_fixed_people_may_not() -> None:
    candidates = [candidate(f"人物{i}") for i in range(MAX_CHARACTERS + 1)]
    roster: list[dict] = []
    merge_cast(roster, candidates, limit=CAST_CANDIDATE_LIMIT)
    assert len(roster) == MAX_CHARACTERS + 1
    assert cast_review_schema(roster)["properties"]["groups"]["maxItems"] == len(roster)
    with pytest.raises(CharacterValidationError) as raised:
        normalize_cast_review({"groups": [{"name": item["name"], "members": [item["name"]]} for item in roster],
                               "excluded": []}, roster)
    assert raised.value.error_category == "character_limit"
