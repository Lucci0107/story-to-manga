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
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Protocol
from urllib.parse import urlparse

from ..config import Settings, get_settings


logger = logging.getLogger(__name__)

# Persistent Diskを使う本番環境と、ローカルの同じ保存境界で共有する閾値。
# しきい値到達時の書き込み停止は、生成・Exportの開始前にだけ適用する。
STORAGE_WARNING_THRESHOLD = 0.80
STORAGE_CRITICAL_THRESHOLD = 0.90
STORAGE_BLOCK_THRESHOLD = 0.95

# 孤児監査は誤削除を避けるため、生成直後のファイルを少なくとも24時間
# 「不確実」として保護する。削除機能はこのモジュールにも提供しない。
ORPHAN_GRACE_PERIOD_SECONDS = 24 * 60 * 60
STORAGE_REFERENCE_STATUSES = (
    "REFERENCED",
    "CONFIRMED_ORPHAN",
    "UNCERTAIN",
    "IGNORED_SYSTEM_FILE",
)
_MANAGED_ASSET_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "svg"}
_MANAGED_EXPORT_EXTENSIONS = {"pdf", "zip"}


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


def normalize_storage_reference(
    storage_or_root: Any,
    value: Any,
    *,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """参照値を管理Storage keyへ正規化する。

    旧``file_path``、``/media/<project>/<filename>``、Storage keyを受け付けるが、
    URL・パストラバーサル・保存ルート外の絶対パスは参照として扱わない。
    ``category``は曖昧な旧basenameを解決するためだけに使い、ファイル操作は行わない。
    """

    if value is None:
        return None
    raw = str(value).strip()
    if not raw or "\x00" in raw:
        return None
    parsed = urlparse(raw)
    # https://等の外部URLやWindowsドライブ風の値をローカル参照にしない。
    if parsed.scheme or parsed.netloc:
        return None

    root = _capacity_root(storage_or_root)
    normalized = raw.replace("\\", "/")
    # 現行media URLはDBに画像URLとして残るため、asset keyへ変換する。
    media_parts = normalized.lstrip("/").split("/")
    if len(media_parts) >= 3 and media_parts[0].lower() == "media":
        if category not in {None, "assets"}:
            return None
        media_project = project_id or media_parts[1]
        filename = PurePosixPath(media_parts[-1]).name
        if not filename or filename in {".", ".."}:
            return None
        return f"assets/{_safe_component(media_project, 'project')}/{filename}"

    # 絶対パスは、保存ルート内にある場合だけ相対keyへ落とす。存在しない
    # パスでもresolve(strict=False)で安全に判定できる。
    if normalized.startswith("/"):
        absolute = Path(normalized).expanduser()
        try:
            normalized = absolute.resolve().relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            # 保存ルート外の絶対パスは、見た目がassets/exportsでも採用しない。
            # 旧file_pathは実際の設定済みルート配下であれば上のrelative_toで復元できる。
            return None

    normalized = normalized.lstrip("/")
    # 旧/相対media表記も同じ変換規則へ通す。
    media_parts = normalized.split("/")
    if len(media_parts) >= 3 and media_parts[0].lower() == "media":
        if category not in {None, "assets"}:
            return None
        media_project = project_id or media_parts[1]
        filename = PurePosixPath(media_parts[-1]).name
        if not filename or filename in {".", ".."}:
            return None
        return f"assets/{_safe_component(media_project, 'project')}/{filename}"

    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if parts and parts[0] in {"assets", "exports"}:
        if category and parts[0] != category:
            return None
        return "/".join(parts)

    # 旧DBのbasenameのみの値は、Project文脈があるassetに限って補完する。
    filename = PurePosixPath(normalized).name
    if category == "assets" and project_id and filename not in {"", ".", ".."}:
        return f"assets/{_safe_component(project_id, 'project')}/{filename}"
    if category == "exports" and filename not in {"", ".", ".."}:
        return f"exports/{filename}"
    return None


def _normalize_reference_key(
    storage: Any,
    value: Any,
    *,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """後方互換用の内部別名。すべての監査経路を共通正規化へ揃える。"""

    return normalize_storage_reference(
        storage,
        value,
        category=category,
        project_id=project_id,
    )


def _iter_storage_file_records(root: Path, prefix: str) -> dict[str, dict[str, Any]]:
    directory = root / prefix
    result: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return result
    try:
        for item in directory.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                stat = item.stat()
                result[item.relative_to(root).as_posix()] = {
                    "path": item,
                    "size_bytes": max(0, int(stat.st_size)),
                    "modified_at_epoch": float(stat.st_mtime),
                }
            except (OSError, ValueError):
                continue
    except OSError:
        return result
    return result


def _graph_values(graph: Optional[Mapping[str, Any]], category: str) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    """参照グラフから完全keyとprefix参照を取り出す。"""

    exact: dict[str, list[dict[str, Any]]] = {}
    prefixes: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(graph, Mapping):
        return exact, prefixes

    source = graph.get(category, {})
    if isinstance(source, Mapping):
        for key, owners in source.items():
            owner_list = owners if isinstance(owners, list) else [owners]
            exact[str(key)] = [
                dict(owner) if isinstance(owner, Mapping) else {"source": str(owner)}
                for owner in owner_list
            ]
    elif isinstance(source, (list, tuple, set)):
        for key in source:
            exact[str(key)] = []

    prefix_source = graph.get(f"{category}_prefixes", [])
    if isinstance(prefix_source, Mapping):
        prefix_iter = prefix_source.items()
    elif isinstance(prefix_source, (list, tuple, set)):
        prefix_iter = ((value, {}) for value in prefix_source)
    else:
        prefix_iter = ()
    for prefix, owners in prefix_iter:
        owner_list = owners if isinstance(owners, list) else [owners]
        owner = (
            dict(owner_list[0])
            if owner_list and isinstance(owner_list[0], Mapping)
            else {"source": str(owner_list[0])} if owner_list else {}
        )
        prefixes.append((str(prefix), owner))
    return exact, prefixes


def _graph_protected(graph: Optional[Mapping[str, Any]], category: str) -> tuple[set[str], list[tuple[str, dict[str, Any]]]]:
    """active Job等の保護対象を取り出す。"""

    if not isinstance(graph, Mapping):
        return set(), []
    exact_source = graph.get("protected", {})
    exact_values: Any = exact_source.get(category, []) if isinstance(exact_source, Mapping) else []
    exact = {str(value) for value in exact_values if value}
    prefix_source = graph.get("protected_prefixes", {})
    prefix_values: Any = prefix_source.get(category, {}) if isinstance(prefix_source, Mapping) else {}
    if isinstance(prefix_values, Mapping):
        iterator = prefix_values.items()
    elif isinstance(prefix_values, (list, tuple, set)):
        iterator = ((value, {}) for value in prefix_values)
    else:
        iterator = ()
    prefixes: list[tuple[str, dict[str, Any]]] = []
    for prefix, owner in iterator:
        prefixes.append((str(prefix), dict(owner) if isinstance(owner, Mapping) else {"source": str(owner)}))
    return exact, prefixes


def _is_system_storage_file(key: str, path: Path) -> Optional[str]:
    """アプリケーションが削除候補にしてはいけないシステム/一時ファイル。"""

    name = path.name.lower()
    if (
        name.startswith(".")
        or name.endswith((".tmp", ".part", ".partial", ".lock"))
        or name.endswith(("-wal", "-shm", "-journal"))
        or name in {"thumbs.db", ".ds_store"}
        or "/sqlite" in key.lower()
        or name.endswith((".db", ".sqlite", ".sqlite3"))
    ):
        return "SQLite／一時／システムファイルのため監査対象外"
    return None


def _managed_file_name(category: str, key: str) -> bool:
    """現行Storageが生成する命名規則かを控えめに判定する。"""

    suffix = PurePosixPath(key).suffix.lower().lstrip(".")
    if category == "assets":
        if suffix not in _MANAGED_ASSET_EXTENSIONS:
            return False
        name = PurePosixPath(key).name
        return bool(re.search(r"-r\d+\.[A-Za-z0-9]+$", name))
    if category == "exports":
        if suffix not in _MANAGED_EXPORT_EXTENSIONS:
            return False
        return len(PurePosixPath(key).name) > len(suffix) + 2
    return False


def _format_modified_at(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _size_bucket(size_bytes: int) -> str:
    if size_bytes < 1 * 1024 * 1024:
        return "<1MB"
    if size_bytes < 10 * 1024 * 1024:
        return "1-10MB"
    if size_bytes < 100 * 1024 * 1024:
        return "10-100MB"
    return ">=100MB"


def classify_storage_objects(
    storage_or_root: Any = None,
    *,
    reference_graph: Optional[Mapping[str, Any]] = None,
    referenced_asset_keys: Iterable[str] = (),
    referenced_export_keys: Iterable[str] = (),
    grace_period_seconds: int = ORPHAN_GRACE_PERIOD_SECONDS,
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    """保存ファイルを証拠付きで分類する読み取り専用監査。

    ``CONFIRMED_ORPHAN``は、管理namespace・既知の命名・全参照不在・Job保護なし・
    grace period経過をすべて満たす場合だけ付与する。判断材料が一つでも曖昧なら
    ``UNCERTAIN``へ倒し、自動削除の経路は提供しない。
    """

    root = _capacity_root(storage_or_root)
    now = float(time.time() if now_epoch is None else now_epoch)

    graph: dict[str, Any] = dict(reference_graph or {})
    # 既存呼び出し（単純なkey配列）も同じ分類器へ取り込む。
    if referenced_asset_keys:
        existing = graph.setdefault("assets", {})
        if isinstance(existing, Mapping):
            existing = dict(existing)
            graph["assets"] = existing
            for value in referenced_asset_keys:
                existing.setdefault(str(value), [])
    if referenced_export_keys:
        existing = graph.setdefault("exports", {})
        if isinstance(existing, Mapping):
            existing = dict(existing)
            graph["exports"] = existing
            for value in referenced_export_keys:
                existing.setdefault(str(value), [])

    asset_refs, asset_prefixes = _graph_values(graph, "assets")
    export_refs, export_prefixes = _graph_values(graph, "exports")
    protected_assets, protected_asset_prefixes = _graph_protected(graph, "assets")
    protected_exports, protected_export_prefixes = _graph_protected(graph, "exports")
    unresolved_source = graph.get("unresolved", {}) if isinstance(graph, Mapping) else {}
    unresolved_assets = (
        list(unresolved_source.get("assets", []))
        if isinstance(unresolved_source, Mapping)
        and isinstance(unresolved_source.get("assets", []), (list, tuple, set))
        else []
    )
    unresolved_exports = (
        list(unresolved_source.get("exports", []))
        if isinstance(unresolved_source, Mapping)
        and isinstance(unresolved_source.get("exports", []), (list, tuple, set))
        else []
    )

    def normalize_map(
        values: Mapping[str, list[dict[str, Any]]], category: str
    ) -> dict[str, list[dict[str, Any]]]:
        normalized: dict[str, list[dict[str, Any]]] = {}
        for value, owners in values.items():
            key = normalize_storage_reference(storage_or_root or root, value, category=category)
            if not key:
                continue
            normalized.setdefault(key, []).extend(owners)
        return normalized

    asset_refs = normalize_map(asset_refs, "assets")
    export_refs = normalize_map(export_refs, "exports")
    protected_assets = {
        key
        for value in protected_assets
        if (key := normalize_storage_reference(storage_or_root or root, value, category="assets"))
    }
    protected_exports = {
        key
        for value in protected_exports
        if (key := normalize_storage_reference(storage_or_root or root, value, category="exports"))
    }

    def normalize_prefixes(
        values: list[tuple[str, dict[str, Any]]], category: str
    ) -> list[tuple[str, dict[str, Any]]]:
        normalized: list[tuple[str, dict[str, Any]]] = []
        for prefix, owner in values:
            # Prefixは最後のrevision/export拡張子までを含むため、key全体の
            # 正規化ではなくnamespace/traversalだけを検証する。
            raw = str(prefix).replace("\\", "/").lstrip("/")
            parts = raw.split("/")
            if any(part in {"", ".", ".."} for part in parts):
                continue
            if not parts or parts[0] != category:
                continue
            normalized.append(("/".join(parts), owner))
        return normalized

    asset_prefixes = normalize_prefixes(asset_prefixes, "assets")
    export_prefixes = normalize_prefixes(export_prefixes, "exports")
    protected_asset_prefixes = normalize_prefixes(protected_asset_prefixes, "assets")
    protected_export_prefixes = normalize_prefixes(protected_export_prefixes, "exports")

    records: list[dict[str, Any]] = []
    for category, files, exact_refs, prefixes, protected, protected_prefixes in (
        (
            "assets",
            _iter_storage_file_records(root, "assets"),
            asset_refs,
            asset_prefixes,
            protected_assets,
            protected_asset_prefixes,
        ),
        (
            "exports",
            _iter_storage_file_records(root, "exports"),
            export_refs,
            export_prefixes,
            protected_exports,
            protected_export_prefixes,
        ),
    ):
        unresolved = unresolved_assets if category == "assets" else unresolved_exports
        for key, metadata in sorted(files.items()):
            path = metadata["path"]
            size_bytes = int(metadata["size_bytes"])
            modified_epoch = float(metadata["modified_at_epoch"])
            age_seconds = max(0, int(now - modified_epoch))
            exact_match = key in exact_refs
            owners = list(exact_refs.get(key, []))
            classification = "UNCERTAIN"
            reason = "参照状態を追加確認する必要があります"
            confidence = 0.4
            system_reason = _is_system_storage_file(key, path)
            if system_reason:
                classification = "IGNORED_SYSTEM_FILE"
                reason = system_reason
                confidence = 1.0
            elif exact_match:
                classification = "REFERENCED"
                reason = "DBまたはProjectデータから有効な参照があります"
                confidence = 1.0
            else:
                prefix_owners: list[dict[str, Any]] = []
                for prefix, owner in prefixes:
                    if key.startswith(prefix):
                        prefix_owners.append(owner)
                protected_owner: list[dict[str, Any]] = []
                if key in protected:
                    protected_owner.append({"source": "active_or_retryable_job"})
                for prefix, owner in protected_prefixes:
                    if key.startswith(prefix):
                        protected_owner.append(owner)
                unresolved_match = [
                    dict(item)
                    for item in unresolved
                    if isinstance(item, Mapping)
                    and str(item.get("basename") or "") == path.name
                ]
                if prefix_owners:
                    classification = "REFERENCED"
                    owners.extend(prefix_owners)
                    reason = "現行Panel／Exportのrevision系列として参照されています"
                    confidence = 0.95
                elif protected_owner:
                    classification = "UNCERTAIN"
                    owners.extend(protected_owner)
                    reason = "進行中または再試行可能なJobの出力候補として保護されています"
                    confidence = 0.9
                elif unresolved_match:
                    classification = "UNCERTAIN"
                    # 旧絶対パスそのものは管理画面/APIへ返さず、必要最小限の
                    # 出所・basenameだけで判断根拠を示す。
                    owners.extend(
                        {
                            key: value
                            for key, value in item.items()
                            if key != "value"
                        }
                        for item in unresolved_match
                    )
                    reason = "旧参照のStorage keyを安全に解決できないため判断保留です"
                    confidence = 0.2
                elif age_seconds < max(ORPHAN_GRACE_PERIOD_SECONDS, int(grace_period_seconds)):
                    reason = "作成からgrace period未満のため保護されています"
                    confidence = 0.75
                elif not _managed_file_name(category, key):
                    reason = "Storageの既知命名規則に一致しないため判断保留です"
                    confidence = 0.25
                else:
                    classification = "CONFIRMED_ORPHAN"
                    reason = "管理namespace・既知命名・全参照不在・保護対象外・grace period経過"
                    confidence = 0.95
            legacy_reference = any(bool(owner.get("legacy_reference_detected")) for owner in owners)
            record = {
                "key": key,
                "storage_key": key,
                "category": category,
                "size_bytes": size_bytes,
                "modified_at": _format_modified_at(modified_epoch),
                "age_seconds": age_seconds,
                "classification": classification,
                "reason": reason,
                "referencing_entities": owners,
                "confidence": confidence,
                "legacy_reference_detected": legacy_reference,
                "size_bucket": _size_bucket(size_bytes),
            }
            records.append(record)

    summary: dict[str, dict[str, int]] = {
        status: {"count": 0, "bytes": 0} for status in STORAGE_REFERENCE_STATUSES
    }
    for record in records:
        bucket = summary[record["classification"]]
        bucket["count"] += 1
        bucket["bytes"] += record["size_bytes"]

    confirmed = [item for item in records if item["classification"] == "CONFIRMED_ORPHAN"]
    candidates = [
        item
        for item in records
        if item["classification"] in {"CONFIRMED_ORPHAN", "UNCERTAIN"}
    ]

    def group_by(key_fn: Any) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, int]] = {}
        for item in confirmed:
            label = str(key_fn(item))
            bucket = grouped.setdefault(label, {"label": label, "count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += int(item["size_bytes"])
        return [grouped[key] for key in sorted(grouped)]

    report = {
        "files": records,
        # 旧UI/API互換: assets/exportsは削除候補全体（UNCERTAINを含む）を返す。
        "assets": [item for item in candidates if item["category"] == "assets"],
        "exports": [item for item in candidates if item["category"] == "exports"],
        "referenced": [item for item in records if item["classification"] == "REFERENCED"],
        "confirmed_orphans": confirmed,
        "uncertain": [item for item in records if item["classification"] == "UNCERTAIN"],
        "ignored_system_files": [item for item in records if item["classification"] == "IGNORED_SYSTEM_FILE"],
        "classification_summary": summary,
        "classification_counts": {key: value["count"] for key, value in summary.items()},
        "classification_bytes": {key: value["bytes"] for key, value in summary.items()},
        "referenced_count": summary["REFERENCED"]["count"],
        "confirmed_orphan_count": summary["CONFIRMED_ORPHAN"]["count"],
        "uncertain_count": summary["UNCERTAIN"]["count"],
        "ignored_system_count": summary["IGNORED_SYSTEM_FILE"]["count"],
        "reclaimable_bytes": summary["CONFIRMED_ORPHAN"]["bytes"],
        "asset_count": len([item for item in candidates if item["category"] == "assets"]),
        "export_count": len([item for item in candidates if item["category"] == "exports"]),
        "asset_bytes": sum(item["size_bytes"] for item in candidates if item["category"] == "assets"),
        "export_bytes": sum(item["size_bytes"] for item in candidates if item["category"] == "exports"),
        "groups": {
            "age": group_by(lambda item: "<7日" if item["age_seconds"] < 7 * 86400 else "7-30日" if item["age_seconds"] < 30 * 86400 else ">=30日"),
            "extension": group_by(lambda item: PurePosixPath(item["key"]).suffix.lower() or "(なし)"),
            "directory": group_by(lambda item: "/".join(PurePosixPath(item["key"]).parts[:2])),
            "size_bucket": group_by(lambda item: item["size_bucket"]),
        },
        "unresolved_reference_count": len(unresolved_assets) + len(unresolved_exports),
        "unresolved_reference_counts": {
            "assets": len(unresolved_assets),
            "exports": len(unresolved_exports),
        },
        "grace_period_seconds": max(ORPHAN_GRACE_PERIOD_SECONDS, int(grace_period_seconds)),
        "mode": "report_only",
    }
    return report


def storage_cleanup_dry_run(report: Mapping[str, Any]) -> dict[str, Any]:
    """確認済み孤児だけを対象にした削除予定のdry-run（ファイル変更なし）。"""

    confirmed = [
        dict(item)
        for item in (report.get("confirmed_orphans") or [])
        if isinstance(item, Mapping) and item.get("classification") == "CONFIRMED_ORPHAN"
    ]
    return {
        "mode": "dry_run",
        "would_delete": confirmed,
        "file_count": len(confirmed),
        "reclaimable_bytes": sum(max(0, int(item.get("size_bytes", 0))) for item in confirmed),
        "excluded_counts": {
            "referenced": int((report.get("classification_counts") or {}).get("REFERENCED", 0)),
            "uncertain": int((report.get("classification_counts") or {}).get("UNCERTAIN", 0)),
            "ignored_system_file": int((report.get("classification_counts") or {}).get("IGNORED_SYSTEM_FILE", 0)),
        },
        "automatic_deletion": False,
    }


def find_orphan_storage_objects(
    storage_or_root: Any = None,
    *,
    reference_graph: Optional[Mapping[str, Any]] = None,
    referenced_asset_keys: Iterable[str] = (),
    referenced_export_keys: Iterable[str] = (),
    grace_period_seconds: int = ORPHAN_GRACE_PERIOD_SECONDS,
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    """後方互換名。実体は証拠付き分類器で、削除は行わない。"""

    return classify_storage_objects(
        storage_or_root,
        reference_graph=reference_graph,
        referenced_asset_keys=referenced_asset_keys,
        referenced_export_keys=referenced_export_keys,
        grace_period_seconds=grace_period_seconds,
        now_epoch=now_epoch,
    )


# 呼び出し側が意図を読み取りやすい別名。削除を実行するAPIは提供しない。
get_storage_usage = storage_usage
get_storage_status = storage_status
detect_orphan_storage_objects = find_orphan_storage_objects
audit_storage_objects = classify_storage_objects
dry_run_storage_cleanup = storage_cleanup_dry_run


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
