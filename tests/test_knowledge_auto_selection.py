"""Knowledge自動選択、Scope初期値、QA解決状態の回帰テスト。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services.knowledge import default_knowledge_scope, retrieve_knowledge_context


def client_for(tmp_path: Path, email: str = "auto-knowledge@example.com") -> TestClient:
    os.environ["STORY_MANGA_DATA_DIR"] = str(tmp_path)
    db.init_db()
    client = TestClient(app)
    response = client.post(
        "/register",
        data={"email": email, "password": "long-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def create_knowledge(client: TestClient, title: str, category: str, text: str = "# ルール\n\n制作ルールを守る。") -> dict:
    response = client.post(
        "/api/knowledge",
        data={"title": title, "category": category, "source_text": text},
    )
    assert response.status_code == 200
    return response.json()["knowledge"]


def create_project(client: TestClient, title: str = "自動選択作品", **extra: str) -> dict:
    response = client.post(
        "/api/projects",
        data={"title": title, "story_text": "主人公は街へ向かった。", **extra},
    )
    assert response.status_code == 200
    return response.json()["project"]


def test_new_project_auto_selects_active_defaults_and_skips_disabled(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    layout = create_knowledge(client, "Layout", "layout")
    disabled = create_knowledge(client, "Disabled Style", "style")
    assert client.patch(f"/api/knowledge/{disabled['id']}", json={"active": False}).status_code == 200

    project = create_project(client)
    selected = {item["knowledge_document_id"]: item for item in project["knowledge"]}
    assert layout["id"] in selected
    assert disabled["id"] not in selected
    assert selected[layout["id"]]["enabled"] is True
    assert selected[layout["id"]]["mode"] == "follow_latest"
    assert selected[layout["id"]]["scope"] == default_knowledge_scope("layout")


def test_category_scope_defaults_include_layout_style_and_qa() -> None:
    assert default_knowledge_scope("layout") == [
        "storyboard",
        "page_layout",
        "panel_prompt",
        "quality_check",
        "export",
    ]
    style = default_knowledge_scope("style")
    assert {"storyboard", "page_layout", "quality_check"}.issubset(style)


def test_explicit_existing_selection_is_unchanged_by_recommendation(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    layout = create_knowledge(client, "Layout", "layout")
    style = create_knowledge(client, "Style", "style")
    project = create_project(client, knowledge_ids=layout["id"])
    before = project["knowledge"]

    recommendation = client.get(f"/api/projects/{project['id']}/knowledge/recommendation")
    assert recommendation.status_code == 200
    assert recommendation.json()["can_apply"] is False
    assert client.post(f"/api/projects/{project['id']}/knowledge/recommended").status_code == 409
    after = client.get(f"/api/projects/{project['id']}").json()["project"]["knowledge"]
    assert after == before
    assert {item["knowledge_document_id"] for item in after} == {layout["id"]}
    assert style["id"] not in {item["knowledge_document_id"] for item in after}


def test_existing_project_without_knowledge_gets_recommendation_only(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    project = create_project(client)
    layout = create_knowledge(client, "後から追加したLayout", "layout")

    recommendation = client.get(f"/api/projects/{project['id']}/knowledge/recommendation")
    assert recommendation.status_code == 200
    assert recommendation.json()["can_apply"] is True
    assert recommendation.json()["recommendations"][0]["knowledge_document_id"] == layout["id"]
    assert client.get(f"/api/projects/{project['id']}").json()["project"]["knowledge"] == []

    applied = client.post(f"/api/projects/{project['id']}/knowledge/recommended")
    assert applied.status_code == 200
    assert applied.json()["project"]["knowledge"][0]["knowledge_document_id"] == layout["id"]


def test_qa_distinguishes_no_selection_scope_mismatch_processing_and_disabled(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    project = create_project(client)
    user_id = db.get_user_by_email("auto-knowledge@example.com")["id"]
    no_selection = retrieve_knowledge_context(project["id"], user_id, "quality_check")
    assert no_selection["resolution_status"] == "no_selection"

    layout = create_knowledge(client, "限定Layout", "layout")
    configured = client.put(
        f"/api/projects/{project['id']}/knowledge",
        json={"selections": [{
            "knowledge_document_id": layout["id"],
            "enabled": True,
            "mode": "follow_latest",
            "scope": ["storyboard"],
        }]},
    )
    assert configured.status_code == 200
    mismatch = client.post(f"/api/projects/{project['id']}/quality-check").json()["quality_check"]
    assert mismatch["knowledge_resolution_status"] == "selected_no_relevant_chunks"
    assert "選択されていません" not in next(
        item["detail"] for item in mismatch["warnings"] if item["key"] == "knowledge"
    )

    assert client.put(
        f"/api/projects/{project['id']}/knowledge",
        json={"selections": [{
            "knowledge_document_id": layout["id"],
            "enabled": False,
            "mode": "follow_latest",
            "scope": ["quality_check"],
        }]},
    ).status_code == 200
    disabled = client.post(f"/api/projects/{project['id']}/quality-check").json()["quality_check"]
    assert disabled["knowledge_resolution_status"] == "disabled"

    with db.connection() as conn:
        conn.execute(
            "UPDATE knowledge_versions SET status = 'processing' WHERE id = ?",
            (layout["active_version_id"],),
        )
    assert client.put(
        f"/api/projects/{project['id']}/knowledge",
        json={"selections": [{
            "knowledge_document_id": layout["id"],
            "enabled": True,
            "mode": "follow_latest",
            "scope": ["quality_check"],
        }]},
    ).status_code == 200
    processing = client.post(f"/api/projects/{project['id']}/quality-check").json()["quality_check"]
    assert processing["knowledge_resolution_status"] == "processing_not_ready"


def test_qa_receives_layout_chunks_and_persists_traceability(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    layout = create_knowledge(client, "Layout Rules", "layout", "# QA\n\n余白とコマ密度を確認する。")
    project = create_project(client)
    result = client.post(f"/api/projects/{project['id']}/quality-check")
    assert result.status_code == 200
    quality = result.json()["quality_check"]
    assert quality["knowledge_resolution_status"] == "selected_relevant"
    reference = quality["knowledge_refs"][0]
    assert reference["knowledge_document_id"] == layout["id"]
    assert reference["knowledge_version_id"] == layout["active_version_id"]
    assert reference["chunk_ids"]
    assert reference["scope"] == "quality_check"
    persisted = client.get(f"/api/projects/{project['id']}").json()["project"]["quality_check"]
    assert persisted["knowledge_refs"] == quality["knowledge_refs"]


def test_follow_latest_and_pinned_version_behavior_is_preserved(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    knowledge = create_knowledge(client, "Version Rules", "layout", "Version 1")
    project = create_project(client)
    version_1 = knowledge["active_version_id"]
    version_2_response = client.post(
        f"/api/knowledge/{knowledge['id']}/versions",
        data={"source_text": "Version 2"},
    )
    assert version_2_response.status_code == 200
    version_2 = version_2_response.json()["version"]["id"]
    user_id = db.get_user_by_email("auto-knowledge@example.com")["id"]

    following = retrieve_knowledge_context(project["id"], user_id, "quality_check")
    assert following["references"][0]["knowledge_version_id"] == version_2

    pinned = client.put(
        f"/api/projects/{project['id']}/knowledge",
        json={"selections": [{
            "knowledge_document_id": knowledge["id"],
            "enabled": True,
            "mode": "pinned",
            "selected_version_id": version_1,
            "scope": ["quality_check"],
        }]},
    )
    assert pinned.status_code == 200
    pinned_context = retrieve_knowledge_context(project["id"], user_id, "quality_check")
    assert pinned_context["references"][0]["knowledge_version_id"] == version_1
    persisted = client.get(f"/api/projects/{project['id']}").json()["project"]["knowledge"][0]
    assert persisted["mode"] == "pinned"
    assert persisted["selected_version_id"] == version_1
