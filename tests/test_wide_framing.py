"""横長の身体範囲判定は生成前だけに適用し、原画を操作しない。"""
from copy import deepcopy
import json
import pytest
from app.services.panel_direction import plan_panel_direction, direction_is_ready
from app.services.ai_pipeline import compose_panel_prompt
from scripts.manga_validation_fixture import SETTINGS, validation_pages


def planned(**overrides):
    panel = deepcopy(validation_pages()[2]['panels'][2])
    panel.update(shot_type='close_up', hands_required=True, props_required=True)
    panel.update(overrides)
    panel['panel_direction'] = plan_panel_direction(panel, SETTINGS)
    return panel


def test_wide_required_content_relaxes_close_shot():
    panel = planned()
    d = panel['panel_direction']
    f = d['composition_feasibility']
    assert f['requested_feasibility'] == 'fail'
    assert f['requested_shot_type'] == 'close_up'
    assert f['effective_shot_type'] == 'medium'
    assert f['shot_adjustment_reason']
    assert f['subject_scale_target'] < .60
    assert .38 <= d['camera_framing']['face_center_y'] <= .50
    assert d['camera_framing']['hands_required'] and d['camera_framing']['props_required']
    assert d['reserved_text_zones'] and d['important_hand_zone'] and d['important_prop_zone']
    assert direction_is_ready(panel, SETTINGS)
    assert all(f['scores'].values())
    assert panel['shot_type'] == 'close_up'


def test_prompt_uses_effective_saved_shot():
    panel = planned()
    saved = json.loads(json.dumps(panel))
    prompt = compose_panel_prompt(saved, [], SETTINGS)
    assert 'ショット: medium' in prompt
    assert 'head height must be about 30%' in prompt
    assert saved == panel


@pytest.mark.parametrize('shot', ['extreme_close_up', 'tight_close_up'])
def test_intentional_crop_untouched(shot):
    panel = planned(shot_type=shot, intentional_head_crop=True, intentional_crop_reason='演出')
    f = panel['panel_direction']['composition_feasibility']
    assert f['effective_shot_type'] == shot
    assert not f['applicable']


def test_face_only_close_shot_allowed():
    panel = planned(hands_required=False, props_required=False, action='顔を見せる', description='人物の顔')
    f = panel['panel_direction']['composition_feasibility']
    assert f['effective_shot_type'] == 'close_up'
    assert f['requested_feasibility'] == 'pass'


def test_planner_does_not_mutate_existing_artwork_or_story():
    panel = planned(image_url='/media/existing.png')
    before = deepcopy(panel)
    plan_panel_direction(panel, SETTINGS)
    assert panel == before


def test_excessive_text_still_blocks_generation():
    panel = planned(dialogue=['非常に長いセリフです。' * 100])
    assert not direction_is_ready(panel, SETTINGS)


def test_database_roundtrip_preserves_effective_composition(tmp_path, monkeypatch):
    from app import db
    from app.services.layout import reflow_page, ensure_page_layout
    monkeypatch.setenv('STORY_MANGA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATABASE_URL', '')
    db.init_db()
    user = db.create_user('framing-test@example.com', 'local-test-password')
    project = db.create_project(user['id'], '無課金構図検証', '架空の検証')
    page = validation_pages()[2]
    page['panels'][2].update(shot_type='close_up', hands_required=True, props_required=True)
    page = reflow_page(page, SETTINGS)
    saved = db.update_project(project['id'], user['id'], settings=SETTINGS, storyboard=[page])
    loaded = db.get_project(project['id'], user['id'])
    assert loaded['storyboard'] == saved['storyboard']
    panel = loaded['storyboard'][0]['panels'][2]
    assert panel['panel_direction']['camera_framing']['effective_shot_type'] == 'medium'
    assert direction_is_ready(panel, loaded['settings'])
    frozen = deepcopy(loaded['storyboard'][0])
    frozen['panels'][2]['image_url'] = '/media/test/nonexistent.png'
    assert ensure_page_layout(frozen, loaded['settings']) == frozen
