"""生成直前の設計と承認。稼働状態・生成結果を設計ハッシュへ混ぜない。"""

from __future__ import annotations
import hashlib
import json
from .architect import rendering_profile, tone_parameters, event_issues
from .reading_order import canonicalize_stored_settings

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
        "project_id": project["id"],
        "page": stable(page),
        "target_id": panel["id"],
        "characters": stable(project.get("characters") or []),
        "settings": project.get("settings") or {},
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


def audit_design(project: dict, page: dict, panel: dict) -> list[str]:
    settings = project.get("settings") or {}
    errors = event_issues(page)
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
        settings.get("rendering_style_id") == "STYLE-012"
        and settings.get("color_mode") == "color"
    ):
        errors.append("日本漫画モノクロには白黒を選んでください。")
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
    names = {c.get("name") for c in project.get("characters") or []}
    if any(name not in names for name in panel.get("characters") or []):
        errors.append("登場人物をキャラクター設定へ登録してください。")
    for character in project.get("characters") or []:
        url = character.get("reference_image_url")
        if url and (
            not str(url).startswith("/media/" + project["id"] + "/") or ".." in str(url)
        ):
            errors.append("人物参考画像はこの作品に保存した画像を指定してください。")
    if page.get("page_kind") != "cover" and (
        not isinstance(page.get("page_number"), int) or page["page_number"] < 1
    ):
        errors.append("本文のページ番号が不正です。")
    content_number = 0
    for candidate in project.get("storyboard") or []:
        if candidate.get("page_kind") != "cover":
            content_number += 1
        if candidate.get("id") == page.get("id"):
            expected = 0 if candidate.get("page_kind") == "cover" else content_number
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
    errors = audit_design(project, page, panel)
    page_design = json.loads(json.dumps(page))
    for entry in page_design.get("panels", []):
        entry.pop("generation_prompt", None)
    return {
        "page_design": page_design,
        "design_hash": digest,
        "state": "DESIGN_DRAFT" if errors else "DESIGN_READY",
        "errors": errors,
        "page_number": page.get("page_number"),
        "target_id": panel["id"],
        "description": panel.get("description"),
        "characters": panel.get("characters", []),
        "dialogue": panel.get("dialogue", []),
        "narration": panel.get("narration", []),
        "composition": page.get("composition"),
        "rendering_style": rendering_profile(project.get("settings") or {}),
        "script_tone": tone_parameters(project.get("settings") or {}),
        "settings": project.get("settings"),
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
