"""Story to MangaのFastAPIエントリーポイント。"""

from __future__ import annotations

import logging
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import BASE_DIR, ensure_data_dirs, get_settings
from .schemas import (
    ExportRequest,
    GenerateRequest,
    KnowledgeCreatePayload,
    KnowledgeMetadataPatch,
    ProjectKnowledgePatch,
    PanelPatch,
    ProjectPatch,
    normalize_analysis,
    normalize_characters,
    normalize_storyboard,
    validate_settings,
    validate_storyboard,
)
from .services.ai_pipeline import AIProviderError, DemoAIProvider, get_ai_provider
from .services.artwork import ArtworkGenerationError, asset_url, save_panel_artwork
from .services.extraction import StoryExtractionError, extract_uploaded_file
from .services.export import export_pdf, export_zip
from .services.knowledge import (
    append_knowledge_prompt,
    chunk_knowledge_text,
    knowledge_content_hash,
    quality_check as knowledge_quality_check,
    normalize_knowledge_text,
    retrieve_knowledge_context,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("story_to_manga")

ensure_data_dirs()
db.init_db()

app = FastAPI(title="Story to Manga", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_COOKIE = "story_manga_session"
STATUS_LABELS = {
    "draft": "下書き",
    "analysis_ready": "解析済み",
    "characters_ready": "人物設定済み",
    "storyboard_ready": "ネーム準備済み",
    "processing": "生成中",
    "partially_failed": "一部エラー",
    "completed": "完成に近い",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """最低限の安全ヘッダーを付け、APIキー等をクライアントへ出さない。"""

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.exception_handler(404)
async def not_found(request: Request, exc):
    """画面遷移は案内ページ、APIはJSONで返す。"""

    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "リソースが見つかりません")}, status_code=404)
    return templates.TemplateResponse(request, "404.html", {"request": request}, status_code=404)


def optional_user(request: Request):
    return db.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def current_user(request: Request):
    user = optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    return user


def redirect_with_session(url: str, token: str) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().session_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_env == "production",
    )
    return response


def clean_title(title: str) -> str:
    value = " ".join((title or "").split()).strip()
    if not value:
        raise HTTPException(status_code=422, detail="作品タイトルを入力してください")
    if len(value) > 120:
        raise HTTPException(status_code=422, detail="作品タイトルは120文字以内で入力してください")
    return value


def knowledge_version_view(version: Dict[str, Any]) -> Dict[str, Any]:
    """Knowledge Versionを本文全文なしのUI/API用辞書へ変換する。"""

    return {
        "id": version["id"],
        "version_number": version["version_number"],
        "source_filename": version.get("source_filename"),
        "status": version["status"],
        "chunk_count": version.get("chunk_count", 0),
        "content_hash": version.get("content_hash"),
        "created_at": version.get("created_at"),
        "is_active": version.get("is_active", False),
        "content_preview": str(version.get("normalized_text", ""))[:900],
    }


def knowledge_document_view(document: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Knowledge DocumentをUI/API用に変換し、本文全文は公開しない。"""

    versions = db.list_knowledge_versions(document["id"], user_id)
    return {
        **document,
        "versions": [knowledge_version_view(version) for version in versions],
    }


def require_knowledge_document(document_id: str, user_id: str) -> Dict[str, Any]:
    """Knowledge Documentの所有権を確認する。"""

    document = db.get_knowledge_document(document_id, user_id)
    if not document:
        raise HTTPException(status_code=404, detail="Knowledgeが見つかりません")
    return document


async def extract_knowledge_source(
    source_text: str, knowledge_file: Optional[UploadFile]
) -> Tuple[str, str, Optional[str]]:
    """Knowledgeの直接入力またはファイルを本文へ変換する。"""

    text = (source_text or "").strip()
    source_type = "text"
    source_filename = None
    if knowledge_file and knowledge_file.filename:
        try:
            text, source_type, source_filename = await extract_uploaded_file(knowledge_file)
        except StoryExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="Knowledge本文を入力するか、ファイルを選択してください")
    if len(text) > 300_000:
        raise HTTPException(status_code=422, detail="Knowledge本文は30万文字以内で入力してください")
    return text, source_type, source_filename


def project_view(project: Dict[str, Any]) -> Dict[str, Any]:
    """UI向けに進捗集計を追加する。"""

    pages = project.get("storyboard", []) or []
    panels = [panel for page in pages for panel in page.get("panels", [])]
    generated = sum(1 for panel in panels if panel.get("generation_status") == "completed")
    failed = sum(1 for panel in panels if panel.get("generation_status") == "failed")
    return {
        **project,
        "status_label": STATUS_LABELS.get(project.get("status"), "下書き"),
        "page_count": len(pages),
        "panel_count": len(panels),
        "generated_panel_count": generated,
        "failed_panel_count": failed,
        "knowledge": db.list_project_knowledge(project["id"], project["user_id"]) or [],
    }


def require_project(project_id: str, user_id: str) -> Dict[str, Any]:
    project = db.get_project(project_id, user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    return project


def all_panels(project: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for page in project.get("storyboard", []) or []:
        for panel in page.get("panels", []) or []:
            yield page, panel


def find_panel(project: Dict[str, Any], panel_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for page, panel in all_panels(project):
        if panel.get("id") == panel_id:
            return page, panel
    raise HTTPException(status_code=404, detail="コマが見つかりません")


def safe_job_error(exc: Exception) -> str:
    """ログやAPIに本文を含めず、ユーザーが再試行できるエラーへ変換する。"""

    if isinstance(exc, (AIProviderError, ArtworkGenerationError, StoryExtractionError)):
        return str(exc)
    return "生成処理でエラーが発生しました。もう一度試してください"


def merge_ai_quality_review(
    baseline: Dict[str, Any], review: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    """決定論的チェックへ、実AIレビューを安全に追加する。"""

    issues = list(baseline.get("issues", [])) + list(review.get("issues", []))
    warnings = list(baseline.get("warnings", [])) + list(review.get("warnings", []))
    checks = list(baseline.get("checks", []))
    checks.append(
        {
            "key": "ai_review",
            "label": "AI品質レビュー",
            "status": "pass" if review.get("status") == "pass" else "warning",
            "detail": str(review.get("summary", "AIレビューを完了しました"))[:500],
        }
    )
    return {
        **baseline,
        "status": "attention" if issues or warnings or review.get("status") != "pass" else "pass",
        "issues": issues[:32],
        "warnings": warnings[:32],
        "checks": checks[:32],
        "suggestions": list(review.get("suggestions", []))[:16],
        "ai_review": review,
        "mode": "openai",
        "knowledge_refs": context.get("references", []),
    }


def queue_panels(
    project: Dict[str, Any],
    user_id: str,
    panel_ids: List[str],
    retry_failed: bool,
    force: bool,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """対象コマをキューへ入れ、実行中の重複リクエストを除外する。"""

    storyboard = project.get("storyboard", []) or []
    selected = set(panel_ids)
    candidates: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for _page, panel in all_panels(project):
        if selected and panel.get("id") not in selected:
            continue
        status = panel.get("generation_status", "not_started")
        if status in {"queued", "processing"}:
            skipped.append(str(panel.get("id")))
            continue
        if not force and status == "completed":
            skipped.append(str(panel.get("id")))
            continue
        if status == "failed" and not retry_failed and not force:
            skipped.append(str(panel.get("id")))
            continue
        panel["generation_status"] = "queued"
        panel["generation_error"] = None
        candidates.append(panel)

    if selected:
        found_ids = {str(panel.get("id")) for _page, panel in all_panels(project)}
        missing = selected - found_ids
        if missing:
            raise HTTPException(status_code=404, detail="指定されたコマが見つかりません")

    jobs: List[Dict[str, Any]] = []
    for panel in candidates:
        revision = int(panel.get("revision", 0))
        suffix = secrets.token_hex(4) if force else str(revision)
        idempotency_key = f"panel:{project['id']}:{panel['id']}:{suffix}"
        job = db.create_generation_job(project["id"], str(panel["id"]), idempotency_key)
        if job and job.get("status") in {"queued", "processing"}:
            jobs.append(job)

    if jobs:
        db.update_project(
            project["id"],
            user_id,
            status="processing",
            current_step="generate",
            storyboard=storyboard,
            clear_quality_check=True,
        )
        background_tasks.add_task(process_generation_jobs, project["id"], user_id, [job["id"] for job in jobs])
    return {"jobs": jobs, "queued_panel_ids": [panel.get("id") for panel in candidates], "skipped_panel_ids": skipped}


def process_generation_jobs(project_id: str, user_id: str, job_ids: List[str]) -> None:
    """バックグラウンドでコマを一枚ずつ処理する。各コマを個別に再試行できる。"""

    for job_id in job_ids:
        job = db.get_generation_job(job_id)
        if not job:
            continue
        db.update_generation_job(job_id, "processing")
        project = db.get_project(project_id, user_id)
        if not project:
            db.update_generation_job(job_id, "failed", "Projectが見つかりません")
            continue
        try:
            _page, panel = find_panel(project, str(job["target_id"]))
            panel["generation_status"] = "processing"
            db.update_project(project_id, user_id, storyboard=project["storyboard"], clear_quality_check=True)
            time.sleep(0.18)
            latest = db.get_project(project_id, user_id) or project
            _latest_page, latest_panel = find_panel(latest, str(job["target_id"]))
            knowledge_context = retrieve_knowledge_context(
                project_id,
                user_id,
                "image_generation",
                " ".join(
                    str(latest_panel.get(key, ""))
                    for key in ("description", "action", "expression", "background")
                ),
            )
            provider = get_ai_provider()
            prompt_source = latest_panel.get("prompt_source", "generated")
            if getattr(provider, "uses_external_api", False) and prompt_source != "user":
                base_prompt = provider.panel_prompt(
                    latest_panel,
                    latest.get("characters") or [],
                    latest.get("settings") or {},
                    knowledge_context,
                )
            else:
                base_prompt = str(latest_panel.get("generation_prompt", ""))
                if not base_prompt.strip():
                    base_prompt = provider.panel_prompt(
                        latest_panel,
                        latest.get("characters") or [],
                        latest.get("settings") or {},
                        knowledge_context,
                    )
            latest_panel["generation_prompt"] = append_knowledge_prompt(base_prompt, knowledge_context)
            latest_panel["knowledge_refs"] = knowledge_context.get("references", [])
            file_path = save_panel_artwork(latest["id"], latest_panel, latest["settings"])
            latest_panel["image_url"] = asset_url(latest["id"], file_path)
            latest_panel["generation_status"] = "completed"
            latest_panel["generation_error"] = None
            db.update_project(project_id, user_id, storyboard=latest["storyboard"], clear_quality_check=True)
            db.update_generation_job(job_id, "completed")
        except Exception as exc:  # noqa: BLE001
            message = safe_job_error(exc)
            logger.exception("panel generation failed")
            fallback = db.get_project(project_id, user_id)
            if fallback:
                try:
                    _page, failed_panel = find_panel(fallback, str(job["target_id"]))
                    failed_panel["generation_status"] = "failed"
                    failed_panel["generation_error"] = message
                    db.update_project(project_id, user_id, storyboard=fallback["storyboard"], status="partially_failed", clear_quality_check=True)
                except HTTPException:
                    pass
            db.update_generation_job(job_id, "failed", message)
    finished = db.get_project(project_id, user_id)
    if finished:
        panels = [panel for _page, panel in all_panels(finished)]
        if panels and all(panel.get("generation_status") == "completed" for panel in panels):
            db.update_project(project_id, user_id, status="completed", current_step="edit")
        elif any(panel.get("generation_status") == "failed" for panel in panels):
            db.update_project(project_id, user_id, status="partially_failed")


def demo_story() -> str:
    return (
        "夕暮れの町で、蒼は古いキーホルダーを握りしめて灯台へ向かった。\n\n"
        "そこには、先に来ていた凛がいた。凛は急かさず、蒼が言葉を選ぶ時間を待った。\n\n"
        "ふたりは過去の約束について話し、蒼はようやく自分の決断を伝える。"
    )


def get_or_create_demo_project(user_id: str) -> Dict[str, Any]:
    projects = db.list_projects(user_id)
    if projects:
        return projects[0]
    project = db.create_project(user_id, "灯台までの帰り道", demo_story(), "text")
    # /demoは動作確認用の入口なので、APIキーがあっても課金リクエストを発生させない。
    provider = DemoAIProvider()
    analysis = provider.analyze(project["original_text"], project["title"])
    characters = provider.characters(project["original_text"], analysis)
    storyboard = provider.storyboard(project["original_text"], analysis, project["settings"], characters)
    return db.update_project(
        project["id"],
        user_id,
        analysis=analysis,
        update_analysis=True,
        characters=characters,
        storyboard=storyboard,
        status="storyboard_ready",
        current_step="storyboard",
    ) or project


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse("/dashboard" if optional_user(request) else "/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if optional_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not db.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "メールアドレスまたはパスワードを確認してください"},
            status_code=401,
        )
    return redirect_with_session("/dashboard", db.create_session(user["id"]))


@app.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):
    normalized = email.lower().strip()
    if "@" not in normalized or len(normalized) > 160:
        return JSONResponse({"detail": "有効なメールアドレスを入力してください"}, status_code=422)
    if len(password) < 8:
        return JSONResponse({"detail": "パスワードは8文字以上で入力してください"}, status_code=422)
    if db.get_user_by_email(normalized):
        return JSONResponse({"detail": "このメールアドレスはすでに登録されています"}, status_code=409)
    try:
        user = db.create_user(normalized, password)
    except Exception:  # noqa: BLE001
        return JSONResponse({"detail": "アカウントを作成できませんでした"}, status_code=409)
    return redirect_with_session("/dashboard", db.create_session(user["id"]))


@app.get("/demo")
async def demo_login():
    user = db.get_user_by_email("demo@example.com")
    if not user:
        user_data = db.create_user("demo@example.com", secrets.token_urlsafe(24))
        user = db.get_user(user_data["id"])
    project = get_or_create_demo_project(user["id"])
    return redirect_with_session(f"/projects/{project['id']}", db.create_session(user["id"]))


@app.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    db.delete_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    projects = [project_view(project) for project in db.list_projects(user["id"])]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "user": dict(user), "projects": projects},
    )


@app.get("/projects/new", response_class=HTMLResponse)
async def new_project_page(request: Request):
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    knowledge_documents = [
        knowledge_document_view(document, user["id"])
        for document in db.list_knowledge_documents(user["id"])
        if not document.get("archived")
    ]
    return templates.TemplateResponse(
        request,
        "new_project.html",
        {"request": request, "user": dict(user), "knowledge_documents": knowledge_documents},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """アカウントと接続状態を確認する設定画面を返す。秘密値は渡さない。"""

    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    runtime = get_settings()
    safe_runtime = {
        "app_env": runtime.app_env,
        "ai_provider": runtime.ai_provider,
        "image_provider": runtime.image_provider,
        "max_upload_mb": runtime.max_upload_bytes // (1024 * 1024),
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"request": request, "user": dict(user), "runtime": safe_runtime},
    )


@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_library_page(request: Request):
    """ユーザーのKnowledge Library一覧を表示する。"""

    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    documents = [
        knowledge_document_view(document, user["id"])
        for document in db.list_knowledge_documents(user["id"])
    ]
    return templates.TemplateResponse(
        request,
        "knowledge.html",
        {"request": request, "user": dict(user), "knowledge_documents": documents},
    )


@app.get("/knowledge/{knowledge_id}", response_class=HTMLResponse)
async def knowledge_detail_page(request: Request, knowledge_id: str):
    """Knowledgeの本文概要とVersion履歴を表示する。"""

    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    document = require_knowledge_document(knowledge_id, user["id"])
    return templates.TemplateResponse(
        request,
        "knowledge_detail.html",
        {
            "request": request,
            "user": dict(user),
            "knowledge": knowledge_document_view(document, user["id"]),
        },
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def workspace_page(request: Request, project_id: str):
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    project = project_view(require_project(project_id, user["id"]))
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {"request": request, "user": dict(user), "project": project},
    )


@app.get("/media/{project_id}/{filename}")
async def project_media(project_id: str, filename: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    safe_project_id = Path(project["id"]).name
    safe_filename = Path(filename).name
    path = get_settings().asset_dir / safe_project_id / safe_filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.get("/api/health")
async def health():
    settings = get_settings()
    active_image_provider = (
        "openai" if settings.image_provider == "openai" and settings.openai_api_key else "demo"
    )
    return {
        "status": "ok",
        "service": "story-to-manga",
        "ai_provider": get_ai_provider().provider_name,
        "image_provider": active_image_provider,
    }


@app.get("/api/knowledge")
async def api_knowledge(user=Depends(current_user)):
    """所有Knowledgeを本文なしで返す。"""

    documents = [
        knowledge_document_view(document, user["id"])
        for document in db.list_knowledge_documents(user["id"])
    ]
    return {"knowledge": documents}


@app.get("/api/knowledge/{knowledge_id}")
async def api_get_knowledge(knowledge_id: str, user=Depends(current_user)):
    document = require_knowledge_document(knowledge_id, user["id"])
    return {"knowledge": knowledge_document_view(document, user["id"])}


@app.post("/api/knowledge")
async def api_create_knowledge(
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form("other"),
    source_text: str = Form(""),
    knowledge_file: Optional[UploadFile] = File(None),
    user=Depends(current_user),
):
    """Knowledge Documentを作成し、初回Versionを索引化する。"""

    try:
        metadata = KnowledgeCreatePayload(title=title, description=description, category=category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Knowledgeのタイトルまたはカテゴリを確認してください") from exc
    raw_text, source_type, source_filename = await extract_knowledge_source(source_text, knowledge_file)
    normalized_text = normalize_knowledge_text(raw_text)
    if not normalized_text:
        raise HTTPException(status_code=422, detail="Knowledge本文が空です")
    chunks = chunk_knowledge_text(normalized_text)
    document = db.create_knowledge_document(
        user["id"],
        metadata.title,
        metadata.description.strip(),
        metadata.category,
        source_filename or ("direct-input.txt" if source_type == "text" else None),
        raw_text,
        normalized_text,
        knowledge_content_hash(normalized_text),
        chunks,
    )
    return {"knowledge": knowledge_document_view(document, user["id"]), "status": "ready"}


@app.patch("/api/knowledge/{knowledge_id}")
async def api_update_knowledge(
    knowledge_id: str,
    payload: KnowledgeMetadataPatch,
    user=Depends(current_user),
):
    document = require_knowledge_document(knowledge_id, user["id"])
    title = clean_title(payload.title) if payload.title is not None else None
    updated = db.update_knowledge_document(
        document["id"],
        user["id"],
        title=title,
        description=payload.description.strip() if payload.description is not None else None,
        category=payload.category,
        active=payload.active,
        archived=payload.archived,
    )
    return {"knowledge": knowledge_document_view(updated or document, user["id"])}


@app.delete("/api/knowledge/{knowledge_id}")
async def api_delete_knowledge(knowledge_id: str, user=Depends(current_user)):
    require_knowledge_document(knowledge_id, user["id"])
    if not db.delete_knowledge_document(knowledge_id, user["id"]):
        raise HTTPException(status_code=404, detail="Knowledgeが見つかりません")
    return {"deleted": True, "knowledge_id": knowledge_id}


@app.post("/api/knowledge/{knowledge_id}/versions")
async def api_create_knowledge_version(
    knowledge_id: str,
    source_text: str = Form(""),
    knowledge_file: Optional[UploadFile] = File(None),
    user=Depends(current_user),
):
    """既存Knowledgeへ本文を追加し、内容が同じならVersionを増やさない。"""

    document = require_knowledge_document(knowledge_id, user["id"])
    raw_text, source_type, source_filename = await extract_knowledge_source(source_text, knowledge_file)
    normalized_text = normalize_knowledge_text(raw_text)
    if not normalized_text:
        raise HTTPException(status_code=422, detail="Knowledge本文が空です")
    content_hash = knowledge_content_hash(normalized_text)
    existing = db.find_knowledge_version_by_hash(knowledge_id, content_hash)
    if existing:
        return {
            "knowledge": knowledge_document_view(document, user["id"]),
            "version": knowledge_version_view(existing),
            "duplicate": True,
            "status": existing.get("status", "ready"),
        }
    version = db.create_knowledge_version(
        knowledge_id,
        user["id"],
        source_filename or ("direct-input.txt" if source_type == "text" else None),
        raw_text,
        normalized_text,
        content_hash,
        chunk_knowledge_text(normalized_text),
    )
    if not version:
        raise HTTPException(status_code=404, detail="Knowledgeが見つかりません")
    refreshed = db.get_knowledge_document(knowledge_id, user["id"]) or document
    return {
        "knowledge": knowledge_document_view(refreshed, user["id"]),
        "version": knowledge_version_view(version),
        "duplicate": False,
        "status": "ready",
    }


@app.post("/api/knowledge/{knowledge_id}/versions/{version_id}/activate")
async def api_activate_knowledge_version(
    knowledge_id: str,
    version_id: str,
    user=Depends(current_user),
):
    require_knowledge_document(knowledge_id, user["id"])
    updated = db.activate_knowledge_version(knowledge_id, version_id, user["id"])
    if not updated:
        raise HTTPException(status_code=422, detail="Ready状態のVersionを選択してください")
    return {"knowledge": knowledge_document_view(updated, user["id"])}


@app.get("/api/projects")
async def api_projects(user=Depends(current_user)):
    return {"projects": [project_view(project) for project in db.list_projects(user["id"])]}


@app.post("/api/projects")
async def api_create_project(
    title: str = Form(...),
    story_text: str = Form(""),
    knowledge_ids: Optional[List[str]] = Form(None),
    story_file: Optional[UploadFile] = File(None),
    user=Depends(current_user),
):
    clean = clean_title(title)
    source_type = "text"
    source_filename = None
    text = story_text.strip()
    if story_file and story_file.filename:
        try:
            text, source_type, source_filename = await extract_uploaded_file(story_file)
        except StoryExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not text:
        raise HTTPException(status_code=422, detail="本文を入力するか、ファイルを選択してください")
    if len(text) > 500_000:
        raise HTTPException(status_code=422, detail="本文は50万文字以内で入力してください")
    project = db.create_project(user["id"], clean, text, source_type, source_filename)
    selected_ids = list(dict.fromkeys(str(item) for item in (knowledge_ids or []) if str(item).strip()))
    if selected_ids:
        try:
            db.set_project_knowledge(
                project["id"],
                user["id"],
                [
                    {
                        "knowledge_document_id": document_id,
                        "enabled": True,
                        "priority": 50,
                        "mode": "follow_latest",
                        "scope": ["all"],
                    }
                    for document_id in selected_ids
                ],
            )
        except ValueError as exc:
            db.delete_project(project["id"], user["id"])
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project = db.get_project(project["id"], user["id"]) or project
    return {"project": project_view(project)}


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, user=Depends(current_user)):
    return {"project": project_view(require_project(project_id, user["id"]))}


@app.get("/api/projects/{project_id}/knowledge")
async def api_project_knowledge(project_id: str, user=Depends(current_user)):
    selections = db.list_project_knowledge(project_id, user["id"])
    if selections is None:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    return {"knowledge": selections}


@app.put("/api/projects/{project_id}/knowledge")
async def api_update_project_knowledge(
    project_id: str,
    payload: ProjectKnowledgePatch,
    user=Depends(current_user),
):
    require_project(project_id, user["id"])
    try:
        selections = db.set_project_knowledge(
            project_id,
            user["id"],
            [selection.model_dump() for selection in payload.selections],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.update_project(project_id, user["id"], clear_quality_check=True)
    project = require_project(project_id, user["id"])
    return {"project": project_view(project), "knowledge": selections or []}


@app.post("/api/projects/{project_id}/quality-check")
async def api_quality_check(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    context = retrieve_knowledge_context(
        project_id, user["id"], "quality_check", project.get("original_text", "")
    )
    result = knowledge_quality_check(project, context)
    provider = get_ai_provider()
    if getattr(provider, "uses_external_api", False):
        try:
            review = provider.quality_check(project, context)
        except AIProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        result = merge_ai_quality_review(result, review, context)
    saved = db.save_quality_check(project_id, user["id"], result)
    return {
        "project": project_view(saved or project),
        "quality_check": result,
        "knowledge": context,
    }


@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, payload: ProjectPatch, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    settings = validate_settings(payload.settings.model_dump()) if payload.settings else None
    if payload.original_text is not None and not payload.original_text.strip():
        raise HTTPException(status_code=422, detail="本文を空にすることはできません")
    characters = normalize_characters(payload.characters) if payload.characters is not None else None
    storyboard = normalize_storyboard(payload.storyboard) if payload.storyboard is not None else None
    if storyboard is not None:
        valid, message = validate_storyboard(storyboard)
        if not valid:
            raise HTTPException(status_code=422, detail=message)
    if characters is not None and len(characters) > 64:
        raise HTTPException(status_code=422, detail="キャラクターは64人以内で指定してください")
    clear_quality_check = any(
        value is not None
        for value in (payload.original_text, payload.settings, payload.analysis, payload.characters, payload.storyboard)
    )
    updated = db.update_project(
        project_id,
        user["id"],
        title=clean_title(payload.title) if payload.title is not None else None,
        original_text=payload.original_text,
        current_step=payload.current_step,
        status=payload.status,
        settings=settings,
        analysis=normalize_analysis(payload.analysis) if payload.analysis is not None else None,
        update_analysis=payload.analysis is not None,
        characters=characters,
        storyboard=storyboard,
        clear_quality_check=clear_quality_check,
    )
    return {"project": project_view(updated or project)}


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, user=Depends(current_user)):
    require_project(project_id, user["id"])
    if not db.delete_project(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    return {"deleted": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/analysis")
async def api_generate_analysis(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    knowledge_context = retrieve_knowledge_context(
        project_id, user["id"], "story_analysis", project["original_text"]
    )
    provider = get_ai_provider()
    try:
        analysis = provider.analyze(
            project["original_text"], project["title"], knowledge_context
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    analysis = normalize_analysis(analysis)
    analysis["knowledge_refs"] = knowledge_context.get("references", [])
    updated = db.update_project(
        project_id,
        user["id"],
        analysis=analysis,
        update_analysis=True,
        status="analysis_ready",
        current_step="analysis",
        clear_quality_check=True,
    )
    return {
        "project": project_view(updated or project),
        "mode": provider.provider_name,
        "knowledge": knowledge_context,
    }


@app.post("/api/projects/{project_id}/characters")
async def api_generate_characters(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    if not project.get("analysis"):
        raise HTTPException(status_code=400, detail="先に物語解析を生成してください")
    knowledge_context = retrieve_knowledge_context(
        project_id,
        user["id"],
        "character",
        str(project["analysis"]),
    )
    provider = get_ai_provider()
    try:
        characters = provider.characters(
            project["original_text"], project["analysis"], knowledge_context
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    characters = normalize_characters(characters)
    for character in characters:
        character["knowledge_refs"] = knowledge_context.get("references", [])
    updated = db.update_project(
        project_id,
        user["id"],
        characters=characters,
        status="characters_ready",
        current_step="characters",
        clear_quality_check=True,
    )
    return {
        "project": project_view(updated or project),
        "mode": provider.provider_name,
        "knowledge": knowledge_context,
    }


@app.post("/api/projects/{project_id}/storyboard")
async def api_generate_storyboard(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    if not project.get("analysis"):
        raise HTTPException(status_code=400, detail="先に物語解析を生成してください")
    provider = get_ai_provider()
    knowledge_context = retrieve_knowledge_context(
        project_id,
        user["id"],
        "storyboard",
        str(project["analysis"]),
    )
    try:
        characters = project.get("characters") or provider.characters(
            project["original_text"], project["analysis"], knowledge_context
        )
        storyboard = provider.storyboard(
            project["original_text"],
            project["analysis"],
            project["settings"],
            characters,
            knowledge_context,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    storyboard = normalize_storyboard(storyboard)
    for page in storyboard:
        for panel in page.get("panels", []):
            panel["knowledge_refs"] = knowledge_context.get("references", [])
    updated = db.update_project(
        project_id,
        user["id"],
        characters=characters,
        storyboard=storyboard,
        status="storyboard_ready",
        current_step="storyboard",
        clear_quality_check=True,
    )
    return {
        "project": project_view(updated or project),
        "mode": provider.provider_name,
        "knowledge": knowledge_context,
    }


@app.patch("/api/projects/{project_id}/panels/{panel_id}")
async def api_update_panel(project_id: str, panel_id: str, payload: PanelPatch, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    _page, panel = find_panel(project, panel_id)
    values = payload.model_dump(exclude_unset=True)
    visual_changed = False
    structure_changed = False
    structure_keys = {"description", "characters", "shot_type", "action", "expression", "background"}
    visual_keys = structure_keys | {"generation_prompt"}
    for key, value in values.items():
        if key in visual_keys and panel.get(key) != value:
            visual_changed = True
        if key in structure_keys and panel.get(key) != value:
            structure_changed = True
        if key == "generation_prompt" and panel.get(key) != value:
            panel["prompt_source"] = "user"
        panel[key] = value
    if structure_changed:
        panel["generation_prompt"] = ""
        panel["prompt_source"] = "generated"
        panel["generation_status"] = "not_started"
        panel["generation_error"] = None
    elif visual_changed:
        panel["generation_status"] = "not_started"
        panel["generation_error"] = None
    next_status = "storyboard_ready" if visual_changed and project.get("status") == "completed" else None
    updated = db.update_project(project_id, user["id"], storyboard=project["storyboard"], status=next_status, clear_quality_check=True)
    return {"project": project_view(updated or project)}


@app.post("/api/projects/{project_id}/generate")
async def api_generate_panels(
    project_id: str,
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
    user=Depends(current_user),
):
    project = require_project(project_id, user["id"])
    if not project.get("storyboard"):
        raise HTTPException(status_code=400, detail="先にネームを生成してください")
    result = queue_panels(
        project,
        user["id"],
        payload.panel_ids,
        payload.retry_failed,
        payload.force,
        background_tasks,
    )
    return {"accepted": True, **result}


@app.post("/api/projects/{project_id}/panels/{panel_id}/retry")
async def api_retry_panel(
    project_id: str,
    panel_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_user),
):
    project = require_project(project_id, user["id"])
    _page, panel = find_panel(project, panel_id)
    if panel.get("generation_status") not in {"failed", "not_started", "completed"}:
        raise HTTPException(status_code=409, detail="このコマはすでに生成処理中です")
    result = queue_panels(project, user["id"], [panel_id], True, True, background_tasks)
    return {"accepted": True, **result}


@app.get("/api/projects/{project_id}/generation/status")
async def api_generation_status(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    panels = []
    for page, panel in all_panels(project):
        panels.append(
            {
                "id": panel.get("id"),
                "page_id": page.get("id"),
                "page_number": page.get("page_number"),
                "status": panel.get("generation_status", "not_started"),
                "error": panel.get("generation_error"),
                "revision": panel.get("revision", 0),
                "image_url": panel.get("image_url"),
            }
        )
    return {"project_status": project.get("status"), "panels": panels, "jobs": db.list_generation_jobs(project_id)}


@app.post("/api/projects/{project_id}/export")
async def api_export(project_id: str, payload: ExportRequest, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    export_record = db.create_export(project_id, payload.format, None, "processing")
    settings = get_settings()
    target = settings.export_dir / f"{project_id}-{export_record['id']}.{payload.format}"
    try:
        if payload.format == "pdf":
            export_pdf(project, target)
        else:
            export_zip(project, target)
    except Exception as exc:  # noqa: BLE001
        logger.exception("export failed")
        db.update_export(export_record["id"], None, "failed")
        raise HTTPException(status_code=500, detail="書き出しに失敗しました") from exc
    completed = db.update_export(export_record["id"], str(target), "completed")
    ungenerated = [
        panel.get("id")
        for _page, panel in all_panels(project)
        if panel.get("generation_status") != "completed"
    ]
    return {
        "export": {"id": completed["id"], "format": completed["format"], "status": completed["status"]},
        "download_url": f"/api/projects/{project_id}/export/{completed['id']}/download",
        "warning": f"未生成のコマが{len(ungenerated)}件あります" if ungenerated else None,
    }


@app.get("/api/projects/{project_id}/export/{export_id}/download")
async def api_download_export(project_id: str, export_id: str, user=Depends(current_user)):
    require_project(project_id, user["id"])
    export_record = db.get_export(export_id)
    if not export_record or export_record["project_id"] != project_id or export_record["status"] != "completed":
        raise HTTPException(status_code=404, detail="書き出しファイルが見つかりません")
    path = Path(str(export_record["file_path"])).resolve()
    export_root = get_settings().export_dir.resolve()
    if export_root not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="書き出しファイルが見つかりません")
    media_type = "application/pdf" if export_record["format"] == "pdf" else "application/zip"
    return FileResponse(path, media_type=media_type, filename=f"story-to-manga.{export_record['format']}")
