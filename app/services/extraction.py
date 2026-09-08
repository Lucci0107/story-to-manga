"""物語ファイルの安全な本文抽出。"""

from __future__ import annotations

import io
import zipfile
from html import unescape
from pathlib import PurePath
from typing import Optional, Tuple
from xml.etree import ElementTree

from fastapi import UploadFile

from ..config import get_settings


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
TEXT_MIME_TYPES = {"text/plain", "text/markdown", "text/x-markdown", "application/x-markdown"}
MAX_EXTRACTED_CHARACTERS = 1_000_000


class StoryExtractionError(ValueError):
    """ユーザーが修正可能な入力エラー。"""


def validate_filename(filename: Optional[str]) -> str:
    """拡張子を検証し、表示用のファイル名だけを返す。"""

    if not filename:
        raise StoryExtractionError("ファイル名を確認できません")
    safe_name = PurePath(filename).name
    suffix = PurePath(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise StoryExtractionError("対応形式は txt、md、PDF、docx です")
    return safe_name


async def extract_uploaded_file(upload: UploadFile) -> Tuple[str, str, str]:
    """アップロード内容を読み込み、(本文,形式,ファイル名)を返す。"""

    filename = validate_filename(upload.filename)
    suffix = PurePath(filename).suffix.lower()
    if upload.content_type and not mime_matches_suffix(upload.content_type, suffix):
        raise StoryExtractionError("ファイル形式とMIMEタイプが一致しません")
    settings = get_settings()
    content = await upload.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise StoryExtractionError(
            f"ファイルが大きすぎます。上限は{settings.max_upload_bytes // (1024 * 1024)}MBです"
        )
    text = extract_text_from_bytes(content, suffix)
    if not text.strip():
        raise StoryExtractionError("本文が空のファイルは利用できません")
    return text, suffix.lstrip("."), filename


def mime_matches_suffix(content_type: str, suffix: str) -> bool:
    """ブラウザ差のあるテキストMIMEを許可し、バイナリ形式は一致を求める。"""

    if content_type == "application/octet-stream":
        return True
    if suffix in {".txt", ".md"}:
        return content_type.startswith("text/") or content_type in TEXT_MIME_TYPES
    if suffix == ".pdf":
        return content_type == "application/pdf"
    if suffix == ".docx":
        return content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return False


def extract_text_from_bytes(content: bytes, suffix: str) -> str:
    """拡張子ごとに本文を抽出する。本文は切り捨てない。"""

    normalized_suffix = suffix.lower().lstrip(".")
    if normalized_suffix in {"txt", "md"}:
        return decode_text(content)
    if normalized_suffix == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = []
            extracted_characters = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                extracted_characters += len(page_text)
                if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                    raise StoryExtractionError("抽出後の本文が大きすぎます")
                pages.append(page_text)
        except StoryExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StoryExtractionError("PDF本文を読み取れませんでした") from exc
        return "\n\n".join(pages).strip()
    if normalized_suffix == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > MAX_EXTRACTED_CHARACTERS * 4:
                    raise StoryExtractionError("抽出後の本文が大きすぎます")
                xml_content = archive.read(document_info)
            root = ElementTree.fromstring(xml_content)
        except StoryExtractionError:
            raise
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise StoryExtractionError("docx本文を読み取れませんでした") from exc
        paragraphs = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            chunks = [
                node.text or ""
                for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
            ]
            if chunks:
                paragraphs.append(unescape("".join(chunks)))
        result = "\n\n".join(paragraphs).strip()
        if len(result) > MAX_EXTRACTED_CHARACTERS:
            raise StoryExtractionError("抽出後の本文が大きすぎます")
        return result
    raise StoryExtractionError("対応していないファイル形式です")


def decode_text(content: bytes) -> str:
    """UTF-8を優先し、日本語Windows文字コードにも対応する。"""

    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StoryExtractionError("テキストをUTF-8またはShift_JISとして読めませんでした")
