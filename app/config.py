"""環境変数とアプリケーション共通設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
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
    openai_base_url: str
    openai_image_url: str
    openai_model: str
    openai_image_model: str

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

    configured_data_dir = os.getenv("STORY_MANGA_DATA_DIR", str(BASE_DIR / "data"))
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=Path(configured_data_dir).expanduser().resolve(),
        max_upload_bytes=_int_env("MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
        session_days=max(1, _int_env("SESSION_DAYS", 14)),
        ai_provider=os.getenv("AI_PROVIDER", "demo").lower(),
        image_provider=os.getenv("IMAGE_PROVIDER", "demo").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions"
        ),
        openai_image_url=os.getenv(
            "OPENAI_IMAGE_URL", "https://api.openai.com/v1/images/generations"
        ),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
    )


def ensure_data_dirs() -> Settings:
    """永続化に必要なディレクトリを作成する。"""

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.asset_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    return settings
