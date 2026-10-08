"""本文と表紙類の区分。本文の目標数と通し番号から表紙類を除外する。"""

MAX_CONTENT_PAGES = 120
COVER_PAGE_KINDS = frozenset({"cover", "back_cover"})


def is_content_page(page: dict) -> bool:
    return page.get("page_kind") not in COVER_PAGE_KINDS


def page_label(page: dict) -> str:
    return {"cover": "表紙", "back_cover": "裏表紙"}.get(page.get("page_kind"), f"本文 {page.get('page_number')}ページ")


def requested_cover_kinds(settings: dict) -> list[str]:
    kinds = []
    if settings.get("title_mode") == "cover":
        kinds.append("cover")
    if settings.get("back_cover_mode") == "generate":
        kinds.append("back_cover")
    return kinds
