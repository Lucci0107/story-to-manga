"""Panel生成の集約進捗とstale復旧を確認するテスト。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from app import db
from app.main import all_panels, panel_generation_snapshot


def _project_with_panels(tmp_path: Path) -> tuple[dict, dict]:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    os.environ.pop("DATABASE_URL", None)
    db.init_db()
    user = db.create_user(f"panel-progress-{tmp_path.name}@example.com", "long-password")
    project = db.create_project(user["id"], "Panel進捗", "短い本文")
    storyboard = [
        {
            "id": "page-1",
            "page_number": 1,
            "layout": "classic",
            "panels": [
                {"id": "panel-1", "order": 1, "generation_status": "completed"},
                {"id": "panel-2", "order": 2, "generation_status": "processing"},
            ],
        },
        {
            "id": "page-2",
            "page_number": 2,
            "layout": "classic",
            "panels": [
                {"id": "panel-3", "order": 1, "generation_status": "queued"},
                {"id": "panel-4", "order": 2, "generation_status": "queued"},
            ],
        },
    ]
    updated = db.update_project(
        project["id"],
        user["id"],
        storyboard=storyboard,
        status="processing",
        current_step="generate",
    )
    assert updated
    return user, updated


def _create_panel_jobs(project: dict) -> list[dict]:
    jobs = []
    for panel_id in ("panel-1", "panel-2", "panel-3", "panel-4"):
        job = db.create_generation_job(
            project["id"], panel_id, f"panel:{panel_id}", batch_id="batch-1"
        )
        assert job
        jobs.append(job)
    db.update_generation_job(jobs[0]["id"], "completed")
    db.update_generation_job(jobs[1]["id"], "processing")
    return jobs


def test_active_panel_snapshot_reports_real_counts_and_current_target(tmp_path: Path) -> None:
    _user, project = _project_with_panels(tmp_path)
    _create_panel_jobs(project)

    snapshot = panel_generation_snapshot(project, db.list_generation_jobs(project["id"]))

    assert snapshot["active"] is True
    assert snapshot["status"] == "processing"
    assert snapshot["total"] == 4
    assert snapshot["completed"] == 1
    assert snapshot["generating"] == 1
    assert snapshot["waiting"] == 2
    assert snapshot["failed"] == 0
    assert snapshot["current_panel_id"] == "panel-2"
    assert snapshot["current_page"] == 1
    assert snapshot["current_panel"] == 2
    assert set(snapshot["batch_panel_ids"]) == {"panel-1", "panel-2", "panel-3", "panel-4"}


def test_completed_panel_snapshot_is_not_active(tmp_path: Path) -> None:
    _user, project = _project_with_panels(tmp_path)
    jobs = _create_panel_jobs(project)
    for job in jobs[1:]:
        db.update_generation_job(job["id"], "completed")
    completed = db.get_project(project["id"], project["user_id"])
    assert completed
    for _page, panel in all_panels(completed):
        panel["generation_status"] = "completed"
    completed = db.update_project(
        project["id"], project["user_id"], storyboard=completed["storyboard"], status="completed"
    )
    assert completed

    snapshot = panel_generation_snapshot(completed, db.list_generation_jobs(project["id"]))

    assert snapshot["active"] is False
    assert snapshot["status"] == "completed"


def test_stale_panel_jobs_fail_and_panels_become_retryable(tmp_path: Path) -> None:
    user, project = _project_with_panels(tmp_path)
    jobs = _create_panel_jobs(project)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db.connection() as connection:
        connection.execute(
            "UPDATE generation_jobs SET created_at = ?, updated_at = ? WHERE project_id = ?",
            (old_time, old_time, project["id"]),
        )

    recovered = db.recover_stale_panel_jobs(project["id"], user["id"], 900)
    saved = db.get_project(project["id"], user["id"])

    assert set(recovered) == {job["id"] for job in jobs[1:]}
    assert saved and saved["status"] == "partially_failed"
    assert all(
        panel["generation_status"] == "failed"
        for _page, panel in all_panels(saved)
        if panel["id"] in {"panel-2", "panel-3", "panel-4"}
    )
    assert all(db.get_generation_job(job["id"])["status"] == "failed" for job in jobs[1:])


def test_inactive_panel_job_cannot_be_completed_after_recovery(tmp_path: Path) -> None:
    user, project = _project_with_panels(tmp_path)
    job = db.create_generation_job(project["id"], "panel-2", "panel:panel-2")
    assert job
    db.update_generation_job(job["id"], "failed", "stale")
    current = db.get_project(project["id"], user["id"])
    assert current
    result = db.complete_panel_generation_job(
        job["id"], project["id"], user["id"], current["storyboard"]
    )

    assert result is False
    assert db.get_generation_job(job["id"])["status"] == "failed"


def test_stale_failed_panel_job_cannot_restart(tmp_path: Path) -> None:
    _user, project = _project_with_panels(tmp_path)
    job = db.create_generation_job(project["id"], "panel-2", "panel:restart-guard")
    assert job
    db.update_generation_job(job["id"], "failed", "stale")

    assert db.start_generation_job(job["id"]) is False
    assert db.get_generation_job(job["id"])["status"] == "failed"


def test_orphaned_active_panel_state_becomes_retryable(tmp_path: Path) -> None:
    """Jobが失われてもPanelだけをactiveのまま残さず、再試行可能へ収束させる。"""

    user, project = _project_with_panels(tmp_path)

    recovered = db.recover_orphaned_panel_states(project["id"], user["id"])
    saved = db.get_project(project["id"], user["id"])

    assert set(recovered) == {"panel-2", "panel-3", "panel-4"}
    assert saved and saved["status"] == "partially_failed"
    assert all(
        panel["generation_status"] == "failed"
        for _page, panel in all_panels(saved)
        if panel["id"] in set(recovered)
    )
