"""表紙だけの変更案。原稿・登録人物を参照し、本文や既存画像を保持する。"""

from collections import Counter
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from .character_proposal import active_characters
from .cover_layout import cover_artwork_panel
from .layout import reflow_page
from .manga_documents import content_digest, name_snapshot
from .page_types import COVER_PAGE_KINDS, is_content_page, page_label
from .page_contract import page_size
from .story_analysis import analysis_content_fingerprint


class CoverDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=1200)
    characters: list[str] = Field(max_length=3)
    shot_type: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=500)
    expression: str = Field(max_length=240)
    background: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


def find_cover(project: dict, page_id: str) -> dict:
    page = next((p for p in project.get("storyboard") or [] if p.get("id") == page_id), None)
    if not page or page.get("page_kind") not in COVER_PAGE_KINDS:
        raise ValueError("表紙または裏表紙を選んでください。")
    if len(page.get("panels") or []) != 1:
        raise ValueError("表紙のネームを一枚絵の1コマにしてください。")
    return page


def proposal_input_hash(project: dict, page_id: str, models: dict, knowledge: dict) -> str:
    return content_digest({
        "policy": "cover_proposal_v1", "page_id": page_id,
        "name_hash": content_digest(name_snapshot(project)),
        "analysis": analysis_content_fingerprint(project.get("analysis") or {}),
        "selected_characters": [c.get("id") for c in active_characters(project)],
        "models": models, "knowledge": knowledge,
        "cover_text": {key: (project.get("settings") or {}).get(key) for key in
                       ("cover_title", "cover_subtitle", "cover_author", "back_cover_blurb", "back_cover_publication")},
    })


def proposal_context(project: dict, page: dict, instruction: str) -> dict:
    people = active_characters(project)
    panels = [panel for p in project.get("storyboard") or [] if is_content_page(p) for panel in p.get("panels") or []]
    frequency = Counter(name for panel in panels for name in set(panel.get("characters") or []))
    people = sorted(people, key=lambda c: -frequency.get(c.get("name"), 0))[:12]
    sample = [panels[i] for i in sorted({round(n * (len(panels) - 1) / 5) for n in range(6)})] if panels else []
    source = str(project.get("original_text") or "")
    return {
        "title": project.get("title"), "kind": page["page_kind"], "instruction": instruction,
        "canvas_size": list(page_size(page)),
        "analysis": {k: str((project.get("analysis") or {}).get(k) or "")[:1600] for k in ("summary", "theme")},
        "source_reference": source if len(source) <= 6000 else source[:2500] + "\n…中略…\n" + source[-2500:],
        "characters": [{k: str(c.get(k) or "")[:500 if k == "name" else 300] for k in
                        ("name", "role", "appearance", "clothing", "identity_notes", "negative_constraints")} for c in people],
        "body_scenes": [{k: panel.get(k) if k == "characters" else str(panel.get(k) or "")[:300]
                         for k in ("characters", "description", "background", "action")} for panel in sample],
        "current_cover": {k: page["panels"][0].get(k) for k in
                          ("description", "characters", "shot_type", "action", "expression", "background")},
        "style": {k: (project.get("settings") or {}).get(k) for k in
                  ("visual_style", "rendering_style_id", "color_mode", "style_adjustments", "source_kind")},
    }


def proposal_schema(context: dict) -> dict:
    schema = CoverDesign.model_json_schema()
    names = [c["name"] for c in context["characters"] if c["name"]]
    # enumが空になるとStructured Outputsが拒否するため、空配列を制約する。
    if names:
        schema["properties"]["characters"]["items"]["enum"] = names
    else:
        schema["properties"]["characters"]["maxItems"] = 0
    return schema


def validate_proposal(value: dict, context: dict) -> dict:
    design = CoverDesign.model_validate(value).model_dump()
    names = {c["name"] for c in context["characters"]}
    if any(name not in names for name in design["characters"]):
        raise ValueError("表紙の人物は選択済みの登録人物から選んでください。")
    design["characters"] = list(dict.fromkeys(design["characters"]))
    return design


def demo_proposal(context: dict) -> dict:
    back = context["kind"] == "back_cover"
    name = next((c["name"] for c in context["characters"] if c["name"]), "")
    scene = next((s for s in context["body_scenes"] if name in (s.get("characters") or [])), None)
    scene = scene or next(iter(context["body_scenes"]), {})
    return validate_proposal({
        "description": ("裏表紙" if back else "表紙") + "。作品を象徴する風景と人物を、一枚絵で描く。",
        "characters": [name] if name and not back else [],
        "shot_type": "風景を見せる縦長の一枚絵" if back or not name else "人物の半身と背景を見せる縦長の一枚絵",
        "action": ("背景を連続した一つの風景として描く。" if back or not name else "主人公を画面の中央から下に配置する。")
                  + "上部のタイトルと下部の作者名の余白を確保する。" + context["instruction"][:300],
        "expression": "穏やかに前方を見つめる" if name and not back else "",
        "background": scene.get("background") or "本文に登場する風景",
        "reason": "登録した主要人物と本文の風景を使ったデモ提案です。文字はアプリ側で重ねます。",
    }, context)


def changed_cover(project: dict, page: dict, design: dict, refs: list) -> dict:
    result = deepcopy(page)
    panel = result["panels"][0]
    panel.update({key: value for key, value in design.items() if key != "reason"})
    prepared = cover_artwork_panel(result, panel)
    panel.update({key: prepared[key] for key in ("scene_type", "geometry", "panel_shape", "allow_breakout", "text_safe_zones")})
    panel.update(dialogue=[], dialogue_details=[], narration=[], sfx=[], event_ids=[],
                 in_world_exact_text="", in_world_text_policy="none", generation_prompt="", prompt_source="generated",
                 generation_status="not_started", generation_error=None, knowledge_refs=refs,
                 protected_zones={}, character_position="center", subject_position="center", face_position="center")
    result["knowledge_refs"] = refs
    return reflow_page(result, project.get("settings") or {})


def render_proposal_markdown(page: dict, design: dict, number: int) -> str:
    return f"# {page_label(page)}の変更案 / 版 {number}\n\n" + "\n\n".join(f"{key}：{value}" for key, value in design.items())
