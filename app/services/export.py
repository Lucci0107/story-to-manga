"""PDFとページ画像ZIPの書き出し。"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .artwork import render_panel_image
from .reading_order import bubble_side, panel_visual_position
from .storage import StorageError, StorageObjectNotFound, StorageService, get_storage


PDF_FONT = "Helvetica"
try:
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    PDF_FONT = "HeiseiMin-W3"
except Exception:  # noqa: BLE001
    # 実行環境にCIDフォントがない場合は英字フォントへフォールバックする。
    PDF_FONT = "Helvetica"


def _page_panel_boxes(
    page: Dict[str, Any],
    width: float,
    height: float,
    settings: Optional[Dict[str, Any]] = None,
) -> List[tuple[float, float, float, float]]:
    panels = page.get("panels", [])
    count = max(1, len(panels))
    margin = 42
    gap = 12
    available_width = width - margin * 2
    available_height = height - 132
    boxes: List[tuple[float, float, float, float]] = []
    if count == 1:
        return [(margin, 72, available_width, available_height)]
    if count == 2:
        panel_height = (available_height - gap) / 2
        return [(margin, 72 + panel_height + gap, available_width, panel_height), (margin, 72, available_width, panel_height)]
    columns = 2
    rows = (count + columns - 1) // columns
    box_width = (available_width - gap) / columns
    box_height = (available_height - gap * (rows - 1)) / rows
    for index in range(count):
        position = panel_visual_position(index, count, settings)
        row = position["row"] - 1
        col = position["column"] - 1
        x = margin + col * (box_width + gap)
        y = 72 + (rows - 1 - row) * (box_height + gap)
        boxes.append((x, y, box_width, box_height))
    return boxes


def _draw_wrapped(c: canvas.Canvas, value: str, x: float, y: float, max_width: float, font_size: int = 10, line_gap: int = 14, max_lines: int = 4) -> float:
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
    c.setFont(PDF_FONT, font_size)
    for line in lines[:max_lines]:
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


def export_pdf(project: Dict[str, Any], storage: StorageService | None = None) -> bytes:
    """生成済みパネル画像へアプリ側の文字要素を重ねたPDFを返す。"""

    storage = storage or get_storage()
    width, height = A4
    output = BytesIO()
    c = canvas.Canvas(output, pagesize=A4)
    c.setTitle(project.get("title", "Story to Manga"))
    for page in project.get("storyboard", []):
        c.setFillColorRGB(0.96, 0.95, 0.91)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        c.setFillColorRGB(0.12, 0.12, 0.11)
        c.setFont(PDF_FONT, 18)
        c.drawString(42, height - 58, str(project.get("title", "Story to Manga")))
        c.setFont(PDF_FONT, 9)
        c.drawRightString(width - 42, height - 56, f"PAGE {page.get('page_number', '')}")
        for panel, box in zip(
            page.get("panels", []),
            _page_panel_boxes(page, width, height, project.get("settings") or {}),
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
            dialogues = [str(item).strip() for item in panel.get("dialogue", []) if str(item).strip()]
            narrations = [str(item).strip() for item in panel.get("narration", []) if str(item).strip()]
            sfx_items = [str(item).strip() for item in panel.get("sfx", []) if str(item).strip()]
            bubble_width = min(box_width * 0.68, 190)
            bubble_height = 42
            for bubble_index, text in enumerate(dialogues[:6]):
                side = bubble_side(bubble_index, project.get("settings") or {})
                bubble_x = x + 16 if side == "left" else x + box_width - bubble_width - 16
                bubble_row = bubble_index // 2
                bubble_y = max(
                    y + 12,
                    y + box_height - bubble_height - 16 - bubble_row * (bubble_height + 7),
                )
                c.setFillColorRGB(0.98, 0.97, 0.94)
                c.roundRect(bubble_x, bubble_y, bubble_width, bubble_height, 12, stroke=0, fill=1)
                c.setFillColorRGB(0.12, 0.12, 0.11)
                _draw_wrapped(c, text, bubble_x + 10, bubble_y + bubble_height - 17, bubble_width - 20, 9, 12, 3)
            for narration_index, text in enumerate(narrations[:4]):
                side = bubble_side(narration_index, project.get("settings") or {})
                narration_width = min(box_width * 0.58, 170)
                narration_x = x + 10 if side == "left" else x + box_width - narration_width - 10
                narration_y = y + 10 + narration_index * 22
                c.setFillColorRGB(0.98, 0.97, 0.94)
                c.rect(narration_x, narration_y, narration_width, 18, stroke=0, fill=1)
                c.setFillColorRGB(0.12, 0.12, 0.11)
                _draw_wrapped(c, text, narration_x + 6, narration_y + 12, narration_width - 12, 7, 9, 1)
            for sfx_index, text in enumerate(sfx_items[:4]):
                side = bubble_side(sfx_index, project.get("settings") or {})
                sfx_width = min(94, box_width * 0.24)
                sfx_x = x + 10 if side == "left" else x + box_width - sfx_width - 10
                sfx_y = y + box_height - 28 - sfx_index * 18
                c.setFillColorRGB(0.90, 0.43, 0.27)
                _draw_wrapped(c, text, sfx_x, sfx_y, sfx_width, 11, 12, 2)
        c.showPage()
    if not project.get("storyboard"):
        c.setFillColorRGB(0.12, 0.12, 0.11)
        c.drawString(42, height - 58, "まだネームが生成されていません")
        c.showPage()
    c.save()
    return output.getvalue()


def export_zip(project: Dict[str, Any], storage: StorageService | None = None) -> bytes:
    """ページ構成、生成画像、編集可能なJSONをStorageからまとめて返す。"""

    storage = storage or get_storage()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "title": project.get("title"),
            "settings": project.get("settings"),
            "storyboard": project.get("storyboard"),
        }
        archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for page in project.get("storyboard", []):
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
