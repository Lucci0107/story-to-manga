"""Character Bible Jobが成功・失敗・復旧のいずれでも収束することを確認する。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app, process_character_job
from app.services.ai_pipeline import AIProviderError, DemoAIProvider


def _client_and_project(tmp_path: Path) -> tuple[TestClient, dict]:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post(
        "/register",
        data={"email": f"character-{tmp_path.name}@example.com", "password": "long-password"},
        follow_redirects=False,
    )
    project = client.post(
        "/api/projects",
        data={"title": "Character Job", "story_text": "蒼は灯台へ向かい、凛に決意を伝えた。"},
    ).json()["project"]
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 200
    return client, client.get(f"/api/projects/{project['id']}").json()["project"]


class ExternalCharacterProvider(DemoAIProvider):
    provider_name = "openai"
    uses_external_api = True


class FailingCharacterProvider(ExternalCharacterProvider):
    def characters(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AIProviderError("テスト用のCharacter生成失敗", retryable=False)


class InvalidCharacterProvider(ExternalCharacterProvider):
    def characters(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [{"name": "蒼", "appearance": ""}]


def _run_job(project: dict, provider: DemoAIProvider, monkeypatch: pytest.MonkeyPatch) -> dict:
    job, created = db.create_async_generation_job(
        project["id"], "character", f"character:{project['id']}"
    )
    assert created and job
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: provider)
    process_character_job(project["id"], project["user_id"], job["id"])
    return db.get_generation_job(job["id"]) or {}


def test_character_job_completes_and_persists_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, project = _client_and_project(tmp_path)
    job = _run_job(project, ExternalCharacterProvider(), monkeypatch)

    assert job["status"] == "completed"
    assert job["completed_at"]
    assert job["error_category"] is None
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and saved["characters"]
    assert saved["status"] == "characters_ready"
    assert saved["current_step"] == "characters"


@pytest.mark.parametrize(
    "provider,error_text",
    [
        (FailingCharacterProvider(), "テスト用のCharacter生成失敗"),
        (InvalidCharacterProvider(), "キャラクター設定"),
    ],
)
def test_character_failure_reaches_retryable_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: DemoAIProvider,
    error_text: str,
) -> None:
    _client, project = _client_and_project(tmp_path)
    job = _run_job(project, provider, monkeypatch)

    assert job["status"] == "failed"
    assert error_text in job["error"]
    assert job["error_category"]
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and saved["status"] == "partially_failed"
    retry, created = db.create_async_generation_job(
        project["id"], "character", f"character:{project['id']}"
    )
    assert created and retry and retry["id"] != job["id"]


def test_duplicate_character_job_is_blocked(tmp_path: Path) -> None:
    _client, project = _client_and_project(tmp_path)
    first, first_created = db.create_async_generation_job(
        project["id"], "character", f"character:{project['id']}"
    )
    duplicate, duplicate_created = db.create_async_generation_job(
        project["id"], "character", f"character:{project['id']}"
    )

    assert first_created is True
    assert duplicate_created is False
    assert first and duplicate and duplicate["id"] == first["id"]


def test_stale_character_job_recovers_and_status_api_exposes_terminal_state(
    tmp_path: Path,
) -> None:
    client, project = _client_and_project(tmp_path)
    job, _created = db.create_async_generation_job(
        project["id"], "character", f"character:{project['id']}"
    )
    assert job
    db.update_generation_job(job["id"], "processing")
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db.connection() as connection:
        connection.execute(
            "UPDATE generation_jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (stale_time, stale_time, job["id"]),
        )

    status = client.get(f"/api/projects/{project['id']}/generation/status")

    assert status.status_code == 200
    payload = status.json()
    assert payload["character_job"]["id"] == job["id"]
    assert payload["character_job"]["status"] == "failed"
    assert "再試行" in payload["character_job"]["error"]
    assert payload["character_job"]["error_category"] == "timeout"
    assert payload["project_status"] == "partially_failed"


def test_successful_character_retry_clears_partial_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, project = _client_and_project(tmp_path)
    failed = _run_job(project, FailingCharacterProvider(), monkeypatch)
    assert failed["status"] == "failed"

    current = db.get_project(project["id"], project["user_id"])
    assert current
    completed = _run_job(current, ExternalCharacterProvider(), monkeypatch)

    assert completed["status"] == "completed"
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and saved["status"] == "characters_ready"
    assert db.latest_generation_job(project["id"], "character")["id"] == completed["id"]


def test_external_character_endpoint_returns_persisted_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, project = _client_and_project(tmp_path)
    monkeypatch.setattr(
        "app.main.get_ai_provider", lambda _settings: ExternalCharacterProvider()
    )

    response = client.post(f"/api/projects/{project['id']}/characters")

    assert response.status_code == 202
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["job"]["job_type"] == "character"
    status = client.get(f"/api/projects/{project['id']}/generation/status").json()
    assert status["character_job"]["status"] == "completed"
    assert client.get(f"/api/projects/{project['id']}").json()["project"]["characters"]
