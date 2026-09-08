"""AIモデルの能力、設定解決、可用性確認を一元管理する。

ブラウザから届くモデルIDはこのモジュールのallowlistを通過したものだけを
利用する。OpenAI APIキーやレスポンス本文はこの層から返さない。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from ..config import get_settings
from .openai_client import OpenAIRequestError, request_json


TEXT_MODEL_TYPE = "text"
IMAGE_MODEL_TYPE = "image"
AUTO_MODEL = "auto"
AUTO_REASONING = "auto"


@dataclass(frozen=True)
class ModelCapability:
    """UI、入力検証、API dispatchで共有するモデル能力。"""

    id: str
    display_name: str
    provider: str
    type: str
    description: str
    quality_label: str
    supports_responses: bool = False
    supports_structured_outputs: bool = False
    supports_reasoning: bool = False
    supported_reasoning_levels: tuple[str, ...] = ()
    supports_image_generation: bool = False
    fallback_model: Optional[str] = None
    enabled: bool = True


MODEL_REGISTRY: Dict[str, ModelCapability] = {
    "gpt-6-astra": ModelCapability(
        id="gpt-6-astra",
        display_name="GPT-6 Astra",
        provider="openai",
        type=TEXT_MODEL_TYPE,
        description="最高性能。複雑な物語解析・漫画化・ネーム向け（アカウントにより利用可否が異なります）。",
        quality_label="最高能力",
        supports_responses=True,
        supports_structured_outputs=True,
        supports_reasoning=True,
        supported_reasoning_levels=("low", "medium", "high", "xhigh", "max"),
        fallback_model="gpt-5.6-sol",
    ),
    "gpt-5.6-sol": ModelCapability(
        id="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        provider="openai",
        type=TEXT_MODEL_TYPE,
        description="高品質。複雑な生成と一貫性を重視する制作向け。",
        quality_label="高品質",
        supports_responses=True,
        supports_structured_outputs=True,
        supports_reasoning=True,
        supported_reasoning_levels=("none", "low", "medium", "high", "xhigh", "max"),
    ),
    "gpt-5.6-terra": ModelCapability(
        id="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        provider="openai",
        type=TEXT_MODEL_TYPE,
        description="品質とコストのバランスに優れた標準モデル。",
        quality_label="バランス",
        supports_responses=True,
        supports_structured_outputs=True,
        supports_reasoning=True,
        supported_reasoning_levels=("none", "low", "medium", "high", "xhigh", "max"),
    ),
    "gpt-5.6-luna": ModelCapability(
        id="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        provider="openai",
        type=TEXT_MODEL_TYPE,
        description="コストを抑えたい反復処理・大量処理向け。",
        quality_label="エコノミー",
        supports_responses=True,
        supports_structured_outputs=True,
        supports_reasoning=True,
        supported_reasoning_levels=("none", "low", "medium", "high", "xhigh", "max"),
    ),
    "gpt-image-2": ModelCapability(
        id="gpt-image-2",
        display_name="GPT-Image-2",
        provider="openai",
        type=IMAGE_MODEL_TYPE,
        description="コマ画像生成専用。テキストモデルとは別に設定します。",
        quality_label="画像生成",
        supports_image_generation=True,
    ),
}

TEXT_MODEL_IDS = tuple(
    model_id for model_id, capability in MODEL_REGISTRY.items() if capability.type == TEXT_MODEL_TYPE
)
IMAGE_MODEL_IDS = tuple(
    model_id for model_id, capability in MODEL_REGISTRY.items() if capability.type == IMAGE_MODEL_TYPE
)
SUPPORTED_PRESETS = ("auto", "highest_quality", "balanced", "economy")
SUPPORTED_REASONING_LEVELS = (AUTO_REASONING, "low", "medium", "high")
MODEL_TASKS = (
    "story_analysis",
    "adaptation",
    "settings_recommendation",
    "character",
    "storyboard",
    "qa",
    "panel_prompt",
)
TASK_LABELS = {
    "story_analysis": "Story Analysis",
    "adaptation": "Manga Adaptation",
    "settings_recommendation": "漫画化設定の推奨",
    "character": "Character Bible",
    "storyboard": "Storyboard",
    "qa": "Knowledge-aware QA",
    "panel_prompt": "Panel Prompt",
    "image": "Image Generation",
}

PRESET_LABELS = {
    "auto": "Auto",
    "highest_quality": "Highest Quality",
    "balanced": "Balanced",
    "economy": "Economy",
}

PRESET_DESCRIPTIONS = {
    "auto": "工程ごとに品質とコストのバランスを自動選択します。",
    "highest_quality": "Astraを優先し、利用できない場合はSolへ切り替えます。",
    "balanced": "日常的な制作に向いた品質とコストの設定です。",
    "economy": "反復処理や大量処理のコストを抑えます。",
}

PRESET_POLICIES: Dict[str, Dict[str, str]] = {
    "auto": {
        "story_analysis": "gpt-5.6-terra",
        "adaptation": "gpt-5.6-sol",
        "settings_recommendation": "gpt-5.6-terra",
        "character": "gpt-5.6-sol",
        "storyboard": "gpt-5.6-sol",
        "qa": "gpt-5.6-luna",
        "panel_prompt": "gpt-5.6-terra",
        "image": "gpt-image-2",
    },
    "highest_quality": {
        "story_analysis": "gpt-6-astra",
        "adaptation": "gpt-6-astra",
        "settings_recommendation": "gpt-6-astra",
        "character": "gpt-5.6-sol",
        "storyboard": "gpt-6-astra",
        "qa": "gpt-5.6-terra",
        "panel_prompt": "gpt-5.6-sol",
        "image": "gpt-image-2",
    },
    "balanced": {
        "story_analysis": "gpt-5.6-terra",
        "adaptation": "gpt-5.6-terra",
        "settings_recommendation": "gpt-5.6-terra",
        "character": "gpt-5.6-terra",
        "storyboard": "gpt-5.6-terra",
        "qa": "gpt-5.6-luna",
        "panel_prompt": "gpt-5.6-terra",
        "image": "gpt-image-2",
    },
    "economy": {
        "story_analysis": "gpt-5.6-luna",
        "adaptation": "gpt-5.6-luna",
        "settings_recommendation": "gpt-5.6-luna",
        "character": "gpt-5.6-luna",
        "storyboard": "gpt-5.6-luna",
        "qa": "gpt-5.6-luna",
        "panel_prompt": "gpt-5.6-luna",
        "image": "gpt-image-2",
    },
}

DEFAULT_AI_MODEL_SETTINGS: Dict[str, Any] = {
    "preset": "auto",
    **{f"{task}_model": AUTO_MODEL for task in MODEL_TASKS},
    "image_model": "gpt-image-2",
    "reasoning_effort": AUTO_REASONING,
}


@dataclass(frozen=True)
class ModelAvailability:
    """モデル一覧APIの結果を安全なUI状態へ変換したもの。"""

    status: str
    checked_at: Optional[str] = None


_AVAILABILITY_CACHE: Dict[str, tuple[float, Dict[str, ModelAvailability]]] = {}
AVAILABILITY_CACHE_SECONDS = 300.0


def capability(model_id: str) -> Optional[ModelCapability]:
    return MODEL_REGISTRY.get(str(model_id or ""))


def is_allowed_text_model(model_id: str, *, allow_auto: bool = False) -> bool:
    return str(model_id or "") in TEXT_MODEL_IDS or (allow_auto and model_id == AUTO_MODEL)


def is_allowed_image_model(model_id: str) -> bool:
    return str(model_id or "") in IMAGE_MODEL_IDS


def validate_model_settings(value: Mapping[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    """モデル設定をサーバーallowlistで検証し、保存可能な辞書へそろえる。"""

    if not isinstance(value, Mapping):
        raise ValueError("AIモデル設定の形式が不正です")
    allowed_keys = {"preset", "reasoning_effort"}
    allowed_keys.update(f"{task}_model" for task in MODEL_TASKS)
    allowed_keys.add("image_model")
    unknown = set(value) - allowed_keys
    if unknown:
        raise ValueError("未対応のAIモデル設定が含まれています")

    result: Dict[str, Any] = {}
    if "preset" in value and value.get("preset") is not None:
        preset = str(value["preset"])
        if preset not in SUPPORTED_PRESETS:
            raise ValueError("AIモデルプリセットが不正です")
        result["preset"] = preset
    if "reasoning_effort" in value and value.get("reasoning_effort") is not None:
        reasoning = str(value["reasoning_effort"])
        if reasoning not in SUPPORTED_REASONING_LEVELS:
            raise ValueError("Reasoning設定が不正です")
        result["reasoning_effort"] = reasoning
    for task in MODEL_TASKS:
        key = f"{task}_model"
        if key not in value or value.get(key) is None:
            continue
        model_id = str(value[key]).strip()
        if not is_allowed_text_model(model_id, allow_auto=True):
            raise ValueError(f"{TASK_LABELS[task]}のモデルが未対応です")
        result[key] = model_id
    if "image_model" in value and value.get("image_model") is not None:
        image_model = str(value["image_model"]).strip()
        if not is_allowed_image_model(image_model):
            raise ValueError("画像生成モデルが未対応です")
        result["image_model"] = image_model
    if not partial:
        merged = dict(DEFAULT_AI_MODEL_SETTINGS)
        merged.update(result)
        return merged
    return result


def _legacy_settings(legacy_text_model: str, legacy_image_model: str) -> Dict[str, Any]:
    """モデル設定未導入の既存ユーザーが従来のモデルを継続利用するための値。"""

    text_model = legacy_text_model if legacy_text_model in TEXT_MODEL_IDS else "gpt-5.6-luna"
    image_model = legacy_image_model if legacy_image_model in IMAGE_MODEL_IDS else "gpt-image-2"
    return {
        "preset": "auto",
        **{f"{task}_model": text_model for task in MODEL_TASKS},
        "image_model": image_model,
        "reasoning_effort": AUTO_REASONING,
    }


def resolve_model_settings(
    global_settings: Optional[Mapping[str, Any]] = None,
    project_settings: Optional[Mapping[str, Any]] = None,
    *,
    legacy_text_model: str = "gpt-5.6-luna",
    legacy_image_model: str = "gpt-image-2",
) -> Dict[str, Any]:
    """グローバル → Project override → preset → taskの順で実効値を解決する。"""

    if global_settings is None:
        base = _legacy_settings(legacy_text_model, legacy_image_model)
    else:
        base = dict(DEFAULT_AI_MODEL_SETTINGS)
        try:
            base.update(validate_model_settings(global_settings, partial=True))
        except ValueError:
            # 壊れた保存値は既定プリセットへ戻し、Projectを開けなくしない。
            base = dict(DEFAULT_AI_MODEL_SETTINGS)
    if project_settings:
        try:
            base.update(validate_model_settings(project_settings, partial=True))
        except ValueError:
            # Project overrideだけを無効化し、親の設定は保持する。
            pass
    preset = base.get("preset") if base.get("preset") in PRESET_POLICIES else "auto"
    policy = PRESET_POLICIES[preset]
    resolved: Dict[str, Any] = {
        "preset": preset,
        "reasoning_effort": base.get("reasoning_effort")
        if base.get("reasoning_effort") in SUPPORTED_REASONING_LEVELS
        else AUTO_REASONING,
    }
    for task in MODEL_TASKS:
        value = base.get(f"{task}_model")
        resolved[f"{task}_model"] = value if is_allowed_text_model(value or "") else policy[task]
        if value == AUTO_MODEL or not is_allowed_text_model(value or ""):
            resolved[f"{task}_model"] = policy[task]
    image_value = base.get("image_model")
    resolved["image_model"] = image_value if is_allowed_image_model(image_value or "") else "gpt-image-2"
    return resolved


def model_for_task(settings: Mapping[str, Any], task: str) -> str:
    """解決済み設定からテキストまたは画像モデルを取り出す。"""

    key = "image_model" if task in {"image", "image_generation"} else f"{task}_model"
    value = str(settings.get(key, ""))
    if key == "image_model":
        return value if is_allowed_image_model(value) else "gpt-image-2"
    if is_allowed_text_model(value):
        return value
    return PRESET_POLICIES["auto"].get(task, "gpt-5.6-luna")


def reasoning_for_model(settings: Mapping[str, Any], model_id: str) -> str:
    """選択モデルで使えるreasoningを返し、Autoや未対応値は送信しない。"""

    requested = str(settings.get("reasoning_effort", AUTO_REASONING))
    if requested == AUTO_REASONING:
        return AUTO_REASONING
    model = capability(model_id)
    if not model or not model.supports_reasoning or requested not in model.supported_reasoning_levels:
        return AUTO_REASONING
    return requested


def model_registry_view() -> Dict[str, Any]:
    """秘密情報を含まないUI用レジストリ。"""

    options = []
    for model in MODEL_REGISTRY.values():
        if not model.enabled:
            continue
        data = asdict(model)
        data["supported_reasoning_levels"] = list(model.supported_reasoning_levels)
        # UIでは過剰な内部能力値を使わないが、検証済みの推奨選択肢だけを渡す。
        data["ui_reasoning_levels"] = [
            level for level in ("low", "medium", "high") if level in model.supported_reasoning_levels
        ]
        options.append(data)
    return {
        "text_models": [item for item in options if item["type"] == TEXT_MODEL_TYPE],
        "image_models": [item for item in options if item["type"] == IMAGE_MODEL_TYPE],
        "tasks": [{"id": task, "label": TASK_LABELS[task]} for task in MODEL_TASKS],
        "presets": [
            {"id": preset, "label": PRESET_LABELS[preset], "description": PRESET_DESCRIPTIONS[preset]}
            for preset in SUPPORTED_PRESETS
        ],
        "reasoning_levels": [
            {"id": AUTO_REASONING, "label": "Auto"},
            {"id": "low", "label": "Low"},
            {"id": "medium", "label": "Medium"},
            {"id": "high", "label": "High"},
        ],
    }


def _cache_key(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()


def get_model_availability(*, force: bool = False) -> Dict[str, Any]:
    """GET /v1/modelsで低コストにモデル可用性を確認する。

    モデル一覧に存在してもProject権限がない場合があるため、最終判定は
    実生成時のbounded fallbackでも行う。失敗時はunknownではなく一時障害として
    表示し、設定画面自体は利用できるようにする。
    """

    runtime = get_settings()
    checked_at = datetime.now(timezone.utc).isoformat()
    if not runtime.openai_api_key or runtime.ai_provider != "openai":
        return {model_id: {"status": "not_checked", "checked_at": None} for model_id in TEXT_MODEL_IDS}
    key = _cache_key(runtime.openai_api_key)
    cached = _AVAILABILITY_CACHE.get(key)
    now = time.monotonic()
    if cached and not force and now - cached[0] < AVAILABILITY_CACHE_SECONDS:
        return {model_id: asdict(status) for model_id, status in cached[1].items()}
    try:
        body = request_json(
            getattr(runtime, "openai_models_url", "https://api.openai.com/v1/models"),
            api_key=runtime.openai_api_key,
            payload=None,
            timeout=min(getattr(runtime, "openai_timeout_seconds", 90.0), 15.0),
            max_retries=0,
        )
        raw_models = body.get("data", [])
        model_ids = {
            str(item.get("id"))
            for item in raw_models
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        result = {
            model_id: ModelAvailability(
                status="available" if model_id in model_ids else "unavailable",
                checked_at=checked_at,
            )
            for model_id in TEXT_MODEL_IDS
        }
    except (OpenAIRequestError, TypeError, ValueError, AttributeError):
        result = {
            model_id: ModelAvailability(status="temporarily_unavailable", checked_at=checked_at)
            for model_id in TEXT_MODEL_IDS
        }
    _AVAILABILITY_CACHE[key] = (now, result)
    return {model_id: asdict(status) for model_id, status in result.items()}


def new_generation_metadata(
    *,
    task: str,
    requested_model: str,
    actual_model: str,
    fallback: bool = False,
    reasoning_effort: str = AUTO_REASONING,
    provider: str = "openai",
) -> Dict[str, Any]:
    """生成履歴へ保存する秘密を含まないメタデータを作る。"""

    return {
        "task": task,
        "requested_model": requested_model,
        "actual_model": actual_model,
        "fallback": bool(fallback),
        "reasoning_effort": reasoning_effort,
        "provider": provider,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
