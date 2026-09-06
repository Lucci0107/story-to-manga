"""環境変数とアプリケーション共通設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env() -> None:
    """プロジェクト直下の.envを読み込む（既存の環境変数を優先）。"""

    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """実行時設定を一箇所に集約する。"""

    app_env: str
    data_dir: Path
    max_upload_bytes: int
    session_days: int
    ai_provider: str
    image_provider: str
    openai_api_key: str
    openai_responses_url: str
    openai_base_url: str
    openai_image_url: str
    openai_text_model: str
    openai_model: str
    openai_image_model: str
    openai_timeout_seconds: float
    openai_max_retries: int
    openai_max_output_tokens: int

    @property
    def database_path(self) -> Path:
        return self.data_dir / "story_manga.sqlite3"

    @property
    def asset_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"


def get_settings() -> Settings:
    """環境変数を読み込み、デフォルト値を適用する。"""

    _load_local_env()
    configured_data_dir = os.getenv("STORY_MANGA_DATA_DIR", str(BASE_DIR / "data"))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    configured_ai_provider = os.getenv("AI_PROVIDER")
    configured_image_provider = os.getenv("IMAGE_PROVIDER")
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=Path(configured_data_dir).expanduser().resolve(),
        max_upload_bytes=_int_env("MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
        session_days=max(1, _int_env("SESSION_DAYS", 14)),
        ai_provider=(configured_ai_provider or ("openai" if api_key else "demo")).lower(),
        image_provider=(configured_image_provider or ("openai" if api_key else "demo")).lower(),
        openai_api_key=api_key,
        openai_responses_url=os.getenv(
            "OPENAI_RESPONSES_URL",
            "https://api.openai.com/v1/responses",
        ),
        # 既存の利用者がSettings.openai_base_urlを参照しても壊れないように残す。
        openai_base_url=os.getenv(
            "OPENAI_BASE_URL",
            os.getenv("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses"),
        ),
        openai_image_url=os.getenv(
            "OPENAI_IMAGE_URL", "https://api.openai.com/v1/images/generations"
        ),
        openai_text_model=os.getenv(
            "OPENAI_TEXT_MODEL",
            os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        ),
        # 旧環境変数との互換性を保つための別名。
        openai_model=os.getenv(
            "OPENAI_TEXT_MODEL",
            os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        ),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        openai_timeout_seconds=max(5.0, min(300.0, _float_env("OPENAI_TIMEOUT_SECONDS", 90.0))),
        openai_max_retries=max(0, min(2, _int_env("OPENAI_MAX_RETRIES", 1))),
        openai_max_output_tokens=max(512, min(32_000, _int_env("OPENAI_MAX_OUTPUT_TOKENS", 12_000))),
    )


def ensure_data_dirs() -> Settings:
    """永続化に必要なディレクトリを作成する。"""

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.asset_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    return settings
