"""API入力の検証と制作データの正規化。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .services.reading_order import (
    ALLOWED_LANGUAGES,
    LEGACY_DIRECTION_ALIASES,
    canonicalize_settings,
)


ALLOWED_STEPS = {"story", "knowledge", "analysis", "settings", "characters", "storyboard", "generate", "edit", "qa", "preview", "export"}
ALLOWED_DIRECTIONS = set(LEGACY_DIRECTION_ALIASES)
ALLOWED_COLOR_MODES = {"bw", "color"}
ALLOWED_STYLES = {"dynamic", "elegant", "cinematic", "comedy", "minimal", "webtoon"}
ALLOWED_PACING = {"fast", "balanced", "slow"}
ALLOWED_DIALOGUE_DENSITY = {"low", "medium", "high"}
ALLOWED_LAYOUTS = {"hero", "classic", "grid", "wide"}
ALLOWED_KNOWLEDGE_MODES = {"follow_latest", "pinned"}
ALLOWED_KNOWLEDGE_SCOPES = {
    "all",
    "story_analysis",
    "adaptation",
    "character",
    "storyboard",
    "page_layout",
    "panel_prompt",
    "dialogue",
    "image_generation",
    "quality_check",
    "export",
}
ALLOWED_KNOWLEDGE_CATEGORIES = {
    "style",
    "layout",
    "character",
    "genre",
    "dialogue",
    "world",
    "brand",
    "other",
}
ALLOWED_PROJECT_STATUSES = {
    "draft",
    "analysis_ready",
    "characters_ready",
    "storyboard_ready",
    "processing",
    "partially_failed",
    "completed",
}
ALLOWED_CROP_MODES = {"fit", "fill"}


class SettingsPayload(BaseModel):
    """漫画化設定。"""

    target_page_count: int = Field(default=8, ge=1, le=120)
    language: Optional[str] = None
    reading_direction: Optional[str] = None
    color_mode: str = "bw"
    visual_style: str = "cinematic"
    target_audience: str = Field(default="一般読者", max_length=80)
    pacing: str = "balanced"
    dialogue_density: str = "medium"

    @field_validator("language")
    @classmethod
    def valid_language(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_LANGUAGES:
            raise ValueError("languageが不正です。jaまたはenを指定してください")
        return value

    @field_validator("reading_direction")
    @classmethod
    def valid_direction(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_DIRECTIONS:
            raise ValueError("reading_directionが不正です")
        return value

    @model_validator(mode="after")
    def lock_language_direction(self) -> "SettingsPayload":
        """言語をSource of Truthにして、矛盾した方向を正規化する。"""

        explicitly_set = set(self.model_fields_set)
        canonical = canonicalize_settings(
            {"language": self.language, "reading_direction": self.reading_direction}
        )
        self.language = canonical["language"]
        self.reading_direction = canonical["reading_direction"]
        # 部分更新では、言語か旧方向のどちらかが指定されたときだけ、正規化した
        # 両方を送信値として扱う。何も指定されていない場合は既存設定を維持する。
        if explicitly_set & {"language", "reading_direction"}:
            self.model_fields_set.update({"language", "reading_direction"})
        else:
            self.model_fields_set.difference_update({"language", "reading_direction"})
        return self

    @field_validator("color_mode")
    @classmethod
    def valid_color_mode(cls, value: str) -> str:
        if value not in ALLOWED_COLOR_MODES:
            raise ValueError("color_modeが不正です")
        return value

    @field_validator("visual_style")
    @classmethod
    def valid_style(cls, value: str) -> str:
        if value not in ALLOWED_STYLES:
            raise ValueError("visual_styleが不正です")
        return value

    @field_validator("pacing")
    @classmethod
    def valid_pacing(cls, value: str) -> str:
        if value not in ALLOWED_PACING:
            raise ValueError("pacingが不正です")
        return value

    @field_validator("dialogue_density")
    @classmethod
    def valid_dialogue_density(cls, value: str) -> str:
        if value not in ALLOWED_DIALOGUE_DENSITY:
            raise ValueError("dialogue_densityが不正です")
        return value


class AIModelSettingsPayload(BaseModel):
    """グローバルまたはProject固有のAIモデル設定。"""

    model_config = ConfigDict(extra="forbid")

    preset: Optional[str] = Field(default=None, max_length=40)
    story_analysis_model: Optional[str] = Field(default=None, max_length=80)
    adaptation_model: Optional[str] = Field(default=None, max_length=80)
    character_model: Optional[str] = Field(default=None, max_length=80)
    storyboard_model: Optional[str] = Field(default=None, max_length=80)
    qa_model: Optional[str] = Field(default=None, max_length=80)
    panel_prompt_model: Optional[str] = Field(default=None, max_length=80)
    image_model: Optional[str] = Field(default=None, max_length=80)
    reasoning_effort: Optional[str] = Field(default=None, max_length=20)


class ProjectPatch(BaseModel):
    """Projectの部分更新。"""

    title: Optional[str] = Field(default=None, max_length=120)
    original_text: Optional[str] = Field(default=None, max_length=500_000)
    current_step: Optional[str] = None
    settings: Optional[SettingsPayload] = None
    analysis: Optional[Dict[str, Any]] = None
    characters: Optional[List[Dict[str, Any]]] = None
    storyboard: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None

    @field_validator("current_step")
    @classmethod
    def valid_step(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_STEPS:
            raise ValueError("current_stepが不正です")
        return value

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_PROJECT_STATUSES:
            raise ValueError("statusが不正です")
        return value


class SettingsRecommendationRequest(BaseModel):
    """漫画化設定の推奨取得。force=Trueはユーザーが明示した再提案だけに使う。"""

    force: bool = False


class GenerateRequest(BaseModel):
    """コマ生成リクエスト。panel_idsが空なら未生成の全コマを対象にする。"""

    panel_ids: List[str] = Field(default_factory=list, max_length=128)
    retry_failed: bool = False
    force: bool = False


class PanelPatch(BaseModel):
    """コマの編集項目。"""

    description: Optional[str] = Field(default=None, max_length=2_000)
    shot_type: Optional[str] = Field(default=None, max_length=80)
    characters: Optional[List[str]] = Field(default=None, max_length=16)
    action: Optional[str] = Field(default=None, max_length=500)
    expression: Optional[str] = Field(default=None, max_length=240)
    background: Optional[str] = Field(default=None, max_length=500)
    dialogue: Optional[List[str]] = Field(default=None, max_length=16)
    narration: Optional[List[str]] = Field(default=None, max_length=16)
    sfx: Optional[List[str]] = Field(default=None, max_length=16)
    generation_prompt: Optional[str] = Field(default=None, max_length=4_000)
    crop_mode: Optional[str] = Field(default=None, max_length=20)

    @field_validator("characters", "dialogue", "narration", "sfx")
    @classmethod
    def normalize_list_values(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return [str(item).strip()[:500] for item in value if str(item).strip()][:16]

    @field_validator("crop_mode")
    @classmethod
    def valid_crop_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_CROP_MODES:
            raise ValueError("crop_modeが不正です")
        return value


class ExportRequest(BaseModel):
    """書き出し形式。"""

    format: str

    @field_validator("format")
    @classmethod
    def valid_format(cls, value: str) -> str:
        if value not in {"pdf", "zip"}:
            raise ValueError("formatはpdfまたはzipを指定してください")
        return value


class KnowledgeSelection(BaseModel):
    """Projectで利用するKnowledge Documentの設定。"""

    knowledge_document_id: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=1000)
    mode: str = "follow_latest"
    selected_version_id: Optional[str] = Field(default=None, max_length=80)
    scope: List[str] = Field(default_factory=lambda: ["all"], max_length=16)

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in ALLOWED_KNOWLEDGE_MODES:
            raise ValueError("KnowledgeのVersionモードが不正です")
        return value

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value: List[str]) -> List[str]:
        normalized = [str(item) for item in value if str(item) in ALLOWED_KNOWLEDGE_SCOPES]
        return normalized or ["all"]


class ProjectKnowledgePatch(BaseModel):
    """Project Knowledge選択の置き換え。"""

    selections: List[KnowledgeSelection] = Field(default_factory=list, max_length=64)


class KnowledgeCreatePayload(BaseModel):
    """Knowledge Document作成時のメタデータ。"""

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    category: str = "other"

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        clean = " ".join(value.split()).strip()
        if not clean:
            raise ValueError("Knowledgeタイトルを入力してください")
        return clean

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        if value not in ALLOWED_KNOWLEDGE_CATEGORIES:
            raise ValueError("Knowledgeカテゴリが不正です")
        return value


class KnowledgeMetadataPatch(BaseModel):
    """Knowledge Documentのメタデータ更新。"""

    title: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2_000)
    category: Optional[str] = Field(default=None, max_length=40)
    active: Optional[bool] = None
    archived: Optional[bool] = None

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ALLOWED_KNOWLEDGE_CATEGORIES:
            raise ValueError("Knowledgeカテゴリが不正です")
        return value


def validate_settings(value: Any) -> Dict[str, Any]:
    """辞書をSettingsPayloadで検証してJSON用辞書へ戻す。"""

    if isinstance(value, SettingsPayload):
        return value.model_dump()
    return SettingsPayload.model_validate(value or {}).model_dump()


def validate_storyboard(value: Any) -> Tuple[bool, str]:
    """最低限のページ・コマ構造を検証する。"""

    if not isinstance(value, list):
        return False, "storyboardは配列で指定してください"
    for page in value:
        if not isinstance(page, dict) or not page.get("id"):
            return False, "ページIDがありません"
        if page.get("layout", "classic") not in ALLOWED_LAYOUTS:
            return False, "ページレイアウトが不正です"
        panels = page.get("panels", [])
        if not isinstance(panels, list):
            return False, "panelsは配列で指定してください"
        for panel in panels:
            if not isinstance(panel, dict) or not panel.get("id"):
                return False, "コマIDがありません"
    return True, ""


def normalize_analysis(value: Any) -> Dict[str, Any]:
    """AI出力をUIで扱いやすい形にそろえる。"""

    raw = value if isinstance(value, dict) else {}
    list_keys = [
        "main_characters",
        "supporting_characters",
        "locations",
        "major_events",
        "story_beats",
        "conflicts",
        "important_objects",
    ]
    normalized: Dict[str, Any] = {
        "title": str(raw.get("title", ""))[:200],
        "synopsis": str(raw.get("synopsis", ""))[:4_000],
        "genre": str(raw.get("genre", ""))[:120],
        "tone": str(raw.get("tone", ""))[:240],
        "world_setting": str(raw.get("world_setting", ""))[:2_000],
        "climax": str(raw.get("climax", ""))[:2_000],
        "ending": str(raw.get("ending", ""))[:2_000],
        "knowledge_refs": normalize_knowledge_refs(raw.get("knowledge_refs", [])),
    }
    for key in list_keys:
        source = raw.get(key, [])
        if isinstance(source, str):
            source = [line.strip() for line in source.splitlines() if line.strip()]
        normalized[key] = [str(item)[:500] for item in source if str(item).strip()][:32] if isinstance(source, list) else []
    return normalized


def normalize_characters(value: Any) -> List[Dict[str, Any]]:
    """外部AIの人物配列を編集画面用の必須項目へそろえる。"""

    if not isinstance(value, list):
        return []
    fields = [
        "role",
        "age_range",
        "personality",
        "appearance",
        "hairstyle",
        "hair_color",
        "eye_characteristics",
        "body_type",
        "clothing",
        "accessories",
        "distinguishing_features",
        "expressions",
        "relationship_notes",
        "visual_prompt",
        "negative_constraints",
        "reference_image_url",
    ]
    normalized: List[Dict[str, Any]] = []
    for item in value[:64]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "人物"))[:80].strip() or "人物"
        character = {"id": str(item.get("id") or uuid.uuid4()), "name": name}
        for field in fields:
            character[field] = str(item.get(field, ""))[:2_000] if item.get(field) is not None else None
        character["knowledge_refs"] = normalize_knowledge_refs(item.get("knowledge_refs", []))
        normalized.append(character)
    return normalized


def normalize_knowledge_refs(value: Any) -> List[Dict[str, Any]]:
    """AI/検索処理のKnowledge参照メタデータを安全な形へそろえる。"""

    if not isinstance(value, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in value[:16]:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id", ""))[:80]
        version_id = str(item.get("version_id", ""))[:80]
        if not document_id or not version_id:
            continue
        chunk_ids = item.get("chunk_ids", [])
        headings = item.get("headings", [])
        normalized.append(
            {
                "document_id": document_id,
                "version_id": version_id,
                "title": str(item.get("title", "Knowledge"))[:120],
                "version_number": max(0, int(item.get("version_number", 0))) if str(item.get("version_number", 0)).isdigit() else 0,
                "chunk_ids": [str(entry)[:80] for entry in chunk_ids[:16] if str(entry).strip()] if isinstance(chunk_ids, list) else [],
                "headings": [str(entry)[:200] for entry in headings[:16] if str(entry).strip()] if isinstance(headings, list) else [],
            }
        )
    return normalized


def normalize_generation_metadata(value: Any) -> Optional[Dict[str, Any]]:
    """モデル生成履歴のうち、UIへ返してよい値だけを保持する。"""

    if not isinstance(value, dict):
        return None
    allowed = {
        "task",
        "target_id",
        "requested_model",
        "actual_model",
        "fallback",
        "reasoning_effort",
        "provider",
        "created_at",
    }
    normalized: Dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, bool):
            normalized[key] = item
        elif item is not None:
            normalized[key] = str(item)[:160]
    if "requested_model" not in normalized or "actual_model" not in normalized:
        return None
    normalized["fallback"] = bool(value.get("fallback", False))
    return normalized


def normalize_storyboard(value: Any) -> List[Dict[str, Any]]:
    """外部AIのネームをページ・コマの編集可能な形へ正規化する。"""

    if not isinstance(value, list):
        return []
    list_fields = {"characters", "dialogue", "narration", "sfx"}
    normalized_pages: List[Dict[str, Any]] = []
    for page_index, item in enumerate(value[:120]):
        if not isinstance(item, dict):
            continue
        layout = str(item.get("layout", "classic"))
        if layout not in ALLOWED_LAYOUTS:
            layout = "classic"
        panels: List[Dict[str, Any]] = []
        raw_panels = item.get("panels", [])
        if not isinstance(raw_panels, list):
            raw_panels = []
        for panel_index, raw_panel in enumerate(raw_panels[:24]):
            if not isinstance(raw_panel, dict):
                continue
            crop_mode = str(raw_panel.get("crop_mode", "fit"))
            if crop_mode not in ALLOWED_CROP_MODES:
                crop_mode = "fit"
            panel: Dict[str, Any] = {
                "id": str(raw_panel.get("id") or f"panel-{page_index + 1}-{panel_index + 1}-{uuid.uuid4().hex[:6]}"),
                "order": panel_index + 1,
                "description": str(raw_panel.get("description", ""))[:2_000],
                "shot_type": str(raw_panel.get("shot_type", ""))[:80],
                "action": str(raw_panel.get("action", ""))[:500],
                "expression": str(raw_panel.get("expression", ""))[:240],
                "background": str(raw_panel.get("background", ""))[:500],
                "generation_prompt": str(raw_panel.get("generation_prompt", ""))[:4_000],
                "prompt_source": raw_panel.get("prompt_source") if raw_panel.get("prompt_source") in {"generated", "user"} else "generated",
                "image_url": raw_panel.get("image_url") if isinstance(raw_panel.get("image_url"), str) else None,
                "generation_status": raw_panel.get("generation_status") if raw_panel.get("generation_status") in {"not_started", "queued", "processing", "completed", "failed"} else "not_started",
                "generation_error": raw_panel.get("generation_error") if isinstance(raw_panel.get("generation_error"), str) else None,
                "revision": max(0, int(raw_panel.get("revision", 0))) if str(raw_panel.get("revision", 0)).isdigit() else 0,
                "crop_mode": crop_mode,
                "knowledge_refs": normalize_knowledge_refs(raw_panel.get("knowledge_refs", [])),
                "generation_metadata": normalize_generation_metadata(raw_panel.get("generation_metadata")),
            }
            for field in list_fields:
                source = raw_panel.get(field, [])
                if isinstance(source, str):
                    source = [line.strip() for line in source.splitlines() if line.strip()]
                panel[field] = [str(entry)[:500] for entry in source[:16] if str(entry).strip()] if isinstance(source, list) else []
            # 配列自体が読順のSource of Truth。テキストは翻訳せず順序メタデータだけ再計算する。
            panel["bubble_order"] = list(range(1, len(panel["dialogue"]) + 1))
            panel["narration_order"] = list(range(1, len(panel["narration"]) + 1))
            panel["sfx_order"] = list(range(1, len(panel["sfx"]) + 1))
            panels.append(panel)
        normalized_pages.append(
            {
                "id": str(item.get("id") or f"page-{page_index + 1}-{uuid.uuid4().hex[:6]}"),
                "page_number": page_index + 1,
                "title": str(item.get("title", f"ページ {page_index + 1}"))[:200],
                "layout": layout,
                "panels": panels,
            }
        )
    return normalized_pages
