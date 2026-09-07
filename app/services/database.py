"""データベース接続の抽象化。

現在のRepository層は既存のSQLを利用するが、接続・トランザクション・行形式は
このモジュールへ閉じ込める。SQLiteを維持したまま、将来DATABASE_URLを
PostgreSQLへ切り替えられる境界を提供する。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Set
from urllib.parse import unquote, urlparse

from ..config import Settings, get_settings


class DatabaseConfigurationError(RuntimeError):
    """接続先の設定またはbackendが利用できない。"""


class DatabaseConnectionError(RuntimeError):
    """データベース接続に失敗した。"""


def _adapt_qmark_placeholders(statement: str, backend_name: str) -> str:
    """SQLiteの?形式をPostgreSQLの%s形式へ変換する。

    SQLリテラル内の疑問符は変換しないため、単純な文字列置換にしない。
    """

    if backend_name != "postgresql" or "?" not in statement:
        return statement
    output: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(statement):
        char = statement[index]
        if char == "'" and not in_double:
            output.append(char)
            if in_single and index + 1 < len(statement) and statement[index + 1] == "'":
                output.append("'")
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            output.append(char)
            in_double = not in_double
        elif char == "?" and not in_single and not in_double:
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _split_sql_script(script: str) -> Iterable[str]:
    """現在のDDL（文字列内セミコロンなし）を複数文へ分ける。"""

    for statement in script.split(";"):
        clean = statement.strip()
        if clean:
            yield clean


class DatabaseConnection:
    """SQLite/PostgreSQLの差をRepositoryから隠す薄い接続ラッパー。"""

    def __init__(self, raw_connection: Any, backend_name: str, backend: "DatabaseBackend") -> None:
        self._raw_connection = raw_connection
        self.backend_name = backend_name
        self._backend = backend

    def __enter__(self) -> "DatabaseConnection":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        try:
            if exc_type is None:
                self._raw_connection.commit()
            else:
                self._raw_connection.rollback()
        finally:
            self._raw_connection.close()

    def execute(self, statement: str, parameters: Iterable[Any] = ()) -> Any:
        adapted = _adapt_qmark_placeholders(statement, self.backend_name)
        return self._raw_connection.execute(adapted, tuple(parameters))

    def executemany(self, statement: str, parameters: Iterable[Iterable[Any]]) -> Any:
        adapted = _adapt_qmark_placeholders(statement, self.backend_name)
        return self._raw_connection.executemany(adapted, parameters)

    def executescript(self, script: str) -> None:
        if self.backend_name == "sqlite":
            self._raw_connection.executescript(script)
            return
        for statement in _split_sql_script(script):
            self.execute(statement)

    def table_columns(self, table_name: str) -> Set[str]:
        return self._backend.table_columns(self, table_name)


class DatabaseBackend:
    """Database backendの最小契約。"""

    backend_name = ""

    def connect(self) -> DatabaseConnection:
        raise NotImplementedError

    def table_columns(self, connection: DatabaseConnection, table_name: str) -> Set[str]:
        raise NotImplementedError


class SQLiteDatabase(DatabaseBackend):
    """既存のローカルSQLite backend。"""

    backend_name = "sqlite"

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> DatabaseConnection:
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(self.database_path, timeout=20)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA journal_mode = WAL")
        return DatabaseConnection(raw, self.backend_name, self)

    def table_columns(self, connection: DatabaseConnection, table_name: str) -> Set[str]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            raise DatabaseConfigurationError("テーブル名が不正です")
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}


class PostgreSQLDatabase(DatabaseBackend):
    """PostgreSQL接続の準備済みbackend。

    外部DBの作成・データ移行はこの変更では行わない。psycopgは遅延importし、
    DATABASE_URLがPostgreSQLの場合だけ接続に必要となる。
    """

    backend_name = "postgresql"

    def __init__(self, database_url: str, connect_timeout: int = 10) -> None:
        self.database_url = database_url
        self.connect_timeout = connect_timeout

    def connect(self) -> DatabaseConnection:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise DatabaseConfigurationError(
                "PostgreSQLを使うにはpsycopg[binary]をインストールしてください"
            ) from exc
        try:
            raw = psycopg.connect(
                self.database_url,
                connect_timeout=self.connect_timeout,
                row_factory=dict_row,
            )
        except Exception:  # noqa: BLE001
            # URL（認証情報を含む可能性がある）はログや例外へ含めない。
            raise DatabaseConnectionError("PostgreSQLへ接続できません") from None
        return DatabaseConnection(raw, self.backend_name, self)

    def table_columns(self, connection: DatabaseConnection, table_name: str) -> Set[str]:
        rows = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}


def _sqlite_path_from_url(database_url: str, fallback: Path) -> Path:
    """sqlite:///形式をPathへ変換し、従来のdata_dir既定値も維持する。"""

    if database_url in {"sqlite:///:memory:", "sqlite:memory:"}:
        return Path(":memory:")
    if database_url.startswith("sqlite:////"):
        raw_path = database_url[len("sqlite:////") :].split("?", 1)[0]
        return Path("/" + unquote(raw_path)).expanduser().resolve()
    if database_url.startswith("sqlite:///"):
        raw_path = database_url[len("sqlite:///") :].split("?", 1)[0]
        path = Path(unquote(raw_path)).expanduser()
        return path.resolve() if path.is_absolute() else path.resolve()
    parsed = urlparse(database_url)
    if parsed.scheme in {"sqlite", "sqlite3"} and parsed.path:
        return Path(unquote(parsed.path)).expanduser().resolve()
    return fallback


def create_database(settings: Optional[Settings] = None) -> DatabaseBackend:
    """DATABASE_URLからbackendを選択する。"""

    runtime = settings or get_settings()
    database_url = runtime.database_url.strip()
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    if scheme in {"sqlite", "sqlite3"}:
        return SQLiteDatabase(_sqlite_path_from_url(database_url, runtime.database_path))
    if scheme in {"postgres", "postgresql"}:
        return PostgreSQLDatabase(database_url, runtime.database_connect_timeout_seconds)
    raise DatabaseConfigurationError(
        "DATABASE_URLはsqlite://、postgres://、またはpostgresql://で指定してください"
    )


def connection(settings: Optional[Settings] = None) -> DatabaseConnection:
    """Repository向けの接続を返す。"""

    return create_database(settings).connect()
