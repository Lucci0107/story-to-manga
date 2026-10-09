"""別年代・未選択の脇役を扱い、別人の統合や成人設定の流用を防ぐ。"""

from copy import deepcopy
import json

import pytest

from app.services.ai_pipeline import compose_panel_prompt, OpenAIProvider
from app.services.character_references import character_reference, registered_character, unresolved_characters
from app.services.character_proposal import (
    build_character_proposal, panel_characters, protected_character_ids, source_cast,
)


@pytest.mark.parametrize("name", ["葵（幼少期）", "あおい(小学生時代)", "葵（７歳）"])
def test_life_stage_reuses_identity_without_adult_appearance(name):
    person = {"id": "main", "name": "葵", "aliases": ["あおい"], "appearance": "成人の固有容貌",
              "clothing": "大人用の制服", "reference_image_url": "/media/adult.png"}
    project = {"characters": [person]}
    before = deepcopy(project)
    panel = {"characters": [name], "description": "子供の手だけが見える。"}
    assert registered_character([person], name) is person
    assert unresolved_characters(panel, [person]) == []
    scene = panel_characters(project, panel)
    assert scene[0]["id"] == "main" and "reference_image_url" not in scene[0]
    prompt = compose_panel_prompt(panel, scene, {})
    assert "登録済み「葵」と同一人物" in prompt
    assert "成人の固有容貌" not in prompt and "大人用の制服" not in prompt
    assert project == before
    assert protected_character_ids({**project, "storyboard": [{"panels": [panel]}]}) == {"main"}


def test_dedicated_child_profile_and_exact_name_take_priority():
    adult = {"name": "葵", "aliases": ["あおい"]}
    child = {"name": "葵（幼少期）", "appearance": "子供の資料から定義済みの容貌"}
    assert registered_character([adult, child], "葵（幼少期）") is child
    assert "子供の資料から定義済み" in compose_panel_prompt({"characters": [child["name"]]}, [adult, child], {})
    # 親・役職の注記は年代ではなく、別人を示す場合がある。
    assert registered_character([adult], "葵（父）") is None
    assert registered_character([adult], "葵の子") is None


def test_ambiguous_alias_and_family_role_are_not_merged():
    people = [{"name": "葵の兄", "aliases": ["先輩"]}, {"name": "凛の兄", "aliases": ["先輩"]}]
    assert character_reference("兄", people)["kind"] == "ambiguous"
    assert character_reference("兄", people, people)["kind"] == "ambiguous"
    # 複数の原稿候補が同じ続柄でも、未選択の脇役に登録は要求しない。
    reference = character_reference("兄", people[:1], people)
    assert reference["kind"] == "anonymous" and reference["character"] is None
    assert character_reference("先輩（青年期）", people)["kind"] == "ambiguous"
    assert character_reference("兄", [])["kind"] == "anonymous"
    assert character_reference("弟", [], [{"name": "葵の弟", "aliases": [], "role": "弟"}])["kind"] == "supporting"


def test_supporting_cast_is_source_backed_and_invalidated_after_story_edit():
    project = {"original_text": "律は葵を手伝った。", "analysis": {}, "characters": []}
    proposal = build_character_proposal([{"name": "律", "aliases": ["りつ"], "role": "葵を手伝う脇役",
                                         "source_quotes": [project["original_text"]]}], project)
    proposal["selected_candidate_ids"] = []
    project["character_proposal"] = proposal
    panel = {"characters": ["りつ"]}
    assert unresolved_characters(panel, [], source_cast(project)) == []
    scene = panel_characters(project, panel)
    assert len(scene) == 1 and scene[0]["supporting_role"]
    assert "原稿に登場する脇役「律」" in compose_panel_prompt(panel, scene, {})
    assert project["characters"] == []
    project["original_text"] = "葵だけが町を歩く。"
    assert source_cast(project) == []
    assert unresolved_characters(panel, [], source_cast(project)) == []
    scene = panel_characters(project, panel)
    assert scene[0]["reference_source"] == "storyboard" and scene[0]["role"] == ""
    assert "ネームに登場する脇役" in compose_panel_prompt(panel, scene, {})
    assert "原稿に登場する脇役" not in compose_panel_prompt(panel, scene, {})


@pytest.mark.parametrize("name", ["あおい（声のみ）", "葵（幼少期）（手元のみ）"])
def test_presentation_notes_preserve_registered_identity(name):
    person = {"id": "main", "name": "葵", "aliases": ["あおい"], "appearance": "成人の固有容貌"}
    panel = {"characters": [name]}
    assert registered_character([person], name) is person
    assert unresolved_characters(panel, [person]) == []
    scene = panel_characters({"characters": [person]}, panel)
    if "幼少期" in name:
        assert scene[0]["life_stage"] == "幼少期" and "appearance" not in scene[0]
    # 病院・役職による人物区別は削らない。
    assert registered_character([person], "葵（執刀医）") is None


def test_only_confirmed_selected_main_character_requires_a_profile():
    project = {"original_text": "葵と律が歩く。", "analysis": {}, "characters": []}
    roster = [{"name": name, "source_quotes": [project["original_text"]], "importance": 5}
              for name in ("葵", "律")]
    proposal = build_character_proposal(roster, project)
    proposal["selected_candidate_ids"] = [proposal["candidates"][0]["id"]]
    project["character_proposal"] = proposal
    assert character_reference("葵", [], source_cast(project))["kind"] == "supporting"
    proposal["confirmed_at"] = "2026-10-09T00:00:00+00:00"
    assert character_reference("葵", [], source_cast(project))["kind"] == "missing_profile"
    assert character_reference("律", [], source_cast(project))["kind"] == "supporting"


def test_external_prompt_uses_scene_profiles_for_age_variants_and_aliases(monkeypatch):
    provider = OpenAIProvider()
    captured = {}
    def capture(system, user, **kwargs):
        captured.update(json.loads(user))
        return "架空の検証用コマのイラストを描くための指示。"
    monkeypatch.setattr(provider, "_validated_call", capture)
    people = [{"id": "main", "name": "葵", "aliases": ["あおい"], "appearance": "成人の固有容貌"}]
    prompt = provider.panel_prompt({"characters": ["あおい（幼少期）"]}, people, {})
    assert captured["characters"][0]["name"] == "葵"
    assert captured["characters"][0]["life_stage"] == "幼少期"
    assert "成人の固有容貌" not in json.dumps(captured, ensure_ascii=False)
    assert "同一人物の幼少期" in prompt
