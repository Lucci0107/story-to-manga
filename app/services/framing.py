"""人物とショットの意味に応じた、画像生成前の頭部フレーミング。"""

SHOT_RANGES = {'full_body': (.06, .10), 'medium': (.06, .10),
               'medium_close': (.05, .09), 'close_up': (.03, .07),
               'tight_close_up': (0, .04), 'extreme_close_up': (0, 0)}


def canonical_shot_type(value):
    """保存済みの英日表記を限定的に正規化し、不明な種類はmediumへ戻す。"""
    value = str(value or '').lower().strip().replace('-', '_').replace(' ', '_')
    aliases = {'full_body_shot': 'full_body', '全身': 'full_body', 'medium_shot': 'medium',
               'ミディアム': 'medium', 'バストアップ': 'medium_close', 'medium_close_up': 'medium_close',
               'close_shot': 'close_up', '近景': 'close_up', 'クローズアップ': 'close_up',
               '極端なクローズアップ': 'extreme_close_up', '超アップ': 'extreme_close_up'}
    return aliases.get(value, value if value in SHOT_RANGES else 'medium')

def resolve_head_framing(panel):
    """極端な近景は明示ショット、任意の意図的cropは理由付き指定だけ許す。"""
    shot = str(panel.get('shot_type') or '').lower().strip()
    object_shot = any(value in shot for value in ('object-only', '手元', '小物のみ', '物のみ'))
    visible = bool(panel.get('characters')) and not object_shot
    visible = visible and panel.get('head_visible') is not False
    canonical = canonical_shot_type(shot)
    extreme = canonical == 'extreme_close_up'
    reason = str(panel.get('intentional_crop_reason') or '').strip()
    intentional = visible and (extreme or panel.get('intentional_head_crop') is True and bool(reason))
    if canonical == 'tight_close_up' and not intentional:
        canonical = 'close_up'
    minimum, maximum = SHOT_RANGES[canonical] if visible else (0, 0)
    requirements = str(panel.get('action') or '') + str(panel.get('description') or '')
    explicit_hands = panel.get('hands_required')
    explicit_props = panel.get('props_required')
    hands_required = (
        bool(explicit_hands)
        if isinstance(explicit_hands, bool)
        else any(w in requirements for w in ('手', 'hand', '器具'))
    )
    props_required = (
        bool(explicit_props)
        if isinstance(explicit_props, bool)
        else any(w in requirements for w in ('器具', '小物', 'instrument', 'prop'))
    )
    return {'preserve_entire_head': visible and not intentional,
            'shot_type': canonical, 'head_visible': visible,
            'headroom_target_min': minimum, 'headroom_target_max': maximum,
            'minimum_headroom': minimum,
            'body_extent_requirement': 'full_body' if canonical == 'full_body' else 'shoulders_and_required_hands' if canonical in {'close_up', 'medium_close'} else 'preserve_requested_shot',
            'hands_required': hands_required,
            'props_required': props_required,
            'intentional_crop': intentional,
            'reason': 'explicit extreme close-up' if extreme and visible else reason if intentional else '',
            'close_up_treatment': 'head_and_shoulders_with_headroom' if visible and not intentional else 'preserve_requested_shot'}


def head_framing_prompt(panel):
    """人物なし原画に人物の追加を誘発しない。"""
    framing = (panel.get('panel_direction') or {}).get('camera_framing') or resolve_head_framing(panel)
    if framing['intentional_crop']:
        return 'Intentional dramatic crop: preserve the explicitly requested shot; normal headroom is not required. Keep story-critical features and text-safe zones readable. '
    if not framing['preserve_entire_head']:
        return ''
    scale = framing.get('subject_scale_target')
    budget = (f"Required body extent={framing.get('required_body_extent')}; head height must be about {scale:.0%} of image height, not a frame-filling face. " if scale else '')
    extent_instruction = ('follow the planned body extent' if scale and framing.get('required_body_extent') not in {'face_only', 'head_shoulders'}
                          else 'use a head-and-shoulders portrait for close shots')
    if framing.get('composition_mode') == 'wide_multi_element':
        bbox = framing.get('subject_bbox_target') or {}
        headroom_min = float(framing.get('headroom_target_min', .06) or .06)
        headroom_max = float(framing.get('headroom_target_max', .10) or .10)
        return (f"Holistic composition: wide landscape upper-torso medium-wide manga panel. "
                f"Keep the subject inside the planned bbox x={bbox.get('x', 0):.2f}, y={bbox.get('y', 0):.2f}, "
                f"width={bbox.get('width', 0):.2f}, height={bbox.get('height', 0):.2f}; do not fill the vertical frame. "
                f"Keep the entire crown and hair below the top edge with a visible {headroom_min:.0%}-{headroom_max:.0%} "
                f"background band above it. Place the face in the upper-middle, both hands and the important "
                "instrument in the lower-middle, and keep the opposite lateral area quiet for dialogue. "
                "The source canvas is intentionally taller than the final panel and includes a saved inner crop. "
                "Place the crown about 8–12% below the source top, never touching the source edge, and keep the entire "
                "compact upper-torso composition, including both hands and the instrument, above the saved inner crop bottom; "
                "do not treat the source border as the final panel edge. Keep the visible character height to roughly 55–65% "
                "of the source and do not enlarge the face to fill the vertical frame. "
                "This is one coherent upper-torso composition, not separate close-up constraints. "
                "Do not enlarge the face to fill the panel or crop the crown, hands, instrument, or dialogue area. ")
    return (budget + f"Framing priority: shot={framing['shot_type']}; {extent_instruction}, not an extreme facial crop. "
            f"Keep the entire hair, hat or surgical-cap silhouette inside head_safe_zone, with a visually natural {framing['headroom_target_min']:.0%}-{framing['headroom_target_max']:.0%} top margin. "
            'No accidental crown crop, forehead crop, hair-top crop, or face pushed against the image top edge. '
            'Keep the face readable and large; do not shrink the subject excessively. Do not sacrifice important hands or props to create headroom. '
            'Preserve planned dialogue space. Reduce decorative background or unnecessary torso extent before sacrificing face, crown, hands, props or text safety. ')


def headroom_status(panel, observed_margin=None, cropped=False):
    """目視測定された余白を評価。未観測の画像を合格と判定しない。"""
    framing = resolve_head_framing(panel)
    if not framing['head_visible'] or framing['shot_type'] == 'extreme_close_up':
        return 'NOT_APPLICABLE'
    if observed_margin is None:
        return None
    if framing['intentional_crop']:
        return 'TIGHT_BUT_ACCEPTABLE' if observed_margin <= framing['headroom_target_max'] else 'PASS'
    if cropped or observed_margin < .01:
        return 'FAIL_CROP_RISK'
    return 'TIGHT_BUT_ACCEPTABLE' if observed_margin < framing['headroom_target_min'] else 'PASS'
