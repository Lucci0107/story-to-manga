"""ネーム生成の入力と途中保存を識別し、再試行用の進捗だけを公開する。"""

from __future__ import annotations

import hashlib
import json

from .character_proposal import active_characters
from .story_analysis import analysis_content_fingerprint
from .story_profile import story_fingerprint


STORYBOARD_INPUT_VERSION = 1


def content_fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def storyboard_project_inputs(project: dict) -> dict:
    return {
        "version": STORYBOARD_INPUT_VERSION,
        "source_fingerprint": story_fingerprint(project["original_text"]),
        "analysis_fingerprint": analysis_content_fingerprint(project.get("analysis") or {}),
        "character_fingerprint": content_fingerprint(active_characters(project)),
        "storyboard_fingerprint": content_fingerprint(project.get("storyboard") or []),
        "settings": project["settings"], "title": project["title"],
    }


def storyboard_inputs_match(project: dict, inputs: dict) -> bool:
    return all(inputs.get(key) == value for key, value in storyboard_project_inputs(project).items())


def public_storyboard_job(job: dict | None) -> dict | None:
    if not job:
        return None
    result = {key: value for key, value in job.items() if key != "input_json"}
    inputs = json.loads(job.get("input_json") or "{}")
    completed = len(inputs.get("pages") or [])
    result.update(completed_pages=completed, target_pages=(inputs.get("settings") or {}).get("target_page_count", 0),
                  next_page=completed + 1)
    progress = inputs.get("progress") or {}
    result["progress"] = {key: progress[key] for key in ("page_start", "page_end", "phase", "attempt", "started_at") if key in progress}
    return result
