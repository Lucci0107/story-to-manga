"""表紙の文字、保存・確認状態の維持、Preview/PDF/ZIPの同一性。"""

from copy import deepcopy
from io import BytesIO
import json
import zipfile

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageChops
from pypdf import PdfReader
import pytest

from app import db
from app.main import app
from app.schemas import SettingsPayload, normalize_storyboard
from app.services.architect import finalize_storyboard
from app.services.cover_text import cover_text
from app.services.export import _fit_cover_text, render_page_png, export_pdf, export_zip
from app.services.generation_design import design_data, design_hash
from app.services.manga_documents import name_script_summary
from app.services.storage import LocalFileStorage
from app.services.storyboard_jobs import storyboard_project_inputs, storyboard_inputs_match


@pytest.fixture
def covers(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_MANGA_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    client.post("/register", data={"email": "typography@example.test", "password": "local-test-password"})
    project = client.post("/api/projects", data={"title": "ある旅人の物語", "story_text": "旅人が町へ帰る。"}).json()["project"]
    settings = SettingsPayload(target_page_count=1, title_mode="cover", back_cover_mode="generate").model_dump()
    pages = normalize_storyboard(finalize_storyboard([{
        "id": "body", "page_number": 1, "layout": "hero", "panels": [{
            "id": "body-panel", "order": 1, "description": "町に帰る", "background": "町", "characters": [], "dialogue": [], "narration": [], "sfx": [],
        }],
    }], {"title": project["title"]}, settings), settings)
    project = db.update_project(project["id"], project["user_id"], settings=settings, storyboard=pages)
    return client, project, LocalFileStorage(tmp_path)


def test_cover_text_save_keeps_artwork_and_confirmation_and_design(covers, monkeypatch):
    client, project, storage = covers
    base = f"/api/projects/{project['id']}"
    client.post(base + "/name-script")
    summary = name_script_summary(db.get_project(project["id"], project["user_id"]))
    client.get(base + f"/manga-documents/{summary['version_id']}/download")
    assert client.post(base + "/name-script/confirmation", json={"design_hash": summary["content_hash"]}).status_code == 200
    project = db.get_project(project["id"], project["user_id"])
    page, panel = project["storyboard"][0], project["storyboard"][0]["panels"][0]
    design = design_hash(design_data(project, page, panel, {}, []))
    inputs = storyboard_project_inputs(project)
    body_before = render_page_png(project, project["storyboard"][1], storage)
    def no_ai(*args, **kwargs):
        pytest.fail("表紙の文字変更にAIを呼び出さない")
    monkeypatch.setattr("app.main.get_ai_provider", no_ai)
    response = client.patch(base, json={"settings": {"cover_author": "著：架空 太郎", "cover_title": "新しい表題", "back_cover_blurb": "ひとりの旅人の帰り道。"}})
    assert response.status_code == 200
    saved = response.json()["project"]
    assert saved["settings"]["cover_author"] == "著：架空 太郎"
    assert saved["name_script"]["state"] == "confirmed"
    assert saved["name_script"]["content_hash"] == summary["content_hash"]
    assert saved["storyboard"] == project["storyboard"]
    assert saved["settings"]["target_page_count"] == 1
    assert storyboard_inputs_match(saved, inputs)
    assert design_hash(design_data(saved, page, panel, {}, [])) == design
    assert render_page_png(saved, saved["storyboard"][1], storage) == body_before
    assert client.get(base).json()["project"]["settings"]["back_cover_blurb"] == "ひとりの旅人の帰り道。"
    other = TestClient(app)
    other.post("/register", data={"email": "other-type@example.test", "password": "local-test-password"})
    assert other.patch(base, json={"settings": {"cover_author": "別人"}}).status_code == 404


@pytest.mark.parametrize("kind", ["cover", "back_cover"])
def test_cover_artwork_fills_page_and_text_is_overlaid(covers, kind):
    _, project, storage = covers
    page = next(page for page in project["storyboard"] if page["page_kind"] == kind)
    source = Image.new("RGB", (400, 500), "green")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 29, 499), fill="red")
    draw.rectangle((370, 0, 399, 499), fill="blue")
    output = BytesIO()
    source.save(output, format="PNG")
    storage.put_bytes(storage.asset_key(project["id"], "cover.png"), output.getvalue(), content_type="image/png")
    page["panels"][0]["image_url"] = "/media/cover.png"
    page["panels"][0]["revision"] = 7
    original = deepcopy(project)
    before = Image.open(BytesIO(render_page_png(project, page, storage)))
    project["settings"].update(cover_author="著：架空 太郎", cover_subtitle="故郷への道", back_cover_blurb="帰る場所を探す旅。", back_cover_publication="発行：架空出版\n2026年10月")
    after = Image.open(BytesIO(render_page_png(project, page, storage)))
    colors = after.getcolors(after.width * after.height)
    assert all(any(color == expected and count > 1000 for count, color in colors) for expected in ((255, 0, 0), (0, 128, 0), (0, 0, 255)))
    # 文字のない中央では絵が四辺まで広がり、クリーム色の枠は残らない。
    assert after.getpixel((0, 600)) == (255, 0, 0)
    assert after.getpixel((899, 600)) == (0, 0, 255)
    assert after.getpixel((450, 600)) == (0, 128, 0)
    assert ImageChops.difference(before.crop((0, 1040, 900, 1180)), after.crop((0, 1040, 900, 1180))).getbbox()
    ink = [pixel for pixel in after.crop((70, 60, 830, 180)).getdata() if max(pixel) < 100]
    assert len(ink) > 1000  # 本文見出しより大きな専用タイトル
    assert project["storyboard"] == original["storyboard"]


def test_cover_preserves_protected_subject_when_full_page_crop_cannot_fit():
    from app.services.export import _cover_background
    source = Image.new("RGB", (600, 1200), "green")
    ImageDraw.Draw(source).rectangle((0, 0, 599, 49), fill="red")
    panel = {"panel_direction": {"head_safe_zone": {"x": 0, "y": 0, "width": 1, "height": 1}}}
    image = _cover_background(source, panel, 900, 1200)
    assert image.getpixel((450, 10)) == (255, 0, 0)
    assert image.getpixel((450, 1190)) == (0, 128, 0)


def test_cover_preview_zip_and_pdf_use_identical_render_and_complete_metadata(covers):
    client, project, storage = covers
    settings = {"cover_title": "帰り道", "cover_subtitle": "旅の記録", "cover_author": "原作：架空 太郎／漫画：架空 花子", "back_cover_blurb": "あらすじの末尾まで残す。" * 30, "back_cover_publication": "発行：架空出版\n© 架空 花子"}
    project = client.patch(f"/api/projects/{project['id']}", json={"settings": settings}).json()["project"]
    pdf = PdfReader(BytesIO(export_pdf(project, storage)))
    assert len(pdf.pages) == 3
    for index, page in ((0, project["storyboard"][0]), (2, project["storyboard"][-1])):
        png = render_page_png(project, page, storage)
        assert client.get(f"/api/projects/{project['id']}/pages/{page['id']}/composition.png").content == png
        extracted = pdf.pages[index].extract_text()
        for key in ("cover_title", "cover_subtitle", "cover_author"):
            assert settings[key] in extracted
        assert pdf.pages[index].images[0].image.convert("RGB").tobytes() == Image.open(BytesIO(png)).tobytes()
    assert settings["back_cover_blurb"] in pdf.pages[-1].extract_text()
    assert "© 架空 花子" in pdf.pages[-1].extract_text()
    with zipfile.ZipFile(BytesIO(export_zip(project, storage))) as archive:
        assert archive.read("pages/back-cover.png") == render_page_png(project, project["storyboard"][-1], storage)
        assert json.loads(archive.read("project.json"))["settings"]["cover_author"] == settings["cover_author"]


def test_cover_defaults_and_input_limits():
    assert cover_text({"title": "作品名"}) == {"title": "作品名", "subtitle": "", "author": "", "blurb": "", "publication": ""}
    for key, size in (("cover_title", 160), ("cover_author", 160), ("back_cover_blurb", 600), ("back_cover_publication", 300)):
        with pytest.raises(ValueError):
            SettingsPayload(**{key: "あ" * (size + 1)})


def test_cover_title_keeps_qualifier_and_blurb_does_not_orphan_punctuation():
    draw = ImageDraw.Draw(Image.new("RGB", (900, 1200)))
    title = "ある外科医の思考（本人視点）"
    wrapped, font = _fit_cover_text(draw, title, 752, 194, 59, title=True)
    assert wrapped.splitlines() == ["ある外科医の思考", "（本人視点）"]
    assert font.size >= 40
    blurb = "外科医としての歩みと、家族や人々との出会い。\n自分自身の経験をたどりながら、人生と仕事への思いを語る。"
    wrapped, _ = _fit_cover_text(draw, blurb, 716, 236, 26)
    assert wrapped.replace("\n", "") == blurb.replace("\n", "")
    assert not any(line.startswith(("。", "、", "）")) or len(line) < 3 for line in wrapped.splitlines())
    # 文字を消すのではなく、表紙専用の枠へ収めている。
    fitted, fitted_font = _fit_cover_text(draw, "句読点まで省略せずに表示します。" * 30, 716, 236, 26)
    assert fitted.replace("\n", "") == "句読点まで省略せずに表示します。" * 30
    bounds = draw.multiline_textbbox((0, 0), fitted, font=fitted_font, spacing=1)
    assert bounds[2] - bounds[0] <= 716 and bounds[3] - bounds[1] <= 236
