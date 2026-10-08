"""全編ネームと人物シートの公開可能な設計資料。旧版を保持して保存する。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import db
from ..schemas import MAX_CONTENT_PAGES
from .architect import rendering_profile, event_issues
from .manga_contract import contract_metadata

PAGE_FIELDS = (
    "id", "page_number", "page_kind", "title", "page_role", "layout", "layout_policy",
    "location_time", "panel_count_reason", "layout_reason", "start_state", "page_end_state",
    "allowed_events", "forbidden_until_later", "first_reveal", "carry_over", "architect_source",
)
PANEL_FIELDS = (
    "id", "order", "description", "shot_type", "characters", "action", "expression", "background",
    "location", "spatial_relationship", "reaction", "dialogue", "dialogue_details", "narration", "sfx",
    "panel_role", "scene_type", "importance", "panel_shape", "shape_reason", "event_ids",
    "in_world_text_policy", "in_world_exact_text",
)
CHARACTER_FIELDS = (
    "id", "name", "role", "age_range", "height", "appearance", "personality", "hairstyle",
    "hair_color", "eye_characteristics", "body_type", "clothing", "accessories",
    "distinguishing_features", "expressions", "relationship_notes", "negative_constraints",
    "identity_notes", "palette_notes", "costume_detail_notes", "sheet_ratio", "reference_roles",
)
GEOMETRY_FIELDS = ("x", "y", "width", "height", "shape", "shape_reason", "polygon_points")
VALUE_LABELS = {
    "ja": "日本語", "en": "英語", "right_to_left": "右 → 左", "left_to_right": "左 → 右",
    "bw": "白黒", "color": "カラー", "unspecified": "未指定", "fiction": "創作", "documentary": "実話・記録",
    "fast": "速め", "balanced": "標準", "slow": "余韻を長く", "low": "少なめ", "medium": "標準", "high": "多め",
    "classic": "標準ドラマ", "grid": "均等コマ", "wide": "横長重視", "hero": "1コマ", "auto": "自動",
    "rectangle": "長方形", "trapezoid": "台形", "slanted-left": "斜め左", "slanted-right": "斜め右",
}
SHEET_SECTIONS = [
    {"number": 1, "name": "人物プロフィール", "count": 8, "items": ["名前", "役割・職業", "年齢", "身長・サイズ", "体格・頭身", "性格", "識別特徴", "基調色"], "note": "顔が分かる大型の主役図を併設"},
    {"number": 2, "name": "全身4方向", "count": 4, "items": ["正面", "斜め前", "真横", "背面"], "note": "同じ縮尺・接地線で、頭頂と足先を完全に表示"},
    {"number": 3, "name": "顔と識別部位", "count": 8, "items": ["顔の正面", "顔の斜め前", "顔の横", "目", "眉", "鼻", "口", "耳"], "note": "顔3方向と5パーツ。非人間は実在する識別部位へ適応"},
    {"number": 4, "name": "表情8種類", "count": 8, "items": ["通常", "笑顔", "怒り", "悲しみ", "驚き", "心配", "自信", "決意"], "note": "4列×2行。同じ顔・髪・装飾を保持"},
    {"number": 5, "name": "ポーズ6種類", "count": 6, "items": ["基本立ち", "歩行", "座位", "腕組み", "思案", "行動準備"], "note": "身体構造と物語に適合する動作へ調整"},
    {"number": 6, "name": "衣装・外装の詳細", "count": 8, "items": ["衣装の正面", "衣装の背面", "異なる構造の拡大6箇所"], "note": "採用済みの衣装・構造だけを使い、未設定の部品を点数合わせで追加しない"},
    {"number": 7, "name": "色と素材のパレット", "count": 5, "items": ["主色", "アクセント", "髪・肌／表面", "生地・素材", "既存の小物"], "note": "白黒はグレー階調。素材組成や未設定の小物を断定しない"},
]


def content_digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _select(value: dict, fields: tuple[str, ...]) -> dict:
    return {key: value.get(key) for key in fields if key in value}


def _display(value: Any, empty: str = "未設定") -> str:
    if isinstance(value, list):
        return " / ".join(str(item) for item in value) or empty
    return str(value).strip() if value is not None and str(value).strip() else empty


def _label(value: Any, empty: str = "未設定") -> str:
    text = _display(value, empty)
    return VALUE_LABELS.get(text, text)


def name_snapshot(project: dict) -> dict:
    pages = []
    refs = {}
    for page in project.get("storyboard") or []:
        entry = _select(page, PAGE_FIELDS)
        geometries = {g.get("panel_id"): g for g in (page.get("composition") or {}).get("panels", [])}
        panels = []
        for panel in page.get("panels") or []:
            item = _select(panel, PANEL_FIELDS)
            item["geometry"] = _select(geometries.get(panel.get("id")) or panel.get("geometry") or {}, GEOMETRY_FIELDS)
            panels.append(item)
        for ref in page.get("knowledge_refs") or []:
            key = (ref.get("document_id"), ref.get("version_id"))
            refs[key] = _select(ref, ("document_id", "version_id", "version_number", "title"))
        entry["panels"] = panels
        pages.append(entry)
    settings = {key: value for key, value in (project.get("settings") or {}).items() if key not in {"style_reason", "script_tone_reason"}}
    return {
        "title": project["title"], "source_filename": project.get("source_filename") or "本文入力",
        "source_hash": hashlib.sha256(str(project.get("original_text") or "").encode("utf-8")).hexdigest(),
        "settings": settings, "characters": [_select(c, CHARACTER_FIELDS) for c in project.get("characters") or []],
        "pages": pages, "knowledge_versions": sorted(refs.values(), key=lambda r: str(r.get("document_id"))),
    }


def name_page_counts(snapshot: dict) -> dict:
    pages = snapshot["pages"]
    covers = sum(page.get("page_kind") == "cover" for page in pages)
    content = len(pages) - covers
    return {
        "target_content_pages": int(snapshot["settings"].get("target_page_count") or content),
        "content_pages": content, "cover_pages": covers, "total_pages": len(pages),
        "maximum_content_pages": MAX_CONTENT_PAGES,
    }


def name_validation(project: dict, snapshot: dict) -> dict:
    errors, warnings = [], []
    pages = snapshot["pages"]
    content = [page for page in pages if page.get("page_kind") != "cover"]
    counts = name_page_counts(snapshot)
    if not content:
        errors.append("本文のネームを作成してください。")
    if [p.get("page_number") for p in content] != list(range(1, len(content) + 1)):
        errors.append("本文ページ番号を1から順番に揃えてください。")
    required = db.name_review_required(project["id"]) or any(p.get("name_review_required") for p in project.get("storyboard") or [])
    if required and counts["content_pages"] != counts["target_content_pages"]:
        errors.append(f"本文ページ数が目標ページ数と一致しません（目標{counts['target_content_pages']}ページ・現在{counts['content_pages']}ページ）。表紙{counts['cover_pages']}ページは本文に含みません。")
    if len(content) > MAX_CONTENT_PAGES:
        errors.append(f"本文は{MAX_CONTENT_PAGES}ページ以内にしてください。")
    if counts["cover_pages"] > 1 or any(page.get("page_kind") == "cover" for page in pages[1:]):
        errors.append("表紙は先頭の1ページだけにしてください。")
    ids = []
    for page in pages:
        ids.append(page.get("id"))
        errors.extend(event_issues(page))
        if not page.get("panels"):
            errors.append(f"ページ{page.get('page_number')}にコマがありません。")
        for panel in page.get("panels") or []:
            ids.append(panel.get("id"))
            label = f"ページ{page.get('page_number')}・コマ{panel.get('order')}"
            if not panel.get("description") and not panel.get("action"):
                errors.append(label + "の内容を入力してください。")
            if not panel.get("background") and panel.get("scene_type") in {"establishing", "transition"}:
                warnings.append(label + "の場所が分かる背景を確認してください。")
            for index, text in enumerate(panel.get("dialogue") or []):
                details = panel.get("dialogue_details") or []
                detail = details[index] if index < len(details) else {}
                if not detail.get("speaker"):
                    warnings.append(label + "の話者が未設定です。")
                if detail.get("source") == "source_quote" and text not in project.get("original_text", ""):
                    errors.append(label + "の「原作の発言」が原文と一致しません。出所または文字列を修正してください。")
    if len(ids) != len(set(ids)):
        errors.append("ページ・コマのIDが重複しています。")
    return {"errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)), "page_counts": counts}


def render_name_markdown(snapshot: dict, number: int) -> str:
    settings = snapshot["settings"]
    content = [p for p in snapshot["pages"] if p.get("page_kind") != "cover"]
    style = rendering_profile(settings)
    lines = [f"# {snapshot['title']} 漫画ネーム台本", "", f"版：{number} / 本文：{len(content)}ページ", "",
             "確定状態はアプリの版履歴に保存します。このファイルの提供だけでは画像生成を開始しません。", "",
             "## 制作条件と原作", "", f"原作：{snapshot['source_filename']}（提供本文の全体）",
             f"原作識別：{snapshot['source_hash']}", f"言語・読順：{_label(settings.get('language'))} / {_label(settings.get('reading_direction'))}",
             f"画風・色：{style.get('name') or settings.get('visual_style', '未設定')} / {_label(settings.get('color_mode'))}",
             f"想定読者：{_display(settings.get('target_audience'))}", f"原作区分：{_label(settings.get('source_kind'), '未指定')}",
             f"テンポ・会話密度：{_label(settings.get('pacing'))} / {_label(settings.get('dialogue_density'))}",
             f"画風の調整：{_display(settings.get('style_adjustments'), 'なし')}", "原作の意味・事実・因果・順序を保持。脚色の発言は確認済み引用と区別します。", "",
             "## 人物と関係", ""]
    for character in snapshot["characters"]:
        lines += [f"- {character.get('name')}：{_display(character.get('role'))}。{_display(character.get('relationship_notes'))}"]
    lines += ["", "## 全体構成", ""]
    for page in snapshot["pages"]:
        label = "表紙" if page.get("page_kind") == "cover" else f"本文 {page.get('page_number')}ページ"
        lines += [f"- {label} / {len(page['panels'])}コマ / {_display(page.get('page_role') or page.get('title'))} / {_display(page.get('allowed_events'), '出来事の追加なし')}"]
    sources = {json.dumps(page["architect_source"], ensure_ascii=False, sort_keys=True) for page in snapshot["pages"] if page.get("architect_source")}
    if sources or snapshot["knowledge_versions"]:
        lines += ["", "## 参照資料の版", ""]
        for value in sorted(sources):
            source = json.loads(value)
            lines += [f"- 漫画設計仕様：{_display(source.get('name'))} / 指示 {_display(source.get('instructions_version'))} / 設計資料 {_display(source.get('knowledge_version'))}"]
        for ref in snapshot["knowledge_versions"]:
            lines += [f"- 選択したKnowledge：{_display(ref.get('title'))} / 版 {_display(ref.get('version_number'))} / 版ID {_display(ref.get('version_id'))}"]
    for page in snapshot["pages"]:
        label = "表紙" if page.get("page_kind") == "cover" else f"本文 {page.get('page_number')}ページ"
        lines += ["", f"## {label}", "", f"目的：{_display(page.get('page_role') or page.get('title'))}",
                  f"場所・時間：{_display(page.get('location_time'))}", f"コマ数：{len(page['panels'])} / 選定理由：{_display(page.get('panel_count_reason'), '要確認')}",
                  f"配置・テンポ：{_label(page.get('layout'))} / {_display(page.get('layout_reason'), '内容と読順を確認')}",
                  f"開始状態：{_display(page.get('start_state'))}", f"今回の出来事：{_display(page.get('allowed_events'), 'なし')}",
                  f"後まで描かない出来事：{_display(page.get('forbidden_until_later'), 'なし')}",
                  f"ページ末・次への接続：{_display(page.get('page_end_state'))} / {_display(page.get('carry_over'), 'なし')}"]
        for panel in page["panels"]:
            g = panel["geometry"]
            lines += ["", f"### {panel.get('order')}コマ目", "", f"内容：{_display(panel.get('description'))}",
                      f"枠：{_label(g.get('shape') or panel.get('panel_shape'), '長方形')} / 演出理由：{_display(g.get('shape_reason') or panel.get('shape_reason'), '長方形で読順と内容を保持')}",
                      f"配置・面積：{json.dumps(_select(g, ('x', 'y', 'width', 'height')), ensure_ascii=False)}（ページ相対座標）",
                      f"構図・画角：{_display(panel.get('shot_type'))}", f"場所：{_display(panel.get('location') or panel.get('background'))}",
                      f"人物：{_display(panel.get('characters'), 'なし')} / 位置関係：{_display(panel.get('spatial_relationship'))}",
                      f"動作・表情：{_display(panel.get('action'))} / {_display(panel.get('expression'))}",
                      f"必要な背景・道具：{_display(panel.get('background'))}", "セリフ（表示順）："]
            details = panel.get("dialogue_details") or []
            if not panel.get("dialogue"):
                lines += ["- なし（動作・表情・間で伝える）"]
            for index, text in enumerate(panel.get("dialogue") or []):
                detail = details[index] if index < len(details) else {}
                source = {"source_quote": "原作の発言", "adaptation": "脚色案（本版の確認対象）"}.get(detail.get("source"), "出所未設定（要確認）")
                lines += [f"- {index + 1}. {_display(detail.get('speaker'))} → {_display(detail.get('addressee'))}：{text}",
                          f"  出所：{source} / 反応：{_display(detail.get('reaction'))}"]
            lines += [f"ナレーション：{_display(panel.get('narration'), 'なし')}", f"相手の反応・沈黙：{_display(panel.get('reaction') or panel.get('expression'))}",
                      f"擬音・効果：{_display(panel.get('sfx'), 'なし')}", f"場面内の正確な文字：{_display(panel.get('in_world_exact_text'), 'なし')}"]
    lines += ["", "## 修正・版履歴・事前確認", "", "アプリのネーム画面でページ・コマを編集して保存し、全編を再出力してください。旧版は版履歴に保持します。",
              "本文ページ、全コマ、背景、話者、反応、出所、表示文字、読順を確認し、この版を確定してください。画像の品質は生成後に別途確認します。", ""]
    return "\n".join(lines)


def name_script_summary(project: dict) -> dict:
    snapshot = name_snapshot(project)
    digest = content_digest(snapshot)
    versions = db.manga_document_versions(project["id"], project["user_id"], "name_script", "storyboard")
    current = next((v for v in versions if v["content_hash"] == digest), None)
    required = db.name_review_required(project["id"]) or any(p.get("name_review_required") for p in project.get("storyboard") or [])
    return {
        "required": required, "content_hash": digest,
        "state": "confirmed" if current and current.get("approved_at") else "draft" if current else "outdated" if versions else "not_created",
        "version_id": current["id"] if current else None, "version_number": current["version_number"] if current else None,
        "provided_at": current.get("provided_at") if current else None,
        "approved_at": current.get("approved_at") if current else None,
        "versions": versions,
        "page_counts": name_page_counts(snapshot), "validation": name_validation(project, snapshot),
    }


def prepare_name_document(project: dict) -> tuple[dict, dict]:
    snapshot = name_snapshot(project)
    validation = name_validation(project, snapshot)
    if not snapshot["pages"]:
        raise ValueError("先に全編のネームを作成してください。")
    document = db.save_manga_document(project, "name_script", "storyboard", content_digest(snapshot), snapshot, lambda n: render_name_markdown(snapshot, n))
    return document, validation


def character_sheet_design(project: dict, character: dict) -> dict:
    fields = [
        ("名前", "name"), ("役割・職業", "role"), ("年齢", "age_range"), ("身長・サイズ", "height"),
        ("体格・頭身", "body_type"), ("性格", "personality"), ("識別特徴", "distinguishing_features"), ("基調色", "palette_notes"),
    ]
    ids = [c["id"] for c in project.get("characters") or []]
    return {
        "character_id": character["id"], "character_name": character["name"],
        "sequence_index": ids.index(character["id"]) + 1, "total_characters": len(ids),
        "ratio": character.get("sheet_ratio") or "3:4", "detail_level": "detailed",
        "state": "design_document", "architect_source": contract_metadata(),
        "rendering_style": rendering_profile(project.get("settings") or {}), "color_mode": (project.get("settings") or {}).get("color_mode", "bw"),
        "profile_fields": [{"label": label, "value": _display(character.get(key)), "source": "current_character_setting" if character.get(key) else "unknown"} for label, key in fields],
        "sections": SHEET_SECTIONS, "identity_notes": _display(character.get("identity_notes") or character.get("negative_constraints")),
        "costume_detail_notes": _display(character.get("costume_detail_notes") or character.get("clothing")),
        "character": _select(character, CHARACTER_FIELDS),
        "layout": "タイトル→主役図とプロフィール→全身→顔→表情→ポーズと衣装→パレット→同一性メモ" if character.get("sheet_ratio", "3:4") == "3:4" else "3列（人物とポーズ／全身と衣装／顔と表情）＋下帯にパレットと同一性メモ",
    }


def render_sheet_markdown(design: dict, number: int) -> str:
    lines = [f"# {design['character_name']} スタイルシート設計", "", f"版：{number} / 人物：{design['sequence_index']} / {design['total_characters']}名 / 比率：{design['ratio']}",
             "", "状態：設計資料（人物画像の生成・実画像監査は未実施）", f"画風：{design['rendering_style'].get('name') or '現在の漫画描画'} / 色：{_label(design['color_mode'])}",
             f"配置：{design['layout']}", "", "## プロフィール", ""]
    for field in design["profile_fields"]:
        lines += [f"- {field['label']}：{field['value']}（{'人物設定' if field['source'] != 'unknown' else '未設定'}）"]
    for section in design["sections"]:
        lines += ["", f"## {section['number']}. {section['name']}（{section['count']}項目）", "", _display(section["items"]), section["note"]]
    lines += ["", "## 同一性メモ", "", design["identity_notes"], "", "## 採用衣装と補完範囲", "", design["costume_detail_notes"], "",
              "未知の年齢・身長・経歴や見えない背面の特徴を確認済みの事実にしません。非人間は元の身体構造に適応し、該当しない項目には理由を記録してください。",
              "項目数は画像制作時の設計要件です。文書の作成を画像の完成や品質合格として扱いません。", ""]
    return "\n".join(lines)
