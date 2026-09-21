"""脚本・描画方式・承認の回帰。外部画像APIは呼ばない。"""

import json
import pytest
from fastapi.testclient import TestClient
from app import db
from app.main import app
from app.schemas import SettingsPayload, normalize_storyboard
from app.services.architect import (
    STYLES,
    compile_architect,
    tone_parameters,
    recommend_architect,
    style_signature,
    plan_event_boundaries,
    event_issues,
    finalize_storyboard,
)
from app.services.ai_pipeline import compose_panel_prompt


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    client.post(
        "/register",
        data={"email": "architect@example.com", "password": "local-testing-password"},
    )
    project = client.post(
        "/api/projects",
        data={"title": "検証", "story_text": "医師が鍵を見つける。扉を開ける。"},
    ).json()["project"]
    page = {
        "id": "page-1",
        "page_number": 1,
        "layout": "hero",
        "panels": [
            {
                "id": "panel-1",
                "description": "鍵を見つける",
                "action": "鍵を拾う",
                "characters": [],
                "dialogue": [],
            }
        ],
    }
    project = db.update_project(
        project["id"],
        project["user_id"],
        storyboard=normalize_storyboard([page], project["settings"]),
    )
    return client, project


def approve(client, project, panel_id="panel-1"):
    url = f"/api/projects/{project['id']}/panels/{panel_id}"
    design = client.get(url + "/design")
    assert design.status_code == 200
    result = client.post(
        url + "/approval", json={"design_hash": design.json()["design_hash"]}
    )
    assert result.status_code == 200, result.text
    return design.json()


@pytest.mark.parametrize("style_id", list(STYLES))
def test_all_style_constraints_survive_compiler(style_id):
    profile = STYLES[style_id]
    text = compose_panel_prompt(
        {"id": "p", "characters": ["葵"]},
        [{"name": "葵", "hairstyle": "短い黒髪", "accessories": "赤い眼鏡"}],
        {"rendering_style_id": style_id, "language": "ja"},
    )
    for condition in profile["required"] + profile["forbidden"]:
        assert condition in text
    assert "短い黒髪" in text and "赤い眼鏡" in text
    if style_id == "STYLE-022":
        assert "not a photograph" not in text


def test_style_profiles_differ_beyond_color():
    assert len({style_signature(p) for p in STYLES.values()}) == 31
    assert "uneven" in json.dumps(STYLES["STYLE-029"])
    assert "scribble" in json.dumps(STYLES["STYLE-030"])
    assert "continuous-line" in json.dumps(STYLES["STYLE-031"])


def test_tones_and_profile_precedence():
    assert tone_parameters({"script_tone_primary": "serious"}) != tone_parameters(
        {"script_tone_primary": "comedy"}
    )
    assert (
        tone_parameters({"script_tone_primary": "emotional"})["pause_frequency"]
        == "high"
    )
    assert (
        tone_parameters({"script_tone_primary": "mystery"})["reveal_policy"]
        == "preserve_clue_order_no_premature_reveal"
    )
    assert tone_parameters({"script_tone_primary": "horror"})["gore"] == "do_not_add"
    assert "2.5–3 heads tall" in compile_architect(
        {"rendering_style_id": "STYLE-001", "character_proportion": "natural"}
    )
    assert recommend_architect({"tone": "医療"}, {})["script_tone_primary"] == "serious"
    with pytest.raises(ValueError):
        SettingsPayload(script_tone_secondary=["comedy", "serious"])
    with pytest.raises(ValueError):
        SettingsPayload(style_category="category-01", rendering_style_id="STYLE-022")


def test_settings_roundtrip(setup):
    client, project = setup
    response = client.patch(
        f"/api/projects/{project['id']}",
        json={
            "settings": {
                "script_tone_primary": "mystery",
                "script_tone_secondary": "serious",
                "rendering_style_id": "STYLE-029",
                "style_category": "category-04",
            }
        },
    )
    assert response.status_code == 200, response.text
    stored = db.get_project(project["id"], project["user_id"])
    assert stored["settings"]["script_tone_primary"] == "mystery"
    assert stored["settings"]["rendering_style_id"] == "STYLE-029"


def test_events_future_dialogue_and_carryover():
    pages = plan_event_boundaries(
        [{"panels": [{"dialogue": ["扉を開ける"]}]}, {"panels": []}],
        {"major_events": ["鍵を見つける", "扉を開ける"]},
    )
    assert pages[0]["allowed_events"] == ["鍵を見つける"]
    assert event_issues(pages[0])
    result = normalize_storyboard(pages, {})
    assert result[0]["carry_over"] == ["扉を開ける"]
    pages[0]["panels"][0]["dialogue"] = []
    pages[0]["panels"][0]["event_ids"] = ["扉を開ける"]
    assert event_issues(pages[0])


def test_cover_metadata_and_numbering():
    pages = [{"panels": [{"id": "p", "description": "本文"}]}]
    result = normalize_storyboard(
        finalize_storyboard(pages, {"title": "検証"}, {"title_mode": "cover"}), {}
    )
    assert [p["page_number"] for p in result] == [0, 1]
    assert result[0]["page_kind"] == "cover"


def test_approval_required_navigation_save_auto_not_approval(setup):
    client, project = setup
    url = f"/api/projects/{project['id']}"
    for patch in [
        {"current_step": "generate"},
        {"title": "保存"},
        {"settings": {"style_requested_mode": "auto"}},
    ]:
        assert client.patch(url, json=patch).status_code == 200
        result = client.post(url + "/generate", json={"panel_ids": ["panel-1"]})
        assert result.status_code == 409, result.text
    assert db.list_generation_jobs(project["id"]) == []


def test_edit_invalidates_and_correct_design_generates_once(setup):
    client, project = setup
    url = f"/api/projects/{project['id']}"
    old = approve(client, project)
    client.patch(url + "/panels/panel-1", json={"action": "鍵を見つめる"})
    assert (
        client.post(
            url + "/panels/panel-1/approval", json={"design_hash": old["design_hash"]}
        ).status_code
        == 409
    )
    assert (
        client.post(url + "/generate", json={"panel_ids": ["panel-1"]}).status_code
        == 409
    )
    approve(client, project)
    response = client.post(url + "/generate", json={"panel_ids": ["panel-1"]})
    assert response.status_code == 200, response.text
    assert (
        db.get_project(project["id"], project["user_id"])["storyboard"][0]["panels"][0][
            "generation_status"
        ]
        == "completed"
    )
    assert (
        client.post(
            url + "/generate", json={"panel_ids": ["panel-1"], "force": True}
        ).status_code
        == 409
    )
    assert len(db.list_generation_jobs(project["id"])) == 1


def test_approval_ownership_and_new_target(setup):
    client, project = setup
    approve(client, project)
    other = TestClient(app)
    other.post(
        "/register",
        data={"email": "other@example.com", "password": "local-testing-password"},
    )
    assert (
        other.get(f"/api/projects/{project['id']}/panels/panel-1/design").status_code
        == 404
    )
    assert (
        client.post(
            f"/api/projects/{project['id']}/generate", json={"panel_ids": ["other"]}
        ).status_code
        == 404
    )


def test_existing_artwork_not_changed_by_settings(setup):
    client, project = setup
    approve(client, project)
    client.post(
        f"/api/projects/{project['id']}/generate", json={"panel_ids": ["panel-1"]}
    )
    before = db.get_project(project["id"], project["user_id"])["storyboard"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"settings": {"rendering_style_id": "STYLE-022"}},
    )
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == before


def test_approval_rejects_style_conflict_and_future_event(setup):
    client, project = setup
    url = f"/api/projects/{project['id']}"
    client.patch(
        url,
        json={"settings": {"rendering_style_id": "STYLE-012", "color_mode": "color"}},
    )
    design = client.get(url + "/panels/panel-1/design").json()
    assert design["state"] == "DESIGN_DRAFT"
    assert (
        client.post(
            url + "/panels/panel-1/approval",
            json={"design_hash": design["design_hash"]},
        ).status_code
        == 422
    )
    assert not db.list_generation_jobs(project["id"])


def test_edit_then_revert_does_not_restore_approval(setup):
    client, project = setup
    url = f"/api/projects/{project['id']}"
    approve(client, project)
    client.patch(url + "/panels/panel-1", json={"action": "変更"})
    client.patch(url + "/panels/panel-1", json={"action": "鍵を拾う"})
    assert (
        client.post(url + "/generate", json={"panel_ids": ["panel-1"]}).status_code
        == 409
    )


def test_atomic_approval_is_single_use(setup):
    client, project = setup
    view = approve(client, project)
    approval = db.current_generation_approval(
        project["id"], "panel-1", view["design_hash"], project["user_id"]
    )
    first = db.queue_approved_generation(approval, "batch")
    assert first
    assert db.queue_approved_generation(approval, "batch") is None
    assert len(db.list_generation_jobs(project["id"])) == 1


def test_job_design_changed_before_worker_never_calls_image(setup, monkeypatch):
    from fastapi import BackgroundTasks
    from app.main import queue_panels, process_generation_jobs

    client, project = setup
    approve(client, project)
    tasks = BackgroundTasks()
    queued = queue_panels(project, project["user_id"], ["panel-1"], False, False, tasks)
    client.patch(
        f"/api/projects/{project['id']}/panels/panel-1", json={"action": "変更後の行動"}
    )
    called = []
    monkeypatch.setattr(
        "app.main.save_panel_artwork", lambda *a, **k: called.append(True)
    )
    process_generation_jobs(
        project["id"], project["user_id"], [j["id"] for j in queued["jobs"]]
    )
    assert not called
    assert db.list_generation_jobs(project["id"])[0]["status"] == "failed"


@pytest.mark.parametrize(
    "tone", ["serious", "emotional", "comedy", "mystery", "horror"]
)
def test_visual_fixture_tone_changes_structured_plan(tone):
    from app.services.ai_pipeline import demo_storyboard, _storyboard_context
    from app.services.reading_order import reading_order_context

    settings = {
        "target_page_count": 2,
        "script_tone_primary": tone,
        "composition_version": 3,
        "language": "ja",
    }
    analysis = {"major_events": ["鍵を拾う", "扉を開ける"]}
    pages = demo_storyboard(
        "鍵を拾う。扉を開ける。", analysis, settings, [{"name": "葵"}]
    )
    assert pages[0]["script_tone_parameters"]["primary"] == tone
    assert pages[0]["allowed_events"] == ["鍵を拾う"]
    assert not event_issues(pages[0])
    context = _storyboard_context(
        "原文", analysis, settings, [], reading_order_context(settings), 1
    )
    assert context["script_tone_parameters"] == tone_parameters(settings)


def test_qa_reports_uninspected_images_honestly(setup):
    from app.services.knowledge import quality_check

    client, project = setup
    project["storyboard"][0]["panels"][0]["image_url"] = "/media/demo.png"
    report = quality_check(project, {})
    checks = {c["key"]: c for c in report["checks"]}
    for key in ("face", "body", "hands", "style-compliance"):
        assert checks[key]["status"] == "warning"
        assert "目視確認" in checks[key]["detail"]


def test_image_api_does_not_automatically_retry():
    import inspect
    from app.services.artwork import save_openai_image

    assert "max_retries=0" in inspect.getsource(save_openai_image)


def test_untrusted_panel_event_shape_is_normalized():
    result = normalize_storyboard(
        [{"panels": [None, {"id": "safe", "event_ids": {"bad": "value"}}]}], {}
    )
    assert len(result[0]["panels"]) == 1
    assert result[0]["panels"][0]["event_ids"] == []


@pytest.mark.parametrize("mode,stale", [("pinned", False), ("follow_latest", True)])
def test_knowledge_version_approval_binding(setup, mode, stale):
    client, project = setup
    document = client.post(
        "/api/knowledge",
        data={"title": "描画条件", "source_text": "# 描画\n黒い線で描く。"},
    ).json()["knowledge"]
    selected = client.put(
        f"/api/projects/{project['id']}/knowledge",
        json={
            "selections": [
                {
                    "knowledge_document_id": document["id"],
                    "enabled": True,
                    "mode": mode,
                    "selected_version_id": document["active_version_id"]
                    if mode == "pinned"
                    else None,
                    "scope": ["image_generation"],
                }
            ]
        },
    )
    assert selected.status_code == 200, selected.text
    approved = approve(client, project)
    response = client.post(
        f"/api/knowledge/{document['id']}/versions",
        data={"source_text": "# 描画\n余白を増やして描く。"},
    )
    assert response.status_code == 200, response.text
    current = client.get(f"/api/projects/{project['id']}/panels/panel-1/design").json()
    assert (approved["design_hash"] != current["design_hash"]) == stale
