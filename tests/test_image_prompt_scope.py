"""画像APIへ渡す範囲と、確定済みの描画条件の保持を検証する。"""

import copy

from app.services.ai_pipeline import compose_panel_prompt, image_generation_prompt


def test_image_prompt_keeps_current_scene_and_identity_without_future_story():
    panel = {
        "description": "駅前で鍵を拾う", "action": "鍵を拾う", "characters": ["葵"],
        "event_boundary": {
            "allowed_events": ["駅前で鍵を発見"], "page_end_state": "鍵を持つ",
            "forbidden_until_later": ["未来の出来事の全文"], "carry_over": ["扉を開く"],
        },
    }
    settings = {"rendering_style_id": "STYLE-012"}
    people = [{"name": "葵", "appearance": "黒い髪", "accessories": "赤い眼鏡"}]
    before = copy.deepcopy(panel)
    anchor = compose_panel_prompt(panel, people, settings)
    prompt = image_generation_prompt("駅前の静かなコマ。\nContinuity anchor: " + anchor, panel, people, settings, {"prompt_text": "濃い陰影で描く"})
    assert "駅前で鍵を発見" in prompt and "鍵を持つ" in prompt
    assert "未来の出来事の全文" not in prompt and "扉を開く" not in prompt
    assert "黒い髪" in prompt and "赤い眼鏡" in prompt
    assert "濃い陰影で描く" in prompt
    assert prompt.count("構造化された描画条件") == 1
    assert prompt.count("登場人物:") == 1
    assert panel == before


def test_long_image_prompt_preserves_knowledge_boundary_and_confirmed_layout():
    panel = {"action": "灯台の前に立つ", "characters": []}
    prompt = image_generation_prompt("生成された指示。" * 1_100, panel, [], {}, {"prompt_text": "参照条件" * 1_500})
    assert prompt.count("<knowledge_reference>") == prompt.count("</knowledge_reference>") == 1
    assert "参照条件" * 1_500 in prompt
    assert prompt.index("</knowledge_reference>") < prompt.index("確定済み構図・描画条件")
    assert "灯台の前に立つ" in prompt and "Reserved text safe zones" in prompt


def test_user_prompt_is_preserved_and_demo_conditions_are_not_duplicated():
    panel = {"prompt_source": "user", "characters": []}
    assert "Continuity anchor: 本人が編集した指示" in image_generation_prompt(
        "Continuity anchor: 本人が編集した指示", panel, [], {}, {},
    )
    panel["prompt_source"] = "generated"
    prompt = image_generation_prompt(compose_panel_prompt(panel, [], {}), panel, [], {}, {})
    assert prompt.count("Reserved text safe zones") == 1
