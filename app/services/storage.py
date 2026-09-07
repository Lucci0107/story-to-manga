"""生成アセットとExportの保存境界。

アプリケーションはファイルシステムのPathを直接扱わず、storage keyで保存する。
現在はLocalFileStorageのみ実装し、将来S3互換Object Storageを同じ契約へ追加できる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional, Protocol

from ..config import Settings, get_settings


class StorageConfigurationError(RuntimeError):
    """Storage backendの設定が不正または未実装。"""


class StorageObjectNotFound(FileNotFoundError):
    """指定したstorage keyのオブジェクトが存在しない。"""


class StorageError(RuntimeError):
    """保存・読み出しに失敗した。"""


@dataclass(frozen=True)
class StoredObject:
    """保存結果。keyはDBやProject JSONへ保存可能な不透明参照。"""

    key: str
    content_type: Optional[str] = None
    size: int = 0


class StorageService(Protocol):
    """Object Storageを実装するためのアプリケーション契約。"""

    backend_name: str

    def asset_key(self, project_id: str, filename: str) -> str:
        ...

    def export_key(self, project_id: str, export_id: str, file_format: str) -> str:
        ...

    def put_bytes(self, key: str, content: bytes, content_type: Optional[str] = None) -> StoredObject:
        ...

    def get_bytes(self, key: str) -> bytes:
        ...

    def exists(self, key: str) -> bool:
        ...


def _safe_component(value: str, fallback: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")
    return clean or fallback


class LocalFileStorage:
    """既存data/assets・data/exportsを使うローカル実装。"""

    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "assets").mkdir(parents=True, exist_ok=True)
        (self.root / "exports").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_key(key: str) -> str:
        value = str(key).replace("\\", "/")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise StorageError("storage keyが不正です")
        return "/".join(path.parts)

    def _path_for_key(self, key: str) -> Path:
        normalized = self._normalize_key(key)
        candidate = (self.root / Path(*normalized.split("/"))).resolve()
        if self.root not in candidate.parents:
            raise StorageError("storage keyが保存領域外を指しています")
        return candidate

    def _key_for_reference(self, reference: str) -> str:
        value = str(reference)
        if value.startswith("/"):
            path = Path(value).expanduser().resolve()
            try:
                return path.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise StorageObjectNotFound("storage objectが保存領域外です") from exc
        return self._normalize_key(value)

    def asset_key(self, project_id: str, filename: str) -> str:
        safe_project = _safe_component(project_id, "project")
        safe_filename = PurePosixPath(str(filename).replace("\\", "/")).name
        if not safe_filename or safe_filename in {".", ".."}:
            raise StorageError("asset filenameが不正です")
        return f"assets/{safe_project}/{safe_filename}"

    def export_key(self, project_id: str, export_id: str, file_format: str) -> str:
        safe_project = _safe_component(project_id, "project")
        safe_export = _safe_component(export_id, "export")
        safe_format = _safe_component(file_format.lower(), "bin")
        return f"exports/{safe_project}-{safe_export}.{safe_format}"

    def put_bytes(self, key: str, content: bytes, content_type: Optional[str] = None) -> StoredObject:
        path = self._path_for_key(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = bytes(content)
            path.write_bytes(payload)
        except OSError as exc:
            raise StorageError("ローカルStorageへの保存に失敗しました") from exc
        return StoredObject(self._normalize_key(key), content_type, len(payload))

    def get_bytes(self, key: str) -> bytes:
        try:
            path = self._path_for_key(self._key_for_reference(key))
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageObjectNotFound("storage objectが見つかりません") from exc
        except OSError as exc:
            raise StorageError("ローカルStorageの読み出しに失敗しました") from exc

    def exists(self, key: str) -> bool:
        try:
            return self._path_for_key(self._key_for_reference(key)).is_file()
        except (StorageError, StorageObjectNotFound):
            return False


def get_storage(settings: Optional[Settings] = None) -> StorageService:
    """STORAGE_BACKENDから保存実装を選択する。"""

    runtime = settings or get_settings()
    backend = runtime.storage_backend.lower()
    if backend == "local":
        return LocalFileStorage(runtime.data_dir)
    raise StorageConfigurationError(
        f"Storage backend '{backend}' は未実装です。現在はSTORAGE_BACKEND=localのみ利用できます"
    )
