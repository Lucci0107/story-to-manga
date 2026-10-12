"""生成直前の設計と承認。稼働状態・生成結果を設計ハッシュへ混ぜない。"""

from __future__ import annotations

import hashlib
import json
from .cover_text import artwork_settings, cover_text
from .cover_layout import COVER_ARTWORK_POLICY, cover_artwork_panel, cover_text_regions
from .architect import rendering_profile, tone_parameters, event_issues
from .reading_order import canonicalize_stored_settings
from .page_types import is_content_page
from .character_references import anonymous_characters, unresolved_characters, character_reference
from .character_proposal import source_cast

VOLATILE = {
    "generation_status",
    "generation_error",
    "generation_metadata",
    "generation_image_model",
    "image_url",
    "knowledge_refs",
    "revision",
    "updated_at",
    "created_at",
    "prompt_source",
}


def design_data(
    project: dict, page: dict, panel: dict, models: dict, knowledge: list
) -> dict:
    def stable(value):
        if isinstance(value, dict):
            return {
                k: stable(v)
                for k, v in value.items()
                if k not in VOLATILE
                and (k != "generation_prompt" or value.get("prompt_source") == "user")
            }
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value

    return {
        "version": 1,
        "character_reference_policy": "registered_life_stages_and_supporting_cast_v2",
        "project_id": project["id"],
        "page": stable(page),
        **({"cover_artwork_policy": COVER_ARTWORK_POLICY} if not is_content_page(page) else {}),
        **({"cover_generation_plan": stable(cover_artwork_panel(page, panel))} if not is_content_page(page) else {}),
        "target_id": panel["id"],
        "characters": stable(project.get("characters") or []),
        "source_cast": source_cast(project),
        "settings": artwork_settings(project.get("settings") or {}),
        "models": models,
        "knowledge": knowledge,
        "source_hash": hashlib.sha256(
            str(project.get("original_text", "")).encode()
        ).hexdigest(),
    }


def design_hash(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def generation_layout_issues(page: dict, panel: dict, settings: dict) -> list[str]:
    """確認・キュー・実行で同じ配置条件を使う。表紙は本文の空間計画から独立する。"""
    if not is_content_page(page):
        if len(page.get("panels") or []) != 1:
            return ["表紙・裏表紙は一枚絵の1コマにしてください。ネームでコマ数を修正してください。"]
        # 全面3:4の絵と固定のタイトル・著者領域を別々に描画する。
        # 保存済みの本文用PanelDirectionは、実際の表紙生成には使用しない。
        return []
    errors = []
    from .panel_direction import direction_is_ready
    from .composition import composition_quality_issues
    from .composition_fallback import panel_spatial_complexity, COMPLEXITY_INFEASIBLE

    if (
        panel.get("panel_direction")
        or (page.get("composition") or {}).get("style_profile")
    ) and not direction_is_ready(panel, settings):
        errors.append(
            "構図が古いか文字領域が不足しています。ネームで配置を再計算してください。"
        )
    if (
        int(settings.get("composition_version") or 0) >= 3
        and not panel.get("image_url")
        and not panel.get("fallback_applied")
        and panel_spatial_complexity(panel).get("level") == COMPLEXITY_INFEASIBLE
    ):
        errors.append("横長コマの情報量が多すぎます。ネームでコマを分割してください。")
    for issue in composition_quality_issues(page):
        if any(
            word in issue["key"]
            for word in (
                "sliver",
                "boundary",
                "panel-overlap",
                "protected-collision",
                "face-collision",
                "reading-order",
                "ellipse",
            )
        ):
            errors.append(issue["detail"])
    return list(dict.fromkeys(errors))


def audit_design(project: dict, page: dict, panel: dict) -> list[str]:
    settings = project.get("settings") or {}
    errors = event_issues(page) + generation_layout_issues(page, panel, settings)
    from .manga_documents import name_script_summary
    if project.get("id") and project.get("user_id"):
        name = name_script_summary(project)
        if name["required"] and name["state"] != "confirmed":
            errors.append("ネーム画面で全編Markdownを出力・確認し、現在の版を確定してください。")
    profile = rendering_profile(settings)
    if panel.get("prompt_source") == "user":
        prompt = str(panel.get("generation_prompt") or "").casefold()
        if any(rule.casefold() in prompt for rule in profile.get("forbidden", [])):
            errors.append(
                "手入力の描画指示とスタイルの禁止条件が重複しています。指示を整理してください。"
            )
    if canonicalize_stored_settings(settings).get("reading_direction") != settings.get(
        "reading_direction"
    ):
        errors.append("言語と読順が一致していません。設定を保存し直してください。")
    if not panel.get("description") and not panel.get("action"):
        errors.append("コマの内容を入力してください。")
    if settings.get("rendering_style_id") and not rendering_profile(settings):
        errors.append("描画スタイルを選び直してください。")
    if (
        (settings.get("rendering_style_id") == "STYLE-012" or profile.get("color_mode") == "bw")
        and settings.get("color_mode") == "color"
    ):
        errors.append("この描画方式には白黒を選んでください。")
    if (
        settings.get("rendering_style_id") == "custom"
        and not settings.get("rendering_style_custom", "").strip()
    ):
        errors.append("描画方式の自由記述を入力してください。")
    if (
        settings.get("script_tone_primary") == "custom"
        and not settings.get("script_tone_custom", "").strip()
    ):
        errors.append("脚本トーンの自由記述を入力してください。")
    cast = source_cast(project)
    character_kinds = {character_reference(name, project.get("characters") or [], cast)["kind"]
                       for name in panel.get("characters") or []}
    if "missing_profile" in character_kinds:
        errors.append("選択した主要人物の人物設定が未完了です。キャラクター画面で選択済みの人物だけ設定を完了してください。")
    if "ambiguous" in character_kinds:
        errors.append("人物の呼び方が複数の人物に一致しています。ネームで登録名・別名を使って区別してください。脇役の個別設定は不要です。")
    for character in project.get("characters") or []:
        url = character.get("reference_image_url")
        if url and (
            not str(url).startswith("/media/" + project["id"] + "/") or ".." in str(url)
        ):
            errors.append("人物参考画像はこの作品に保存した画像を指定してください。")
    if is_content_page(page) and (
        not isinstance(page.get("page_number"), int) or page["page_number"] < 1
    ):
        errors.append("本文のページ番号が不正です。")
    content_number = 0
    for candidate in project.get("storyboard") or []:
        if is_content_page(candidate):
            content_number += 1
        if candidate.get("id") == page.get("id"):
            expected = content_number if is_content_page(candidate) else 0
            if page.get("page_number") != expected:
                errors.append("本文は1から順番に番号を付けてください。")
            break
    size = (page.get("composition") or {}).get("size")
    if size and (
        len(size) != 2 or size[1] <= 0 or abs(size[0] / size[1] - 0.75) > 0.001
    ):
        errors.append("ページ設計を3:4の縦長にしてください。")
    return list(dict.fromkeys(errors))


def public_design(project: dict, page: dict, panel: dict, digest: str) -> dict:
    from .manga_documents import name_script_summary
    errors = audit_design(project, page, panel)
    page_design = json.loads(json.dumps(page))
    for entry in page_design.get("panels", []):
        entry.pop("generation_prompt", None)
    cover_plan = None
    if not is_content_page(page):
        prepared = cover_artwork_panel(page, panel)
        # 新しい設計に以前の画像を混ぜない。変更前の原画は保存したまま別項目で返す。
        prepared.pop("generation_prompt", None)
        prepared.pop("image_url", None)
        page_design["panels"] = [prepared]
        page_design["composition"] = {"size": [900, 1200], "policy": COVER_ARTWORK_POLICY}
        cover_plan = {
            "policy": COVER_ARTWORK_POLICY,
            "text": cover_text(project),
            "text_regions": cover_text_regions(str(page.get("page_kind"))),
            "geometry": prepared["geometry"],
            "shot_type": prepared.get("shot_type", ""),
            "background": prepared.get("background", ""),
            "action": prepared.get("action", ""),
            "expression": prepared.get("expression", ""),
        }
    cast = source_cast(project)
    references = [character_reference(name, project.get("characters") or [], cast)
                  for name in panel.get("characters") or []]
    return {
        "page_design": page_design,
        "cover_plan": cover_plan,
        "previous_artwork": ({"image_url": panel.get("image_url"), "revision": panel.get("revision", 0)}
                             if cover_plan and panel.get("image_url") else None),
        "design_hash": digest,
        "state": "DESIGN_DRAFT" if errors else "DESIGN_READY",
        "errors": errors,
        "page_number": page.get("page_number"),
        "target_id": panel["id"],
        "description": panel.get("description"),
        "characters": panel.get("characters", []),
        "anonymous_characters": anonymous_characters(panel, project.get("characters") or [], cast),
        "supporting_characters": [r["name"] for r in references if r["kind"] == "supporting"],
        "character_references": [{"name": r["name"], "kind": r["kind"], "life_stage": r["life_stage"],
                                  "reference_source": r["reference_source"],
                                  "registered_name": (r["character"] or {}).get("name")}
                                 for r in references],
        "unresolved_characters": unresolved_characters(panel, project.get("characters") or [], cast),
        "dialogue": panel.get("dialogue", []),
        "narration": panel.get("narration", []),
        "composition": page_design.get("composition"),
        "rendering_style": rendering_profile(project.get("settings") or {}),
        "script_tone": tone_parameters(project.get("settings") or {}),
        "settings": project.get("settings"),
        "name_script": name_script_summary(project),
        "event_boundary": {
            k: page.get(k)
            for k in (
                "allowed_events",
                "forbidden_until_later",
                "dialogue_scope",
                "start_state",
                "page_end_state",
                "carry_over",
            )
        },
    }
