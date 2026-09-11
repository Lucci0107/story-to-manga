"""DB接続とObject Storage境界の移行準備テスト。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app import db
from app.config import get_settings
from app.services import database as database_service
from app.services.database import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    PostgreSQLDatabase,
    SQLiteDatabase,
    _adapt_qmark_placeholders,
    create_database,
)
from app.services.storage import (
    LocalFileStorage,
    StorageConfigurationError,
    StorageError,
    get_storage,
)


class _FakeCursor:
    """SQLite cursorの最小テストdouble。"""

    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _FakeSQLiteConnection:
    """WAL切替失敗を再現する非永続のSQLite接続double。"""

    def __init__(
        self,
        *,
        journal_mode: str = "delete",
        wal_error: str | None = None,
        journal_read_error: str | None = None,
    ) -> None:
        self.journal_mode = journal_mode
        self.wal_error = wal_error
        self.journal_read_error = journal_read_error
        self.calls: list[str] = []
        self.row_factory: object = None
        self.closed = False

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> _FakeCursor:
        del parameters
        self.calls.append(statement)
        normalized = " ".join(statement.lower().split())
        if normalized == "pragma journal_mode":
            if self.journal_read_error:
                raise sqlite3.OperationalError(self.journal_read_error)
            return _FakeCursor((self.journal_mode,))
        if normalized == "pragma journal_mode = wal":
            if self.wal_error:
                raise sqlite3.OperationalError(self.wal_error)
            self.journal_mode = "wal"
            return _FakeCursor(("wal",))
        if normalized == "pragma journal_mode = delete":
            self.journal_mode = "delete"
            return _FakeCursor(("delete",))
        if normalized == "select 1":
            return _FakeCursor((1,))
        if normalized == "pragma schema_version":
            return _FakeCursor((1,))
        if normalized == "pragma foreign_keys":
            return _FakeCursor((1,))
        if normalized == "pragma busy_timeout":
            return _FakeCursor((20_000,))
        if normalized == "pragma synchronous":
            return _FakeCursor((1,))
        return _FakeCursor()

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def test_sqlite_wal_is_preferred_and_connection_pragmas_are_preserved(tmp_path: Path) -> None:
    """通常のSQLiteではWALを有効化し、基本pragmaを維持する。"""

    backend = SQLiteDatabase(tmp_path / "story.sqlite3")
    with backend.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 20_000


def test_sqlite_wal_disk_error_falls_back_without_repeating_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """WALのdisk I/Oだけを既存DELETEへフォールバックし、次の接続で再試行しない。"""

    database_service._SQLITE_JOURNAL_MODE_CACHE.clear()
    first = _FakeSQLiteConnection(wal_error="disk I/O error")
    second = _FakeSQLiteConnection()
    connections = iter((first, second))
    monkeypatch.setattr(database_service.sqlite3, "connect", lambda *args, **kwargs: next(connections))
    backend = SQLiteDatabase(tmp_path / "story.sqlite3")

    with caplog.at_level(logging.WARNING, logger=database_service.logger.name):
        with backend.connect() as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1
        with backend.connect() as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1

    assert first.journal_mode == "delete"
    assert any("互換journal mode" in record.message for record in caplog.records)
    assert any("PRAGMA journal_mode = WAL" == statement for statement in first.calls)
    assert not any("journal_mode = WAL" in statement for statement in second.calls)


def test_sqlite_wal_error_keeps_existing_wal_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既にWALのDBでは再交渉失敗時もモードを変更しない。"""

    database_service._SQLITE_JOURNAL_MODE_CACHE.clear()
    raw = _FakeSQLiteConnection(journal_mode="wal", wal_error="disk I/O error")
    monkeypatch.setattr(database_service.sqlite3, "connect", lambda *args, **kwargs: raw)

    with SQLiteDatabase(tmp_path / "story.sqlite3").connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    assert raw.journal_mode == "wal"
    assert not any("journal_mode = DELETE" in statement for statement in raw.calls)


def test_sqlite_wal_and_mode_read_disk_errors_use_delete_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WALと現在モードの読取がともにI/O失敗でもDELETEを試す。"""

    database_service._SQLITE_JOURNAL_MODE_CACHE.clear()
    raw = _FakeSQLiteConnection(
        journal_mode="",
        wal_error="disk I/O error",
        journal_read_error="disk I/O error",
    )
    monkeypatch.setattr(database_service.sqlite3, "connect", lambda *args, **kwargs: raw)

    with SQLiteDatabase(tmp_path / "story.sqlite3").connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    assert raw.journal_mode == "delete"
    assert any("PRAGMA journal_mode = DELETE" == statement for statement in raw.calls)


def test_sqlite_wal_unrelated_operational_error_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """破損などWAL切替以外のエラーはフォールバックせず起動を失敗させる。"""

    database_service._SQLITE_JOURNAL_MODE_CACHE.clear()
    raw = _FakeSQLiteConnection(wal_error="database disk image is malformed")
    monkeypatch.setattr(database_service.sqlite3, "connect", lambda *args, **kwargs: raw)

    with caplog.at_level(logging.WARNING, logger=database_service.logger.name):
        with pytest.raises(sqlite3.OperationalError, match="malformed"):
            SQLiteDatabase(tmp_path / "story.sqlite3").connect()

    assert not any("互換journal mode" in record.message for record in caplog.records)
    assert raw.closed


def test_sqlite_unopenable_database_still_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB自体を開けない場合はWALフォールバック対象にしない。"""

    database_service._SQLITE_JOURNAL_MODE_CACHE.clear()

    def fail_connect(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(database_service.sqlite3, "connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        SQLiteDatabase(tmp_path / "missing" / "story.sqlite3").connect()


def test_database_url_defaults_to_sqlite_and_supports_repository_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既定のSQLiteを維持しつつ、Repositoryがbackendへ接続できる。"""

    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = get_settings()
    backend = create_database(settings)

    assert isinstance(backend, SQLiteDatabase)
    assert settings.database_url.startswith("sqlite:")
    assert backend.database_path == tmp_path / "story_manga.sqlite3"
    with backend.connect() as connection:
        connection.execute("CREATE TABLE boundary_check (id TEXT PRIMARY KEY)")
        assert "id" in connection.table_columns("boundary_check")


def test_database_url_selects_postgresql_backend_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部DBへ接続せず、DATABASE_URLだけで将来backendを切り替えられる。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@example.invalid/app")
    backend = create_database(get_settings())

    assert isinstance(backend, PostgreSQLDatabase)
    assert backend.backend_name == "postgresql"


def test_postgresql_connection_error_does_not_expose_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接続失敗時もDSNを例外メッセージへ含めない。"""

    import psycopg

    def fail_connect(*args: object, **kwargs: object) -> None:
        raise RuntimeError("接続先の詳細を含む内部エラー")

    monkeypatch.setattr(psycopg, "connect", fail_connect)
    backend = PostgreSQLDatabase("postgresql://user:password@example.invalid/app")
    with pytest.raises(DatabaseConnectionError) as error:
        backend.connect()
    assert str(error.value) == "PostgreSQLへ接続できません"
    assert "password" not in str(error.value)


def test_database_rejects_unknown_url_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://example.invalid/app")
    with pytest.raises(DatabaseConfigurationError):
        create_database(get_settings())


def test_postgresql_placeholder_adapter_preserves_sql_literals() -> None:
    statement = "SELECT '?' AS literal, ? AS value, \"?\" AS identifier"
    assert _adapt_qmark_placeholders(statement, "postgresql") == (
        "SELECT '?' AS literal, %s AS value, \"?\" AS identifier"
    )


def test_legacy_exports_table_receives_storage_key_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存のfile_pathだけのSQLiteでも新しいstorage_key列を追加できる。"""

    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    legacy_path = tmp_path / "story_manga.sqlite3"
    with sqlite3.connect(legacy_path) as connection:
        connection.execute(
            """
            CREATE TABLE exports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                format TEXT NOT NULL,
                file_path TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    db.init_db()
    with db.connection() as connection:
        assert "storage_key" in connection.table_columns("exports")


def test_local_storage_round_trip_and_key_safety(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    asset_key = storage.asset_key("project/1", "panel.png")
    export_key = storage.export_key("project/1", "export/1", "pdf")

    stored = storage.put_bytes(asset_key, b"image", content_type="image/png")
    storage.put_bytes(export_key, b"pdf", content_type="application/pdf")

    assert stored.key == "assets/project-1/panel.png"
    assert storage.get_bytes(asset_key) == b"image"
    assert storage.get_bytes(export_key) == b"pdf"
    assert storage.exists(asset_key)
    with pytest.raises(StorageError):
        storage.get_bytes("../outside.txt")


def test_local_storage_deletes_only_selected_project_objects(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    own_asset = storage.asset_key("project-one", "panel.png")
    own_export = storage.export_key("project-one", "export-one", "pdf")
    other_asset = storage.asset_key("project-two", "panel.png")
    other_export = storage.export_key("project-two", "export-two", "zip")
    for key in (own_asset, own_export, other_asset, other_export):
        storage.put_bytes(key, b"data")

    assert storage.delete_project_objects("project-one") == 2
    assert not storage.exists(own_asset)
    assert not storage.exists(own_export)
    assert storage.exists(other_asset)
    assert storage.exists(other_export)


def test_interrupted_jobs_become_retryable_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.init_db()
    user = db.create_user("job-recovery@example.com", "long-password")
    project = db.create_project(user["id"], "再起動", "本文")
    db.update_project(
        project["id"],
        user["id"],
        storyboard=[
            {
                "id": "page-1",
                "page_number": 1,
                "layout": "hero",
                "panels": [
                    {
                        "id": "panel-1",
                        "order": 1,
                        "generation_status": "processing",
                    }
                ],
            }
        ],
        status="processing",
        current_step="generate",
    )

    first = db.create_generation_job(project["id"], "panel-1", "same-request")
    duplicate = db.create_generation_job(project["id"], "panel-1", "same-request")
    assert first is not None and duplicate is not None
    assert duplicate["id"] == first["id"]

    db.init_db()
    interrupted = db.get_generation_job(first["id"])
    assert interrupted is not None
    assert interrupted["status"] == "failed"
    assert "再試行" in interrupted["error"]
    recovered_project = db.get_project(project["id"], user["id"])
    assert recovered_project is not None
    assert recovered_project["status"] == "partially_failed"
    recovered_panel = recovered_project["storyboard"][0]["panels"][0]
    assert recovered_panel["generation_status"] == "failed"
    assert "再試行" in recovered_panel["generation_error"]

    retry = db.create_generation_job(project["id"], "panel-1", "same-request")
    assert retry is not None
    assert retry["id"] != first["id"]


def test_storage_factory_rejects_unimplemented_external_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    with pytest.raises(StorageConfigurationError):
        get_storage(get_settings())
