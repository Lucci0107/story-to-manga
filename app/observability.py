"""外部監視専用API。通常のDB初期化・認証・ジョブ処理を呼び出さない。"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

FIELDS = {
    "projects": ("id", "status", "current_step", "created_at", "updated_at"),
    "generation_jobs": (
        "id",
        "project_id",
        "job_type",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
    ),
    "exports": ("id", "project_id", "format", "status", "created_at"),
}
ENUMS = {
    "status": {
        "draft",
        "analysis_ready",
        "characters_ready",
        "storyboard_ready",
        "processing",
        "partially_failed",
        "completed",
        "queued",
        "failed",
        "pending",
        "ready",
        "cancelled",
    },
    "current_step": {
        "upload",
        "input",
        "analysis",
        "analyze",
        "characters",
        "storyboard",
        "generate",
        "export",
        "complete",
    },
    "job_type": {"character", "storyboard", "panel_artwork", "image"},
    "format": {"pdf", "zip", "png", "jpg", "jpeg", "webp"},
}
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
READ_SLOTS = threading.BoundedSemaphore(4)


def install_observer(app: FastAPI) -> Callable[[], None]:
    """通常起動時だけWALの調整ファイルを維持する。GETは初期化しない。"""
    app.mount("/api/observer", create_observer_app())

    def prepare() -> None:
        if getattr(app.state, "observer_keeper", None) is not None:
            return
        app.state.observer_keeper = None
        if len(os.getenv("MCP_OBSERVER_TOKEN", "")) < 32:
            return
        connection = None
        try:
            path = database_path()
            connection = sqlite3.connect(
                f"file:{quote(str(path.resolve()), safe='/')}?mode=ro",
                uri=True,
                timeout=0.2,
                check_same_thread=False,
            )
            connection.execute("PRAGMA query_only=ON")
            connection.execute('SELECT "id" FROM "projects" LIMIT 1').fetchone()
            app.state.observer_keeper = connection
        except (OSError, sqlite3.Error, HTTPException):
            if connection is not None:
                connection.close()

    def release() -> None:
        connection = getattr(app.state, "observer_keeper", None)
        if connection is not None:
            connection.close()
            app.state.observer_keeper = None

    app.add_event_handler("startup", prepare)
    app.add_event_handler("shutdown", release)
    return prepare


def normalized_time(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            result.replace(tzinfo=result.tzinfo or timezone.utc)
            .astimezone(timezone.utc)
            .isoformat()
        )
    except ValueError:
        return None


def safe_row(row: dict) -> dict:
    """許可列に不正な自由文が保存されていても公開しない。"""
    result = {}
    for key, value in row.items():
        if key.endswith("_at"):
            result[key] = normalized_time(value)
        elif key in {"id", "project_id"}:
            result[key] = (
                value if isinstance(value, str) and UUID.fullmatch(value) else None
            )
        else:
            result[key] = (
                value
                if isinstance(value, str) and value in ENUMS.get(key, set())
                else "unknown"
            )
    return result


def database_path() -> Path:
    # 設定を読むだけで、通常のDBサービスを生成しない。
    from .config import get_settings
    from .services.database import _sqlite_path_from_url

    settings = get_settings()
    if not settings.database_url.startswith("sqlite:"):
        raise HTTPException(503, "観測対象はSQLiteのみです")
    return _sqlite_path_from_url(settings.database_url, settings.database_path)


def connect_read_only(path: Path) -> sqlite3.Connection:
    """存在するDBだけを開く。WAL補助ファイルの新規作成も拒否する。"""
    with path.open("rb") as source:
        header = source.read(20)
    if header[18:20] == b"\x02\x02" and not all(
        Path(str(path) + suffix).is_file() for suffix in ("-wal", "-shm")
    ):
        raise HTTPException(503, "WAL読み取り準備が必要です")
    connection = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&readonly_shm=1",
        uri=True,
        timeout=0.2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    deadline = time.monotonic() + 1.5
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 500)
    # 接続自体にも書き込み・ATTACH・任意PRAGMA禁止を課す。
    connection.set_authorizer(
        lambda action, a, b, db, trigger: (
            sqlite3.SQLITE_OK
            if (
                action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION)
                or (
                    action == sqlite3.SQLITE_READ
                    and a in FIELDS
                    and (not b or b in FIELDS[a])
                )
            )
            else sqlite3.SQLITE_DENY
        )
    )
    return connection


def isolated_read(path: Path, sql: str, values: list) -> list:
    """既存の書込接続とSHMマッピングを共有しない新規プロセスで読む。"""
    if not READ_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "観測処理が混雑しています")
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "app.observability_reader"],
            input=json.dumps({"path": str(path), "sql": sql, "values": values}),
            text=True,
            capture_output=True,
            check=False,
            timeout=4,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        if result.returncode != 0 or len(result.stdout) > 64000:
            raise ValueError
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise TypeError
        return rows
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise HTTPException(503, "観測DBを読み取れません") from None
    finally:
        READ_SLOTS.release()


def create_observer_app(path_provider: Callable[[], Path] = database_path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        expected = os.getenv("MCP_OBSERVER_TOKEN", "")
        supplied = request.headers.get("authorization", "")
        if len(expected) < 32:
            return JSONResponse(
                {"error": "OBSERVABILITY_NOT_CONFIGURED"}, status_code=503
            )
        if not secrets.compare_digest(
            supplied.encode(), ("Bearer " + expected).encode()
        ):
            return JSONResponse(
                {"error": "UNAUTHORIZED"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.method != "GET":
            return JSONResponse(
                {"error": "GET_ONLY"}, status_code=405, headers={"Allow": "GET"}
            )
        if len(request.url.query) > 8192:
            return JSONResponse({"error": "QUERY_TOO_LARGE"}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"error": "INVALID_QUERY"}, status_code=400)

    def window(request: Request):
        allowed = {
            "limit",
            "offset",
            "start_at",
            "end_at",
            "columns",
            "filters",
            "order",
        }
        if set(request.query_params) - allowed or len(
            request.query_params.multi_items()
        ) != len(request.query_params):
            raise HTTPException(400, "不正な引数です")
        try:
            limit = int(request.query_params.get("limit", "25"))
            offset = int(request.query_params.get("offset", "0"))
            end = (
                request.query_params.get("end_at")
                or datetime.now(timezone.utc).isoformat()
            )
            end = datetime.fromisoformat(normalized_time(end) or "invalid")
            start = (
                request.query_params.get("start_at")
                or (end - timedelta(days=7)).isoformat()
            )
            start = datetime.fromisoformat(normalized_time(start) or "invalid")
            if not (
                1 <= limit <= 25
                and 0 <= offset <= 10000
                and timedelta(0) <= end - start <= timedelta(days=7)
            ):
                raise ValueError
            return limit, offset, start.isoformat(), end.isoformat()
        except (ValueError, TypeError):
            raise HTTPException(400, "件数・期間の上限を確認してください")

    def read_table(table: str, request: Request):
        if table not in FIELDS:
            raise HTTPException(403, "許可されていないテーブルです")
        limit, offset, start, end = window(request)
        try:
            columns = json.loads(request.query_params.get("columns", "[]")) or list(
                FIELDS[table]
            )
            filters = json.loads(request.query_params.get("filters", "[]"))
            order = json.loads(request.query_params.get("order", "[]"))
            if (
                not isinstance(columns, list)
                or not columns
                or any(c not in FIELDS[table] for c in columns)
            ):
                raise ValueError
            if (
                not isinstance(filters, list)
                or len(filters) > 20
                or not isinstance(order, list)
                or len(order) > 10
            ):
                raise ValueError
            time_column = "created_at" if table == "exports" else "updated_at"
            clauses = [
                f'julianday("{time_column}") BETWEEN julianday(?) AND julianday(?)'
            ]
            values: list = [start, end]
            operators = {
                "eq": "=",
                "neq": "!=",
                "gt": ">",
                "gte": ">=",
                "lt": "<",
                "lte": "<=",
            }
            for item in filters:
                if not isinstance(item, dict) or set(item) - {
                    "field",
                    "operator",
                    "value",
                }:
                    raise ValueError
                field, op, value = (
                    item.get("field"),
                    item.get("operator"),
                    item.get("value"),
                )
                if field not in FIELDS[table]:
                    raise ValueError
                if op == "is_null":
                    clauses.append(f'"{field}" IS NULL')
                elif op == "in" and isinstance(value, list) and 1 <= len(value) <= 100:
                    if any(
                        not isinstance(v, (str, int, float, bool)) or len(str(v)) > 200
                        for v in value
                    ):
                        raise ValueError
                    clauses.append(f'"{field}" IN ({",".join("?" for _ in value)})')
                    values.extend(value)
                elif op in operators or op == "contains":
                    if (
                        not isinstance(value, (str, int, float, bool))
                        or len(str(value)) > 200
                    ):
                        raise ValueError
                    clauses.append(
                        f'instr("{field}", ?) > 0'
                        if op == "contains"
                        else f'"{field}" {operators[op]} ?'
                    )
                    values.append(value)
                else:
                    raise ValueError
            ordering = []
            for item in order:
                if (
                    not isinstance(item, dict)
                    or set(item) - {"field", "direction"}
                    or item.get("field") not in FIELDS[table]
                    or item.get("direction", "asc") not in {"asc", "desc"}
                ):
                    raise ValueError
                ordering.append(f'"{item["field"]}" {item.get("direction", "asc")}')
            ordering.append('"id" ASC')
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "許可列・構造化条件だけを指定してください")
        selected = ",".join(f'"{column}"' for column in columns)
        sql = f'SELECT {selected} FROM "{table}" WHERE {" AND ".join(clauses)} ORDER BY {",".join(ordering)} LIMIT ? OFFSET ?'
        try:
            rows = isolated_read(path_provider(), sql, values + [limit + 1, offset])
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "観測DBを読み取れません")
        return {
            "rows": rows[:limit],
            "count": min(len(rows), limit),
            "offset": offset,
            "next_offset": offset + limit
            if len(rows) > limit and offset + limit <= 10000
            else None,
            "read_only": True,
            "history_complete": False,
        }

    @app.get("/health")
    def health(request: Request):
        if request.query_params:
            raise HTTPException(400, "引数は不要です")
        try:
            for table in FIELDS:
                isolated_read(
                    path_provider(), f'SELECT "id" FROM "{table}" LIMIT 1', []
                )
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "観測DBを読み取れません")
        return {"status": "healthy", "read_only": True}

    @app.get("/tables/{table}")
    def table(table: str, request: Request):
        return read_table(table, request)

    @app.get("/projects")
    def projects(request: Request):
        return read_table("projects", request)

    @app.get("/generation-jobs")
    def jobs(request: Request):
        return read_table("generation_jobs", request)

    @app.get("/exports")
    def exports(request: Request):
        return read_table("exports", request)

    @app.get("/activity")
    def activity(request: Request):
        # entityごとのページ。完全な状態遷移履歴ではなく現在状態の観測。
        return {
            "entities": {name: read_table(name, request) for name in FIELDS},
            "history_complete": False,
        }

    return app
