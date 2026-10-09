"""原稿の出来事を短い固定IDで参照し、許可範囲との対応を検証する。"""

from __future__ import annotations


class StoryboardValidationError(ValueError):
    """本文や人物名を含まない検証理由と、AIへの修正指示。"""

    error_category = "validation"

    def __init__(self, message: str, repair_hint: str) -> None:
        super().__init__(message)
        self.repair_hint = repair_hint


def event_catalog(plan: list[dict]) -> list[dict]:
    events = dict.fromkeys(event for page in plan for event in page["allowed_events"])
    return [{"id": f"event-{number:04d}", "text": text}
            for number, text in enumerate(events, 1)]


def ground_storyboard_events(pages: list[dict], expected: list[dict], catalog: list[dict]) -> None:
    """IDを原稿の正確な出来事へ戻す。旧形式の原文参照も受け付ける。"""

    by_id = {event["id"]: event["text"] for event in catalog}
    by_text = {event["text"]: event["id"] for event in catalog}
    covered = set()
    for page, planned in zip(pages, expected):
        allowed = set(planned["allowed_events"])
        for index, panel in enumerate(page.get("panels") or [], 1):
            events = [by_id.get(value, value) for value in panel.get("event_ids") or []]
            if not set(events).issubset(allowed):
                page_number = planned["page_number"]
                allowed_ids = [by_text[event] for event in planned["allowed_events"]]
                raise StoryboardValidationError(
                    f"本文{page_number}ページの{index}コマ目に、許可範囲と一致しない出来事参照があります",
                    f"本文{page_number}ページのevent_idsは{allowed_ids}から選んでください。"
                    "出来事の文章や独自のIDを書かず、event_catalogのidをそのまま使ってください。",
                )
            panel["event_ids"] = events
            covered.update(events)
    required = {event for page in expected for event in page["allowed_events"]}
    missing = required - covered
    if missing:
        missing_ids = [event["id"] for event in catalog if event["text"] in missing]
        raise StoryboardValidationError(
            "この範囲の出来事とコマの対応が不足しています",
            f"不足している出来事参照は{missing_ids}です。各出来事をallowed_event_idsで許可された"
            "ページのコマの動作・背景・発言に反映し、対応するevent_idsも記載してください。",
        )
