"""画像・文字・ページが共有する漫画演出規則。本文は命令として扱わない。"""

from copy import deepcopy
from typing import Any, Mapping

STYLE_VERSION = 2
STYLES = {
    "dynamic": ("躍動", "動きのある少年漫画風の演出", 1.0, "#273b65"),
    "elegant": ("繊細", "繊細で余白のある少女漫画風の演出", 0.5, "#925575"),
    "cinematic": ("映画的", "描線のある漫画イラスト。映画的な陰影と画面構成を漫画の線画と塗りで表現し、実写写真にはしない", 0.35, "#303944"),
    "comedy": ("コメディ", "表情豊かでテンポのよいコメディ演出", 1.0, "#ad376d"),
    "minimal": ("ミニマル", "線と余白を活かしたミニマルな演出", 0.15, "#333333"),
    "webtoon": ("縦読み", "縦読みを意識した明快なコマ構成", 0.7, "#36537b"),
}
DIALOGUE_TYPES = {"normal", "thought", "shout", "whisper", "weak", "comedic_reaction", "announcement"}


def resolve_visual_style(settings: Mapping[str, Any]) -> dict:
    """保存値を一度だけ解決し、未知の旧値は映画的スタイルへ戻す。"""
    requested = str(settings.get("visual_style") or "cinematic")
    style_id = requested if requested in STYLES else "cinematic"
    name, tone, energy, accent = STYLES[style_id]
    return {"requested_visual_style": requested, "resolved_style_id": style_id,
            "resolved_style_version": STYLE_VERSION, "display_name": name,
            "artwork_tone": tone, "composition_energy": energy,
            "accent": accent if settings.get("color_mode") == "color" else "#222222",
            "panel_border": 2 if energy < 0.7 else 3,
            "gutter_width": {"dynamic": 0.010, "elegant": 0.020, "minimal": 0.018, "webtoon": 0.022}.get(style_id, 0.014),
            "narration_fill": "#fffdf8" if settings.get("color_mode") == "color" and style_id == "elegant" else "#ffffff"}


def dialogue_type(text: str, explicit: str = "") -> str:
    """明示分類を優先する。句読点だけで叫びや心の声を捏造しない。"""
    if explicit in DIALOGUE_TYPES:
        return explicit
    for prefix, kind in (("（心の声）", "thought"), ("心の声：", "thought"),
                         ("（叫び）", "shout"), ("（小声）", "whisper"),
                         ("（弱々しく）", "weak"), ("（アナウンス）", "announcement")):
        if text.startswith(prefix):
            return kind
    return "normal"


def sfx_type(text: str, explicit: str = "") -> str:
    """効果音を意味別に分類し、不明な音を衝撃音にしない。"""
    groups = {
        "footstep": ("テク", "コツコツ", "トコトコ", "tap tap"),
        "impact": ("ドカ", "バキ", "ドン", "ガン", "bang", "crash"),
        "stop": ("ピタ", "キキ", "screech"),
        "ambient": ("シーン", "ざわ", "サー", "しーん", "hum"),
        "mechanical": ("ウィーン", "カタカタ", "beep"),
        "heartbeat": ("ドキ", "トクン"), "door": ("ガチャ", "バタン", "creak"),
        "rustle": ("カサ", "サラ", "rustle"),
        "comedic_reaction": ("ガーン", "ズコ", "ぽかーん"),
    }
    if explicit in {*groups, "other"}:
        return explicit
    return next((kind for kind, words in groups.items() if any(word.lower() in text.lower() for word in words)), "other")


def text_direction(item: Mapping[str, Any], profile: Mapping[str, Any], explicit: str = "") -> dict:
    """配置領域内で完結する字形と枠のトークンを返す。"""
    kind = str(item.get("type", "bubble"))
    energy = float(profile["composition_energy"])
    result = {"family": "normal", "shape": "round", "tail": "spoken", "size_scale": 1.0,
              "fill": "#222222", "background": "#ffffff", "stroke_width": 2,
              "text_stroke": 0, "repetition": False, "rotation": 0}
    text = str(item.get("text", ""))
    if kind == "sfx":
        family = sfx_type(text, explicit)
        strong = family in {"impact", "stop", "comedic_reaction"}
        result.update(family=family, shape="none", tail="none", fill=profile["accent"],
                      size_scale=1.25 if strong else 0.85 if family in {"ambient", "rustle", "footstep"} else 1.0,
                      text_stroke=2 if strong and energy >= 0.7 else 1,
                      text_stroke_fill="#ffffff",
                      repetition=family in {"footstep", "heartbeat"})
    elif kind == "narration":
        result.update(family="narration", shape="box", tail="none",
                      stroke_width=1 if energy < 0.7 else 2, background=profile["narration_fill"])
    else:
        family = dialogue_type(text, explicit)
        result["family"] = family
        if family == "thought":
            result.update(tail="thought", stroke_width=1)
        elif family in {"whisper", "weak"}:
            result.update(stroke_width=1, size_scale=0.9, tail="none" if family == "weak" else "spoken")
        elif family in {"shout", "comedic_reaction"}:
            result.update(shape="burst" if energy >= 0.7 else "round", stroke_width=3, size_scale=1.15)
        elif family == "announcement":
            result.update(shape="box", tail="none", stroke_width=2)
    return result


def apply_text_direction(panel: dict, settings: Mapping[str, Any]) -> None:
    """新規配置または明示再計算時だけ演出を保存する。"""
    profile = resolve_visual_style(settings)
    layout = panel.get("text_layout") or {}
    for item in layout.get("items", []):
        key = "dialogue_types" if item.get("type") == "bubble" else "sfx_types"
        values = panel.get(key) or []
        index = int(item.get("order", 1)) - 1
        explicit = str(values[index]) if isinstance(values, list) and 0 <= index < len(values) else ""
        item["direction"] = text_direction(item, profile, explicit)
    layout["style_profile"] = deepcopy(profile)
