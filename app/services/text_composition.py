"""実寸の文字計測と、既存絵を隠さない保守的な文字配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from PIL import ImageFont

PAGE_WIDTH = 900
PAGE_HEIGHT = 1200
BODY_FONT_SIZE = 17
LINE_HEIGHT = 23
PADDING = 10


@lru_cache(maxsize=8)
def body_font(size: int = BODY_FONT_SIZE):
    return ImageFont.truetype(str(Path(__file__).resolve().parents[1] / "assets/fonts/MPLUS1p-Regular.ttf"), size)


def wrap_text(text: str, width: float, size: int = BODY_FONT_SIZE) -> list[str]:
    """改行を保持し、各行を実際のフォント幅に収める。"""
    font = body_font(size)
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if line and font.getlength(line + char) > max(1, width):
                # 句読点・閉じ括弧・小書き仮名だけが次の行へ落ちないようにする。
                if char in "、。，．！？!?）」』】〉》ぁぃぅぇぉっゃゅょァィゥェォッャュョー" and len(line) > 1:
                    lines.append(line[:-1])
                    line = line[-1] + char
                elif line[-1] in "（「『【〈《":
                    lines.append(line[:-1])
                    line = line[-1] + char
                elif char.isascii() and char.isalnum() and " " in line.rstrip():
                    prefix, suffix = line.rsplit(" ", 1)
                    lines.append(prefix)
                    line = suffix + char
                else:
                    lines.append(line)
                    line = char
            else:
                line += char
        lines.append(line)
    return lines


def separate_text_from_unverified_artwork(panel: Mapping[str, Any], geometry: Mapping[str, Any], language: str) -> dict | None:
    """実画像の顔位置が未確認なら、絵に重ねず文字専用領域を確保する。

    空白を検出したふりはしない。明示的な保護領域がある場合は既存の配置を使う。
    """
    if not panel.get("image_url") or panel.get("protected_zones"):
        return None
    elements = [(kind, index + 1, str(text).strip())
                for kind, key in (("bubble", "dialogue"), ("narration", "narration"), ("sfx", "sfx"))
                for index, text in enumerate(panel.get(key, []) or []) if str(text).strip()]
    if not elements:
        return None
    width = max(1, float(geometry["width"]) * PAGE_WIDTH)
    height = max(1, float(geometry["height"]) * PAGE_HEIGHT)
    usable_width = width - 2 * PADDING
    items = []
    cursor = PADDING
    for kind, order, text in elements:
        # 横長のUIピルにせず、セリフは数行の丸い吹き出しとして計測する。
        text_width = min(usable_width - 2 * PADDING, BODY_FONT_SIZE * 9) if kind == "bubble" else usable_width - 2 * PADDING
        lines = wrap_text(text, text_width)
        item_height = len(lines) * LINE_HEIGHT + 2 * PADDING
        item_width = usable_width if kind == "narration" else min(usable_width, max(body_font().getlength(line) for line in lines) + 2 * PADDING + 12)
        left = width - PADDING - item_width if language == "ja" else PADDING
        items.append({"id": f"{kind}-{order}", "type": kind, "order": order,
                      "text": text, "lines": lines, "font_size": BODY_FONT_SIZE,
                      "x": left / width, "y": cursor / height,
                      "width": item_width / width, "height": item_height / height,
                      "line_count": len(lines), "font_scale": 1,
                      "side": "right" if language == "ja" else "left"})
        cursor += item_height + PADDING
    rail_height = cursor / height
    # 無理な縮小や人物への上書きで成功扱いにしない。
    overflow = rail_height > 0.48
    for item in items:
        item["overflow"] = overflow
    return {"version": 2, "safe_margin": 0, "items": items,
            "warnings": ["文字専用領域が大きすぎます。コマ面積の拡大またはページ分割が必要です。"] if overflow else [],
            "placement_mode": "reserved_text_band",
            "artwork_viewport": {"x": 0, "y": min(rail_height, 0.90), "width": 1, "height": max(0.10, 1 - rail_height)},
            "reason": "既存画像の人物位置が未確認のため、文字と画像の領域を分離"}
