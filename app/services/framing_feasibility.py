"""生成前だけで使う身体範囲の予算。実画像の検出・合格判定ではない。"""

from .framing import SHOT_RANGES

# 画像高に対する頭部高。手・器具まで必要な構図では身体範囲も確保する。
HEAD_SCALE = {'full_body': .18, 'medium': .30, 'medium_close': .38,
              'close_up': .60, 'tight_close_up': .72, 'extreme_close_up': .85}
BODY_HEAD_UNITS = {'face_only': 1, 'head_shoulders': 1.35, 'upper_torso': 1.65,
                   'chest_hands': 2.05, 'torso_hands': 2.35, 'full_body': 4.5}


def resolve_feasibility(framing, ratio, character_count, zones, character_zone, required_extent=None):
    """横長の身体範囲を評価し、必要な場合のみカメラを引く。"""
    requested = framing['shot_type']
    hands, props = framing['hands_required'], framing['props_required']
    extent = ('chest_hands' if hands and props else 'upper_torso' if hands or props
              else 'face_only' if requested == 'close_up' else 'head_shoulders')
    if requested == 'full_body':
        extent = 'full_body'
    if required_extent in BODY_HEAD_UNITS and BODY_HEAD_UNITS[required_extent] > BODY_HEAD_UNITS[extent]:
        extent = required_extent
    text_area = sum(z['width'] * z['height'] for z in zones)
    applicable = framing['preserve_entire_head'] and ratio >= 1.6
    available_height = .94 - framing['headroom_target_max']
    # 頭幅を高さの約72%と見積もり、人物領域の横幅も上限にする。
    horizontal_limit = character_zone['width'] * ratio / (.72 * max(1, character_count))
    scale_max = min(available_height / BODY_HEAD_UNITS[extent], horizontal_limit)
    initial = HEAD_SCALE[requested]
    effective = requested
    # 横長で頭・手・器具を同時に見せるclose-upは、モデルが顔を過大化しやすい。
    # 文字領域もある場合を含め、mediumへ引いてから生成前に構図を確定する。
    wide_multi_requirement = applicable and ratio >= 1.6 and hands and props
    if wide_multi_requirement:
        effective = 'medium'
    elif applicable and initial > scale_max:
        for candidate in ('medium_close', 'medium'):
            if HEAD_SCALE[candidate] <= min(scale_max, initial):
                effective = candidate
                break
    target = HEAD_SCALE[effective]
    failed = applicable and (target > scale_max or target < .18 or text_area > .50)
    status = 'fail' if failed else 'tight' if applicable and target > scale_max * .9 else 'pass'
    reason = ('横長コマで頭部・必要な手/小物・文字領域を保持するため、close-upからmediumへ身体範囲を広げます。'
              if wide_multi_requirement else
              '横長コマで頭部・必要な手/小物・文字領域を保持するため身体範囲を広げます。'
              if effective != requested else '')
    return {'requested_shot_type': requested, 'effective_shot_type': effective,
            'shot_adjustment_reason': reason, 'required_body_extent': extent,
            'body_head_units': BODY_HEAD_UNITS[extent],
            'subject_scale_unit': 'head_height/image_height',
            'subject_scale_max': round(scale_max, 4), 'subject_scale_target': target,
            'requested_feasibility': 'fail' if applicable and initial > scale_max else 'pass',
            'composition_feasibility': status, 'text_area_ratio': text_area,
            'applicable': applicable, 'aspect_ratio': ratio,
            'wide_multi_requirement': wide_multi_requirement,
            'subject_zone_y': .16 if wide_multi_requirement else .06,
            'head_zone_top_target': .22 if wide_multi_requirement else SHOT_RANGES[effective][0],
            'headroom_target_min': SHOT_RANGES[effective][0],
            'headroom_target_max': SHOT_RANGES[effective][1]}
