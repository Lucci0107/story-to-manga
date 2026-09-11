"""デモ用パネルアートの生成と保存。

画像API未接続時も、生成状態・再試行・吹き出し合成まで検証できるように、
作品の設定を反映した軽量なPNGアートを生成する。外部画像APIは同じ境界へ接続できる。
"""

from __future__ import annotations

import base64
import binascii
import json
from io import BytesIO
import re
from pathlib import PurePosixPath
from typing import Any, Dict
from urllib.parse import urlparse

from PIL import Image, ImageColor, ImageDraw

from ..config import get_settings
from .artwork_geometry import artwork_generation_size, generation_canvas_zones
from .openai_client import OpenAIRequestError, request_bytes, request_json
from .model_registry import is_allowed_image_model, model_for_task, new_generation_metadata
from .storage import StorageError, StorageService, get_storage


class ArtworkGenerationError(RuntimeError):
    """画像生成または画像保存に失敗した。"""


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return clean or "panel"


def _palette(panel: Dict[str, Any], settings: Dict[str, Any]) -> tuple[str, str, str]:
    if settings.get("color_mode") == "color":
        backgrounds = [("#dfe8e4", "#1f4b46", "#e66d45"), ("#e7e0d3", "#4e3a2c", "#d96f4f")]
        stable_seed = sum(ord(char) for char in str(panel.get("id", "panel")))
        return backgrounds[stable_seed % len(backgrounds)]
    return "#e6e5df", "#2e302f", "#e66d45"


def _blend(first: tuple[int, int, int], second: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """2色を混ぜ、デモアートの背景階調を作る。"""

    ratio = max(0.0, min(1.0, amount))
    return tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(first, second))


def render_panel_image(panel: Dict[str, Any], settings: Dict[str, Any]) -> Image.Image:
    """文字を描かず、後工程の吹き出し合成に向いたデモアートを返す。"""

    bg, ink, accent = _palette(panel, settings)
    bg_rgb = ImageColor.getrgb(bg)
    ink_rgb = ImageColor.getrgb(ink)
    accent_rgb = ImageColor.getrgb(accent)
    width, height = 1200, 900
    image = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        draw.line((0, y, width, y), fill=_blend(bg_rgb, accent_rgb, 0.04 * y / height))

    pattern_color = _blend(bg_rgb, ink_rgb, 0.13)
    for y in range(16, height, 18):
        for x in range(16, width, 18):
            draw.ellipse((x, y, x + 2, y + 2), fill=pattern_color)

    seed = sum(ord(char) for char in str(panel.get("id", "panel"))) % 100
    horizon = 480 + (seed % 90)
    figure_x = 420 + (seed % 260)
    close = "寄り" in str(panel.get("shot_type", "")) or "アップ" in str(panel.get("shot_type", ""))
    radius = 210 if close else 132
    face_y = 340 if close else 385
    shadow = _blend(bg_rgb, ink_rgb, 0.18)
    quiet_ink = _blend(bg_rgb, ink_rgb, 0.28)

    draw.polygon([(0, horizon), (220, horizon - 45), (440, horizon), (880, horizon - 14), (1200, horizon + 20), (1200, height), (0, height)], fill=quiet_ink)
    draw.line((0, 160, 320, 90, 590, 175, 890, 66, 1200, 145), fill=_blend(bg_rgb, ink_rgb, 0.24), width=8, joint="curve")
    draw.line((90, 680, 240, 610, 370, 672, 500, 585, 690, 680, 840, 560, 1110, 675), fill=_blend(bg_rgb, ink_rgb, 0.34), width=5, joint="curve")

    draw.ellipse((figure_x - radius, face_y + 180, figure_x + radius, face_y + 324), fill=shadow)
    draw.polygon(
        [
            (figure_x - radius + 25, face_y + 180),
            (figure_x, face_y + 104),
            (figure_x + radius - 10, face_y + 184),
            (figure_x + radius - 30, face_y + 470),
            (figure_x - radius + 28, face_y + 470),
        ],
        fill=_blend(ink_rgb, bg_rgb, 0.14),
    )
    draw.ellipse((figure_x - radius, face_y - radius, figure_x + radius, face_y + radius), fill=bg_rgb, outline=ink_rgb, width=10)
    draw.polygon(
        [
            (figure_x - radius + 12, face_y - 14),
            (figure_x - radius + 12, face_y - radius - 40),
            (figure_x, face_y - radius + 50),
            (figure_x + radius - 18, face_y - radius - 20),
            (figure_x + radius - 10, face_y + 5),
            (figure_x + radius - 60, face_y - radius + 15),
            (figure_x, face_y - radius + 36),
            (figure_x - radius + 55, face_y - radius + 16),
        ],
        fill=ink_rgb,
    )
    draw.line((figure_x - radius + 55, face_y + 26, figure_x - radius + 10, face_y + 8, figure_x - radius + 12, face_y + 45), fill=ink_rgb, width=12, joint="curve")
    draw.line((figure_x + radius - 70, face_y + 38, figure_x + radius - 18, face_y + 10, figure_x + radius - 8, face_y + 62), fill=ink_rgb, width=12, joint="curve")
    eye_offset = max(24, radius // 3)
    draw.ellipse((figure_x - eye_offset - 9, face_y + 6, figure_x - eye_offset + 9, face_y + 24), fill=ink_rgb)
    draw.ellipse((figure_x + eye_offset - 9, face_y + 6, figure_x + eye_offset + 9, face_y + 24), fill=ink_rgb)
    draw.arc((figure_x - radius // 4, face_y + 62, figure_x + radius // 4, face_y + 106), 12, 168, fill=accent_rgb, width=9)
    draw.arc((figure_x - radius + 20, face_y + 100, figure_x + radius - 12, face_y + 158), 12, 168, fill=quiet_ink, width=7)

    if any(word in str(panel.get("action", "")) for word in ("持ち物", "握る", "キーホルダー")):
        hand_x = figure_x - radius - 26
        draw.line((hand_x, face_y + 260, hand_x + 70, face_y + 175), fill=ink_rgb, width=14)
        draw.ellipse((hand_x + 56, face_y + 155, hand_x + 92, face_y + 191), outline=accent_rgb, width=6)

    draw.line((80, 750, 410, 750), fill=_blend(bg_rgb, ink_rgb, 0.25), width=6)
    draw.line((80, 785, 275, 785), fill=_blend(bg_rgb, ink_rgb, 0.25), width=6)
    draw.line((810, 755, 1120, 755), fill=_blend(bg_rgb, ink_rgb, 0.25), width=6)
    draw.line((1050, 90, 1110, 90, 1110, 150), fill=accent_rgb, width=12, joint="curve")
    return image


def save_panel_artwork(
    project_id: str,
    panel: Dict[str, Any],
    settings: Dict[str, Any],
    model_settings: Dict[str, Any] | None = None,
    storage: StorageService | None = None,
) -> str:
    """パネルのrevisionごとにStorageへ保存し、過去生成物を上書きしない。"""

    revision = int(panel.get("revision", 0)) + 1
    panel["revision"] = revision
    storage = storage or get_storage()
    filename = f"{_slug(str(panel.get('id', 'panel')))}-r{revision}.png"
    storage_key = storage.asset_key(project_id, filename)
    runtime = get_settings()
    if runtime.image_provider == "openai" and runtime.openai_api_key:
        image_model = model_for_task(
            model_settings or {"image_model": runtime.openai_image_model}, "image"
        )
        actual_model = save_openai_image(
            panel, runtime, storage, storage_key, model_id=image_model
        )
        panel["generation_metadata"] = new_generation_metadata(
            task="image",
            requested_model=image_model,
            actual_model=actual_model,
        )
        return storage_key
    buffer = BytesIO()
    render_panel_image(panel, settings).save(buffer, format="PNG", optimize=True)
    storage.put_bytes(storage_key, buffer.getvalue(), content_type="image/png")
    panel["generation_metadata"] = new_generation_metadata(
        task="image", requested_model="demo", actual_model="demo", provider="demo"
    )
    return storage_key


def save_openai_image(
    panel: Dict[str, Any],
    runtime: Any,
    storage: StorageService,
    storage_key: str,
    *,
    model_id: str | None = None,
) -> str:
    """OpenAI Images APIの画像を検証し、Storageへ保存する。"""

    requested_model = model_id if is_allowed_image_model(str(model_id or "")) else runtime.openai_image_model
    if not is_allowed_image_model(str(requested_model)):
        requested_model = "gpt-image-2"
    generation_size = artwork_generation_size(panel, str(requested_model))
    prompt = panel.get("generation_prompt") or "漫画のコマ。文字は描かない。"
    if panel.get("panel_direction"):
        canvas_plan = generation_canvas_zones(panel, str(requested_model))
        # Overscanでは元sourceを破棄せず、同じassetと保存済みcropを後工程へ渡す。
        canvas_plan["source_asset"] = storage_key
        canvas_plan["source_asset_role"] = (
            "overscan_source" if canvas_plan.get("strategy") == "overscan_safe_crop" else "direct_panel_source"
        )
        prompt += "\nActual generation canvas (authoritative): " + json.dumps(canvas_plan, ensure_ascii=False)
        panel["panel_direction"]["generation_canvas"] = canvas_plan
    payload = {
        "model": requested_model,
        "prompt": prompt,
        "size": generation_size,
        "quality": "low",
    }
    try:
        body = request_json(
            runtime.openai_image_url,
            api_key=runtime.openai_api_key,
            payload=payload,
            timeout=getattr(runtime, "openai_timeout_seconds", 120.0),
            max_retries=getattr(runtime, "openai_max_retries", 1),
        )
        image_data = body["data"][0]
        encoded = image_data.get("b64_json")
        if encoded:
            image_bytes = base64.b64decode(encoded, validate=True)
            storage.put_bytes(
                storage_key, _validate_image_bytes(image_bytes), content_type="image/png"
            )
            response_model = body.get("model")
            return str(response_model) if is_allowed_image_model(str(response_model)) else str(requested_model)
        image_url = image_data.get("url")
        if image_url:
            parsed = urlparse(str(image_url))
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("安全でない画像URLです")
            image_bytes = request_bytes(
                str(image_url),
                api_key="",
                timeout=getattr(runtime, "openai_timeout_seconds", 120.0),
                max_retries=getattr(runtime, "openai_max_retries", 1),
            )
            storage.put_bytes(
                storage_key, _validate_image_bytes(image_bytes), content_type="image/png"
            )
            response_model = body.get("model")
            return str(response_model) if is_allowed_image_model(str(response_model)) else str(requested_model)
        raise ValueError("画像データがありません")
    except OpenAIRequestError as exc:
        raise ArtworkGenerationError(str(exc)) from exc
    except (AttributeError, OSError, ValueError, KeyError, IndexError, TypeError, binascii.Error, StorageError) as exc:
        raise ArtworkGenerationError("画像生成サービスから有効な画像を取得できませんでした") from exc


def _validate_image_bytes(image_bytes: bytes) -> bytes:
    """保存前に画像形式とサイズを検証し、壊れた応答をProjectへ紐付けない。"""

    if not image_bytes or len(image_bytes) > 20 * 1024 * 1024:
        raise ValueError("画像サイズが不正です")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError("画像形式が不正です") from exc
    return image_bytes


def asset_url(project_id: str, storage_key: str) -> str:
    """既存のmedia URL契約を維持しつつ、DBへ保存する参照はStorage keyにする。"""

    return f"/media/{_slug(project_id)}/{PurePosixPath(str(storage_key)).name}"
