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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import BASE_DIR, ensure_data_dirs, get_settings
from .schemas import (
    ExportRequest,
    GenerateRequest,
    AIModelSettingsPayload,
    KnowledgeCreatePayload,
    KnowledgeMetadataPatch,
    ProjectKnowledgePatch,
    PanelPatch,
    ProjectPatch,
    SettingsRecommendationRequest,
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
    default_knowledge_scope,
    knowledge_content_hash,
    quality_check as knowledge_quality_check,
    normalize_knowledge_text,
    recommended_knowledge_selections,
    retrieve_knowledge_context,
)
from .services.layout import reflow_page, repair_storyboard_page
from .services.model_registry import (
    DEFAULT_AI_MODEL_SETTINGS,
    get_model_availability,
    model_for_task,
    model_registry_view,
    resolve_model_settings,
    validate_model_settings,
)
from .services.settings_recommendation import (
    enrich_recommendation,
    fallback_recommendation,
    normalize_settings_recommendation,
    recommendation_is_stale,
)
from .services.storage import (
    StorageConfigurationError,
    StorageError,
    StorageObjectNotFound,
    get_storage,
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
    settings = get_settings()
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if (
        request.url.path.startswith("/api/")
        or request.url.path in {"/login", "/register", "/logout", "/demo"}
        or (
            SESSION_COOKIE in request.cookies
            and not request.url.path.startswith("/static/")
        )
    ):
        response.headers["Cache-Control"] = "no-store"
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


def require_admin(user=Depends(current_user)):
    """管理者ロールをDB上で確認する。クライアントの値は信用しない。"""

    if not db.is_admin(user):
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
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
        "default_scope": default_knowledge_scope(str(document.get("category", ""))),
        "recommended": bool(default_knowledge_scope(str(document.get("category", ""))))
        and bool(document.get("active"))
        and not bool(document.get("archived"))
        and document.get("active_version_status") == "ready",
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
    recommendation = project.get("manga_settings_recommendation")
    if isinstance(recommendation, dict):
        recommendation = dict(recommendation)
        recommendation["stale"] = recommendation_is_stale(
            recommendation, project.get("analysis") or {}
        )
    return {
        **project,
        "manga_settings_recommendation": recommendation,
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


def project_ai_model_settings(project: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """既存の環境変数互換を保ちながら、Projectの実効モデルを解決する。"""

    runtime = get_settings()
    return resolve_model_settings(
        db.get_user_ai_model_settings(user_id),
        project.get("ai_model_settings"),
        legacy_text_model=runtime.openai_text_model,
        legacy_image_model=runtime.openai_image_model,
    )


def record_provider_generation(
    project_id: str,
    user_id: str,
    provider: Any,
    *,
    target_id: Optional[str] = None,
) -> None:
    """Providerが残したモデルメタデータをProjectへ追記する。"""

    metadata = getattr(provider, "last_generation_metadata", None)
    if not isinstance(metadata, dict):
        return
    event = dict(metadata)
    if target_id:
        event["target_id"] = str(target_id)[:120]
    db.record_generation_metadata(project_id, user_id, event)


def all_panels(project: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for page in project.get("storyboard", []) or []:
        for panel in page.get("panels", []) or []:
            yield page, panel


def panel_generation_snapshot(
    project: Dict[str, Any],
    jobs: List[Dict[str, Any]],
    recovered_job_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Panel生成Jobと保存済みPanelから、画面用の実進捗を集約する。"""

    panel_entries = list(all_panels(project))
    panel_by_id = {
        str(panel.get("id")): (page, panel)
        for page, panel in panel_entries
        if panel.get("id")
    }
    panel_jobs = [job for job in jobs if job.get("job_type") == "panel_artwork"]
    active_jobs = [
        job for job in panel_jobs if job.get("status") in {"queued", "processing"}
    ]
    active_panel_ids = {
        str(job["target_id"])
        for job in active_jobs
        if job.get("target_id") and str(job["target_id"]) in panel_by_id
    }
    panel_active_ids = {
        panel_id
        for panel_id, (_page, panel) in panel_by_id.items()
        if panel.get("generation_status") in {"queued", "processing"}
    }
    active = bool(active_jobs or panel_active_ids)
    batch_jobs: List[Dict[str, Any]] = []
    if active_jobs:
        active_batch_ids = {
            str(job.get("batch_id"))
            for job in active_jobs
            if job.get("batch_id")
        }
        if active_batch_ids:
            batch_jobs = [
                job for job in panel_jobs if str(job.get("batch_id")) in active_batch_ids
            ]
        else:
            # batch_id導入前のJobは、同一生成要求の作成時刻を近接範囲で補う。
            batch_start = min(str(job.get("created_at") or "") for job in active_jobs)
            batch_jobs = [
                job
                for job in panel_jobs
                if str(job.get("created_at") or "") >= batch_start
            ]
    batch_panel_ids = {
        str(job["target_id"])
        for job in batch_jobs
        if job.get("target_id") and str(job["target_id"]) in panel_by_id
    }
    if active and not batch_panel_ids:
        batch_panel_ids = active_panel_ids
    if active:
        batch_panel_ids.update(active_panel_ids)

    status_by_id = {
        panel_id: str(panel.get("generation_status") or "not_started")
        for panel_id, (_page, panel) in panel_by_id.items()
    }
    job_status_by_id = {
        str(job["target_id"]): str(job.get("status"))
        for job in batch_jobs
        if job.get("target_id")
    }
    counts = {"completed": 0, "generating": 0, "waiting": 0, "failed": 0, "not_started": 0}
    for panel_id in batch_panel_ids:
        status = status_by_id.get(panel_id, "not_started")
        if status not in counts:
            status = job_status_by_id.get(panel_id, status)
        if status == "processing":
            counts["generating"] += 1
        elif status == "queued":
            counts["waiting"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status == "failed":
            counts["failed"] += 1
        else:
            counts["not_started"] += 1

    active_sorted = sorted(
        active_jobs,
        key=lambda job: (
            0 if job.get("status") == "processing" else 1,
            str(job.get("created_at") or ""),
        ),
    )
    current_job = active_sorted[0] if active_sorted else None
    current_panel_id = str(current_job["target_id"]) if current_job and current_job.get("target_id") else None
    current_page = None
    current_panel = None
    if current_panel_id and current_panel_id in panel_by_id:
        page, panel = panel_by_id[current_panel_id]
        current_page = page.get("page_number")
        current_panel = panel.get("order")
    updated_values = [
        str(job.get("updated_at") or job.get("created_at") or "")
        for job in active_jobs
    ]
    latest_job = panel_jobs[0] if panel_jobs else None
    return {
        "active": active,
        "status": (
            "processing"
            if counts["generating"]
            else "queued"
            if active
            else (latest_job.get("status") if latest_job else None)
        ),
        "total": len(batch_panel_ids),
        **counts,
        "active_panel_ids": sorted(active_panel_ids),
        "batch_panel_ids": sorted(batch_panel_ids),
        "active_job_ids": [str(job["id"]) for job in active_jobs],
        "current_panel_id": current_panel_id,
        "current_page": current_page,
        "current_panel": current_panel,
        "heartbeat_at": current_job.get("updated_at") if current_job else None,
        "updated_at": max(updated_values) if updated_values else (latest_job.get("updated_at") if latest_job else None),
        "stalled": bool(recovered_job_ids),
        "recovered_job_ids": recovered_job_ids or [],
    }


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
    batch_id = secrets.token_hex(12) if candidates else None
    for panel in candidates:
        revision = int(panel.get("revision", 0))
        suffix = secrets.token_hex(4) if force else str(revision)
        idempotency_key = f"panel:{project['id']}:{panel['id']}:{suffix}"
        job = db.create_generation_job(
            project["id"],
            str(panel["id"]),
            idempotency_key,
            batch_id=batch_id,
        )
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
        if job.get("status") not in {"queued", "processing"}:
            continue
        if not db.start_generation_job(job_id):
            continue
        project = db.get_project(project_id, user_id)
        if not project:
            db.update_generation_job(job_id, "failed", "Projectが見つかりません")
            continue
        try:
            _page, panel = find_panel(project, str(job["target_id"]))
            panel["generation_status"] = "processing"
            db.update_project(project_id, user_id, storyboard=project["storyboard"], clear_quality_check=True)
            db.touch_generation_job(job_id)
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
            model_settings = project_ai_model_settings(latest, user_id)
            provider = get_ai_provider(model_settings)
            prompt_source = latest_panel.get("prompt_source", "generated")
            if getattr(provider, "uses_external_api", False) and prompt_source != "user":
                base_prompt = provider.panel_prompt(
                    latest_panel,
                    latest.get("characters") or [],
                    latest.get("settings") or {},
                    knowledge_context,
                )
                record_provider_generation(project_id, user_id, provider, target_id=latest_panel["id"])
            else:
                base_prompt = str(latest_panel.get("generation_prompt", ""))
                if not base_prompt.strip():
                    base_prompt = provider.panel_prompt(
                        latest_panel,
                        latest.get("characters") or [],
                        latest.get("settings") or {},
                        knowledge_context,
                    )
                    record_provider_generation(project_id, user_id, provider, target_id=latest_panel["id"])
            latest_panel["generation_prompt"] = append_knowledge_prompt(base_prompt, knowledge_context)
            latest_panel["knowledge_refs"] = knowledge_context.get("references", [])
            # 外部画像APIの待機中も、Jobが生きていることを記録する。
            db.touch_generation_job(job_id)
            storage_key = save_panel_artwork(
                latest["id"], latest_panel, latest["settings"], model_settings
            )
            image_metadata = latest_panel.get("generation_metadata")
            if isinstance(image_metadata, dict):
                image_event = dict(image_metadata)
                image_event["target_id"] = str(latest_panel["id"])
                db.record_generation_metadata(project_id, user_id, image_event)
            latest_panel["image_url"] = asset_url(latest["id"], storage_key)
            latest_panel["generation_status"] = "completed"
            latest_panel["generation_error"] = None
            # stale復旧で先にfailedへ進んだJobを遅いWorkerが復活させない。
            if not db.complete_panel_generation_job(
                job_id,
                project_id,
                user_id,
                latest["storyboard"],
            ):
                logger.warning(
                    "panel generation completion ignored for inactive job project_id=%s job_id=%s",
                    project_id,
                    job_id,
                )
        except Exception as exc:  # noqa: BLE001
            message = safe_job_error(exc)
            logger.exception(
                "panel generation failed project_id=%s job_id=%s target_id=%s",
                project_id,
                job_id,
                job.get("target_id"),
            )
            fallback = db.get_project(project_id, user_id)
            if fallback:
                try:
                    _page, failed_panel = find_panel(fallback, str(job["target_id"]))
                    failed_panel["generation_status"] = "failed"
                    failed_panel["generation_error"] = message
                    db.fail_panel_generation_job(
                        job_id,
                        project_id,
                        user_id,
                        fallback["storyboard"],
                        message,
                    )
                except HTTPException:
                    pass
            else:
                db.update_generation_job(job_id, "failed", message)
    finished = db.get_project(project_id, user_id)
    if finished:
        panels = [panel for _page, panel in all_panels(finished)]
        if panels and all(panel.get("generation_status") == "completed" for panel in panels):
            db.update_project(project_id, user_id, status="completed", current_step="edit")
        elif any(panel.get("generation_status") == "failed" for panel in panels):
            db.update_project(project_id, user_id, status="partially_failed")
        else:
            # 一部のコマだけを生成した場合も、Job終了後に「生成中」を残さない。
            db.update_project(
                project_id,
                user_id,
                status="storyboard_ready",
                current_step="generate",
            )


def process_character_job(project_id: str, user_id: str, job_id: str) -> None:
    """Character Bibleをバックグラウンドで処理し、必ずterminal stateへ収束させる。"""

    job = db.get_generation_job(job_id)
    if not job or job.get("status") not in {"queued", "processing"}:
        return
    if not db.start_generation_job(job_id):
        return
    started = time.monotonic()
    try:
        project = db.get_project(project_id, user_id)
        if not project or not project.get("analysis"):
            raise AIProviderError("先に物語解析を生成してください", retryable=False)
        db.touch_generation_job(job_id)
        knowledge_context = retrieve_knowledge_context(
            project_id,
            user_id,
            "character",
            str(project["analysis"]),
        )
        provider = get_ai_provider(project_ai_model_settings(project, user_id))
        characters = provider.characters(
            project["original_text"],
            project["analysis"],
            knowledge_context,
            project["settings"],
        )
        characters = normalize_characters(characters)
        if not characters or any(
            not character.get("name") or not character.get("appearance")
            for character in characters
        ):
            raise AIProviderError(
                "AIのキャラクター設定を検証できませんでした",
                retryable=False,
                error_category="validation",
            )
        for character in characters:
            character["knowledge_refs"] = knowledge_context.get("references", [])
        db.touch_generation_job(job_id)
        if not db.complete_character_job(job_id, project_id, user_id, characters):
            logger.warning(
                "character generation completion ignored for inactive job project_id=%s job_id=%s",
                project_id,
                job_id,
            )
            return
        record_provider_generation(project_id, user_id, provider)
        logger.info(
            "character job completed project_id=%s job_id=%s character_count=%s duration_seconds=%.2f",
            project_id,
            job_id,
            len(characters),
            time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001
        message = safe_job_error(exc)
        logger.error(
            "character job failed project_id=%s job_id=%s error_category=%s duration_seconds=%.2f message=%s",
            project_id,
            job_id,
            getattr(exc, "error_category", None) or type(exc).__name__,
            time.monotonic() - started,
            message,
        )
        try:
            category = getattr(exc, "error_category", None) or (
                "validation"
                if isinstance(exc, AIProviderError) and "検証" in message
                else type(exc).__name__.lower()
            )
            db.fail_character_job(
                job_id,
                project_id,
                user_id,
                message,
                str(category)[:80],
            )
        except Exception as state_exc:  # noqa: BLE001
            # Job状態更新自体の失敗でWorkerが落ちても、次回status APIのstale復旧へ委ねる。
            logger.error(
                "character job terminal-state update failed project_id=%s job_id=%s error_category=%s",
                project_id,
                job_id,
                type(state_exc).__name__,
            )


def process_storyboard_job(project_id: str, user_id: str, job_id: str) -> None:
    """Storyboardの長時間AI処理をHTTP応答から切り離して実行する。"""

    job = db.get_generation_job(job_id)
    if not job:
        return
    started = time.monotonic()
    requested_pages = 0
    requested_model = "unknown"
    try:
        db.update_generation_job(job_id, "processing")
        project = db.get_project(project_id, user_id)
        if not project or not project.get("analysis"):
            raise AIProviderError("先に物語解析を生成してください", retryable=False)
        model_settings = project_ai_model_settings(project, user_id)
        provider = get_ai_provider(model_settings)
        requested_pages = int((project.get("settings") or {}).get("target_page_count", 0))
        requested_model = model_for_task(model_settings, "storyboard")
        logger.info(
            "storyboard job started project_id=%s job_id=%s requested_pages=%s requested_model=%s",
            project_id,
            job_id,
            requested_pages,
            requested_model,
        )
        def heartbeat(
            batch_index: int,
            batch_total: int,
            page_start: int,
            page_end: int,
        ) -> None:
            db.touch_generation_job(job_id)
            logger.info(
                "storyboard job progress project_id=%s job_id=%s batch=%s/%s page_start=%s page_end=%s",
                project_id,
                job_id,
                batch_index,
                batch_total,
                page_start,
                page_end,
            )

        setattr(provider, "storyboard_progress_callback", heartbeat)
        knowledge_context = retrieve_knowledge_context(
            project_id,
            user_id,
            "storyboard",
            str(project["analysis"]),
        )
        characters = project.get("characters") or provider.characters(
            project["original_text"], project["analysis"], knowledge_context, project["settings"]
        )
        storyboard = provider.storyboard(
            project["original_text"],
            project["analysis"],
            project["settings"],
            characters,
            knowledge_context,
        )
        characters = normalize_characters(characters)
        storyboard = normalize_storyboard(storyboard, project["settings"])
        valid, validation_message = validate_storyboard(storyboard)
        if not storyboard or not valid:
            raise AIProviderError(
                validation_message or "Storyboardのページを生成できませんでした",
                retryable=False,
            )
        if not project.get("characters"):
            record_provider_generation(project_id, user_id, provider)
        record_provider_generation(project_id, user_id, provider)
        for page in storyboard:
            for panel in page.get("panels", []):
                panel["knowledge_refs"] = knowledge_context.get("references", [])
        if not db.complete_storyboard_job(
            job_id, project_id, user_id, characters, storyboard
        ):
            raise RuntimeError("Storyboard Jobを完了状態へ更新できませんでした")
        metadata = getattr(provider, "last_generation_metadata", None) or {}
        panel_count = sum(len(page.get("panels", [])) for page in storyboard)
        logger.info(
            "storyboard job completed project_id=%s job_id=%s pages=%s panels=%s requested_model=%s actual_model=%s duration_seconds=%.2f",
            project_id,
            job_id,
            len(storyboard),
            panel_count,
            requested_model,
            metadata.get("actual_model", requested_model),
            time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001
        message = safe_job_error(exc)
        logger.error(
            "storyboard job failed project_id=%s job_id=%s requested_pages=%s requested_model=%s error_category=%s duration_seconds=%.2f message=%s",
            project_id,
            job_id,
            requested_pages,
            requested_model,
            getattr(exc, "error_category", None) or type(exc).__name__,
            time.monotonic() - started,
            message,
        )
        try:
            db.fail_storyboard_job(job_id, project_id, user_id, message)
        except Exception as state_exc:  # noqa: BLE001
            logger.error(
                "storyboard job terminal-state update failed project_id=%s job_id=%s error_category=%s",
                project_id,
                job_id,
                type(state_exc).__name__,
            )


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
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "demo_enabled": get_settings().enable_demo_login},
    )


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not db.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "メールアドレスまたはパスワードを確認してください",
                "demo_enabled": get_settings().enable_demo_login,
            },
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
    if not get_settings().enable_demo_login:
        raise HTTPException(status_code=404, detail="ページが見つかりません")
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
    global_model_settings = db.get_user_ai_model_settings(user["id"])
    effective_model_settings = resolve_model_settings(
        global_model_settings,
        legacy_text_model=runtime.openai_text_model,
        legacy_image_model=runtime.openai_image_model,
    )
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "user": dict(user),
            "runtime": safe_runtime,
            "ai_model_settings": {
                "saved": global_model_settings,
                "effective": effective_model_settings,
                "registry": model_registry_view(),
            },
        },
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
    try:
        storage = get_storage()
        content = storage.get_bytes(storage.asset_key(safe_project_id, safe_filename))
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="保存先の設定を確認してください") from exc
    except (StorageError, StorageObjectNotFound):
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    media_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type)


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


@app.get("/api/admin/status")
async def admin_status(user=Depends(require_admin)):
    """管理者ログインとserver-side権限確認用の最小エンドポイント。"""

    return {"admin": True, "email": user["email"], "role": user["role"]}


def ai_model_settings_response(user_id: str, project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """AIモデル設定を秘密情報なしで返す。"""

    runtime = get_settings()
    global_settings = db.get_user_ai_model_settings(user_id)
    project_settings = project.get("ai_model_settings") if project else None
    effective = resolve_model_settings(
        global_settings,
        project_settings,
        legacy_text_model=runtime.openai_text_model,
        legacy_image_model=runtime.openai_image_model,
    )
    return {
        "settings": {
            "global": global_settings,
            "project": project_settings,
            "effective": effective,
        },
        "registry": model_registry_view(),
        "availability": get_model_availability(),
    }


@app.get("/api/settings/ai-models")
async def api_get_ai_model_settings(user=Depends(current_user)):
    return ai_model_settings_response(user["id"])


@app.put("/api/settings/ai-models")
async def api_update_ai_model_settings(
    payload: AIModelSettingsPayload, user=Depends(current_user)
):
    submitted = payload.model_dump(exclude_unset=True)
    try:
        updates = validate_model_settings(submitted, partial=True)
        current = db.get_user_ai_model_settings(user["id"]) or {}
        try:
            current = validate_model_settings(current, partial=True)
        except ValueError:
            current = {}
        saved = validate_model_settings({**DEFAULT_AI_MODEL_SETTINGS, **current, **updates})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.update_user_ai_model_settings(user["id"], saved)
    return {"saved": saved, **ai_model_settings_response(user["id"])}


@app.get("/api/projects/{project_id}/ai-model-settings")
async def api_get_project_ai_model_settings(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    return ai_model_settings_response(user["id"], project)


@app.put("/api/projects/{project_id}/ai-model-settings")
async def api_update_project_ai_model_settings(
    project_id: str,
    payload: AIModelSettingsPayload,
    user=Depends(current_user),
):
    project = require_project(project_id, user["id"])
    submitted = payload.model_dump(exclude_unset=True)
    try:
        updates = validate_model_settings(submitted, partial=True)
        current = project.get("ai_model_settings") or {}
        try:
            current = validate_model_settings(current, partial=True)
        except ValueError:
            current = {}
        saved = validate_model_settings({**current, **updates}, partial=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.update_project_ai_model_settings(project_id, user["id"], saved)
    refreshed = require_project(project_id, user["id"])
    return {"saved": saved, **ai_model_settings_response(user["id"], refreshed)}


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
    knowledge_selection_present: bool = Form(False),
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
    recommended = recommended_knowledge_selections(user["id"])
    if knowledge_ids is None and not knowledge_selection_present:
        selections = recommended
    elif knowledge_selection_present:
        # 画面で明示的にOFFにした推奨Knowledgeも行として残し、後から勝手に再有効化しない。
        selected_set = set(selected_ids)
        selections = [{**item, "enabled": item["knowledge_document_id"] in selected_set} for item in recommended]
        known_ids = {item["knowledge_document_id"] for item in selections}
        selections.extend(
            {
                "knowledge_document_id": document_id,
                "enabled": True,
                "priority": 50,
                "mode": "follow_latest",
                "selected_version_id": None,
                "scope": ["all"],
            }
            for document_id in selected_ids
            if document_id not in known_ids
        )
    else:
        # APIクライアントがknowledge_idsを渡した場合は、その明示選択だけを保存する。
        recommended_by_id = {item["knowledge_document_id"]: item for item in recommended}
        selections = [
            recommended_by_id.get(
                document_id,
                {
                    "knowledge_document_id": document_id,
                    "enabled": True,
                    "priority": 50,
                    "mode": "follow_latest",
                    "selected_version_id": None,
                    "scope": ["all"],
                },
            )
            for document_id in selected_ids
        ]
    if selections:
        try:
            db.set_project_knowledge(
                project["id"],
                user["id"],
                selections,
            )
        except ValueError as exc:
            db.delete_project(project["id"], user["id"])
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project = db.get_project(project["id"], user["id"]) or project
    return {"project": project_view(project)}


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, user=Depends(current_user)):
    return {"project": project_view(require_project(project_id, user["id"]))}


@app.post("/api/projects/{project_id}/settings/recommendation")
async def api_recommend_manga_settings(
    project_id: str,
    payload: SettingsRecommendationRequest,
    user=Depends(current_user),
):
    """既存Story Analysisから漫画化設定の推奨値を取得する。"""

    project = require_project(project_id, user["id"])
    analysis = project.get("analysis")
    if not analysis:
        raise HTTPException(status_code=400, detail="先に物語解析を生成してください")
    existing = project.get("manga_settings_recommendation")
    stale = recommendation_is_stale(existing, analysis)
    if isinstance(existing, dict) and not payload.force and not stale:
        view = project_view(project)
        return {
            "project": view,
            "recommendation": view.get("manga_settings_recommendation"),
            "mode": "cached",
            "fallback": bool(existing.get("fallback")),
        }
    # Analysis更新後の初回表示では、既存設定を勝手に変えず再提案を案内する。
    if isinstance(existing, dict) and stale and not payload.force:
        view = project_view(project)
        return {
            "project": view,
            "recommendation": view.get("manga_settings_recommendation"),
            "mode": "stale",
            "fallback": bool(existing.get("fallback")),
        }

    knowledge_context = retrieve_knowledge_context(
        project_id,
        user["id"],
        "adaptation",
        str(analysis),
    )
    provider = get_ai_provider(project_ai_model_settings(project, user["id"]))
    used_fallback = False
    try:
        recommendation = provider.recommend_settings(
            analysis,
            project["settings"],
            knowledge_context,
        )
        recommendation = normalize_settings_recommendation(
            recommendation, analysis, project["settings"]
        )
    except AIProviderError:
        # 推奨失敗で設定画面を塞がず、説明可能な決定論的フォールバックを保存する。
        used_fallback = True
        recommendation = fallback_recommendation(analysis, project["settings"])
    previous_override = bool(existing.get("user_override")) if isinstance(existing, dict) else False
    recommendation = enrich_recommendation(
        recommendation,
        analysis,
        fallback=used_fallback,
        user_override=previous_override,
        metadata=getattr(provider, "last_generation_metadata", None),
    )
    saved = db.save_manga_settings_recommendation(
        project_id, user["id"], recommendation
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    record_provider_generation(project_id, user["id"], provider)
    refreshed = require_project(project_id, user["id"])
    view = project_view(refreshed)
    return {
        "project": view,
        "recommendation": view.get("manga_settings_recommendation"),
        "mode": provider.provider_name,
        "fallback": used_fallback,
        "knowledge": knowledge_context,
    }


@app.get("/api/projects/{project_id}/knowledge")
async def api_project_knowledge(project_id: str, user=Depends(current_user)):
    selections = db.list_project_knowledge(project_id, user["id"])
    if selections is None:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    return {"knowledge": selections}


@app.get("/api/projects/{project_id}/knowledge/recommendation")
async def api_project_knowledge_recommendation(project_id: str, user=Depends(current_user)):
    """既存Projectを変更せず、適用可能な推奨Knowledgeだけを返す。"""

    current = db.list_project_knowledge(project_id, user["id"])
    if current is None:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    return {
        "can_apply": len(current) == 0,
        "recommendations": recommended_knowledge_selections(user["id"]),
    }


@app.post("/api/projects/{project_id}/knowledge/recommended")
async def api_apply_recommended_project_knowledge(project_id: str, user=Depends(current_user)):
    """未設定の既存Projectへ、ユーザー操作時だけ推奨Knowledgeを適用する。"""

    current = db.list_project_knowledge(project_id, user["id"])
    if current is None:
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    if current:
        raise HTTPException(status_code=409, detail="既存のKnowledge設定は変更しません")
    recommendations = recommended_knowledge_selections(user["id"])
    selections = db.set_project_knowledge(project_id, user["id"], recommendations) or []
    db.update_project(project_id, user["id"], clear_quality_check=True)
    project = require_project(project_id, user["id"])
    return {"project": project_view(project), "knowledge": selections}


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
    provider = get_ai_provider(project_ai_model_settings(project, user["id"]))
    if getattr(provider, "uses_external_api", False):
        try:
            review = provider.quality_check(project, context)
        except AIProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        record_provider_generation(project_id, user["id"], provider)
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
    settings = None
    if payload.settings:
        submitted_settings = payload.settings.model_dump(exclude_unset=True)
        settings = validate_settings({**(project.get("settings") or {}), **submitted_settings})
    if payload.original_text is not None and not payload.original_text.strip():
        raise HTTPException(status_code=422, detail="本文を空にすることはできません")
    characters = normalize_characters(payload.characters) if payload.characters is not None else None
    storyboard = (
        normalize_storyboard(payload.storyboard, settings or project.get("settings") or {})
        if payload.storyboard is not None
        else None
    )
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
    if payload.settings is not None:
        updated = db.mark_manga_settings_recommendation_override(
            project_id, user["id"]
        ) or updated
    return {"project": project_view(updated or project)}


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, user=Depends(current_user)):
    require_project(project_id, user["id"])
    if not db.delete_project(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Projectが見つかりません")
    try:
        get_storage().delete_project_objects(project_id)
    except (StorageConfigurationError, StorageError):
        # DB削除は完了しているため応答は成功とし、孤立ファイルだけを運用ログへ残す。
        logger.warning("project storage cleanup failed: %s", project_id)
    return {"deleted": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/analysis")
async def api_generate_analysis(project_id: str, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    knowledge_context = retrieve_knowledge_context(
        project_id, user["id"], "story_analysis", project["original_text"]
    )
    provider = get_ai_provider(project_ai_model_settings(project, user["id"]))
    try:
        analysis = provider.analyze(
            project["original_text"], project["title"], knowledge_context, project["settings"]
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    analysis = normalize_analysis(analysis)
    analysis["knowledge_refs"] = knowledge_context.get("references", [])
    record_provider_generation(project_id, user["id"], provider)
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
async def api_generate_characters(
    project_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_user),
):
    project = require_project(project_id, user["id"])
    if not project.get("analysis"):
        raise HTTPException(status_code=400, detail="先に物語解析を生成してください")
    provider = get_ai_provider(project_ai_model_settings(project, user["id"]))

    # 外部AIはHTTPリクエストへ閉じ込めず、保存済みJobとして追跡する。
    # Demoは既存の即時応答契約を維持し、課金APIなしのローカル確認を高速にする。
    if getattr(provider, "uses_external_api", False):
        job, created = db.create_async_generation_job(
            project_id,
            "character",
            f"character:{project_id}",
        )
        if not job:
            raise HTTPException(status_code=503, detail="Character処理を開始できませんでした")
        if created:
            updated = db.update_project(
                project_id,
                user["id"],
                status="processing",
                current_step="characters",
                clear_quality_check=True,
            )
            background_tasks.add_task(
                process_character_job,
                project_id,
                user["id"],
                str(job["id"]),
            )
        else:
            updated = project
            if project.get("status") != "processing" or project.get("current_step") != "characters":
                updated = db.update_project(
                    project_id,
                    user["id"],
                    status="processing",
                    current_step="characters",
                ) or project
        return JSONResponse(
            {
                "accepted": True,
                "job": job,
                "project": project_view(updated or project),
                "mode": provider.provider_name,
            },
            status_code=202,
        )

    knowledge_context = retrieve_knowledge_context(
        project_id,
        user["id"],
        "character",
        str(project["analysis"]),
    )
    try:
        characters = provider.characters(
            project["original_text"], project["analysis"], knowledge_context, project["settings"]
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    characters = normalize_characters(characters)
    record_provider_generation(project_id, user["id"], provider)
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
async def api_generate_storyboard(
    project_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_user),
):
    project = require_project(project_id, user["id"])
    if not project.get("analysis"):
        raise HTTPException(status_code=400, detail="先に物語解析を生成してください")
    provider = get_ai_provider(project_ai_model_settings(project, user["id"]))

    if getattr(provider, "uses_external_api", False):
        job, created = db.create_async_generation_job(
            project_id,
            "storyboard",
            f"storyboard:{project_id}",
        )
        if not job:
            raise HTTPException(status_code=503, detail="Storyboard処理を開始できませんでした")
        if created:
            updated = db.update_project(
                project_id,
                user["id"],
                status="processing",
                current_step="storyboard",
            )
            background_tasks.add_task(
                process_storyboard_job,
                project_id,
                user["id"],
                str(job["id"]),
            )
        else:
            updated = project
        return JSONResponse(
            {
                "accepted": True,
                "job": job,
                "project": project_view(updated or project),
                "mode": provider.provider_name,
            },
            status_code=202,
        )

    knowledge_context = retrieve_knowledge_context(
        project_id,
        user["id"],
        "storyboard",
        str(project["analysis"]),
    )
    try:
        characters = project.get("characters") or provider.characters(
            project["original_text"], project["analysis"], knowledge_context, project["settings"]
        )
        storyboard = provider.storyboard(
            project["original_text"],
            project["analysis"],
            project["settings"],
            characters,
            knowledge_context,
        )
        if not project.get("characters"):
            record_provider_generation(project_id, user["id"], provider)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    storyboard = normalize_storyboard(storyboard, project["settings"])
    record_provider_generation(project_id, user["id"], provider)
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
    if any(
        key in values
        for key in {
            "panel_shape",
            "shape_reason",
            "breakout_reason",
            "semantic_reason",
            "crop_anchor_x",
            "crop_anchor_y",
            "allow_breakout",
            "character_position",
            "subject_position",
            "face_position",
            "text_safe_zones",
            "protected_zones",
        }
    ):
        project["storyboard"] = [
            reflow_page(page, project.get("settings") or {}) if str(page.get("id")) == str(_page.get("id")) else page
            for page in project.get("storyboard", [])
        ]
    next_status = "storyboard_ready" if visual_changed and project.get("status") == "completed" else None
    updated = db.update_project(project_id, user["id"], storyboard=project["storyboard"], status=next_status, clear_quality_check=True)
    return {"project": project_view(updated or project)}


@app.post("/api/projects/{project_id}/pages/{page_id}/layout/repair")
async def api_repair_page_layout(project_id: str, page_id: str, user=Depends(current_user)):
    """Artworkを再生成せず、指定Pageのgeometryと文字配置だけを再計算する。"""

    project = require_project(project_id, user["id"])
    if not any(str(page.get("id")) == page_id for page in project.get("storyboard", [])):
        raise HTTPException(status_code=404, detail="ページが見つかりません")
    storyboard = repair_storyboard_page(
        project.get("storyboard", []),
        page_id,
        project.get("settings") or {},
    )
    updated = db.update_project(
        project_id,
        user["id"],
        storyboard=storyboard,
        clear_quality_check=True,
    )
    return {"project": project_view(updated or project), "repaired_page_id": page_id}


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
    recovered_character = db.recover_stale_character_jobs(
        project_id,
        user["id"],
        get_settings().storyboard_job_stale_seconds,
    )
    recovered_stale_panel = db.recover_stale_panel_jobs(
        project_id,
        user["id"],
        get_settings().storyboard_job_stale_seconds,
    )
    recovered_orphaned_panel = db.recover_orphaned_panel_states(
        project_id, user["id"]
    )
    recovered_storyboard = db.recover_stale_storyboard_jobs(
        project_id,
        user["id"],
        get_settings().storyboard_job_stale_seconds,
    )
    recovered_panel = recovered_stale_panel + recovered_orphaned_panel
    recovered = recovered_character + recovered_panel + recovered_storyboard
    if recovered:
        logger.warning(
            "generation state recovered project_id=%s character_count=%s stale_panel_job_count=%s "
            "orphan_panel_count=%s storyboard_count=%s",
            project_id,
            len(recovered_character),
            len(recovered_stale_panel),
            len(recovered_orphaned_panel),
            len(recovered_storyboard),
        )
        project = require_project(project_id, user["id"])
    character_job = db.latest_generation_job(project_id, "character")
    # Character保存後のJob更新だけが失敗した旧状態を、保存済みデータから整合させる。
    if (
        character_job
        and character_job.get("status") == "completed"
        and project.get("characters")
        and project.get("current_step") == "characters"
        and project.get("status") in {"processing", "partially_failed"}
    ):
        project = db.update_project(
            project_id,
            user["id"],
            status="characters_ready",
            current_step="characters",
        ) or project
    storyboard_job = db.latest_generation_job(project_id, "storyboard")
    # 旧実装でStoryboard保存後のJob更新だけが失敗したProjectを安全に整合させる。
    if (
        storyboard_job
        and storyboard_job.get("status") == "completed"
        and project.get("storyboard")
        and project.get("current_step") == "storyboard"
        and project.get("status") in {"processing", "partially_failed"}
    ):
        project = db.update_project(
            project_id,
            user["id"],
            status="storyboard_ready",
            current_step="storyboard",
        ) or project
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
                "generation_metadata": panel.get("generation_metadata"),
            }
        )
    jobs = db.list_generation_jobs(project_id)
    return {
        "project_status": project.get("status"),
        "panels": panels,
        "jobs": jobs,
        "character_job": character_job,
        "storyboard_job": storyboard_job,
        "panel_generation": panel_generation_snapshot(project, jobs, recovered_panel),
        "panel_recovery": {
            "stale_job_ids": recovered_stale_panel,
            "orphan_panel_ids": recovered_orphaned_panel,
        },
        "character_recovery": {
            "stale_job_ids": recovered_character,
        },
    }


@app.post("/api/projects/{project_id}/export")
async def api_export(project_id: str, payload: ExportRequest, user=Depends(current_user)):
    project = require_project(project_id, user["id"])
    try:
        storage = get_storage()
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="保存先の設定を確認してください") from exc
    export_record = db.create_export(project_id, payload.format, None, "processing")
    storage_key = storage.export_key(project_id, export_record["id"], payload.format)
    try:
        if payload.format == "pdf":
            content = export_pdf(project, storage)
        else:
            content = export_zip(project, storage)
        storage.put_bytes(
            storage_key,
            content,
            content_type="application/pdf" if payload.format == "pdf" else "application/zip",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("export failed")
        db.update_export(export_record["id"], None, "failed")
        raise HTTPException(status_code=500, detail="書き出しに失敗しました") from exc
    completed = db.update_export(export_record["id"], storage_key, "completed")
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
    storage_key = export_record.get("storage_key") or export_record.get("file_path")
    try:
        storage = get_storage()
        content = storage.get_bytes(str(storage_key or ""))
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="保存先の設定を確認してください") from exc
    except (StorageError, StorageObjectNotFound):
        raise HTTPException(status_code=404, detail="書き出しファイルが見つかりません")
    media_type = "application/pdf" if export_record["format"] == "pdf" else "application/zip"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=story-to-manga.{export_record['format']}"
        },
    )
