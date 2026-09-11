"""データベース接続の抽象化。

現在のRepository層は既存のSQLを利用するが、接続・トランザクション・行形式は
このモジュールへ閉じ込める。SQLiteを維持したまま、将来DATABASE_URLを
PostgreSQLへ切り替えられる境界を提供する。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Set
from urllib.parse import unquote, urlparse

from ..config import Settings, get_settings


logger = logging.getLogger(__name__)

# Renderの永続ディスクなど、WAL切替が拒否される環境では既存の
# journal modeを維持する。接続ごとに切替を試みて起動を不安定にしないため、
# プロセス内でDBパスごとの結果を記憶する。
_SQLITE_JOURNAL_MODE_CACHE: dict[str, str] = {}
_SQLITE_JOURNAL_MODE_LOCK = threading.Lock()
_SQLITE_BUSY_TIMEOUT_MS = 20_000
_SQLITE_SAFE_FALLBACK_MODES = {"delete", "truncate", "persist", "memory", "wal"}
_SQLITE_JOURNAL_MODE_UNMANAGED = "unmanaged"
_SQLITE_KNOWN_JOURNAL_MODES = _SQLITE_SAFE_FALLBACK_MODES | {
    _SQLITE_JOURNAL_MODE_UNMANAGED,
}
_SQLITE_WAL_FALLBACK_ERRORS = (
    "disk i/o error",
    "database is locked",
    "database table is locked",
    "readonly database",
    "attempt to write a readonly database",
    "database or disk is full",
)


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


def _sqlite_cache_key(database_path: Path) -> str:
    """SQLiteのjournal modeキャッシュ用に安定したキーを返す。"""

    if str(database_path) == ":memory:":
        return ":memory:"
    return str(database_path.resolve())


def _sqlite_current_journal_mode(raw_connection: Any) -> str:
    """接続中のSQLiteが現在使っているjournal modeを取得する。"""

    row = raw_connection.execute("PRAGMA journal_mode").fetchone()
    if not row:
        return ""
    return str(row[0]).strip().lower()


def _is_wal_activation_error(error: sqlite3.OperationalError) -> bool:
    """WAL切替だけを安全にフォールバックできる環境エラーか判定する。"""

    message = str(error).strip().lower()
    return any(fragment in message for fragment in _SQLITE_WAL_FALLBACK_ERRORS)


def _sqlite_fallback_journal_mode(current_mode: str) -> str:
    """既存モードを優先し、不明な場合だけ安全なDELETEを選ぶ。"""

    if current_mode in _SQLITE_SAFE_FALLBACK_MODES:
        return current_mode
    return "delete"


def _configure_sqlite_journal_mode(raw_connection: Any, database_path: Path) -> str:
    """WALを優先し、切替に限って既存の互換モードへフォールバックする。

    journal modeはデータベース単位の設定なので、同一プロセス内で一度
    negotiationしたパスは再度WALへ切り替えない。WAL以外のOperationalError
    （破損など）はここで握り潰さず、そのまま起動失敗として扱う。
    """

    cache_key = _sqlite_cache_key(database_path)
    with _SQLITE_JOURNAL_MODE_LOCK:
        cached_mode = _SQLITE_JOURNAL_MODE_CACHE.get(cache_key)
        if cached_mode:
            return cached_mode

        current_mode = ""
        try:
            result = raw_connection.execute("PRAGMA journal_mode = WAL").fetchone()
            selected_mode = str(result[0]).strip().lower() if result else "wal"
            if selected_mode == "wal":
                _SQLITE_JOURNAL_MODE_CACHE[cache_key] = selected_mode
                return selected_mode
            # SQLiteが例外を出さず既存モードを返した場合は、その実効モードを使う。
            if selected_mode in _SQLITE_SAFE_FALLBACK_MODES:
                _SQLITE_JOURNAL_MODE_CACHE[cache_key] = selected_mode
                return selected_mode
            logger.warning(
                "SQLite WALの実効モードを判定できないため、journal modeを変更せず接続を継続します"
            )
            selected_mode = _SQLITE_JOURNAL_MODE_UNMANAGED
        except sqlite3.OperationalError as error:
            if not _is_wal_activation_error(error):
                raise
            # WAL切替に失敗した場合だけ既存モードを確認する。確認自体が
            # 同じ環境エラーなら、別モードへの書換えを行わず既存状態を維持する。
            try:
                current_mode = _sqlite_current_journal_mode(raw_connection)
            except sqlite3.OperationalError as mode_error:
                if not _is_wal_activation_error(mode_error):
                    raise
                logger.warning(
                    "SQLite WALを有効化できずjournal modeも確認できないため、既存状態を変更せず接続を継続します"
                )
                _SQLITE_JOURNAL_MODE_CACHE[cache_key] = _SQLITE_JOURNAL_MODE_UNMANAGED
                return _SQLITE_JOURNAL_MODE_UNMANAGED
            if current_mode in _SQLITE_SAFE_FALLBACK_MODES:
                # 現在モードが取得できた場合は、既に有効なモードをそのまま利用する。
                logger.warning(
                    "SQLite WALを有効化できないため、既存journal modeを維持します"
                )
                _SQLITE_JOURNAL_MODE_CACHE[cache_key] = current_mode
                return current_mode
            logger.warning(
                "SQLite WALを有効化できずjournal modeを確定できないため、既存状態を変更せず接続を継続します"
            )
            _SQLITE_JOURNAL_MODE_CACHE[cache_key] = _SQLITE_JOURNAL_MODE_UNMANAGED
            return _SQLITE_JOURNAL_MODE_UNMANAGED

        if selected_mode not in _SQLITE_KNOWN_JOURNAL_MODES:
            raise sqlite3.OperationalError("SQLite journal modeを検証できません")
        _SQLITE_JOURNAL_MODE_CACHE[cache_key] = selected_mode
        return selected_mode


def _validate_sqlite_connection(raw_connection: Any) -> None:
    """フォールバック後もDBの基本読込が可能か非破壊で確認する。"""

    row = raw_connection.execute("SELECT 1").fetchone()
    if not row or int(row[0]) != 1:
        raise sqlite3.DatabaseError("SQLiteの基本検証に失敗しました")
    raw_connection.execute("PRAGMA schema_version").fetchone()


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
        raw: Any = None
        try:
            raw = sqlite3.connect(self.database_path, timeout=20)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
            _configure_sqlite_journal_mode(raw, self.database_path)
            raw.execute("PRAGMA synchronous = NORMAL")
            _validate_sqlite_connection(raw)
            return DatabaseConnection(raw, self.backend_name, self)
        except Exception:
            if raw is not None:
                try:
                    raw.close()
                except Exception:  # noqa: BLE001
                    pass
            raise

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
