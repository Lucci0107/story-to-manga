"""表紙類の一枚絵と、その上へ重ねる文字の配置。本文のコマ割りとは独立する。"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .page_types import COVER_PAGE_KINDS
from .page_contract import page_size, uses_a4


COVER_ARTWORK_POLICY = "full_page_illustration_v1"
COVER_SCENE_TYPES = frozenset({"cover_illustration", "back_cover_illustration"})


def cover_text_regions(kind: str, *, a4: bool = False) -> dict[str, tuple[float, float, float, float]]:
    back = kind == "back_cover"
    regions = {
        "title": (.06, .035, .88, .14 if back else .25),
        "subtitle": (.08, .18 if back else .285, .84, .045),
        "author": (.08, .875 if back else .91, .84, .045 if back else .065),
        "blurb": (.10, .64, .80, .20),
        "publication": (.10, .935, .80, .045),
    }
    if a4:
        regions["title"] = (.06, .05, .88, .14 if back else .25)
        regions["author"] = (.08, .875 if back else .885, .84, .045 if back else .065)
        regions["publication"] = (.10, .92, .80, .03)
    return regions


def cover_artwork_panel(page: Mapping[str, Any], panel: dict) -> dict:
    """生成時だけ全ページの画面比率を使い、保存済みのネームを変更しない。"""
    kind = page.get("page_kind")
    if kind not in COVER_PAGE_KINDS:
        return panel
    result = deepcopy(panel)
    # 旧版はコピー側のrevisionだけを進めていたため、保存値が画像名より古い場合がある。
    # 初回の再生成でも既存原画と同じファイル名を使わない。
    image_revision = re.search(r"-r(\d+)\.png$", urlparse(str(panel.get("image_url") or "")).path)
    if image_revision:
        result["revision"] = max(int(panel.get("revision") or 0), int(image_revision.group(1)))
    result["scene_type"] = "back_cover_illustration" if kind == "back_cover" else "cover_illustration"
    result["panel_direction"] = None
    result["geometry"] = {"x": 0, "y": 0, "width": 1, "height": 1, "shape": "rectangle"}
    if uses_a4(page):
        result["geometry"]["page_size"] = list(page_size(page))
    result["panel_shape"] = "rectangle"
    result["allow_breakout"] = False
    # 変更前の本文コマの形状理由や保護領域を、新しい表紙の被写体指定へ混ぜない。
    for key in ("shape_reason", "breakout_reason", "semantic_reason"):
        result[key] = ""
    result["protected_zones"] = []
    result["text_safe_zones"] = {
        "title": {"x": .06, "y": .035, "width": .88, "height": .20 if kind == "back_cover" else .30},
        "credits": {"x": .08, "y": .875 if kind == "back_cover" else .91, "width": .84, "height": .10 if kind == "back_cover" else .065},
    }
    if kind == "back_cover":
        result["text_safe_zones"]["blurb"] = {"x": .10, "y": .64, "width": .80, "height": .20}
    if uses_a4(page):
        for zone in result["text_safe_zones"].values():
            zone["y"] = max(.05, min(zone["y"], .95 - zone["height"]))
    return result
