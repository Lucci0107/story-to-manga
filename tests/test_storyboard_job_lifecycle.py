"""Storyboard Jobが開始後に必ず完了または再試行可能状態へ収束することを確認する。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app, process_storyboard_job
from app.services.ai_pipeline import AIProviderError, DemoAIProvider


def _client_and_project(tmp_path: Path) -> tuple[TestClient, dict]:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    client.post(
        "/register",
        data={"email": f"storyboard-{tmp_path.name}@example.com", "password": "long-password"},
        follow_redirects=False,
    )
    project = client.post(
        "/api/projects",
        data={"title": "Job lifecycle", "story_text": "蒼は灯台へ向かい、凛に決意を伝えた。"},
    ).json()["project"]
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 200
    assert client.post(f"/api/projects/{project['id']}/characters").status_code == 200
    return client, client.get(f"/api/projects/{project['id']}").json()["project"]


def _page(number: int = 1) -> dict:
    return {
        "page_number": number,
        "title": f"ページ {number}",
        "layout": "classic",
        "panels": [
            {
                "description": f"場面 {number}",
                "shot_type": "遠景",
                "characters": ["蒼"],
                "action": "歩く",
                "expression": "決意",
                "background": "灯台への道",
                "dialogue": [],
                "narration": [],
                "sfx": [],
            }
        ],
    }


class ExternalStoryboardProvider(DemoAIProvider):
    provider_name = "openai"
    uses_external_api = True

    def __init__(self, page_count: int = 1) -> None:
        super().__init__()
        self.page_count = page_count

    def storyboard(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._record_demo("storyboard")
        return [_page(number) for number in range(1, self.page_count + 1)]


class FailingStoryboardProvider(ExternalStoryboardProvider):
    def storyboard(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AIProviderError("テスト用の生成失敗", retryable=False)


class InvalidStoryboardProvider(ExternalStoryboardProvider):
    def storyboard(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return []


def _run_job(project: dict, provider: DemoAIProvider, monkeypatch: pytest.MonkeyPatch) -> dict:
    job, created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
    )
    assert created and job
    monkeypatch.setattr("app.main.get_ai_provider", lambda _settings: provider)
    process_storyboard_job(project["id"], project["user_id"], job["id"])
    return db.get_generation_job(job["id"]) or {}


def test_storyboard_job_completes_and_persists_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, project = _client_and_project(tmp_path)
    job = _run_job(project, ExternalStoryboardProvider(2), monkeypatch)

    assert job["status"] == "completed"
    assert job["completed_at"]
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and len(saved["storyboard"]) == 2
    assert saved["storyboard"][0]["panels"]
    assert saved["status"] == "storyboard_ready"


@pytest.mark.parametrize(
    "provider,error_text",
    [
        (FailingStoryboardProvider(), "テスト用の生成失敗"),
        (InvalidStoryboardProvider(), "Storyboard"),
    ],
)
def test_storyboard_failure_reaches_retryable_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: DemoAIProvider,
    error_text: str,
) -> None:
    _client, project = _client_and_project(tmp_path)
    job = _run_job(project, provider, monkeypatch)

    assert job["status"] == "failed"
    assert error_text in job["error"]
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and saved["status"] == "partially_failed"
    retry, created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
    )
    assert created and retry and retry["id"] != job["id"]


def test_duplicate_storyboard_job_is_blocked(tmp_path: Path) -> None:
    _client, project = _client_and_project(tmp_path)
    first, first_created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
    )
    duplicate, duplicate_created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
    )

    assert first_created is True
    assert duplicate_created is False
    assert first and duplicate and duplicate["id"] == first["id"]


def test_stale_storyboard_job_recovers_and_status_api_exposes_latest(
    tmp_path: Path,
) -> None:
    client, project = _client_and_project(tmp_path)
    job, _created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
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
    assert status.json()["storyboard_job"]["id"] == job["id"]
    assert status.json()["storyboard_job"]["status"] == "failed"
    assert "再試行" in status.json()["storyboard_job"]["error"]
    assert status.json()["project_status"] == "partially_failed"


def test_successful_storyboard_retry_clears_partial_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, project = _client_and_project(tmp_path)
    failed = _run_job(project, FailingStoryboardProvider(), monkeypatch)
    assert failed["status"] == "failed"

    current = db.get_project(project["id"], project["user_id"])
    assert current
    completed = _run_job(current, ExternalStoryboardProvider(), monkeypatch)

    assert completed["status"] == "completed"
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and saved["status"] == "storyboard_ready"
    assert db.latest_generation_job(project["id"], "storyboard")["id"] == completed["id"]


def test_completed_storyboard_job_repairs_legacy_partial_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, project = _client_and_project(tmp_path)
    completed = _run_job(project, ExternalStoryboardProvider(), monkeypatch)
    assert completed["status"] == "completed"
    db.update_project(
        project["id"],
        project["user_id"],
        status="partially_failed",
        current_step="storyboard",
    )

    status = client.get(f"/api/projects/{project['id']}/generation/status")

    assert status.status_code == 200
    assert status.json()["project_status"] == "storyboard_ready"
    assert status.json()["storyboard_job"]["status"] == "completed"


def test_storyboard_job_heartbeat_prevents_false_stale_recovery(tmp_path: Path) -> None:
    _client, project = _client_and_project(tmp_path)
    job, _created = db.create_async_generation_job(
        project["id"], "storyboard", f"storyboard:{project['id']}"
    )
    assert job
    db.update_generation_job(job["id"], "processing")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db.connection() as connection:
        connection.execute(
            "UPDATE generation_jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (old_time, old_time, job["id"]),
        )

    db.touch_generation_job(job["id"])

    assert db.recover_stale_storyboard_jobs(project["id"], project["user_id"], 900) == []
    assert db.get_generation_job(job["id"])["status"] == "processing"


@pytest.mark.parametrize(
    "language,direction",
    [("ja", "right_to_left"), ("en", "left_to_right")],
)
def test_larger_storyboard_preserves_language_direction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    direction: str,
) -> None:
    _client, project = _client_and_project(tmp_path)
    updated = db.update_project(
        project["id"],
        project["user_id"],
        settings={**project["settings"], "target_page_count": 48, "language": language},
    )
    assert updated and updated["settings"]["reading_direction"] == direction

    job = _run_job(updated, ExternalStoryboardProvider(48), monkeypatch)

    assert job["status"] == "completed"
    saved = db.get_project(project["id"], project["user_id"])
    assert saved and len(saved["storyboard"]) == 48
    assert saved["settings"]["language"] == language
    assert saved["settings"]["reading_direction"] == direction
