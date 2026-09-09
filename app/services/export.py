"""PDFとページ画像ZIPの書き出し。"""

from __future__ import annotations

import json
import math
import textwrap
import zipfile
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .artwork import render_panel_image
from .composition import composition_for_page, moved_text_item_set, normalize_polygon
from .layout import ensure_page_layout
from .storage import StorageError, StorageObjectNotFound, StorageService, get_storage


FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
BUNDLED_FONT_PATH = FONT_DIR / "MPLUS1p-Regular.ttf"
PDF_FONT = "Helvetica"
try:
    # PDF閲覧環境の外部CMapへ依存しないよう、OFLの日本語フォントを埋め込む。
    pdfmetrics.registerFont(TTFont("MPlus1p", str(BUNDLED_FONT_PATH)))
    PDF_FONT = "MPlus1p"
except Exception:  # noqa: BLE001
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
        PDF_FONT = "HeiseiMin-W3"
    except Exception:  # noqa: BLE001
        PDF_FONT = "Helvetica"


def _page_panel_boxes(
    page: Dict[str, Any],
    width: float,
    height: float,
    settings: Optional[Dict[str, Any]] = None,
) -> List[tuple[float, float, float, float]]:
    prepared = ensure_page_layout(page, settings or {})
    panels = prepared.get("panels", [])
    composition = composition_for_page(prepared)
    if int(composition.get("composition_version", 1) or 1) >= 2:
        geometry_by_id = {
            str(item.get("panel_id")): item
            for item in composition.get("panels", [])
            if isinstance(item, dict)
        }
        return [
            (
                float((geometry_by_id.get(str(panel.get("id"))) or {}).get("x", 0)) * width,
                height
                - (float((geometry_by_id.get(str(panel.get("id"))) or {}).get("y", 0))
                   + float((geometry_by_id.get(str(panel.get("id"))) or {}).get("height", 0))) * height,
                float((geometry_by_id.get(str(panel.get("id"))) or {}).get("width", 0)) * width,
                float((geometry_by_id.get(str(panel.get("id"))) or {}).get("height", 0)) * height,
            )
            for panel in panels
        ]
    margin = 42
    available_width = width - margin * 2
    available_height = height - 132
    bottom = 56
    boxes: List[tuple[float, float, float, float]] = []
    geometry_by_id = {
        str(item.get("panel_id")): item
        for item in (prepared.get("layout_geometry") or {}).get("panels", [])
        if isinstance(item, dict)
    }
    for panel in panels:
        geometry = geometry_by_id.get(str(panel.get("id"))) or panel.get("geometry") or {}
        x = margin + float(geometry.get("x", 0)) * available_width
        box_width = float(geometry.get("width", 1)) * available_width
        box_height = float(geometry.get("height", 1)) * available_height
        top = float(geometry.get("y", 0)) * available_height
        y = bottom + available_height - top - box_height
        boxes.append((x, y, box_width, box_height))
    return boxes


def _pdf_wrapped_lines(value: str, max_width: float, font_size: int) -> List[str]:
    words = list(value or "")
    lines: List[str] = []
    current = ""
    for char in words:
        candidate = current + char
        if stringWidth(candidate, PDF_FONT, font_size) > max_width and current:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_wrapped(
    c: canvas.Canvas,
    value: str,
    x: float,
    y: float,
    max_width: float,
    max_height: float,
    font_size: int = 10,
) -> float:
    """文面を省略せず、指定枠に収まるまでフォントを有界に縮小する。"""

    selected_size = max(4, font_size)
    lines = _pdf_wrapped_lines(value, max_width, selected_size)
    while selected_size > 4:
        line_gap = selected_size + 2
        if len(lines) * line_gap <= max_height:
            break
        selected_size -= 1
        lines = _pdf_wrapped_lines(value, max_width, selected_size)
    line_gap = selected_size + 2
    c.setFont(PDF_FONT, selected_size)
    for line in lines:
        c.drawString(x, y, line)
        y -= line_gap
    return y


def _crop_image_to_box(
    image: Image.Image,
    width: float,
    height: float,
    crop_mode: str = "fill",
    crop_anchor_x: str = "center",
    crop_anchor_y: str = "middle",
) -> Image.Image:
    """画像をcover cropし、Panel内のletterboxを作らない。"""

    if image.width <= 0 or image.height <= 0:
        return image.copy()
    target_ratio = width / max(height, 1)
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        crop_width = max(1, int(image.height * target_ratio))
        anchor = {"left": 0.0, "center": 0.5, "right": 1.0}.get(str(crop_anchor_x), 0.5)
        left = round((image.width - crop_width) * anchor)
        box = (left, 0, left + crop_width, image.height)
    else:
        crop_height = max(1, int(image.width / target_ratio))
        anchor = {"top": 0.0, "middle": 0.5, "bottom": 1.0}.get(str(crop_anchor_y), 0.5)
        top = round((image.height - crop_height) * anchor)
        box = (0, top, image.width, top + crop_height)
    return image.crop(box)


def _panel_asset(
    project: Dict[str, Any],
    panel: Dict[str, Any],
    width: float,
    height: float,
    storage: StorageService,
) -> Optional[ImageReader]:
    """Storageから生成済み画像を読み、表示方法も反映する。"""

    image_url = str(panel.get("image_url") or "")
    filename = PurePosixPath(image_url).name
    if not filename:
        return None
    if PurePosixPath(filename).suffix.lower() == ".svg":
        image = render_panel_image(panel, project.get("settings") or {})
        return ImageReader(_crop_image_to_box(image, width, height, panel.get("crop_mode", "fit")))
    try:
        source_bytes = storage.get_bytes(storage.asset_key(str(project.get("id")), filename))
    except (StorageError, StorageObjectNotFound):
        return None
    try:
        with Image.open(BytesIO(source_bytes)) as image:
            prepared = _crop_image_to_box(
                image.convert("RGB"), width, height, panel.get("crop_mode", "fit")
            )
        return ImageReader(prepared)
    except (OSError, ValueError):
        return None


def _panel_text_items(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    layout = panel.get("text_layout") or {}
    return [dict(item) for item in layout.get("items", []) if isinstance(item, dict)]


def _draw_panel_text(
    c: canvas.Canvas,
    panel: Dict[str, Any],
    box: tuple[float, float, float, float],
) -> None:
    """保存済みの正規化座標を使い、PDFへ文字要素を合成する。"""

    x, y, box_width, box_height = box
    for item in _panel_text_items(panel):
        item_x = x + float(item.get("x", 0)) * box_width
        item_width = float(item.get("width", 0.3)) * box_width
        item_height = float(item.get("height", 0.15)) * box_height
        item_y = y + box_height - (float(item.get("y", 0)) + float(item.get("height", 0.15))) * box_height
        item_type = str(item.get("type", "bubble"))
        text = str(item.get("text", ""))
        if item_type == "bubble":
            c.setFillColorRGB(0.98, 0.97, 0.94)
            c.setStrokeColorRGB(0.12, 0.12, 0.11)
            c.roundRect(item_x, item_y, item_width, item_height, min(14, item_height / 3), stroke=1, fill=1)
            font_size = max(6, min(10, int(item_height * 0.20)))
            c.setFillColorRGB(0.12, 0.12, 0.11)
            _draw_wrapped(c, text, item_x + 7, item_y + item_height - font_size - 5, item_width - 14, item_height - 10, font_size)
        elif item_type == "narration":
            c.setFillColorRGB(0.98, 0.97, 0.94)
            c.setStrokeColorRGB(0.18, 0.19, 0.18)
            c.rect(item_x, item_y, item_width, item_height, stroke=1, fill=1)
            font_size = max(6, min(9, int(item_height * 0.24)))
            c.setFillColorRGB(0.12, 0.12, 0.11)
            _draw_wrapped(c, text, item_x + 5, item_y + item_height - font_size - 4, item_width - 10, item_height - 8, font_size)
        else:
            c.setFillColorRGB(0.90, 0.43, 0.27)
            font_size = max(7, min(13, int(item_height * 0.30)))
            _draw_wrapped(c, text, item_x, item_y + item_height - font_size, item_width, item_height, font_size)


def _draw_pdf_accessible_text(c: canvas.Canvas, project: Mapping[str, Any], page: Mapping[str, Any]) -> None:
    """PNG合成PDFでも検索可能な日本語テキストを保持する（視覚レイヤーはPNG）。"""

    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.setFont(PDF_FONT, 1)
    values = [str(project.get("title", "")), str(page.get("title", ""))]
    for panel in page.get("panels", []):
        if not isinstance(panel, Mapping):
            continue
        values.extend(str(item) for key in ("dialogue", "narration", "sfx") for item in (panel.get(key) or []))
    for index, value in enumerate(values):
        if value.strip():
            c.drawString(1, 1 + index * 1.2, value[:500])
    c.restoreState()


def export_pdf(project: Dict[str, Any], storage: StorageService | None = None) -> bytes:
    """生成済みパネル画像へアプリ側の文字要素を重ねたPDFを返す。"""

    storage = storage or get_storage()
    width, height = A4
    output = BytesIO()
    c = canvas.Canvas(output, pagesize=A4)
    c.setTitle(project.get("title", "Story to Manga"))
    settings = project.get("settings") or {}
    for raw_page in project.get("storyboard", []):
        page = ensure_page_layout(raw_page, settings)
        composition = composition_for_page(page)
        if int(composition.get("composition_version", 1) or 1) >= 2:
            rendered = render_page_png(
                project,
                page,
                storage,
                width=max(1, round(width)),
                height=max(1, round(height)),
            )
            c.drawImage(ImageReader(BytesIO(rendered)), 0, 0, width, height, preserveAspectRatio=False, mask="auto")
            _draw_pdf_accessible_text(c, project, page)
            c.showPage()
            continue
        c.setFillColorRGB(0.96, 0.95, 0.91)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        c.setFillColorRGB(0.12, 0.12, 0.11)
        c.setFont(PDF_FONT, 18)
        c.drawString(42, height - 58, str(project.get("title", "Story to Manga")))
        c.setFont(PDF_FONT, 8)
        c.drawCentredString(width / 2, 24, str(page.get("page_number", "")))
        for panel, box in zip(
            page.get("panels", []),
            _page_panel_boxes(page, width, height, settings),
        ):
            x, y, box_width, box_height = box
            asset = _panel_asset(project, panel, box_width, box_height, storage)
            c.saveState()
            c.setFillColorRGB(0.88, 0.88, 0.85)
            c.roundRect(x, y, box_width, box_height, 8, stroke=0, fill=1)
            if asset:
                c.drawImage(asset, x, y, box_width, box_height, preserveAspectRatio=True, anchor="c", mask="auto")
            else:
                c.setFillColorRGB(0.35, 0.36, 0.34)
                c.setFont(PDF_FONT, 11)
                c.drawCentredString(x + box_width / 2, y + box_height / 2, "画像未生成")
            c.restoreState()
            c.setStrokeColorRGB(0.18, 0.19, 0.18)
            c.setLineWidth(1.5)
            c.roundRect(x, y, box_width, box_height, 8, stroke=1, fill=0)
            _draw_panel_text(c, panel, box)
        c.showPage()
    if not project.get("storyboard"):
        c.setFillColorRGB(0.12, 0.12, 0.11)
        c.drawString(42, height - 58, "まだネームが生成されていません")
        c.showPage()
    c.save()
    return output.getvalue()


def _load_page_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        str(BUNDLED_FONT_PATH),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            if candidate == "DejaVuSans.ttf" or Path(candidate).exists():
                return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _load_panel_source(
    project: Dict[str, Any],
    panel: Dict[str, Any],
    storage: StorageService,
) -> Optional[Image.Image]:
    """StorageからPanelの元画像を読み、breakoutでも再利用できるようにする。"""

    image_url = str(panel.get("image_url") or "")
    filename = PurePosixPath(image_url).name
    if not filename:
        return None
    if PurePosixPath(filename).suffix.lower() == ".svg":
        return render_panel_image(panel, project.get("settings") or {}).convert("RGB")
    try:
        source_bytes = storage.get_bytes(storage.asset_key(str(project.get("id")), filename))
        with Image.open(BytesIO(source_bytes)) as image:
            return image.convert("RGB")
    except (OSError, ValueError, StorageError, StorageObjectNotFound):
        return None


def _pil_panel_image(
    project: Dict[str, Any],
    panel: Dict[str, Any],
    width: int,
    height: int,
    storage: StorageService,
    *,
    crop_anchor_x: str = "center",
    crop_anchor_y: str = "middle",
) -> Image.Image:
    source = _load_panel_source(project, panel, storage)
    if source is None:
        placeholder = Image.new("RGB", (max(1, width), max(1, height)), (224, 223, 216))
        draw = ImageDraw.Draw(placeholder)
        draw.text((width // 2, height // 2), "ARTWORK", fill=(85, 87, 82), font=_load_page_font(18), anchor="mm")
        return placeholder
    source = _crop_image_to_box(source, width, height, "fill", crop_anchor_x, crop_anchor_y)
    return source.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)


def _pil_lines(text: str, line_count: int) -> str:
    width = max(4, math.ceil(max(1, len(text)) / max(1, line_count)))
    lines = textwrap.wrap(text, width=width, break_long_words=True, replace_whitespace=False)
    while len(lines) > line_count and width < len(text):
        width += 1
        lines = textwrap.wrap(text, width=width, break_long_words=True, replace_whitespace=False)
    return "\n".join(lines or [""])


def _fit_pil_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    height: int,
    line_count: int,
    initial_size: int,
) -> tuple[str, ImageFont.ImageFont]:
    """長文を省略せず、Pillowの実測値で文字枠に収める。"""

    for font_size in range(max(6, initial_size), 5, -1):
        font = _load_page_font(font_size)
        wrapped = _pil_lines(text, max(1, line_count))
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=1)
        if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
            return wrapped, font
    return _pil_lines(text, max(1, line_count)), _load_page_font(6)


def _draw_page_text(
    draw: ImageDraw.ImageDraw,
    panel: Dict[str, Any],
    box: tuple[int, int, int, int],
) -> None:
    x, y, width, height = box
    for item in _panel_text_items(panel):
        left = x + round(float(item.get("x", 0)) * width)
        top = y + round(float(item.get("y", 0)) * height)
        item_width = max(12, round(float(item.get("width", 0.3)) * width))
        item_height = max(10, round(float(item.get("height", 0.15)) * height))
        right = left + item_width
        bottom = top + item_height
        item_type = str(item.get("type", "bubble"))
        text = str(item.get("text", ""))
        font_size = max(9, min(22, int(item_height * (0.19 if item_type == "bubble" else 0.22))))
        line_count = max(1, int(item.get("line_count", 1)))
        horizontal_padding = 16 if item_type == "bubble" else 14 if item_type == "narration" else 0
        vertical_padding = 14 if item_type == "bubble" else 10 if item_type == "narration" else 0
        wrapped, font = _fit_pil_text(
            draw,
            text,
            max(4, item_width - horizontal_padding),
            max(4, item_height - vertical_padding),
            line_count,
            font_size,
        )
        if item_type == "bubble":
            draw.rounded_rectangle((left, top, right, bottom), radius=max(8, item_height // 4), fill=(250, 248, 242), outline=(30, 32, 29), width=2)
            draw.multiline_text((left + 8, top + 7), wrapped, fill=(30, 32, 29), font=font, spacing=2)
        elif item_type == "narration":
            draw.rectangle((left, top, right, bottom), fill=(250, 248, 242), outline=(45, 47, 44), width=2)
            draw.multiline_text((left + 7, top + 5), wrapped, fill=(30, 32, 29), font=font, spacing=2)
        else:
            draw.multiline_text((left, top), wrapped, fill=(230, 110, 69), font=font, spacing=1)


def _draw_text_item(
    draw: ImageDraw.ImageDraw,
    item: Mapping[str, Any],
    left: int,
    top: int,
    width: int,
    height: int,
) -> None:
    """Panel内外どちらの文字要素にも同じ描画スタイルを適用する。"""

    item_type = str(item.get("type", "bubble"))
    text = str(item.get("text", ""))
    font_size = max(9, min(25, int(height * (0.19 if item_type == "bubble" else 0.22))))
    line_count = max(1, int(item.get("line_count", 1) or 1))
    horizontal_padding = 16 if item_type == "bubble" else 14 if item_type == "narration" else 0
    vertical_padding = 14 if item_type == "bubble" else 10 if item_type == "narration" else 0
    wrapped, font = _fit_pil_text(
        draw,
        text,
        max(4, width - horizontal_padding),
        max(4, height - vertical_padding),
        line_count,
        font_size,
    )
    right, bottom = left + width, top + height
    if item_type == "bubble":
        draw.rounded_rectangle((left, top, right, bottom), radius=max(8, height // 4), fill=(250, 248, 242), outline=(30, 32, 29), width=2)
        draw.multiline_text((left + 8, top + 7), wrapped, fill=(30, 32, 29), font=font, spacing=2)
    elif item_type == "narration":
        draw.rectangle((left, top, right, bottom), fill=(250, 248, 242), outline=(45, 47, 44), width=2)
        draw.multiline_text((left + 7, top + 5), wrapped, fill=(30, 32, 29), font=font, spacing=2)
    elif item_type == "title":
        draw.multiline_text((left, top), wrapped, fill=(30, 32, 29), font=font, spacing=1)
    else:
        draw.multiline_text((left, top), wrapped, fill=(230, 110, 69), font=font, spacing=1)


def _draw_rotated_overlay(image: Image.Image, item: Mapping[str, Any], width: int, height: int) -> None:
    x = round(float(item.get("x", 0)) * width)
    y = round(float(item.get("y", 0)) * height)
    item_width = max(12, round(float(item.get("width", 0.2)) * width))
    item_height = max(10, round(float(item.get("height", 0.1)) * height))
    rotation = float(item.get("rotation", 0) or 0)
    if abs(rotation) < 0.5:
        _draw_text_item(ImageDraw.Draw(image), item, x, y, item_width, item_height)
        return
    layer = Image.new("RGBA", (item_width * 2, item_height * 2), (0, 0, 0, 0))
    _draw_text_item(ImageDraw.Draw(layer), item, item_width // 2, item_height // 2, item_width, item_height)
    rotated = layer.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
    image.alpha_composite(rotated, (x + item_width // 2 - rotated.width // 2, y + item_height // 2 - rotated.height // 2))


def _composition_panel_box(panel_geometry: Mapping[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    return (
        round(float(panel_geometry.get("x", 0)) * width),
        round(float(panel_geometry.get("y", 0)) * height),
        max(1, round(float(panel_geometry.get("width", 0.1)) * width)),
        max(1, round(float(panel_geometry.get("height", 0.1)) * height)),
    )


def _render_composition_png(
    project: Dict[str, Any],
    page: Dict[str, Any],
    storage: StorageService,
    composition: Mapping[str, Any],
    width: int,
    height: int,
) -> bytes:
    """Composition v2の全レイヤーを一つのページ画像へ合成する。"""

    image = Image.new("RGBA", (width, height), (245, 243, 235, 255))
    panel_lookup = {str(panel.get("id")): panel for panel in page.get("panels", []) if isinstance(panel, Mapping)}
    geometry_lookup = {str(item.get("panel_id")): item for item in composition.get("panels", []) if isinstance(item, Mapping)}
    for geometry in sorted(geometry_lookup.values(), key=lambda item: (int(item.get("z_index", 1)), int(item.get("reading_order", 1)))):
        panel = panel_lookup.get(str(geometry.get("panel_id")), {})
        x, y, box_width, box_height = _composition_panel_box(geometry, width, height)
        panel_image = _pil_panel_image(
            project,
            panel,
            box_width,
            box_height,
            storage,
            crop_anchor_x=str(geometry.get("crop_anchor_x", "center")),
            crop_anchor_y=str(geometry.get("crop_anchor_y", "middle")),
        ).convert("RGBA")
        points = normalize_polygon(geometry.get("polygon_points"), geometry)
        local_points = [
            (
                round(point[0] * width) - x,
                round(point[1] * height) - y,
            )
            for point in points
        ]
        mask = Image.new("L", (box_width, box_height), 0)
        ImageDraw.Draw(mask).polygon(local_points, fill=255)
        image.paste(panel_image, (x, y), mask)
    draw = ImageDraw.Draw(image)
    for geometry in geometry_lookup.values():
        points = normalize_polygon(geometry.get("polygon_points"), geometry)
        draw.line([(round(point[0] * width), round(point[1] * height)) for point in [*points, points[0]]], fill=(24, 25, 23, 255), width=max(2, round(width * 0.003)))

    # Breakoutは元Artworkを再利用し、画像生成や外部サービスを追加で呼ばない。
    # 文字が人物の前景に来るよう、BreakoutをPanel内の文字より先に描く。
    for breakout in composition.get("breakouts", []):
        if not isinstance(breakout, Mapping) or not breakout.get("enabled", True):
            continue
        panel = panel_lookup.get(str(breakout.get("source_panel_id") or breakout.get("panel_id")), {})
        bx = round(float(breakout.get("x", 0)) * width)
        by = round(float(breakout.get("y", 0)) * height)
        bw = max(1, round(float(breakout.get("width", 0.2)) * width))
        bh = max(1, round(float(breakout.get("height", 0.3)) * height))
        asset = _pil_panel_image(project, panel, bw, bh, storage, crop_anchor_x="center", crop_anchor_y="top").convert("RGBA")
        mask = Image.new("L", (bw, bh), 0)
        if str(breakout.get("clip_shape", "ellipse")) == "ellipse":
            ImageDraw.Draw(mask).ellipse((0, 0, bw - 1, bh - 1), fill=255)
        else:
            ImageDraw.Draw(mask).rectangle((0, 0, bw, bh), fill=255)
        image.paste(asset, (bx, by), mask)

    moved = moved_text_item_set(composition)
    for geometry in geometry_lookup.values():
        panel = panel_lookup.get(str(geometry.get("panel_id")), {})
        x, y, box_width, box_height = _composition_panel_box(geometry, width, height)
        for item in _panel_text_items(panel):
            if (str(geometry.get("panel_id")), str(item.get("id", ""))) in moved:
                continue
            left = x + round(float(item.get("x", 0)) * box_width)
            top = y + round(float(item.get("y", 0)) * box_height)
            _draw_text_item(
                draw,
                item,
                left,
                top,
                max(12, round(float(item.get("width", 0.3)) * box_width)),
                max(10, round(float(item.get("height", 0.15)) * box_height)),
            )

    for overlay in composition.get("overlays", []):
        if isinstance(overlay, Mapping):
            _draw_rotated_overlay(image, overlay, width, height)
    ImageDraw.Draw(image).text((width // 2, height - round(height * 0.025)), str(page.get("page_number", "")), fill=(45, 47, 44, 255), font=_load_page_font(max(12, round(width * 0.016))), anchor="mm")
    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_page_png(
    project: Dict[str, Any],
    page: Dict[str, Any],
    storage: StorageService,
    *,
    width: int = 900,
    height: int = 1200,
) -> bytes:
    """Preview/PDFと同じ保存済みgeometryからページ画像を生成する。"""

    prepared = ensure_page_layout(page, project.get("settings") or {})
    composition = composition_for_page(prepared)
    if int(composition.get("composition_version", 1) or 1) >= 2:
        return _render_composition_png(project, prepared, storage, composition, width, height)
    image = Image.new("RGB", (width, height), (245, 243, 235))
    draw = ImageDraw.Draw(image)
    draw.text((42, 22), str(project.get("title", "Story to Manga")), fill=(30, 32, 29), font=_load_page_font(24))
    raw_boxes = _page_panel_boxes(prepared, width, height, project.get("settings") or {})
    boxes = [tuple(round(value) for value in box) for box in raw_boxes]
    for panel, (x, report_y, box_width, box_height) in zip(prepared.get("panels", []), boxes):
        # _page_panel_boxesはPDFの左下原点なので、Pillowの左上原点へ戻す。
        y = height - report_y - box_height
        panel_image = _pil_panel_image(project, panel, box_width, box_height, storage)
        image.paste(panel_image, (x, y))
        draw.rounded_rectangle((x, y, x + box_width, y + box_height), radius=8, outline=(30, 32, 29), width=3)
        _draw_page_text(draw, panel, (x, y, box_width, box_height))
    draw.text((width // 2, height - 28), str(prepared.get("page_number", "")), fill=(45, 47, 44), font=_load_page_font(14), anchor="mm")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def export_zip(project: Dict[str, Any], storage: StorageService | None = None) -> bytes:
    """ページ構成、生成画像、編集可能なJSONをStorageからまとめて返す。"""

    storage = storage or get_storage()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        storyboard = [
            ensure_page_layout(page, project.get("settings") or {})
            for page in project.get("storyboard", [])
        ]
        manifest = {
            "title": project.get("title"),
            "settings": project.get("settings"),
            "storyboard": storyboard,
        }
        archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for page in storyboard:
            archive.writestr(
                f"pages/page-{int(page.get('page_number', 0) or 0):03d}.png",
                render_page_png(project, page, storage),
            )
            for panel in page.get("panels", []):
                image_url = panel.get("image_url") or ""
                filename = PurePosixPath(str(image_url)).name
                if not filename:
                    continue
                try:
                    content = storage.get_bytes(storage.asset_key(str(project["id"]), filename))
                except (StorageError, StorageObjectNotFound):
                    continue
                archive.writestr(f"pages/page-{page.get('page_number')}/{filename}", content)
    return output.getvalue()
