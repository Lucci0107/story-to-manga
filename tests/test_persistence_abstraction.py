"""DB接続とObject Storage境界の移行準備テスト。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db
from app.config import get_settings
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


def test_storage_factory_rejects_unimplemented_external_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    with pytest.raises(StorageConfigurationError):
        get_storage(get_settings())
