"""Storage孤児のCleanup Planと、二重検証付き削除ワークフロー。

このモジュールは削除を自動実行しない。削除処理は管理者が明示確認を送った
場合だけ呼び出され、各ファイルの直前にcanonical reference graphを再収集して
再分類する。分類が一つでも揺らいだファイルは安全側へスキップする。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional

from .. import db
from .storage import (
    _is_system_storage_file,
    _managed_file_name,
    classify_storage_objects,
    get_storage,
    normalize_storage_reference,
    storage_cleanup_dry_run,
    storage_usage,
)


CLEANUP_CONFIRMATION = "DELETE"
CLEANUP_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}


class StorageCleanupError(RuntimeError):
    """Cleanupワークフローの利用または実行に失敗した。"""


class StorageCleanupNotFound(StorageCleanupError):
    """Cleanup Planが見つからない。"""


class StorageCleanupConflict(StorageCleanupError):
    """別のCleanupが実行中、またはPlanが実行済み。"""


class StorageCleanupConfirmationError(StorageCleanupError):
    """明示確認文字列が一致しない。"""


class _CleanupSkip(Exception):
    """安全検証でファイルを削除対象から外す内部例外。"""

    def __init__(self, status: str, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(reason)


class _CleanupPathError(Exception):
    """Storage root外やsymlink等の危険なパスを拒否する。"""


_cleanup_lock = threading.Lock()


def _storage_root(storage: Any) -> Path:
    root = getattr(storage, "root", None)
    if not root:
        raise StorageCleanupError("ローカルStorageの保存ルートを確認できません")
    return Path(root).expanduser().resolve()


def _scan(storage: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """現行DBから参照グラフを作り、分類とdry-runを一度だけ行う。"""

    root = _storage_root(storage)
    graph = db.collect_storage_references(root)
    report = classify_storage_objects(storage, reference_graph=graph)
    dry_run = storage_cleanup_dry_run(report)
    return graph, report, dry_run


def _excluded_count(report: Mapping[str, Any]) -> int:
    counts = report.get("classification_counts") or {}
    return sum(
        int(counts.get(status, 0) or 0)
        for status in ("REFERENCED", "UNCERTAIN", "IGNORED_SYSTEM_FILE")
    )


def create_cleanup_dry_run_plan(
    admin_id: str,
    *,
    storage: Any = None,
) -> Dict[str, Any]:
    """最新状態を監査して、削除予定だけを永続化する。ファイルは変更しない。"""

    storage = storage or get_storage()
    graph, report, dry_run = _scan(storage)
    items = list(dry_run.get("would_delete") or [])
    summary = report.get("classification_summary") or {}
    plan = db.create_storage_cleanup_plan(
        str(admin_id),
        uuid.uuid4().hex,
        reference_graph_version=int(graph.get("version", 1) or 1),
        candidate_count=len(items),
        candidate_bytes=int(dry_run.get("reclaimable_bytes", 0) or 0),
        confirmed_orphan_count=int((summary.get("CONFIRMED_ORPHAN") or {}).get("count", 0)),
        uncertain_count=int((summary.get("UNCERTAIN") or {}).get("count", 0)),
        excluded_count=_excluded_count(report),
        items=items,
    )
    if not plan:
        raise StorageCleanupError("Cleanup Planを保存できませんでした")
    return {
        "plan": plan,
        "dry_run": {
            "mode": "dry_run",
            "plan_id": plan["id"],
            "candidate_count": len(items),
            "confirmed_orphan_count": plan["confirmed_orphan_count"],
            "uncertain_count": plan["uncertain_count"],
            "referenced_count": int((report.get("classification_counts") or {}).get("REFERENCED", 0) or 0),
            "ignored_system_file_count": int((report.get("classification_counts") or {}).get("IGNORED_SYSTEM_FILE", 0) or 0),
            "excluded_count": plan["excluded_count"],
            "reclaimable_bytes": int(dry_run.get("reclaimable_bytes", 0) or 0),
            "excluded_counts": dry_run.get("excluded_counts") or {},
            "category_counts": {
                "assets": sum(1 for item in items if item.get("category") == "assets"),
                "exports": sum(1 for item in items if item.get("category") == "exports"),
            },
            "automatic_deletion": False,
        },
    }


def _safe_delete_path(storage: Any, storage_key: str, category: str) -> tuple[str, Path]:
    """管理namespace内の通常ファイルだけを返す。symlink escapeも拒否する。"""

    if category not in {"assets", "exports"}:
        raise _CleanupPathError("管理対象namespaceではありません")
    normalized = normalize_storage_reference(storage, storage_key, category=category)
    if not normalized:
        raise _CleanupPathError("Storage keyを安全に正規化できません")
    parts = PurePosixPath(normalized).parts
    if len(parts) < 2 or parts[0] != category or any(part in {"", ".", ".."} for part in parts):
        raise _CleanupPathError("Storage keyが不正です")

    root = _storage_root(storage)
    candidate = root.joinpath(*parts)
    current = root
    # 途中のディレクトリを含むsymlinkを許可しない。resolveだけでは競合時の
    # 判定が曖昧になるため、実在する各要素をlstat相当で確認する。
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                raise _CleanupPathError("symlink経由のStorage keyは削除できません")
        except OSError as exc:
            raise _CleanupPathError("Storage keyの安全性を確認できません") from exc

    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
        namespace = (root / category).resolve(strict=False)
        resolved.relative_to(namespace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _CleanupPathError("保存ルート外のStorage keyは削除できません") from exc

    if candidate.is_symlink():
        raise _CleanupPathError("symlinkの削除は許可されていません")
    if _is_system_storage_file(normalized, candidate):
        raise _CleanupPathError("SQLite／一時／システムファイルは削除できません")
    if not _managed_file_name(category, normalized):
        raise _CleanupPathError("既知の管理ファイル名ではありません")
    return normalized, candidate


def _skip_for_classification(record: Optional[Mapping[str, Any]]) -> Optional[_CleanupSkip]:
    if record is None:
        return _CleanupSkip("SKIPPED_ABSENT", "再検証時にファイルが見つかりません")
    classification = str(record.get("classification") or "UNCERTAIN")
    if classification == "REFERENCED":
        return _CleanupSkip("SKIPPED_REFERENCED", "再検証時にDBまたはProject参照が見つかりました")
    if classification == "UNCERTAIN":
        return _CleanupSkip("SKIPPED_UNCERTAIN", "再検証時に参照状態を確定できませんでした")
    if classification != "CONFIRMED_ORPHAN":
        return _CleanupSkip("SKIPPED_CHANGED", "システム／保護対象として再分類されました")
    return None


def _final_status(deleted: int, skipped: int, failed: int) -> str:
    if failed:
        return "PARTIAL"
    return "COMPLETED"


def execute_cleanup_plan(
    plan_id: str,
    admin_id: str,
    confirmation: str,
    *,
    storage: Any = None,
) -> Dict[str, Any]:
    """Planを実行する。各Itemの直前に参照グラフと分類を再取得する。"""

    if str(confirmation).strip() != CLEANUP_CONFIRMATION:
        raise StorageCleanupConfirmationError("確認文字列が一致しません")
    if not _cleanup_lock.acquire(blocking=False):
        raise StorageCleanupConflict("別のStorage cleanupが実行中です")
    try:
        plan = db.get_storage_cleanup_plan(str(plan_id), include_items=True)
        if not plan:
            raise StorageCleanupNotFound("Cleanup Planが見つかりません")
        status = str(plan.get("status") or "")
        if status in CLEANUP_TERMINAL_STATUSES:
            # 既に完了／部分完了したPlanを再実行しない。結果を返すだけにする。
            return {
                "plan": plan,
                "summary": _plan_summary(plan, idempotent=True),
                "idempotent": True,
            }
        if status in {"REVALIDATING", "RUNNING"}:
            raise StorageCleanupConflict("別のStorage cleanupが実行中です")

        storage = storage or get_storage()
        now = db.utc_now()
        db.update_storage_cleanup_plan(str(plan_id), status="REVALIDATING", revalidated_at=now)
        items = db.list_storage_cleanup_items(str(plan_id))
        requested = len(items)
        deleted = 0
        skipped = 0
        failed = 0
        reclaimed = 0
        try:
            before = storage_usage(storage).as_dict()
        except Exception:  # noqa: BLE001
            before = {}

        for item in items:
            item_id = str(item["id"])
            db.update_storage_cleanup_item(item_id, delete_status="REVALIDATING")
            record: Optional[Mapping[str, Any]] = None
            try:
                # 要件上、削除対象ごとに最新DB参照とStorage一覧を再取得する。
                graph = db.collect_storage_references(_storage_root(storage))
                report = classify_storage_objects(storage, reference_graph=graph)
                record = next(
                    (
                        candidate
                        for candidate in report.get("files", [])
                        if candidate.get("storage_key") == item.get("storage_key")
                    ),
                    None,
                )
                skip = _skip_for_classification(record)
                if skip:
                    skipped += 1
                    db.update_storage_cleanup_item(
                        item_id,
                        classification_at_execution=(record or {}).get("classification", "ABSENT"),
                        delete_status=skip.status,
                        exclusion_reason=skip.reason,
                    )
                    continue
                if str(record.get("category")) != str(item.get("category")):
                    raise _CleanupSkip("SKIPPED_CHANGED", "カテゴリが再検証前後で一致しません")

                evidence = item.get("evidence") or {}
                if int(record.get("size_bytes", 0) or 0) != int(item.get("size_bytes", 0) or 0):
                    raise _CleanupSkip("SKIPPED_CHANGED", "ファイルサイズがPlan作成後に変化しました")
                planned_mtime = str(evidence.get("modified_at") or "")
                if planned_mtime and planned_mtime != str(record.get("modified_at") or ""):
                    raise _CleanupSkip("SKIPPED_CHANGED", "更新時刻がPlan作成後に変化しました")

                normalized, path = _safe_delete_path(
                    storage,
                    str(item.get("storage_key") or ""),
                    str(item.get("category") or ""),
                )
                del normalized  # keyは安全性確認済みで、ログへ重複出力しない。
                if not path.exists():
                    raise _CleanupSkip("SKIPPED_ABSENT", "再検証後にファイルが見つかりません")
                if not path.is_file():
                    raise _CleanupSkip("SKIPPED_CHANGED", "対象が通常ファイルではありません")
                stat = path.stat()
                if int(stat.st_size) != int(item.get("size_bytes", 0) or 0):
                    raise _CleanupSkip("SKIPPED_CHANGED", "削除直前のファイルサイズが一致しません")
                delete_size = max(0, int(stat.st_size))
                path.unlink()
                if path.exists():
                    raise OSError("削除後もファイルが残っています")
                deleted += 1
                reclaimed += delete_size
                db.update_storage_cleanup_item(
                    item_id,
                    classification_at_execution="CONFIRMED_ORPHAN",
                    delete_status="DELETED",
                    deleted_at=db.utc_now(),
                )
            except _CleanupSkip as exc:
                skipped += 1
                db.update_storage_cleanup_item(
                    item_id,
                    classification_at_execution=(record or {}).get("classification", "UNKNOWN"),
                    delete_status=exc.status,
                    exclusion_reason=exc.reason,
                )
            except FileNotFoundError:
                skipped += 1
                db.update_storage_cleanup_item(
                    item_id,
                    classification_at_execution="ABSENT",
                    delete_status="SKIPPED_ABSENT",
                    exclusion_reason="削除直前にファイルが見つかりません",
                )
            except _CleanupPathError as exc:
                skipped += 1
                db.update_storage_cleanup_item(
                    item_id,
                    classification_at_execution=(record or {}).get("classification", "UNKNOWN"),
                    delete_status="SKIPPED_CHANGED",
                    exclusion_reason=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                # 一件のI/O失敗で他の候補の結果を失わない。例外本文やパスは記録しない。
                failed += 1
                db.update_storage_cleanup_item(
                    item_id,
                    classification_at_execution=(record or {}).get("classification", "UNKNOWN"),
                    delete_status="FAILED",
                    error_message=type(exc).__name__[:120],
                )

        try:
            after = storage_usage(storage).as_dict()
        except Exception:  # noqa: BLE001
            after = {}
        final = db.update_storage_cleanup_plan(
            str(plan_id),
            status=_final_status(deleted, skipped, failed),
            executed_at=db.utc_now(),
            executed_by=str(admin_id),
            revalidated_at=db.utc_now(),
            requested_count=requested,
            deleted_count=deleted,
            skipped_count=skipped,
            failed_count=failed,
            reclaimed_bytes=reclaimed,
            disk_used_before=before.get("used_bytes"),
            disk_used_after=after.get("used_bytes"),
            disk_total_bytes=after.get("total_bytes") or before.get("total_bytes"),
        )
        if not final:
            raise StorageCleanupError("Cleanup結果を保存できませんでした")
        final["items"] = db.list_storage_cleanup_items(str(plan_id))
        return {
            "plan": final,
            "summary": _plan_summary(final, idempotent=False),
            "idempotent": False,
        }
    finally:
        _cleanup_lock.release()


def _plan_summary(plan: Mapping[str, Any], *, idempotent: bool) -> Dict[str, Any]:
    """管理画面へ返す最小の実行結果。"""

    return {
        "plan_id": plan.get("id"),
        "status": plan.get("status"),
        "requested_count": int(plan.get("requested_count", 0) or 0),
        "deleted_count": int(plan.get("deleted_count", 0) or 0),
        "skipped_count": int(plan.get("skipped_count", 0) or 0),
        "failed_count": int(plan.get("failed_count", 0) or 0),
        "reclaimed_bytes": int(plan.get("reclaimed_bytes", 0) or 0),
        "disk_used_before": plan.get("disk_used_before"),
        "disk_used_after": plan.get("disk_used_after"),
        "automatic_deletion": False,
        "idempotent": idempotent,
    }


def latest_cleanup_plan() -> Optional[Dict[str, Any]]:
    """管理画面へ表示する最新Planの概要。"""

    return db.get_latest_storage_cleanup_plan()
