"""表紙類の一枚絵と、その上へ重ねる文字の配置。本文のコマ割りとは独立する。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .page_types import COVER_PAGE_KINDS


COVER_ARTWORK_POLICY = "full_page_illustration_v1"
COVER_SCENE_TYPES = frozenset({"cover_illustration", "back_cover_illustration"})


def cover_text_regions(kind: str) -> dict[str, tuple[float, float, float, float]]:
    back = kind == "back_cover"
    return {
        "title": (.06, .035, .88, .14 if back else .25),
        "subtitle": (.08, .18 if back else .285, .84, .045),
        "author": (.08, .875 if back else .91, .84, .045 if back else .065),
        "blurb": (.10, .64, .80, .20),
        "publication": (.10, .935, .80, .045),
    }


def cover_artwork_panel(page: Mapping[str, Any], panel: dict) -> dict:
    """生成時だけ全ページの画面比率を使い、保存済みのネームを変更しない。"""
    kind = page.get("page_kind")
    if kind not in COVER_PAGE_KINDS:
        return panel
    result = deepcopy(panel)
    result["scene_type"] = "back_cover_illustration" if kind == "back_cover" else "cover_illustration"
    result["panel_direction"] = None
    result["geometry"] = {"x": 0, "y": 0, "width": 1, "height": 1, "shape": "rectangle"}
    result["panel_shape"] = "rectangle"
    result["allow_breakout"] = False
    result["text_safe_zones"] = {
        "title": {"x": .06, "y": .035, "width": .88, "height": .20 if kind == "back_cover" else .30},
        "credits": {"x": .08, "y": .875 if kind == "back_cover" else .91, "width": .84, "height": .10 if kind == "back_cover" else .065},
    }
    if kind == "back_cover":
        result["text_safe_zones"]["blurb"] = {"x": .10, "y": .64, "width": .80, "height": .20}
    return result
