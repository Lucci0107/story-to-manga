"""PDFとページ画像ZIPの書き出し。"""

from __future__ import annotations

import json
import math
import textwrap
import zipfile
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .artwork import render_panel_image
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


def _crop_image_to_box(image: Image.Image, width: float, height: float, crop_mode: str) -> Image.Image:
    """PDFのコマ枠に合わせ、fill指定時だけ中央で画像をトリミングする。"""

    if crop_mode != "fill" or image.width <= 0 or image.height <= 0:
        return image.copy()
    target_ratio = width / max(height, 1)
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        crop_width = max(1, int(image.height * target_ratio))
        left = (image.width - crop_width) // 2
        box = (left, 0, left + crop_width, image.height)
    else:
        crop_height = max(1, int(image.width / target_ratio))
        top = (image.height - crop_height) // 2
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


def _pil_panel_image(
    project: Dict[str, Any],
    panel: Dict[str, Any],
    width: int,
    height: int,
    storage: StorageService,
) -> Image.Image:
    image_url = str(panel.get("image_url") or "")
    filename = PurePosixPath(image_url).name
    source: Optional[Image.Image] = None
    if filename and PurePosixPath(filename).suffix.lower() == ".svg":
        source = render_panel_image(panel, project.get("settings") or {}).convert("RGB")
    elif filename:
        try:
            source_bytes = storage.get_bytes(storage.asset_key(str(project.get("id")), filename))
            with Image.open(BytesIO(source_bytes)) as image:
                source = image.convert("RGB")
        except (OSError, ValueError, StorageError, StorageObjectNotFound):
            source = None
    if source is None:
        placeholder = Image.new("RGB", (max(1, width), max(1, height)), (224, 223, 216))
        draw = ImageDraw.Draw(placeholder)
        draw.text((width // 2, height // 2), "ARTWORK", fill=(85, 87, 82), font=_load_page_font(18), anchor="mm")
        return placeholder
    if panel.get("crop_mode") == "fill":
        source = _crop_image_to_box(source, width, height, "fill")
        return source.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    source.thumbnail((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    canvas_image = Image.new("RGB", (max(1, width), max(1, height)), (216, 215, 207))
    canvas_image.paste(source, ((width - source.width) // 2, (height - source.height) // 2))
    return canvas_image


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
