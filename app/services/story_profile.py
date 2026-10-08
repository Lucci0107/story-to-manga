"""ページ数の推定に使う、原稿全体の規模と順序を保った参照情報。"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Dict, List


MAX_SOURCE_SECTIONS = 64
EXCERPT_CHARACTERS = 160
# AIを利用できない場合の暫定換算。漫画の文字掲載量や完成ページ数を保証しない。
REFERENCE_CHARACTERS_PER_PAGE = 900
MINIMUM_CHARACTERS_PER_PAGE = 1_500
SUBSTANTIVE_SECTION_CHARACTERS = 300
CHAPTER_TITLE = re.compile(
    r"^(?:第\s*[\d０-９一二三四五六七八九十百千]+\s*[章話節部]|"
    r"(?:chapter|part|scene)\s+(?:\d+|[ivxlcdm]+)\b)", re.I
)
APPENDIX_TITLE = re.compile(
    r"^(?:参考文献|出典|脚注|参考資料|references|bibliography|sources)\b|"
    r"編集[^\n]{0,20}(?:注記|注釈)", re.I
)


def story_fingerprint(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _headings(text: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    offset = 0
    fence = ""
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = "" if fence == marker else marker if not fence else fence
        elif not fence:
            match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
            title = match.group(2).strip() if match else stripped
            if match or CHAPTER_TITLE.match(title):
                result.append({"start": offset, "title": title, "level": len(match.group(1)) if match else 2})
        offset += len(line)
    return result


def _excerpt(text: str, position: float) -> str:
    cleaned = " ".join(text.split())
    start = max(0, int((len(cleaned) - EXCERPT_CHARACTERS) * position))
    return cleaned[start:start + EXCERPT_CHARACTERS]


def _bounded_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """前半だけを残さず、全区間を順序どおりに最大64件へまとめる。"""

    result: List[Dict[str, Any]] = []
    count = min(MAX_SOURCE_SECTIONS, len(sections))
    for index in range(count):
        group = sections[index * len(sections) // count:(index + 1) * len(sections) // count]
        first, last = group[0], group[-1]
        title = first["title"] if len(group) == 1 else f'{first["title"][:45]} ～ {last["title"][:45]}（{len(group)}区間）'
        content = "\n\n".join(item["text"] for item in group)
        result.append({
            "start_section": first["number"],
            "end_section": last["number"],
            "title": title[:120],
            "characters": sum(item["characters"] for item in group),
            "opening": _excerpt(content, 0),
            "middle": _excerpt(content, 0.5),
            "ending": _excerpt(content, 1),
        })
    return result


def build_story_source_profile(text: str, *, include_section_text: bool = False) -> Dict[str, Any]:
    """原作は参照データとして数え、本文を書き換えずに推定用の情報を返す。"""

    source = str(text or "").replace("\r\n", "\n").strip()
    headings = _headings(source)
    chapters = [item for item in headings if CHAPTER_TITLE.match(item["title"])]
    body_end = len(source)
    # 最終章の後に明示された出典・編集注記は、漫画本文の分量と区別する。
    if chapters:
        appendix = next((item for item in headings if item["start"] > chapters[-1]["start"]
                         and item["level"] <= chapters[-1]["level"]
                         and APPENDIX_TITLE.search(item["title"])), None)
        if appendix:
            body_end = appendix["start"]
    body = source[:body_end].strip()
    selected = [item for item in chapters if item["start"] < body_end]
    if not selected:
        levels = Counter(item["level"] for item in headings if item["start"] < body_end)
        repeated = [level for level, count in levels.items() if count > 1]
        if repeated:
            selected = [item for item in headings if item["level"] == min(repeated) and item["start"] < body_end]
    sections: List[Dict[str, Any]] = []
    if selected:
        for index, item in enumerate(selected):
            start = 0 if index == 0 else item["start"]
            end = selected[index + 1]["start"] if index + 1 < len(selected) else len(body)
            content = body[start:end].strip()
            sections.append({"number": index + 1, "title": item["title"], "characters": len(content), "text": content})
    elif body:
        # 見出しのない長文も末尾まで参照する。機械的な区間を章と呼ばない。
        count = max(1, math.ceil(len(body) / 5_000))
        for index in range(count):
            content = body[index * len(body) // count:(index + 1) * len(body) // count]
            sections.append({"number": index + 1, "title": f"原稿区間 {index + 1}/{count}", "characters": len(content), "text": content})
    substantive = sum(item["characters"] >= SUBSTANTIVE_SECTION_CHARACTERS for item in sections)
    structural_minimum = substantive if selected else 0
    minimum = max(1, structural_minimum, math.ceil(len(body) / MINIMUM_CHARACTERS_PER_PAGE))
    reference_pages = sum(max(1, math.ceil(item["characters"] / REFERENCE_CHARACTERS_PER_PAGE)) for item in sections)
    # 独立した章・場面には導入と転換の余白を見込む。単なる分割区間には加算しない。
    if len(selected) > 1:
        reference_pages += substantive
    profile = {
        "source_fingerprint": story_fingerprint(source),
        "source_character_count": len(source),
        "narrative_character_count": len(body),
        "reference_character_count": len(source) - body_end,
        "chapter_count": len(chapters),
        "section_count": len(sections),
        "paragraph_count": len([part for part in re.split(r"\n\s*\n", body) if part.strip()]) if body else 0,
        "quoted_passage_count": len(re.findall(r"「[^」]*」|“[^”]*”", body)),
        "minimum_page_count": minimum,
        "reference_page_count": max(minimum, reference_pages),
        "sections": _bounded_sections(sections),
    }
    if include_section_text:
        profile["section_texts"] = sections
    return profile
