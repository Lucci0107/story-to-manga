"""生成アセットとExportの保存境界。

アプリケーションはファイルシステムのPathを直接扱わず、storage keyで保存する。
現在はLocalFileStorageのみ実装し、将来S3互換Object Storageを同じ契約へ追加できる。
"""

from __future__ import annotations

import re
import shutil
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional, Protocol

from ..config import Settings, get_settings


logger = logging.getLogger(__name__)

# Persistent Diskを使う本番環境と、ローカルの同じ保存境界で共有する閾値。
# しきい値到達時の書き込み停止は、生成・Exportの開始前にだけ適用する。
STORAGE_WARNING_THRESHOLD = 0.80
STORAGE_CRITICAL_THRESHOLD = 0.90
STORAGE_BLOCK_THRESHOLD = 0.95


@dataclass(frozen=True)
class StorageUsage:
    """ファイルシステムの読み取り専用容量スナップショット。"""

    total_bytes: int
    used_bytes: int
    free_bytes: int
    usage_ratio: float
    status: str

    @property
    def usage_percent(self) -> float:
        return round(self.usage_ratio * 100, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "usage_ratio": round(self.usage_ratio, 5),
            "usage_percent": self.usage_percent,
            "status": self.status,
        }


class StorageConfigurationError(RuntimeError):
    """Storage backendの設定が不正または未実装。"""


class StorageObjectNotFound(FileNotFoundError):
    """指定したstorage keyのオブジェクトが存在しない。"""


class StorageError(RuntimeError):
    """保存・読み出しに失敗した。"""


class StorageCapacityError(StorageError):
    """高コストな保存処理を再試行可能な状態で停止した。"""

    retryable = True

    def __init__(self, operation: str, usage: StorageUsage) -> None:
        self.operation = operation
        self.usage = usage
        super().__init__(
            "保存領域の空き容量が不足しています。容量を確保してから再試行してください"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": "storage_capacity_exceeded",
            "retryable": True,
            "operation": self.operation,
            "usage_percent": self.usage.usage_percent,
            "message": str(self),
        }


def _capacity_root(storage_or_root: Any = None, settings: Optional[Settings] = None) -> Path:
    """容量確認対象のルートを解決する。Pathを直接渡すとテストでも注入できる。"""

    if isinstance(storage_or_root, (str, Path)):
        return Path(storage_or_root).expanduser().resolve()
    root = getattr(storage_or_root, "root", None)
    if root:
        return Path(root).expanduser().resolve()
    runtime = settings or get_settings()
    return runtime.data_dir.expanduser().resolve()


def _directory_size(path: Path) -> int:
    """シンボリックリンクを辿らず、保存領域内の実ファイルだけを合計する。"""

    if not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                total += max(0, int(item.stat().st_size))
            except OSError:
                # 監査は一部ファイルの瞬間的な消失で全体を失敗させない。
                continue
    except OSError:
        return total
    return total


def _database_path_for_storage(settings: Settings, root: Path) -> Path:
    """設定済みSQLiteの実パスを返し、外部DBでは保存ルートの既定値を使う。"""

    try:
        # 遅延importでstorage↔databaseの初期化循環を避ける。
        from .database import SQLiteDatabase, create_database

        backend = create_database(settings)
        if isinstance(backend, SQLiteDatabase):
            return backend.database_path
    except Exception:  # noqa: BLE001
        # 容量表示はDB接続そのものを必要としない診断機能なので、既定値へ戻す。
        pass
    return root / "story_manga.sqlite3"


def _classify_usage(ratio: float) -> str:
    if ratio >= STORAGE_BLOCK_THRESHOLD:
        return "blocked"
    if ratio >= STORAGE_CRITICAL_THRESHOLD:
        return "critical"
    if ratio >= STORAGE_WARNING_THRESHOLD:
        return "warning"
    return "normal"


def storage_usage(
    storage_or_root: Any = None,
    *,
    settings: Optional[Settings] = None,
    disk_usage_fn: Any = None,
) -> StorageUsage:
    """ディスク容量を読み取り、閾値に応じた状態を返す。"""

    root = _capacity_root(storage_or_root, settings)
    try:
        usage = (disk_usage_fn or shutil.disk_usage)(root)
        total = max(0, int(usage.total))
        used = max(0, int(usage.used))
        free = max(0, int(usage.free))
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        raise StorageError("保存領域の容量を確認できませんでした") from exc
    ratio = min(1.0, max(0.0, used / total)) if total else 0.0
    return StorageUsage(total, used, free, ratio, _classify_usage(ratio))


def ensure_storage_capacity(
    storage_or_root: Any = None,
    *,
    operation: str = "high_cost_write",
    settings: Optional[Settings] = None,
    disk_usage_fn: Any = None,
) -> StorageUsage:
    """高コスト書き込み前の共通ガード。95%以上なら一切の出力を始めない。"""

    usage = storage_usage(storage_or_root, settings=settings, disk_usage_fn=disk_usage_fn)
    if usage.status == "blocked":
        logger.warning(
            "storage capacity blocked operation=%s usage_percent=%.2f",
            operation,
            usage.usage_percent,
        )
        raise StorageCapacityError(operation, usage)
    if usage.status in {"warning", "critical"}:
        logger.warning(
            "storage capacity %s operation=%s usage_percent=%.2f",
            usage.status,
            operation,
            usage.usage_percent,
        )
    return usage


def format_bytes(value: int) -> str:
    """管理画面用の短い容量表記。"""

    amount = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return "0 B"


def storage_status(
    storage_or_root: Any = None,
    *,
    settings: Optional[Settings] = None,
    disk_usage_fn: Any = None,
) -> dict[str, Any]:
    """容量と保存カテゴリの読み取り専用ステータスを返す。"""

    root = _capacity_root(storage_or_root, settings)
    runtime = settings or get_settings()
    usage = storage_usage(
        storage_or_root,
        settings=runtime,
        disk_usage_fn=disk_usage_fn,
    )
    database_path = _database_path_for_storage(runtime, root)
    try:
        database_bytes = max(0, int(database_path.stat().st_size)) if database_path.is_file() else 0
    except OSError:
        database_bytes = 0
    assets_bytes = _directory_size(root / "assets")
    exports_bytes = _directory_size(root / "exports")
    result = {
        **usage.as_dict(),
        "thresholds": {
            "warning_percent": round(STORAGE_WARNING_THRESHOLD * 100, 2),
            "critical_percent": round(STORAGE_CRITICAL_THRESHOLD * 100, 2),
            "block_percent": round(STORAGE_BLOCK_THRESHOLD * 100, 2),
        },
        "assets_bytes": assets_bytes,
        "exports_bytes": exports_bytes,
        "database_bytes": database_bytes,
        "assets_display": format_bytes(assets_bytes),
        "exports_display": format_bytes(exports_bytes),
        "database_display": format_bytes(database_bytes),
        "total_display": format_bytes(usage.total_bytes),
        "used_display": format_bytes(usage.used_bytes),
        "free_display": format_bytes(usage.free_bytes),
        "retention_policy": {
            "mode": "report_only",
            "automatic_deletion": False,
            "candidate_targets": [
                "old_exports",
                "superseded_exports",
                "abandoned_temporary_or_revision_artifacts",
            ],
        },
    }
    return result


def _normalize_reference_key(storage: Any, value: Any) -> Optional[str]:
    if not value:
        return None
    raw = str(value)
    try:
        key = storage._key_for_reference(raw)  # type: ignore[attr-defined]
    except (AttributeError, StorageError, StorageObjectNotFound, TypeError, ValueError):
        try:
            if raw.startswith("/"):
                # Storage実装なしのテスト／診断でも、ルート内の絶対参照を扱う。
                key = Path(raw).expanduser().resolve().relative_to(
                    _capacity_root(storage)
                ).as_posix()
            else:
                key = LocalFileStorage._normalize_key(raw)
        except (StorageError, TypeError, ValueError):
            return None
        except (OSError, RuntimeError):
            return None
    return str(key)


def _iter_storage_files(root: Path, prefix: str) -> dict[str, int]:
    directory = root / prefix
    result: dict[str, int] = {}
    if not directory.is_dir():
        return result
    try:
        for item in directory.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                result[item.relative_to(root).as_posix()] = max(0, int(item.stat().st_size))
            except (OSError, ValueError):
                continue
    except OSError:
        return result
    return result


def find_orphan_storage_objects(
    storage_or_root: Any = None,
    *,
    referenced_asset_keys: Iterable[str] = (),
    referenced_export_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """DB参照に存在しないassets/exportsを候補として報告する（削除しない）。"""

    root = _capacity_root(storage_or_root)
    storage = storage_or_root if hasattr(storage_or_root, "_key_for_reference") else None
    assets = _iter_storage_files(root, "assets")
    exports = _iter_storage_files(root, "exports")

    def normalize(values: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for value in values:
            normalized = _normalize_reference_key(storage or root, value)
            if normalized:
                result.add(normalized)
        return result

    referenced_assets = normalize(referenced_asset_keys)
    referenced_exports = normalize(referenced_export_keys)
    orphan_assets = [
        {"key": key, "size_bytes": size}
        for key, size in sorted(assets.items())
        if key not in referenced_assets
    ]
    orphan_exports = [
        {"key": key, "size_bytes": size}
        for key, size in sorted(exports.items())
        if key not in referenced_exports
    ]
    return {
        "assets": orphan_assets,
        "exports": orphan_exports,
        "asset_count": len(orphan_assets),
        "export_count": len(orphan_exports),
        "asset_bytes": sum(item["size_bytes"] for item in orphan_assets),
        "export_bytes": sum(item["size_bytes"] for item in orphan_exports),
        "mode": "report_only",
    }


# 呼び出し側が意図を読み取りやすい別名。削除を実行するAPIは提供しない。
get_storage_usage = storage_usage
get_storage_status = storage_status
detect_orphan_storage_objects = find_orphan_storage_objects


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

    def delete_project_objects(self, project_id: str) -> int:
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
        temporary_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = bytes(content)
            # 途中まで書かれたPNG/PDF/ZIPを残さないよう、同一ディレクトリ内の
            # 一時ファイルへ書き切ってから原子的に置き換える。
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
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

    def delete_project_objects(self, project_id: str) -> int:
        """Project所有の画像とExportだけを削除し、他Projectへ影響させない。"""

        safe_project = _safe_component(project_id, "project")
        deleted = 0
        asset_directory = self._path_for_key(f"assets/{safe_project}")
        try:
            if asset_directory.is_dir():
                deleted += sum(1 for item in asset_directory.rglob("*") if item.is_file())
                shutil.rmtree(asset_directory)
            export_directory = self._path_for_key("exports")
            for item in export_directory.glob(f"{safe_project}-*"):
                if item.is_file():
                    item.unlink()
                    deleted += 1
        except OSError as exc:
            raise StorageError("Projectの保存データを削除できませんでした") from exc
        return deleted


def get_storage(settings: Optional[Settings] = None) -> StorageService:
    """STORAGE_BACKENDから保存実装を選択する。"""

    runtime = settings or get_settings()
    backend = runtime.storage_backend.lower()
    if backend == "local":
        return LocalFileStorage(runtime.data_dir)
    raise StorageConfigurationError(
        f"Storage backend '{backend}' は未実装です。現在はSTORAGE_BACKEND=localのみ利用できます"
    )
