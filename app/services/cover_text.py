"""表紙類の文字情報。Artworkや本文の設計とは独立して編集する。"""

from __future__ import annotations

from typing import Any, Mapping


COVER_TEXT_FIELDS = frozenset({
    "cover_title", "cover_subtitle", "cover_author", "back_cover_blurb", "back_cover_publication",
})


def artwork_settings(settings: Mapping[str, Any]) -> dict:
    """文字だけの変更でネーム確認・画像生成の設計を失効させない。"""
    return {key: value for key, value in settings.items() if key not in COVER_TEXT_FIELDS}


def cover_text(project: Mapping[str, Any]) -> dict[str, str]:
    settings = project.get("settings") or {}
    return {
        "title": str(settings.get("cover_title") or project.get("title") or "").strip(),
        "subtitle": str(settings.get("cover_subtitle") or "").strip(),
        "author": str(settings.get("cover_author") or "").strip(),
        "blurb": str(settings.get("back_cover_blurb") or "").strip(),
        "publication": str(settings.get("back_cover_publication") or "").strip(),
    }
