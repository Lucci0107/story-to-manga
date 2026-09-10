"""通常の頭上余白と意図的cropの例外を、課金なしで検証する。"""
from app.services.framing import resolve_head_framing, head_framing_prompt
from app.services.panel_direction import plan_panel_direction
from app.services.artwork_geometry import generation_canvas_zones
from app.services.framing import headroom_status


def character(shot='medium shot'):
    return {'characters': ['A'], 'shot_type': shot, 'dialogue': ['待とう。'],
            'geometry': {'x': .04, 'y': .04, 'width': .9, 'height': .4}}


def test_normal_headroom_and_text_zones_are_compatible():
    panel = character()
    direction = plan_panel_direction(panel, {})
    head = direction['head_safe_zone']
    assert .05 <= direction['camera_framing']['minimum_headroom'] <= .12
    assert direction['status'] == 'ready'
    assert all(z['x'] >= head['x'] + head['width'] for z in direction['reserved_text_zones'])


def test_extreme_close_up_is_explicit_crop_exception():
    panel = character('extreme close-up')
    assert resolve_head_framing(panel)['intentional_crop']
    assert plan_panel_direction(panel, {})['head_safe_zone'] is None
    assert 'normal headroom is not required' in head_framing_prompt(panel)


def test_arbitrary_crop_requires_reason():
    panel = {**character(), 'intentional_head_crop': True}
    assert resolve_head_framing(panel)['preserve_entire_head']
    panel['intentional_crop_reason'] = '目の反応を見せる'
    assert not resolve_head_framing(panel)['preserve_entire_head']


def test_object_only_has_no_head_requirement():
    for panel in ({**character(), 'characters': []}, character('手元の寄り')):
        assert head_framing_prompt(panel) == ''
        assert plan_panel_direction(panel, {})['head_safe_zone'] is None


def test_generation_crop_preserves_headroom_coordinates():
    panel = character()
    panel['panel_direction'] = plan_panel_direction(panel, {})
    mapping = generation_canvas_zones(panel)
    crop = mapping['final_crop_window']
    head = mapping['regions']['head_safe_zone']
    assert abs((head['y'] - crop['y']) / crop['height'] - .08) < .0001


def test_shot_ranges_and_intentional_tight_exception():
    medium = resolve_head_framing(character('medium'))
    close = resolve_head_framing(character('close_up'))
    assert (medium['headroom_target_min'], medium['headroom_target_max']) == (.06, .10)
    assert (close['headroom_target_min'], close['headroom_target_max']) == (.03, .07)
    tight = {**character('tight_close_up'), 'intentional_head_crop': True, 'intentional_crop_reason': '表情の強調'}
    assert resolve_head_framing(tight)['headroom_target_max'] == .04
    assert resolve_head_framing(character('tight_close_up'))['shot_type'] == 'close_up'


def test_metric_requires_observation_and_allows_natural_tight_margin():
    assert headroom_status(character()) is None
    assert headroom_status(character(), .08) == 'PASS'
    assert headroom_status(character('close_up'), .02) == 'TIGHT_BUT_ACCEPTABLE'
    assert headroom_status(character(), .005) == 'FAIL_CROP_RISK'
    assert headroom_status(character(), .08, cropped=True) == 'FAIL_CROP_RISK'
    assert headroom_status({**character(), 'head_visible': False}) == 'NOT_APPLICABLE'


def test_hands_props_and_text_metadata_reach_direction():
    panel = {**character('medium_close'), 'action': '手で器具を持つ'}
    direction = plan_panel_direction(panel, {})
    framing = direction['camera_framing']
    assert framing['hands_required'] and framing['props_required']
    assert framing['top_safe_zone']['height'] == .07
    assert framing['text_reserved_zones'] == direction['reserved_text_zones']
    assert direction['status'] == 'ready'
