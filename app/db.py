"""制作データのRepository層。

Projectの制作データはJSON列にまとめつつ、検索と所有権確認に必要な値は
通常の列として保持する。AIの出力は編集可能な中間データとして保存する。
接続先の選択やDB-API差分はservices.databaseへ委譲し、SQLite固有処理を
Repositoryの外へ閉じ込める。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .config import get_settings
from .services.database import DatabaseConnection, connection as database_connection
from .services.model_registry import DEFAULT_AI_MODEL_SETTINGS
from .services.reading_order import (
    canonicalize_stored_settings,
    canonicalize_storyboard_panel_orders,
)


DEFAULT_SETTINGS: Dict[str, Any] = {
    "target_page_count": 8,
    "language": "ja",
    "reading_direction": "right_to_left",
    "color_mode": "bw",
    "visual_style": "cinematic",
    "target_audience": "一般読者",
    "pacing": "balanced",
    "dialogue_density": "medium",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def connection() -> DatabaseConnection:
    """Repository向けに設定済みbackendの接続を返す。"""

    return database_connection()


def init_db() -> None:
    """テーブルとインデックスを作成する。"""

    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                ai_model_settings_json TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_filename TEXT,
                original_text TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                analysis_json TEXT,
                manga_settings_recommendation_json TEXT,
                characters_json TEXT NOT NULL,
                storyboard_json TEXT NOT NULL,
                ai_model_settings_json TEXT,
                generation_metadata_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generation_jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                target_id TEXT,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                format TEXT NOT NULL,
                storage_key TEXT,
                file_path TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                archived INTEGER NOT NULL DEFAULT 0,
                active_version_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_versions (
                id TEXT PRIMARY KEY,
                knowledge_document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                source_filename TEXT,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(knowledge_document_id, version_number),
                UNIQUE(knowledge_document_id, content_hash)
            );

            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY,
                knowledge_version_id TEXT NOT NULL REFERENCES knowledge_versions(id) ON DELETE CASCADE,
                chunk_order INTEGER NOT NULL,
                heading_path TEXT NOT NULL,
                content TEXT NOT NULL,
                token_estimate INTEGER NOT NULL,
                UNIQUE(knowledge_version_id, chunk_order)
            );

            CREATE TABLE IF NOT EXISTS project_knowledge (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                knowledge_document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                selected_version_id TEXT REFERENCES knowledge_versions(id) ON DELETE SET NULL,
                mode TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 50,
                scope_json TEXT NOT NULL,
                UNIQUE(project_id, knowledge_document_id)
            );

            CREATE TABLE IF NOT EXISTS knowledge_processing_jobs (
                id TEXT PRIMARY KEY,
                knowledge_version_id TEXT NOT NULL REFERENCES knowledge_versions(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_projects_user_updated
                ON projects(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_project_status
                ON generation_jobs(project_id, status);
            CREATE INDEX IF NOT EXISTS idx_knowledge_user_updated
                ON knowledge_documents(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_knowledge_versions_document
                ON knowledge_versions(knowledge_document_id, version_number DESC);
            CREATE INDEX IF NOT EXISTS idx_project_knowledge_project
                ON project_knowledge(project_id, enabled, priority DESC);
            """
        )
        project_columns = conn.table_columns("projects")
        if "quality_check_json" not in project_columns:
            conn.execute("ALTER TABLE projects ADD COLUMN quality_check_json TEXT")
        if "ai_model_settings_json" not in project_columns:
            conn.execute("ALTER TABLE projects ADD COLUMN ai_model_settings_json TEXT")
        if "manga_settings_recommendation_json" not in project_columns:
            conn.execute("ALTER TABLE projects ADD COLUMN manga_settings_recommendation_json TEXT")
        if "generation_metadata_json" not in project_columns:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN generation_metadata_json TEXT NOT NULL DEFAULT '[]'"
            )
        user_columns = conn.table_columns("users")
        if "role" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if "ai_model_settings_json" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN ai_model_settings_json TEXT")
        knowledge_columns = conn.table_columns("knowledge_documents")
        if "archived" not in knowledge_columns:
            conn.execute("ALTER TABLE knowledge_documents ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        export_columns = conn.table_columns("exports")
        if "storage_key" not in export_columns:
            conn.execute("ALTER TABLE exports ADD COLUMN storage_key TEXT")
        # Process再起動でBackgroundTasksは復元できないため、取り残したJobとPanelを
        # 失敗状態へ揃え、UIから個別に再試行できるようにする。
        interrupted_message = "処理が中断されました。再試行してください"
        interrupted_jobs = conn.execute(
            """
            SELECT project_id, target_id, job_type
            FROM generation_jobs
            WHERE status IN ('queued', 'processing')
            """
        ).fetchall()
        interrupted_by_project: Dict[str, List[Mapping[str, Any]]] = {}
        for job in interrupted_jobs:
            interrupted_by_project.setdefault(str(job["project_id"]), []).append(job)
        for project_id, jobs in interrupted_by_project.items():
            project_row = conn.execute(
                "SELECT storyboard_json, current_step FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if not project_row:
                continue
            storyboard = _loads(project_row["storyboard_json"], [])
            panel_ids = {
                str(job["target_id"])
                for job in jobs
                if job["job_type"] == "panel_artwork" and job["target_id"]
            }
            for page in storyboard if isinstance(storyboard, list) else []:
                for panel in page.get("panels", []) if isinstance(page, dict) else []:
                    if isinstance(panel, dict) and str(panel.get("id")) in panel_ids:
                        panel["generation_status"] = "failed"
                        panel["generation_error"] = interrupted_message
            next_step = (
                "storyboard"
                if any(job["job_type"] == "storyboard" for job in jobs)
                else project_row["current_step"]
            )
            conn.execute(
                """
                UPDATE projects
                SET storyboard_json = ?, status = 'partially_failed', current_step = ?, updated_at = ?
                WHERE id = ?
                """,
                (_json(storyboard), next_step, utc_now(), project_id),
            )
        conn.execute(
            """
            UPDATE generation_jobs
            SET status = 'failed', error = ?, completed_at = ?
            WHERE status IN ('queued', 'processing')
            """,
            (interrupted_message, utc_now()),
        )
        # 同時リクエストでも同じ有料処理を二重登録できないようDBでも保証する。
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency
            ON generation_jobs(project_id, job_type, idempotency_key)
            WHERE status IN ('queued', 'processing')
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exports_project ON exports(project_id, created_at DESC)"
        )
        # 既存ProjectのJSONを壊さず、言語・方向とコマの読順だけを冪等に移行する。
        project_rows = conn.execute(
            "SELECT id, settings_json, storyboard_json FROM projects"
        ).fetchall()
        for row in project_rows:
            stored_settings = _loads(row["settings_json"], dict(DEFAULT_SETTINGS))
            next_settings = canonicalize_stored_settings(stored_settings)
            stored_storyboard = _loads(row["storyboard_json"], [])
            next_storyboard = canonicalize_storyboard_panel_orders(stored_storyboard)
            if next_settings != stored_settings or next_storyboard != stored_storyboard:
                conn.execute(
                    "UPDATE projects SET settings_json = ?, storyboard_json = ? WHERE id = ?",
                    (_json(next_settings), _json(next_storyboard), row["id"]),
                )
    # 環境変数が揃っている場合だけ初期管理者を作成し、未設定でも起動を妨げない。
    bootstrap_admin()


def hash_password(password: str) -> str:
    """PBKDF2でパスワードをハッシュ化する。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """保存済みハッシュとパスワードを比較する。"""

    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _configured_admin_credentials() -> tuple[str, str] | None:
    """管理者bootstrap用の環境変数を検証し、秘密値を外へ返さない。"""

    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    if "@" not in email or len(email) > 160 or len(password) < 12 or len(password) > 256:
        return None
    return email, password


def get_admin_user() -> Optional[Mapping[str, Any]]:
    """最初の管理者を返す。パスワードハッシュは呼び出し側で公開しない。"""

    with connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        ).fetchone()


def is_admin(user: Mapping[str, Any] | None) -> bool:
    """DBから取得したユーザーの管理者権限だけを判定する。"""

    if not user:
        return False
    try:
        role = user["role"]
    except (KeyError, IndexError):
        role = "user"
    return str(role) == "admin"


def bootstrap_admin() -> Optional[Mapping[str, Any]]:
    """環境変数で指定された初期管理者をidempotentに作成する。

    既存の管理者は変更せず、同じメールアドレスの既存ユーザーを見つけた場合も
    パスワードを上書きせずにそのアカウントへadminロールだけを付与する。
    環境変数が未設定・不正な場合はアプリを停止せず、何もしない。
    """

    configured = _configured_admin_credentials()
    if not configured:
        return get_admin_user()
    email, password = configured
    with connection() as conn:
        existing_admin = conn.execute(
            "SELECT * FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if existing_admin:
            return existing_admin

        existing_user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing_user:
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (existing_user["id"],))
            return conn.execute(
                "SELECT * FROM users WHERE id = ?", (existing_user["id"],)
            ).fetchone()

        user_id = str(uuid.uuid4())
        created_at = utc_now()
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, created_at, role, ai_model_settings_json)
            VALUES (?, ?, ?, ?, 'admin', ?)
            """,
            (user_id, email, hash_password(password), created_at, _json(DEFAULT_AI_MODEL_SETTINGS)),
        )
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(email: str, password: str) -> Dict[str, Any]:
    """ユーザーを作成する。"""

    user_id = str(uuid.uuid4())
    created_at = utc_now()
    with connection() as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at, role, ai_model_settings_json) VALUES (?, ?, ?, ?, 'user', ?)",
            (
                user_id,
                email.lower().strip(),
                hash_password(password),
                created_at,
                _json(DEFAULT_AI_MODEL_SETTINGS),
            ),
        )
    return {
        "id": user_id,
        "email": email.lower().strip(),
        "role": "user",
        "created_at": created_at,
    }


def get_user_by_email(email: str) -> Optional[Mapping[str, Any]]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()


def get_user(user_id: str) -> Optional[Mapping[str, Any]]:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_ai_model_settings(user_id: str) -> Optional[Dict[str, Any]]:
    """ユーザーのグローバルAIモデル設定を返す。旧ユーザーはNoneのまま扱う。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT ai_model_settings_json FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _loads(row["ai_model_settings_json"], None) if row else None


def update_user_ai_model_settings(
    user_id: str, settings: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """グローバルAIモデル設定を保存する。"""

    with connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET ai_model_settings_json = ? WHERE id = ?",
            (_json(settings), user_id),
        )
        if cursor.rowcount == 0:
            return None
    return settings


def create_session(user_id: str) -> str:
    """推測困難な不透明セッションIDを発行する。"""

    token = secrets.token_urlsafe(40)
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=get_settings().session_days)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, created_at.isoformat(), expires_at.isoformat()),
        )
    return token


def get_user_by_session(token: Optional[str]) -> Optional[Mapping[str, Any]]:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = utc_now()
    with connection() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return row


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def _project_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "source_filename": row["source_filename"],
        "original_text": row["original_text"],
        "status": row["status"],
        "current_step": row["current_step"],
        "settings": canonicalize_stored_settings(
            _loads(row["settings_json"], dict(DEFAULT_SETTINGS))
        ),
        "analysis": _loads(row["analysis_json"], None),
        "manga_settings_recommendation": _loads(
            row["manga_settings_recommendation_json"], None
        ),
        "characters": _loads(row["characters_json"], []),
        "storyboard": canonicalize_storyboard_panel_orders(
            _loads(row["storyboard_json"], [])
        ),
        "quality_check": _loads(row["quality_check_json"], None),
        "ai_model_settings": _loads(row["ai_model_settings_json"], None),
        "generation_metadata": _loads(row["generation_metadata_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_project(
    user_id: str,
    title: str,
    original_text: str,
    source_type: str = "text",
    source_filename: Optional[str] = None,
) -> Dict[str, Any]:
    project_id = str(uuid.uuid4())
    now = utc_now()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, user_id, title, source_type, source_filename, original_text,
                status, current_step, settings_json, analysis_json,
                manga_settings_recommendation_json, characters_json, storyboard_json, ai_model_settings_json,
                generation_metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                user_id,
                title,
                source_type,
                source_filename,
                original_text,
                "draft",
                "story",
                _json(DEFAULT_SETTINGS),
                None,
                None,
                _json([]),
                _json([]),
                None,
                _json([]),
                now,
                now,
            ),
        )
    return get_project(project_id, user_id)  # type: ignore[return-value]


def get_project(project_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    return _project_from_row(row) if row else None


def list_projects(user_id: str) -> List[Dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [_project_from_row(row) for row in rows]


def update_project(
    project_id: str,
    user_id: str,
    *,
    title: Optional[str] = None,
    original_text: Optional[str] = None,
    status: Optional[str] = None,
    current_step: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    analysis: Any = None,
    update_analysis: bool = False,
    characters: Optional[List[Dict[str, Any]]] = None,
    storyboard: Optional[List[Dict[str, Any]]] = None,
    clear_quality_check: bool = False,
) -> Optional[Dict[str, Any]]:
    """指定されたProject項目だけを更新する。"""

    current = get_project(project_id, user_id)
    if not current:
        return None
    next_values = {
        "title": title if title is not None else current["title"],
        "original_text": (
            original_text if original_text is not None else current["original_text"]
        ),
        "status": status if status is not None else current["status"],
        "current_step": (
            current_step if current_step is not None else current["current_step"]
        ),
        "settings": canonicalize_stored_settings(
            settings if settings is not None else current["settings"]
        ),
        "analysis": analysis if update_analysis else current["analysis"],
        "characters": characters if characters is not None else current["characters"],
        "storyboard": storyboard if storyboard is not None else current["storyboard"],
        "quality_check": None if clear_quality_check else current.get("quality_check"),
    }
    now = utc_now()
    with connection() as conn:
        conn.execute(
            """
            UPDATE projects SET title = ?, original_text = ?, status = ?, current_step = ?,
                settings_json = ?, analysis_json = ?, characters_json = ?, storyboard_json = ?,
                quality_check_json = ?,
                updated_at = ? WHERE id = ? AND user_id = ?
            """,
            (
                next_values["title"],
                next_values["original_text"],
                next_values["status"],
                next_values["current_step"],
                _json(next_values["settings"]),
                _json(next_values["analysis"]) if next_values["analysis"] is not None else None,
                _json(next_values["characters"]),
                _json(next_values["storyboard"]),
                _json(next_values["quality_check"]) if next_values["quality_check"] is not None else None,
                now,
                project_id,
                user_id,
            ),
        )
    return get_project(project_id, user_id)


def save_manga_settings_recommendation(
    project_id: str,
    user_id: str,
    recommendation: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """漫画化設定の推奨値をProjectへ保存する。"""

    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE projects
            SET manga_settings_recommendation_json = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (_json(recommendation), utc_now(), project_id, user_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_project(project_id, user_id)


def mark_manga_settings_recommendation_override(
    project_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """ユーザー保存を記録し、以後の初期推奨で設定値を上書きしない。"""

    project = get_project(project_id, user_id)
    if not project:
        return None
    recommendation = project.get("manga_settings_recommendation")
    if not isinstance(recommendation, dict):
        return project
    recommendation = dict(recommendation)
    recommendation["user_override"] = True
    with connection() as conn:
        conn.execute(
            "UPDATE projects SET manga_settings_recommendation_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (_json(recommendation), utc_now(), project_id, user_id),
        )
    return get_project(project_id, user_id)


def get_project_ai_model_settings(
    project_id: str, user_id: str
) -> Optional[Dict[str, Any]]:
    """Project固有のAIモデル上書きを返す。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT ai_model_settings_json FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    return _loads(row["ai_model_settings_json"], None) if row else None


def update_project_ai_model_settings(
    project_id: str, user_id: str, settings: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Project固有のAIモデル上書きを保存する。"""

    with connection() as conn:
        cursor = conn.execute(
            "UPDATE projects SET ai_model_settings_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (_json(settings), utc_now(), project_id, user_id),
        )
        if cursor.rowcount == 0:
            return None
    return settings


def record_generation_metadata(
    project_id: str, user_id: str, event: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """生成に使ったモデル情報をProjectへ追記する。秘密はeventへ渡さない。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT generation_metadata_json FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        if not row:
            return None
        events = _loads(row["generation_metadata_json"], [])
        if not isinstance(events, list):
            events = []
        clean = {
            str(key): value
            for key, value in event.items()
            if str(key)
            in {
                "task",
                "target_id",
                "requested_model",
                "actual_model",
                "fallback",
                "reasoning_effort",
                "provider",
                "created_at",
            }
        }
        clean["id"] = str(uuid.uuid4())
        events.append(clean)
        events = events[-256:]
        conn.execute(
            "UPDATE projects SET generation_metadata_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (_json(events), utc_now(), project_id, user_id),
        )
    return events


def create_generation_job(
    project_id: str,
    target_id: Optional[str],
    idempotency_key: str,
    job_type: str = "panel_artwork",
) -> Optional[Dict[str, Any]]:
    """同じ処理が実行中なら新規Jobを作らない。"""

    with connection() as conn:
        existing = conn.execute(
            """
            SELECT * FROM generation_jobs
            WHERE project_id = ? AND job_type = ? AND idempotency_key = ?
              AND status IN ('queued', 'processing')
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, job_type, idempotency_key),
        ).fetchone()
        if existing:
            return dict(existing)
    job_id = str(uuid.uuid4())
    now = utc_now()
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO generation_jobs
                  (id, project_id, target_id, job_type, status, error, idempotency_key, created_at)
                VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?)
                """,
                (job_id, project_id, target_id, job_type, idempotency_key, now),
            )
    except Exception:  # DB固有の一意制約例外をRepository境界で吸収する。
        with connection() as conn:
            existing = conn.execute(
                """
                SELECT * FROM generation_jobs
                WHERE project_id = ? AND job_type = ? AND idempotency_key = ?
                  AND status IN ('queued', 'processing')
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, job_type, idempotency_key),
            ).fetchone()
        if existing:
            return dict(existing)
        raise
    return get_generation_job(job_id)


def create_async_generation_job(
    project_id: str, job_type: str, idempotency_key: str
) -> tuple[Optional[Dict[str, Any]], bool]:
    """長時間のProject処理をJobへ登録し、新規作成かどうかも返す。"""

    with connection() as conn:
        existing = conn.execute(
            """
            SELECT * FROM generation_jobs
            WHERE project_id = ? AND job_type = ? AND idempotency_key = ?
              AND status IN ('queued', 'processing')
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, job_type, idempotency_key),
        ).fetchone()
        if existing:
            return dict(existing), False
    job_id = str(uuid.uuid4())
    now = utc_now()
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO generation_jobs
                  (id, project_id, target_id, job_type, status, error, idempotency_key, created_at)
                VALUES (?, ?, NULL, ?, 'queued', NULL, ?, ?)
                """,
                (job_id, project_id, job_type, idempotency_key, now),
            )
    except Exception:  # DB固有の一意制約例外をRepository境界で吸収する。
        with connection() as conn:
            existing = conn.execute(
                """
                SELECT * FROM generation_jobs
                WHERE project_id = ? AND job_type = ? AND idempotency_key = ?
                  AND status IN ('queued', 'processing')
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, job_type, idempotency_key),
            ).fetchone()
        if existing:
            return dict(existing), False
        raise
    return get_generation_job(job_id), True


def get_active_generation_job(project_id: str, job_type: str) -> Optional[Dict[str, Any]]:
    """Project単位の長時間Jobが実行中か確認する。"""

    with connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM generation_jobs
            WHERE project_id = ? AND job_type = ? AND status IN ('queued', 'processing')
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, job_type),
        ).fetchone()
    return dict(row) if row else None


def get_generation_job(job_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def update_generation_job(
    job_id: str, status: str, error: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    completed_at = utc_now() if status in {"completed", "failed"} else None
    with connection() as conn:
        conn.execute(
            "UPDATE generation_jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            (status, error, completed_at, job_id),
        )
    return get_generation_job(job_id)


def list_generation_jobs(project_id: str) -> List[Dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM generation_jobs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_export(
    project_id: str, file_format: str, storage_key: Optional[str], status: str
) -> Dict[str, Any]:
    """Export記録を作成する。storage_keyはbackend非依存の参照値として保存する。"""

    export_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, format, storage_key, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (export_id, project_id, file_format, storage_key, status, utc_now()),
        )
    return get_export(export_id)  # type: ignore[return-value]


def get_export(export_id: str) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    # 旧SQLite行はfile_pathだけを持つため、移行完了まで互換参照を返す。
    if not result.get("storage_key"):
        result["storage_key"] = result.get("file_path")
    return result


def update_export(
    export_id: str, storage_key: Optional[str], status: str
) -> Optional[Dict[str, Any]]:
    with connection() as conn:
        conn.execute(
            "UPDATE exports SET storage_key = ?, status = ? WHERE id = ?",
            (storage_key, status, export_id),
        )
    return get_export(export_id)


def delete_project(project_id: str, user_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        )
    return cursor.rowcount > 0


def _knowledge_document_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Knowledge DocumentのDB行をAPI内部の辞書へ変換する。"""

    keys = set(row.keys())
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "active": bool(row["active"]),
        "archived": bool(row["archived"]) if "archived" in keys else False,
        "active_version_id": row["active_version_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version_count": row["version_count"] if "version_count" in keys else 0,
        "active_version_number": row["active_version_number"] if "active_version_number" in keys else None,
        "active_version_status": row["active_version_status"] if "active_version_status" in keys else None,
        "active_source_filename": row["active_source_filename"] if "active_source_filename" in keys else None,
    }


def list_knowledge_documents(user_id: str) -> List[Dict[str, Any]]:
    """ユーザー所有のKnowledge Documentを最新更新順で返す。"""

    with connection() as conn:
        rows = conn.execute(
            """
            SELECT d.*, COUNT(v.id) AS version_count,
                   av.version_number AS active_version_number,
                   av.status AS active_version_status,
                   av.source_filename AS active_source_filename
            FROM knowledge_documents d
            LEFT JOIN knowledge_versions v ON v.knowledge_document_id = d.id
            LEFT JOIN knowledge_versions av ON av.id = d.active_version_id
            WHERE d.user_id = ?
            GROUP BY d.id
            ORDER BY d.updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_knowledge_document_from_row(row) for row in rows]


def get_knowledge_document(document_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """所有権を確認してKnowledge Documentを返す。"""

    with connection() as conn:
        row = conn.execute(
            """
            SELECT d.*, COUNT(v.id) AS version_count,
                   av.version_number AS active_version_number,
                   av.status AS active_version_status,
                   av.source_filename AS active_source_filename
            FROM knowledge_documents d
            LEFT JOIN knowledge_versions v ON v.knowledge_document_id = d.id
            LEFT JOIN knowledge_versions av ON av.id = d.active_version_id
            WHERE d.id = ? AND d.user_id = ?
            GROUP BY d.id
            """,
            (document_id, user_id),
        ).fetchone()
    return _knowledge_document_from_row(row) if row else None


def update_knowledge_document(
    document_id: str,
    user_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    active: Optional[bool] = None,
    archived: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Knowledge Documentのメタデータを更新する。"""

    current = get_knowledge_document(document_id, user_id)
    if not current:
        return None
    values = {
        "title": title if title is not None else current["title"],
        "description": description if description is not None else current["description"],
        "category": category if category is not None else current["category"],
        "active": int(active) if active is not None else int(current["active"]),
        "archived": int(archived) if archived is not None else int(current.get("archived", False)),
    }
    with connection() as conn:
        conn.execute(
            """
            UPDATE knowledge_documents
            SET title = ?, description = ?, category = ?, active = ?, archived = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                values["title"],
                values["description"],
                values["category"],
                values["active"],
                values["archived"],
                utc_now(),
                document_id,
                user_id,
            ),
        )
    return get_knowledge_document(document_id, user_id)


def delete_knowledge_document(document_id: str, user_id: str) -> bool:
    """Knowledge DocumentとそのVersion/Chunkを削除する。"""

    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM knowledge_documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        )
    return cursor.rowcount > 0


def list_knowledge_versions(document_id: str, user_id: str) -> List[Dict[str, Any]]:
    """所有権を確認し、Knowledge Version履歴を返す。"""

    if not get_knowledge_document(document_id, user_id):
        return []
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT v.*, COUNT(c.id) AS chunk_count,
                   d.active_version_id AS document_active_version_id
            FROM knowledge_versions v
            JOIN knowledge_documents d ON d.id = v.knowledge_document_id
            LEFT JOIN knowledge_chunks c ON c.knowledge_version_id = v.id
            WHERE v.knowledge_document_id = ? AND d.user_id = ?
            GROUP BY v.id
            ORDER BY v.version_number DESC
            """,
            (document_id, user_id),
        ).fetchall()
    versions = []
    for row in rows:
        item = dict(row)
        item["is_active"] = item["id"] == item.get("document_active_version_id")
        versions.append(item)
    return versions


def get_knowledge_version(version_id: str) -> Optional[Dict[str, Any]]:
    """Knowledge Versionを所有者によらず内部参照する。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_versions WHERE id = ?", (version_id,)
        ).fetchone()
    return dict(row) if row else None


def find_knowledge_version_by_hash(document_id: str, content_hash: str) -> Optional[Dict[str, Any]]:
    """同一Document内の本文重複を検出する。"""

    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_document_id = ? AND content_hash = ?",
            (document_id, content_hash),
        ).fetchone()
    return dict(row) if row else None


def create_knowledge_document(
    user_id: str,
    title: str,
    description: str,
    category: str,
    source_filename: Optional[str],
    raw_text: str,
    normalized_text: str,
    content_hash: str,
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Knowledge Documentと初回Version/Chunkを一括作成する。"""

    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = utc_now()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_documents
              (id, user_id, title, description, category, active, active_version_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (document_id, user_id, title, description, category, version_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO knowledge_versions
              (id, knowledge_document_id, version_number, source_filename, raw_text,
               normalized_text, content_hash, status, created_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, 'ready', ?)
            """,
            (version_id, document_id, source_filename, raw_text, normalized_text, content_hash, now),
        )
        conn.executemany(
            """
            INSERT INTO knowledge_chunks
              (id, knowledge_version_id, chunk_order, heading_path, content, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(uuid.uuid4()),
                    version_id,
                    int(chunk["order"]),
                    str(chunk.get("heading_path", "")),
                    str(chunk["content"]),
                    int(chunk.get("token_estimate", 1)),
                )
                for chunk in chunks
            ],
        )
        conn.execute(
            "INSERT INTO knowledge_processing_jobs (id, knowledge_version_id, status, created_at, completed_at) VALUES (?, ?, 'completed', ?, ?)",
            (str(uuid.uuid4()), version_id, now, now),
        )
    return get_knowledge_document(document_id, user_id) or {
        "id": document_id,
        "user_id": user_id,
        "title": title,
        "description": description,
        "category": category,
        "active": True,
        "active_version_id": version_id,
    }


def create_knowledge_version(
    document_id: str,
    user_id: str,
    source_filename: Optional[str],
    raw_text: str,
    normalized_text: str,
    content_hash: str,
    chunks: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """既存Documentへ新しいVersionを追加する。"""

    document = get_knowledge_document(document_id, user_id)
    if not document:
        return None
    existing = find_knowledge_version_by_hash(document_id, content_hash)
    if existing:
        return existing
    version_id = str(uuid.uuid4())
    now = utc_now()
    with connection() as conn:
        current = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM knowledge_versions WHERE knowledge_document_id = ?",
            (document_id,),
        ).fetchone()
        version_number = int(current["max_version"]) + 1
        conn.execute(
            """
            INSERT INTO knowledge_versions
              (id, knowledge_document_id, version_number, source_filename, raw_text,
               normalized_text, content_hash, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?)
            """,
            (version_id, document_id, version_number, source_filename, raw_text, normalized_text, content_hash, now),
        )
        conn.executemany(
            """
            INSERT INTO knowledge_chunks
              (id, knowledge_version_id, chunk_order, heading_path, content, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(uuid.uuid4()),
                    version_id,
                    int(chunk["order"]),
                    str(chunk.get("heading_path", "")),
                    str(chunk["content"]),
                    int(chunk.get("token_estimate", 1)),
                )
                for chunk in chunks
            ],
        )
        conn.execute(
            "UPDATE knowledge_documents SET active_version_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (version_id, now, document_id, user_id),
        )
        conn.execute(
            "INSERT INTO knowledge_processing_jobs (id, knowledge_version_id, status, created_at, completed_at) VALUES (?, ?, 'completed', ?, ?)",
            (str(uuid.uuid4()), version_id, now, now),
        )
    with connection() as conn:
        row = conn.execute("SELECT * FROM knowledge_versions WHERE id = ?", (version_id,)).fetchone()
    return dict(row) if row else None


def activate_knowledge_version(document_id: str, version_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Ready状態のVersionをDocumentのActive Versionへ設定する。"""

    document = get_knowledge_document(document_id, user_id)
    version = get_knowledge_version(version_id)
    if not document or not version or version["knowledge_document_id"] != document_id or version["status"] != "ready":
        return None
    with connection() as conn:
        conn.execute(
            "UPDATE knowledge_documents SET active_version_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (version_id, utc_now(), document_id, user_id),
        )
    return get_knowledge_document(document_id, user_id)


def list_knowledge_chunks(version_id: str) -> List[Dict[str, Any]]:
    """Versionの構造化Chunkを順序どおり返す。"""

    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_chunks WHERE knowledge_version_id = ? ORDER BY chunk_order",
            (version_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_project_knowledge(project_id: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """Projectに紐づくKnowledge選択をVersion情報つきで返す。"""

    if not get_project(project_id, user_id):
        return None
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT pk.*, d.title, d.description, d.category, d.active AS document_active,
                   d.archived AS document_archived,
                   d.active_version_id,
                   av.version_number AS active_version_number,
                   av.status AS active_version_status,
                   av.source_filename AS active_source_filename,
                   sv.version_number AS selected_version_number,
                   sv.status AS selected_version_status,
                   sv.source_filename AS selected_source_filename
            FROM project_knowledge pk
            JOIN knowledge_documents d ON d.id = pk.knowledge_document_id
            LEFT JOIN knowledge_versions av ON av.id = d.active_version_id
            LEFT JOIN knowledge_versions sv ON sv.id = pk.selected_version_id
            WHERE pk.project_id = ? AND d.user_id = ?
            ORDER BY pk.priority DESC, LOWER(d.title)
            """,
            (project_id, user_id),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["document_active"] = bool(item["document_active"])
        item["document_archived"] = bool(item["document_archived"])
        item["scope"] = _loads(item.get("scope_json"), [])
        item.pop("scope_json", None)
        result.append(item)
    return result


def set_project_knowledge(
    project_id: str,
    user_id: str,
    selections: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """ProjectのKnowledge選択を所有権とVersion整合性を確認して置き換える。"""

    if not get_project(project_id, user_id):
        return None
    seen = set()
    with connection() as conn:
        conn.execute("DELETE FROM project_knowledge WHERE project_id = ?", (project_id,))
        for selection in selections:
            document_id = str(selection.get("knowledge_document_id", ""))
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            document = conn.execute(
                "SELECT id, archived FROM knowledge_documents WHERE id = ? AND user_id = ?",
                (document_id, user_id),
            ).fetchone()
            if not document or document["archived"]:
                raise ValueError("選択したKnowledgeが見つかりません")
            mode = str(selection.get("mode", "follow_latest"))
            if mode not in {"follow_latest", "pinned"}:
                raise ValueError("KnowledgeのVersionモードが不正です")
            selected_version_id = selection.get("selected_version_id") if mode == "pinned" else None
            if mode == "pinned":
                version = conn.execute(
                    "SELECT id, status FROM knowledge_versions WHERE id = ? AND knowledge_document_id = ?",
                    (str(selected_version_id or ""), document_id),
                ).fetchone()
                if not version or version["status"] != "ready":
                    raise ValueError("固定するKnowledge Versionが利用できません")
            priority = max(0, min(1000, int(selection.get("priority", 50))))
            scope = selection.get("scope", [])
            if not isinstance(scope, list):
                scope = []
            conn.execute(
                """
                INSERT INTO project_knowledge
                  (id, project_id, knowledge_document_id, selected_version_id, mode, enabled, priority, scope_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    project_id,
                    document_id,
                    str(selected_version_id) if selected_version_id else None,
                    mode,
                    int(bool(selection.get("enabled", True))),
                    priority,
                    _json([str(item) for item in scope][:16]),
                ),
            )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ? AND user_id = ?",
            (utc_now(), project_id, user_id),
        )
    return list_project_knowledge(project_id, user_id)


def save_quality_check(project_id: str, user_id: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Knowledge-aware QA結果をProjectへ保存する。"""

    if not get_project(project_id, user_id):
        return None
    with connection() as conn:
        conn.execute(
            "UPDATE projects SET quality_check_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (_json(result), utc_now(), project_id, user_id),
        )
    return get_project(project_id, user_id)
