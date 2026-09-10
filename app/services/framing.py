"""人物とショットの意味に応じた、画像生成前の頭部フレーミング。"""


def resolve_head_framing(panel):
    """極端な近景は明示ショット、任意の意図的cropは理由付き指定だけ許す。"""
    shot = str(panel.get('shot_type') or '').lower().strip()
    object_shot = any(value in shot for value in ('object-only', '手元', '小物のみ', '物のみ'))
    visible = bool(panel.get('characters')) and not object_shot
    extreme = shot in {'extreme close-up', 'extreme_close_up', '極端なクローズアップ', '超アップ'}
    reason = str(panel.get('intentional_crop_reason') or '').strip()
    intentional = visible and (extreme or panel.get('intentional_head_crop') is True and bool(reason))
    return {'preserve_entire_head': visible and not intentional,
            'minimum_headroom': 0.08 if visible and not intentional else 0,
            'intentional_crop': intentional,
            'reason': 'explicit extreme close-up' if extreme and visible else reason if intentional else '',
            'close_up_treatment': 'head_and_shoulders_with_headroom' if visible and not intentional else 'preserve_requested_shot'}


def head_framing_prompt(panel):
    """人物なし原画に人物の追加を誘発しない。"""
    framing = resolve_head_framing(panel)
    if framing['intentional_crop']:
        return 'Intentional dramatic crop: preserve the explicitly requested shot; normal headroom is not required. Keep story-critical features and text-safe zones readable. '
    if not framing['preserve_entire_head']:
        return ''
    return ('Framing priority: for a close-up, use a head-and-shoulders portrait, not an extreme facial crop. '
            'Keep the entire hair, hat or surgical-cap silhouette inside head_safe_zone, with 8% background headroom above its highest point. '
            'Zoom out before cutting hair, chin, or an important hand. This overrides a tight close-up interpretation. ')
