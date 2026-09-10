"""通常の頭上余白と意図的cropの例外を、課金なしで検証する。"""
from app.services.framing import resolve_head_framing, head_framing_prompt
from app.services.panel_direction import plan_panel_direction
from app.services.artwork_geometry import generation_canvas_zones


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
