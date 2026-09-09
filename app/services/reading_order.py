"""Projectの言語と漫画の読順を一貫して扱う純粋なルール。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


LANGUAGE_JA = "ja"
LANGUAGE_EN = "en"
ALLOWED_LANGUAGES = {LANGUAGE_JA, LANGUAGE_EN}

READING_RIGHT_TO_LEFT = "right_to_left"
READING_LEFT_TO_RIGHT = "left_to_right"
ALLOWED_READING_DIRECTIONS = {READING_RIGHT_TO_LEFT, READING_LEFT_TO_RIGHT}

LANGUAGE_DIRECTIONS = {
    LANGUAGE_JA: READING_RIGHT_TO_LEFT,
    LANGUAGE_EN: READING_LEFT_TO_RIGHT,
}
LEGACY_DIRECTION_ALIASES = {
    "rtl": READING_RIGHT_TO_LEFT,
    "ltr": READING_LEFT_TO_RIGHT,
    READING_RIGHT_TO_LEFT: READING_RIGHT_TO_LEFT,
    READING_LEFT_TO_RIGHT: READING_LEFT_TO_RIGHT,
}


def normalize_language(value: Any) -> Optional[str]:
    """言語値を検証する。未指定はNoneとして返す。"""

    if value is None or not str(value).strip():
        return None
    language = str(value).strip().lower()
    if language not in ALLOWED_LANGUAGES:
        raise ValueError("languageが不正です。jaまたはenを指定してください")
    return language


def normalize_reading_direction(value: Any) -> Optional[str]:
    """新旧の方向値を内部の意味的な値へ変換する。"""

    if value is None or not str(value).strip():
        return None
    direction = LEGACY_DIRECTION_ALIASES.get(str(value).strip().lower())
    if direction is None:
        raise ValueError("reading_directionが不正です")
    return direction


def infer_language_from_direction(value: Any) -> str:
    """旧Projectの方向値から言語を推定する。"""

    direction = normalize_reading_direction(value)
    if direction == READING_LEFT_TO_RIGHT:
        return LANGUAGE_EN
    return LANGUAGE_JA


def canonicalize_settings(value: Mapping[str, Any] | None, *, strict: bool = True) -> Dict[str, Any]:
    """言語をSource of Truthにして方向を必ず再計算する。

    browserから矛盾した方向が届いても、保存される値は言語に対応した方向だけに
    なる。旧Projectのlanguageなし設定は、旧rtl/ltrから安全に推定する。
    """

    raw: Dict[str, Any] = dict(value) if isinstance(value, Mapping) else {}
    try:
        language = normalize_language(raw.get("language"))
        direction = normalize_reading_direction(raw.get("reading_direction"))
        if language is None:
            language = infer_language_from_direction(direction) if direction else LANGUAGE_JA
        raw["language"] = language
        raw["reading_direction"] = LANGUAGE_DIRECTIONS[language]
        return raw
    except ValueError:
        if strict:
            raise
        return canonicalize_stored_settings(raw)


def canonicalize_stored_settings(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    """壊れた／旧形式の保存値を壊さずに読める設定へ移行する。"""

    raw: Dict[str, Any] = dict(value) if isinstance(value, Mapping) else {}
    # 既存データに明示された有効なlanguageがある場合は、それを方向値より優先する。
    # 方向値が壊れていても、英語Projectを日本語へ意図せず変換しない。
    try:
        explicit_language = normalize_language(raw.get("language"))
    except ValueError:
        explicit_language = None
    if explicit_language is not None:
        raw["language"] = explicit_language
        raw["reading_direction"] = LANGUAGE_DIRECTIONS[explicit_language]
        return raw
    try:
        return canonicalize_settings(raw)
    except ValueError:
        # languageがない／不正な旧Projectは、有効な旧方向から安全に推定する。
        try:
            language = infer_language_from_direction(raw.get("reading_direction"))
        except ValueError:
            language = LANGUAGE_JA
        raw["language"] = language
        raw["reading_direction"] = LANGUAGE_DIRECTIONS[language]
        return raw


def language_name(settings: Mapping[str, Any] | None) -> str:
    """設定から表示用の言語名を返す。"""

    language = canonicalize_stored_settings(settings).get("language")
    return "English" if language == LANGUAGE_EN else "日本語"


def html_direction(settings: Mapping[str, Any] | None) -> str:
    """HTML/CSSのdir属性用の短い値を返す。"""

    return "ltr" if canonicalize_stored_settings(settings).get("language") == LANGUAGE_EN else "rtl"


def reading_order_context(settings: Mapping[str, Any] | None) -> Dict[str, str]:
    """AIやUIへ渡す、サーバー確定済みの読順コンテキスト。"""

    canonical = canonicalize_stored_settings(settings)
    language = str(canonical.get("language", LANGUAGE_JA))
    direction = str(canonical.get("reading_direction", READING_RIGHT_TO_LEFT))
    if language == LANGUAGE_EN:
        return {
            "language": language,
            "language_name": "English",
            "reading_direction": direction,
            "panel_reading_order": "left_to_right",
            "bubble_reading_order": "left_to_right",
            "output_language_instruction": "生成する漫画のセリフ、ナレーション、SFX、レビュー文は原文を尊重しつつ英語を基本にする",
        }
    return {
        "language": language,
        "language_name": "日本語",
        "reading_direction": direction,
        "panel_reading_order": "right_to_left",
        "bubble_reading_order": "right_to_left",
        "output_language_instruction": "生成する漫画のセリフ、ナレーション、SFX、レビュー文は原文を尊重しつつ日本語を基本にする",
    }


def panel_visual_position(index: int, count: int, settings: Mapping[str, Any] | None = None) -> Dict[str, int]:
    """論理読順のコマを明示的なグリッド位置へ対応付ける。

    1コマ／2コマの縦レイアウトでは配列順を上下順として扱い、2列以上の
    グリッドでは言語に応じて左右列だけを入れ替える。
    """

    safe_index = max(0, int(index))
    safe_count = max(1, int(count))
    if safe_count < 3:
        return {"row": safe_index + 1, "column": 1, "column_count": 1}
    language = canonicalize_stored_settings(settings).get("language", LANGUAGE_JA)
    row = safe_index // 2 + 1
    logical_column = safe_index % 2
    column = logical_column + 1 if language == LANGUAGE_EN else 2 - logical_column
    return {"row": row, "column": column, "column_count": 2}


def bubble_side(index: int, settings: Mapping[str, Any] | None = None) -> str:
    """同一コマ内の吹き出しを読順の開始側から配置する。"""

    language = canonicalize_stored_settings(settings).get("language", LANGUAGE_JA)
    first_side = "left" if language == LANGUAGE_EN else "right"
    second_side = "right" if first_side == "left" else "left"
    return first_side if max(0, int(index)) % 2 == 0 else second_side


def canonicalize_storyboard_panel_orders(value: Any) -> List[Dict[str, Any]]:
    """既存Storyboardの配列順を維持し、コマorderだけ1始まりへ移行する。"""

    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    for page in value:
        if not isinstance(page, Mapping):
            continue
        next_page = dict(page)
        panels = page.get("panels", [])
        if isinstance(panels, list):
            next_panels: List[Dict[str, Any]] = []
            for index, panel in enumerate(panels):
                if not isinstance(panel, Mapping):
                    continue
                next_panel = dict(panel)
                next_panel["order"] = index + 1
                for key, source_key in (
                    ("bubble_order", "dialogue"),
                    ("narration_order", "narration"),
                    ("sfx_order", "sfx"),
                ):
                    entries = panel.get(source_key, [])
                    if isinstance(entries, list):
                        next_panel[key] = list(range(1, len(entries) + 1))
                next_panels.append(next_panel)
            next_page["panels"] = next_panels
        result.append(next_page)
    return result


def _saved_layout_position_errors(
    page: Mapping[str, Any],
    panels: List[Any],
    language: str,
) -> Optional[List[int]]:
    """新レイアウトの行内物理列が論理読順と一致するかを返す。

    layout_version 2以降は不均等な行構成を持つため、旧2列グリッドの計算結果とは
    比較しない。論理Panel順の行進行と、各行内のLTR／RTL列順を検証する。
    """

    layout = page.get("layout_geometry")
    if not isinstance(layout, Mapping):
        return None
    try:
        layout_version = int(layout.get("version", 0) or 0)
    except (TypeError, ValueError):
        return None
    if layout_version < 2:
        return None
    groups: List[tuple[int, List[tuple[int, int, int]]]] = []
    errors: List[int] = []
    for index, panel in enumerate(panels):
        if not isinstance(panel, Mapping):
            errors.append(index)
            continue
        position = panel.get("visual_position")
        try:
            row = int(position.get("row", -1)) if isinstance(position, Mapping) else -1
            column = int(position.get("column", -1)) if isinstance(position, Mapping) else -1
            column_count = int(position.get("column_count", -1)) if isinstance(position, Mapping) else -1
        except (TypeError, ValueError):
            errors.append(index)
            continue
        if not groups or groups[-1][0] != row:
            groups.append((row, []))
        groups[-1][1].append((index, column, column_count))

    for expected_row, (actual_row, entries) in enumerate(groups, start=1):
        expected_columns = list(range(1, len(entries) + 1))
        if language == LANGUAGE_JA:
            expected_columns.reverse()
        for (index, column, column_count), expected_column in zip(entries, expected_columns):
            if actual_row != expected_row or column != expected_column or column_count != len(entries):
                errors.append(index)
    return sorted(set(errors))


def reading_order_issues(project: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Project内の方向・コマ順・吹き出し順の不整合を検出する。"""

    issues: List[Dict[str, str]] = []
    raw_settings = project.get("settings") if isinstance(project, Mapping) else {}
    raw_settings = raw_settings if isinstance(raw_settings, Mapping) else {}
    raw_language = raw_settings.get("language")
    raw_direction = raw_settings.get("reading_direction")
    try:
        language = normalize_language(raw_language) or infer_language_from_direction(raw_direction)
        actual_direction = normalize_reading_direction(raw_direction)
    except ValueError:
        issues.append({"key": "language_direction", "label": "言語と読み方向", "detail": "言語または読み方向が不正です。"})
        language = LANGUAGE_JA
        actual_direction = None
    expected_direction = LANGUAGE_DIRECTIONS[language]
    if actual_direction is not None and actual_direction != expected_direction:
        issues.append(
            {
                "key": "language_direction",
                "label": "言語と読み方向",
                "detail": f"{language}の読み方向が{actual_direction}になっています。{expected_direction}へ統一してください。",
            }
        )

    pages = project.get("storyboard") or []
    if not isinstance(pages, list):
        return issues
    canonical_settings = {"language": language}
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        panels = page.get("panels") or []
        if not isinstance(panels, list):
            continue
        expected_orders = list(range(1, len(panels) + 1))
        actual_orders = [panel.get("order") for panel in panels if isinstance(panel, Mapping)]
        if actual_orders != expected_orders:
            issues.append(
                {
                    "key": f"panel-order-{page.get('page_number', '')}",
                    "label": f"ページ{page.get('page_number', '')}のコマ順",
                    "detail": "Panel.orderは実際の読者の読順（1始まり）で連続している必要があります。",
                }
            )
        saved_position_errors = _saved_layout_position_errors(page, panels, language)
        if saved_position_errors:
            issues.append(
                {
                    "key": f"panel-position-{page.get('page_number', '')}",
                    "label": f"ページ{page.get('page_number', '')}のコマ配置",
                    "detail": "視覚上のコマ位置がProjectの言語別読順と一致していません。",
                }
            )
        for index, panel in enumerate(panels):
            if not isinstance(panel, Mapping):
                continue
            if saved_position_errors is None:
                position = panel.get("visual_position")
                expected_position = panel_visual_position(index, len(panels), canonical_settings)
                if isinstance(position, Mapping):
                    try:
                        actual_column = int(position.get("column", -1))
                        actual_row = int(position.get("row", -1))
                    except (TypeError, ValueError):
                        actual_column = actual_row = -1
                    if actual_column != expected_position["column"] or actual_row != expected_position["row"]:
                        issues.append(
                            {
                                "key": f"panel-position-{page.get('page_number', '')}-{index + 1}",
                                "label": f"ページ{page.get('page_number', '')}のコマ配置",
                                "detail": "視覚上のコマ位置がProjectの言語別読順と一致していません。",
                            }
                        )
            for order_key, content_key, label in (
                ("bubble_order", "dialogue", "吹き出し順"),
                ("narration_order", "narration", "ナレーション順"),
                ("sfx_order", "sfx", "SFX順"),
            ):
                entries = panel.get(content_key, [])
                order = panel.get(order_key)
                if isinstance(order, list) and isinstance(entries, list) and order != list(range(1, len(entries) + 1)):
                    issues.append(
                        {
                            "key": f"{order_key}-{page.get('page_number', '')}-{index + 1}",
                            "label": label,
                            "detail": "テキスト要素のorderがコマ内の読順と一致していません。",
                        }
                    )
    return issues
