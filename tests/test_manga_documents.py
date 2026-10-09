"""漫画設計資料の版・確認・所有権と、内容に即したレイアウトの回帰。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import normalize_characters, normalize_storyboard
from app.services.ai_pipeline import compose_panel_prompt, storyboard_contract_prompt
from app.services.architect import finalize_storyboard
from app.services.composition import composition_quality_score, composition_quality_metrics
from app.services.layout import reflow_page, repair_storyboard_page
from app.services.knowledge import retrieve_knowledge_context
from app.services.manga_documents import character_sheet_design, name_script_summary


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "manga-docs@example.com", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "約束", "story_text": "葵は公園へ来た。「ここで待つ」と葵が言った。凛と灯が到着した。"}).json()["project"]
    settings = {**project["settings"], "target_page_count": 2, "rendering_style_id": "MASTER:STYLE-047/PRESET-26", "style_category": "category-03"}
    characters = normalize_characters([{"id": name, "name": name, "role": role, "appearance": "同じ黒髪", "identity_notes": "左耳の装飾を保持"} for name, role in [("葵", "主人公"), ("凛", "支援者"), ("灯", "同行者")]])
    pages = []
    for number in (1, 2):
        pages.append({"id": f"page-{number}", "page_number": number, "layout": "classic", "page_role": "到着と対話", "panel_count_reason": "場所の導入と返答を分ける", "layout_reason": "順に受け答えを読む", "panels": [
            {"id": f"panel-{number}-1", "description": "公園の入口で待つ", "characters": ["葵"], "action": "待つ", "background": "公園のベンチと入口", "location": "公園", "spatial_relationship": "ベンチの前に立つ", "reaction": "入口を見る", "dialogue": ["ここで待つ"], "dialogue_details": [{"speaker": "葵", "addressee": "凛", "source": "source_quote", "reaction": "うなずく"}], "scene_type": "establishing", "generation_prompt": "非公開の生成指示", "knowledge_refs": []},
            {"id": f"panel-{number}-2", "description": "視線を交わす", "characters": ["凛"], "background": "同じ公園", "dialogue": [], "expression": "笑顔"},
        ]})
    pages = normalize_storyboard(finalize_storyboard(pages, {}, settings), settings)
    project = db.update_project(project["id"], project["user_id"], settings=settings, characters=characters, storyboard=pages)
    return client, project


def prepare_and_confirm(client, project):
    base = f"/api/projects/{project['id']}"
    prepared = client.post(base + "/name-script")
    assert prepared.status_code == 200, prepared.text
    document = prepared.json()["document"]
    download = client.get(base + f"/manga-documents/{document['id']}/download")
    assert download.status_code == 200
    confirmed = client.post(base + "/name-script/confirmation", json={"design_hash": document["content_hash"]})
    assert confirmed.status_code == 200, confirmed.text
    return document, download.text


def test_whole_name_download_confirmation_and_dedup(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    prepared = client.post(base + "/name-script").json()
    document = prepared["document"]
    denied = client.post(base + "/name-script/confirmation", json={"design_hash": document["content_hash"]})
    assert denied.status_code == 409
    assert client.post(base + "/name-script").json()["document"]["id"] == document["id"]
    document, markdown = prepare_and_confirm(client, project)
    assert "本文 1ページ" in markdown and "本文 2ページ" in markdown
    assert markdown.count("### 1コマ目") == markdown.count("### 2コマ目") == 2
    for value in ("公園のベンチと入口", "ベンチの前に立つ", "葵 → 凛：ここで待つ", "出所：原作の発言", "反応：うなずく", "コマ数：2"):
        assert value in markdown
    assert "非公開の生成指示" not in markdown
    assert "言語・読順：日本語 / 右 → 左" in markdown
    assert "枠：長方形" in markdown
    assert "指示 6.33.0 / 設計資料 2.16.0" in markdown
    status = client.get(base + "/name-script").json()["name_script"]
    assert status["state"] == "confirmed" and status["version_number"] == 1
    assert "markdown" not in status["versions"][0] and "snapshot" not in status["versions"][0]
    db.init_db()
    assert name_script_summary(db.get_project(project["id"], project["user_id"]))["state"] == "confirmed"


def test_new_name_gate_and_client_cannot_remove_requirement(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    design = client.get(base + "/panels/panel-1-1/design").json()
    assert any("全編" in e for e in design["errors"])
    assert client.post(base + "/panels/panel-1-1/approval", json={"design_hash": design["design_hash"]}).status_code == 422
    stripped = deepcopy(project["storyboard"])
    for page in stripped:
        page.pop("name_review_required", None)
        page.pop("architect_source", None)
    result = client.patch(base, json={"storyboard": stripped})
    assert result.status_code == 200
    assert result.json()["project"]["name_script"]["required"]
    assert db.list_generation_jobs(project["id"]) == []


def test_repair_centered_text_is_local_and_keeps_unchanged_name_confirmation(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    pages = deepcopy(project["storyboard"])
    pages[0]["panels"][0].update(character_position="center", narration=["あ" * 25, "あ" * 14])
    pages[0] = reflow_page(pages[0], project["settings"])
    target = pages[0]["panels"][0]
    # 保存済み旧計画の、中央の人物に文字が重なる状態を再現する。
    for item in target["text_layout"]["items"]:
        item.update(x=.55, overflow=True)
    target["panel_direction"]["status"] = "needs_revision"
    pages[1]["panels"][0]["image_url"] = "/static/assets/existing.png"
    project = db.update_project(project["id"], project["user_id"], storyboard=pages)
    old_document, _ = prepare_and_confirm(client, project)
    before = db.get_project(project["id"], project["user_id"])
    old_design = client.get(base + f"/panels/{target['id']}/design").json()

    response = client.post(base + f"/pages/{pages[0]['id']}/layout/repair", json={})
    assert response.status_code == 200
    after = response.json()["project"]
    repaired = after["storyboard"][0]["panels"][0]
    assert not any(item["overflow"] for item in repaired["text_layout"]["items"])
    assert repaired["panel_direction"]["status"] == "ready"
    for key in ("id", "description", "dialogue", "narration", "characters", "image_url"):
        assert repaired.get(key) == target.get(key)
    assert after["storyboard"][1] == before["storyboard"][1]
    assert after["settings"] == before["settings"]
    assert after["characters"] == before["characters"]
    assert db.list_generation_jobs(project["id"]) == []
    assert after["name_script"]["state"] == "confirmed"
    assert after["name_script"]["version_id"] == old_document["id"]
    new_design = client.get(base + f"/panels/{target['id']}/design").json()
    assert new_design["design_hash"] != old_design["design_hash"]
    assert new_design["errors"] == []


def test_revision_invalidates_name_without_replacing_previous_file(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    old, original = prepare_and_confirm(client, project)
    edited = client.patch(base + "/panels/panel-2-2", json={"expression": "安堵"})
    assert edited.status_code == 200
    assert edited.json()["project"]["name_script"]["state"] == "outdated"
    assert client.post(base + "/name-script/confirmation", json={"design_hash": old["content_hash"]}).status_code == 409
    new, revised = prepare_and_confirm(client, project)
    assert new["version_number"] == 2 and new["id"] != old["id"]
    assert "安堵" in revised
    assert client.get(base + f"/manga-documents/{old['id']}/download").text == original


def test_generation_keeps_name_confirmation_and_records_version(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    name, _ = prepare_and_confirm(client, project)
    for panel_id in ("panel-1-1", "panel-2-2"):
        design = client.get(base + f"/panels/{panel_id}/design").json()
        assert design["errors"] == [], design["errors"]
        assert client.post(base + f"/panels/{panel_id}/approval", json={"design_hash": design["design_hash"]}).status_code == 200
        response = client.post(base + "/generate", json={"panel_ids": [panel_id]})
        assert response.status_code == 200, response.text
        refreshed = client.get(base).json()["project"]
        assert refreshed["name_script"]["state"] == "confirmed"
        panel = next(p for page in refreshed["storyboard"] for p in page["panels"] if p["id"] == panel_id)
        assert panel["generation_status"] == "completed", panel.get("generation_error")
    events = client.get(base).json()["project"]["generation_metadata"]
    assert any(e.get("name_script_version_id") == name["id"] for e in events)


def test_unknown_or_false_quote_and_incomplete_name(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    assert client.patch(base + "/panels/panel-1-1", json={"dialogue": ["原作にない証言"], "dialogue_details": [{"speaker": "葵", "source": "source_quote"}]}).status_code == 200
    document = client.post(base + "/name-script").json()
    assert any("原文" in e for e in document["validation"]["errors"])
    client.get(base + f"/manga-documents/{document['document']['id']}/download")
    assert client.post(base + "/name-script/confirmation", json={"design_hash": document['document']['content_hash']}).status_code == 422
    client.patch(base, json={"settings": {"target_page_count": 3}})
    assert any("ページ数" in e for e in client.post(base + "/name-script").json()["validation"]["errors"])


def test_documents_enforce_project_ownership(workspace):
    client, project = workspace
    name, _ = prepare_and_confirm(client, project)
    other = TestClient(app)
    other.post("/register", data={"email": "another-manga@example.com", "password": "local-test-password"})
    base = f"/api/projects/{project['id']}"
    for path in ("/name-script", f"/manga-documents/{name['id']}/download", "/characters/葵/style-sheet", "/characters/葵/style-sheet.md"):
        assert other.get(base + path).status_code == 404
    assert other.post(base + "/name-script").status_code == 404
    assert other.post(base + "/name-script/confirmation", json={"design_hash": name["content_hash"]}).status_code == 404
    assert other.post(base + "/name-script/page-count", json={"design_hash": name["content_hash"]}).status_code == 404


def _long_name(project, body_count):
    settings = {**project["settings"], "target_page_count": 120, "title_mode": "cover"}
    pages = [{"id": f"long-{number}", "page_number": number, "panels": [{
        "id": f"long-panel-{number}", "description": f"架空原稿の場面{number}",
        "image_url": f"/media/retained-{number}.png", "revision": 4,
    }], "knowledge_refs": [{"document_id": "reference", "version_id": "reference-v1", "version_number": 1}]} for number in range(1, body_count + 1)]
    return settings, finalize_storyboard(pages, {"title": "架空原稿"}, settings)


def test_maximum_body_and_cover_survive_save_and_confirmation(workspace):
    client, project = workspace
    settings, pages = _long_name(project, 120)
    normalized = normalize_storyboard(pages, settings)
    assert len(normalized) == 121
    assert normalized[-1]["id"] == "long-120"
    assert normalized[-1]["page_number"] == 120
    base = f"/api/projects/{project['id']}"
    for _ in range(2):
        response = client.patch(base, json={"settings": settings, "storyboard": normalized})
        assert response.status_code == 200, response.text
        normalized = response.json()["project"]["storyboard"]
    assert len(normalized) == 121
    assert normalized[-1]["panels"][0]["image_url"] == "/media/retained-120.png"
    assert normalized[-1]["panels"][0]["revision"] == 4
    assert normalized[-1]["knowledge_refs"][0]["version_id"] == "reference-v1"
    prepared = client.post(base + "/name-script").json()
    assert prepared["validation"]["errors"] == []
    assert prepared["validation"]["page_counts"] == {
        "target_content_pages": 120, "content_pages": 120, "cover_pages": 1,
        "total_pages": 121, "maximum_content_pages": 120, "back_cover_pages": 0,
    }
    _, markdown = prepare_and_confirm(client, project)
    assert "## 本文 120ページ" in markdown
    assert db.list_generation_jobs(project["id"]) == []


@pytest.mark.parametrize("cover", [False, True])
def test_oversized_name_is_rejected_without_truncating_saved_pages(workspace, cover):
    client, project = workspace
    settings, pages = _long_name(project, 121)
    if not cover:
        pages = pages[1:]
    with pytest.raises(ValueError, match="本文は120ページ"):
        normalize_storyboard(pages, settings)
    response = client.patch(f"/api/projects/{project['id']}", json={"storyboard": pages})
    assert response.status_code == 422
    assert "削らず" in response.json()["detail"]
    assert db.get_project(project["id"], project["user_id"])["storyboard"] == project["storyboard"]


def test_adopt_current_body_count_preserves_work_and_old_version(workspace, monkeypatch):
    client, project = workspace
    settings, pages = _long_name(project, 119)
    db.save_manga_settings_recommendation(project["id"], project["user_id"], {"recommended_page_count": 120})
    project = db.update_project(project["id"], project["user_id"], settings=settings, storyboard=normalize_storyboard(pages, settings))
    base = f"/api/projects/{project['id']}"
    old = client.post(base + "/name-script").json()
    old_id = old["document"]["id"]
    original = client.get(base + f"/manga-documents/{old_id}/download").text
    counts = old["name_script"]["page_counts"]
    assert (counts["target_content_pages"], counts["content_pages"], counts["cover_pages"]) == (120, 119, 1)
    assert "目標120ページ・現在119ページ" in old["validation"]["errors"][0]
    assert client.post(base + "/name-script/confirmation", json={"design_hash": old["document"]["content_hash"]}).status_code == 422

    def forbid_ai(*args, **kwargs):
        pytest.fail("目標ページ数の反映でAIを呼び出してはいけません")

    monkeypatch.setattr("app.main.get_ai_provider", forbid_ai)
    response = client.post(base + "/name-script/page-count", json={"design_hash": old["document"]["content_hash"]})
    assert response.status_code == 200, response.text
    adopted = response.json()
    saved = adopted["project"]
    assert saved["settings"] == {**project["settings"], "target_page_count": 119}
    for key in ("storyboard", "characters", "analysis", "original_text", "ai_model_settings"):
        assert saved[key] == project[key]
    assert saved["manga_settings_recommendation"]["user_override"] is True
    assert adopted["validation"]["errors"] == []
    assert adopted["document"]["version_number"] == 2
    assert adopted["name_script"]["state"] == "draft"
    assert [v["version_number"] for v in adopted["name_script"]["versions"]] == [2, 1]
    assert client.get(base + f"/manga-documents/{old_id}/download").text == original
    new_hash = adopted["document"]["content_hash"]
    assert client.post(base + "/name-script/confirmation", json={"design_hash": new_hash}).status_code == 409
    prepare_and_confirm(client, project)
    assert client.get(base + "/name-script").json()["name_script"]["state"] == "confirmed"
    assert client.post(base + "/name-script/page-count", json={"design_hash": old["document"]["content_hash"]}).status_code == 409
    assert db.list_generation_jobs(project["id"]) == []


def test_page_count_adoption_rejects_stale_or_active_work(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    old_hash = name_script_summary(project)["content_hash"]
    fresh = db.update_project(project["id"], project["user_id"], settings={**project["settings"], "target_page_count": 3})
    assert client.post(base + "/name-script/page-count", json={"design_hash": old_hash}).status_code == 409
    with pytest.raises(ValueError, match="更新されました"):
        db.adopt_name_page_count(project, 2)
    digest = name_script_summary(fresh)["content_hash"]
    job, created = db.create_async_generation_job(project["id"], "storyboard", "adoption-active")
    assert created and job
    response = client.post(base + "/name-script/page-count", json={"design_hash": digest})
    assert response.status_code == 409
    assert "生成処理中" in response.json()["detail"]
    assert db.get_project(project["id"], project["user_id"])["settings"]["target_page_count"] == 3


def test_page_count_adoption_requires_body_pages(workspace):
    client, project = workspace
    project = db.update_project(project["id"], project["user_id"], storyboard=[])
    response = client.post(f"/api/projects/{project['id']}/name-script/page-count", json={"design_hash": name_script_summary(project)["content_hash"]})
    assert response.status_code == 422


def test_sheet_all_characters_unknown_fields_ratios_and_history(workspace):
    client, project = workspace
    base = f"/api/projects/{project['id']}"
    sheet = client.get(base + "/characters/灯/style-sheet").json()["sheet"]
    assert sheet["sequence_index"] == sheet["total_characters"] == 3
    assert len(sheet["profile_fields"]) == 8
    assert [s["count"] for s in sheet["sections"]] == [8, 4, 8, 8, 6, 8, 5]
    assert next(f for f in sheet["profile_fields"] if f["label"] == "身長・サイズ") == {"label": "身長・サイズ", "value": "未設定", "source": "unknown"}
    first = client.get(base + "/characters/灯/style-sheet.md")
    assert first.status_code == 200 and "人物画像の生成・実画像監査は未実施" in first.text
    assert "正面 / 斜め前 / 真横 / 背面" in first.text and "3:4" in first.text
    client.get(base + "/characters/灯/style-sheet.md")
    assert len(client.get(base + "/characters/灯/style-sheet").json()["versions"]) == 1
    characters = deepcopy(project["characters"])
    characters[-1].update(sheet_ratio="3:2", palette_notes="黒と青", identity_notes="左耳の装飾と短い黒髪を保持")
    assert client.patch(base, json={"characters": characters}).status_code == 200
    second = client.get(base + "/characters/灯/style-sheet.md")
    assert "3:2" in second.text and "黒と青" in second.text and "3列" in second.text
    saved = client.get(base + "/characters/灯/style-sheet").json()
    versions = saved["versions"]
    assert saved["document"]["version_number"] == 2
    assert [v["version_number"] for v in versions] == [2, 1]
    assert client.get(base + f"/manga-documents/{versions[-1]['id']}/download").text == first.text


def test_knowledge_update_does_not_rewrite_confirmed_name(workspace):
    client, project = workspace
    created = client.post("/api/knowledge", data={"title": "背景資料", "source_text": "公園の入口とベンチを残す", "category": "layout"})
    assert created.status_code == 200, created.text
    knowledge = created.json()["knowledge"]
    document_id = knowledge["id"]
    selected = client.put(f"/api/projects/{project['id']}/knowledge", json={"selections": [{
        "knowledge_document_id": document_id, "mode": "follow_latest", "scope": ["storyboard"],
    }]})
    assert selected.status_code == 200, selected.text
    refs = retrieve_knowledge_context(project["id"], project["user_id"], "storyboard", "公園の入口とベンチ")["references"]
    assert refs[0]["version_id"] == knowledge["active_version_id"]
    pages = deepcopy(project["storyboard"])
    for page in pages:
        page["knowledge_refs"] = refs
    project = db.update_project(project["id"], project["user_id"], storyboard=pages)
    name, original = prepare_and_confirm(client, project)
    assert "選択したKnowledge：背景資料 / 版 1" in original
    updated = client.post(f"/api/knowledge/{document_id}/versions", data={"source_text": "公園の入口とベンチ、道の位置を残す"})
    assert updated.status_code == 200, updated.text
    assert client.get(f"/api/projects/{project['id']}/name-script").json()["name_script"]["state"] == "confirmed"
    assert client.get(f"/api/projects/{project['id']}/manga-documents/{name['id']}/download").text == original
    assert retrieve_knowledge_context(project["id"], project["user_id"], "storyboard", "公園の入口とベンチ")["references"][0]["version_number"] == 2


@pytest.mark.parametrize("number", [1, 2, 8])
def test_rectangular_layout_has_no_position_based_transform(number):
    page = {"id": "p", "page_number": number, "layout": "conversation", "panels": [{"id": f"p-{i}", "description": "静かな会話", "dialogue": [], "importance": "medium"} for i in range(6)]}
    result = reflow_page(page, {"composition_version": 4, "layout_policy": "content_driven"})
    assert all(g["shape"] == "rectangle" for g in result["composition"]["panels"])
    assert composition_quality_score(result)["dynamic_layout_score"] == 100
    assert composition_quality_metrics(result)["meaningful_dynamic_boundary_count"] == 0


def test_explicit_content_reason_allows_shared_diagonal_and_local_repair():
    reason = "二人が走り出す方向を共有斜辺で表現"
    page = {"id": "p", "page_number": 1, "layout": "conversation", "panels": [{"id": f"p-{i}", "description": "走り出す", "dialogue": [], "importance": "medium", "panel_shape": "trapezoid", "shape_reason": reason} for i in range(4)]}
    settings = {"composition_version": 4, "layout_policy": "content_driven"}
    result = reflow_page(page, settings)
    assert any(g["shape"] != "rectangle" and g["shape_reason"] == reason for g in result["composition"]["panels"])
    for panel in result["panels"]:
        panel.update(image_url="/media/old/art.png", revision=7)
    other = reflow_page({**page, "id": "other"}, settings)
    repaired = repair_storyboard_page([result, other], "p", settings)
    assert repaired[1] == other
    assert [(p["image_url"], p["revision"]) for p in repaired[0]["panels"]] == [("/media/old/art.png", 7)] * 4


def test_contract_reaches_image_and_storyboard_prompts(workspace):
    _, project = workspace
    prompt = compose_panel_prompt(project["storyboard"][0]["panels"][0], project["characters"], project["settings"])
    for value in ("公園", "ベンチの前に立つ", "入口を見る", "左耳の装飾を保持", "位置由来の自動変形禁止", "身体比率"):
        assert value in prompt
    assert "source_quote" in storyboard_contract_prompt()
    assert "位置だけで自動変形しない" in storyboard_contract_prompt()
    sheet = character_sheet_design(project, project["characters"][0])
    assert sheet["architect_source"]["knowledge_version"] == "2.16.0"
