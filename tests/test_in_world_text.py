"""画像生成文字の方針と意図した本文を分離する。外部APIなし。"""
from app.services.in_world_text import resolve_in_world_text, in_world_text_prompt
from app.services.ai_pipeline import compose_panel_prompt
from app.services.composition import composition_quality_issues
from scripts.manga_validation_fixture import validation_pages, SETTINGS
from app.services.panel_direction import direction_fingerprint


def test_default_abstract_only():
    assert resolve_in_world_text({})['policy'] == 'abstract_only'
    assert 'pseudo-Japanese' in in_world_text_prompt({})


def test_exact_exception_requires_story_text_and_controlled_overlay():
    assert resolve_in_world_text({'in_world_text_policy': 'intentional_exact_text'})['policy'] == 'abstract_only'
    panel = {'in_world_text_policy': 'intentional_exact_text', 'in_world_exact_text': '入口'}
    assert resolve_in_world_text(panel)['exact_text'] == '入口'
    assert 'controlled overlay' in in_world_text_prompt(panel)


def test_artwork_prompt_excludes_incidental_text_not_dialogue_overlay():
    panel = validation_pages()[0]['panels'][0]
    assert 'Do not render readable words' in compose_panel_prompt(panel, [], SETTINGS)
    assert panel['text_layout']['items'][0]['text'] == panel['dialogue'][0]


def test_visual_review_flag_only_reports_observed_incidental_text():
    page = validation_pages()[0]
    assert not any('INCIDENTAL_GENERATED_TEXT' in i['key'] for i in composition_quality_issues(page))
    page['panels'][0]['in_world_text_review'] = {'incidental_generated_text': True}
    assert any('INCIDENTAL_GENERATED_TEXT' in i['key'] for i in composition_quality_issues(page))


def test_implicit_default_does_not_invalidate_existing_direction():
    panel = validation_pages()[0]['panels'][0]
    before = direction_fingerprint(panel, SETTINGS)
    panel['in_world_text_policy'] = 'abstract_only'
    assert direction_fingerprint(panel, SETTINGS) == before
    panel['in_world_text_policy'] = 'none'
    assert direction_fingerprint(panel, SETTINGS) != before
