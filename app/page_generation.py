"""ページ一体生成の確認・生成・版履歴API。既存のコマ画像は変更しない。"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from . import db
from .config import get_settings
from .schemas import ApprovalRequest
from .services.artwork import ArtworkGenerationError
from .services.generation_design import design_hash
from .services.knowledge import retrieve_knowledge_context
from .services.manga_documents import name_script_summary
from .services.page_artwork import (
    artwork_is_current, generate_page, generation_prompt, page_design, page_errors,
)
from .services.storage import StorageError, get_storage, ensure_storage_capacity

logger = logging.getLogger(__name__)


def create_page_generation_router(current_user, require_project, project_ai_model_settings):
    router = APIRouter(prefix="/api/projects/{project_id}")

    def inputs(project_id, page_id, user_id):
        project = require_project(project_id, user_id)
        page = next((p for p in project.get("storyboard") or [] if p["id"] == page_id), None)
        if page is None:
            raise HTTPException(404, "ページが見つかりません")
        query = " ".join(str(p.get("description", "")) for p in page.get("panels") or [])
        knowledge = retrieve_knowledge_context(project_id, user_id, "image_generation", query, limit=4)
        design = page_design(project, page, project_ai_model_settings(project, user_id), knowledge)
        return project, page, design

    def run_page(job_id, project_id, page_id, user_id):
        try:
            if not db.start_generation_job(job_id):
                return
            project, page, design = inputs(project_id, page_id, user_id)
            approval = db.generation_job_approval(job_id)
            if not approval or approval["design_hash"] != design_hash(design):
                raise ArtworkGenerationError("台本・モデル・Knowledgeが変更されました。現在の内容で再試行してください。")
            errors = page_errors(project, page)
            if errors:
                raise ArtworkGenerationError(" / ".join(errors))
            record = generate_page(design, project, page, job_id, get_storage())
            name = name_script_summary(project)
            record["name_script_version_id"] = name["version_id"]
            record["name_script_version_number"] = name["version_number"]
            # 生成に使った設計を版と一緒に保存。後のKnowledge更新で既存画像を失効させない。
            record["design_snapshot"] = json.loads(approval["design_json"])
            db.complete_page_generation_job(job_id, project, record)
        except Exception as exc:  # noqa: BLE001
            # 例外本文にはAPI応答や原稿が混ざり得るため、ログは種類とJob識別だけ。
            logger.warning("page generation failed job_id=%s error_type=%s", job_id, type(exc).__name__)
            job = db.get_generation_job(job_id)
            if job and job["status"] in {"queued", "processing"}:
                message = str(exc) if isinstance(exc, ArtworkGenerationError) else "ページ画像の生成・保存に失敗しました。このページだけ再試行できます。"
                db.update_generation_job(job_id, "failed", message, "page_generation")

    @router.get("/pages/{page_id}/design")
    def design_view(project_id: str, page_id: str, user=Depends(current_user)):
        project, page, design = inputs(project_id, page_id, user["id"])
        errors = page_errors(project, page)
        try:
            generation_prompt(design)
        except ArtworkGenerationError as exc:
            errors.append(str(exc))
        return {"target_id": page_id, "mode": "integrated", "design_hash": design_hash(design),
                "content": design["content"], "model": design["model"], "size": design["size"],
                "quality": design["quality"], "errors": errors,
                "knowledge_refs": design["knowledge"].get("references", []),
                "has_current_image": artwork_is_current(project, page)}

    @router.post("/pages/{page_id}/approval")
    def approve(project_id: str, page_id: str, payload: ApprovalRequest, user=Depends(current_user)):
        project, page, design = inputs(project_id, page_id, user["id"])
        digest = design_hash(design)
        if digest != payload.design_hash:
            raise HTTPException(409, "台本・モデル・Knowledgeが変更されました。内容を再確認してください。")
        errors = page_errors(project, page)
        if errors:
            raise HTTPException(422, " / ".join(errors))
        try:
            generation_prompt(design)
        except ArtworkGenerationError as exc:
            raise HTTPException(422, str(exc)) from exc
        approved = db.approve_generation_design(project_id, page_id, digest, design, user["id"])
        return {"state": "USER_APPROVED", "design_hash": digest, "approved_at": approved["approved_at"]}

    @router.post("/pages/{page_id}/generate")
    def generate(project_id: str, page_id: str, background_tasks: BackgroundTasks,
                 payload: ApprovalRequest, user=Depends(current_user)):
        project, page, design = inputs(project_id, page_id, user["id"])
        digest = design_hash(design)
        if payload.design_hash != digest:
            raise HTTPException(409, "生成内容が変更されました。最新の内容を確認してください。")
        approved = db.current_generation_approval(project_id, page_id, digest, user["id"])
        if not approved:
            raise HTTPException(409, "このページの生成内容を確認してください。")
        runtime = get_settings()
        if runtime.image_provider != "openai" or not runtime.openai_api_key:
            raise HTTPException(503, "ページ一体生成には画像APIの接続が必要です。画像モデル設定を確認してください。")
        errors = page_errors(project, page)
        if errors:
            raise HTTPException(422, " / ".join(errors))
        try:
            ensure_storage_capacity(get_storage(), operation="image_generation")
        except StorageError as exc:
            raise HTTPException(503, "保存領域を確認できないため、画像生成を開始できません。") from exc
        job = db.queue_approved_generation(approved, str(uuid.uuid4()), job_type="page_artwork")
        if not job:
            raise HTTPException(409, "このページは生成中、またはこの確認内容で生成を開始済みです。")
        background_tasks.add_task(run_page, job["id"], project_id, page_id, user["id"])
        return {"accepted": True, "job": job}

    @router.get("/page-generation/status")
    def status(project_id: str, user=Depends(current_user)):
        project = require_project(project_id, user["id"])
        db.recover_stale_page_jobs(project_id, user["id"], max(600, int(get_settings().openai_timeout_seconds) + 120))
        jobs = [j for j in db.list_generation_jobs(project_id) if j["job_type"] == "page_artwork"]
        latest = {}
        for job in jobs:
            latest.setdefault(job["target_id"], job)
        return {"active": any(j["status"] in {"queued", "processing"} for j in jobs),
                "pages": latest, "artworks": {p["id"]: {"id": a["id"], "revision": a["revision"], "image_url": a["image_url"], "stale": not artwork_is_current(project, p)}
                    for p in project.get("storyboard") or [] if (a := project["page_artworks"].get(p["id"]))}}

    @router.get("/pages/{page_id}/artwork-versions")
    def versions(project_id: str, page_id: str, user=Depends(current_user)):
        require_project(project_id, user["id"])
        return {"versions": db.page_artwork_versions(project_id, page_id, user["id"])}

    return router
