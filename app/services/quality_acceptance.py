"""実際に表示するページの品質採用。監査結果や生成承認とは独立して保持する。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
import json

from PIL import Image

from .cover_text import artwork_settings
from .export import render_page_png
from .generation_design import VOLATILE
from .storage import StorageError


def page_review(project: dict, page: dict, storage) -> tuple[dict, bytes]:
    """欠落画像を代替表示で採用しない。読んだ同じバイト列で組版する。"""
    images = {}
    panels = page.get("panels") or []
    if not panels:
        raise ValueError("このページにはコマがありません。")
    for panel in panels:
        if panel.get("generation_status") != "completed" or not panel.get("image_url"):
            raise ValueError("このページの画像生成が完了してから確認してください。")
        filename = PurePosixPath(str(panel["image_url"])).name
        key = storage.asset_key(project["id"], filename)
        try:
            data = storage.get_bytes(key)
            if filename.lower().endswith(".svg"):
                if ElementTree.fromstring(data).tag.split("}")[-1] != "svg":
                    raise ValueError("SVGではありません")
            else:
                with Image.open(BytesIO(data)) as image:
                    image.verify()
            images[key] = data
        except (OSError, StorageError, ValueError, ElementTree.ParseError) as exc:
            raise ValueError("保存画像を確認できません。画像を復旧してから確認してください。") from exc

    class SnapshotStorage:
        asset_key = storage.asset_key

        def get_bytes(self, key):
            return images[key]

    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in VOLATILE and k != "signature"}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value

    refs = {str(c) for p in panels for c in p.get("characters") or []}
    design = {"project_id": project["id"], "page": stable(page),
              "settings": artwork_settings(project.get("settings") or {}),
              "characters": [stable(c) for c in project.get("characters") or [] if c.get("id") in refs or c.get("name") in refs]}
    png = render_page_png(project, page, SnapshotStorage())
    audit = deepcopy(project.get("quality_check"))
    review = {"target_id": page["id"], "page_number": page.get("page_number"),
              "design_hash": sha256(json.dumps(design, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              "image_sha256": sha256(png).hexdigest(),
              "image_refs": [{"panel_id": p["id"], "image_url": p["image_url"], "revision": p.get("revision", 0)} for p in panels],
              "audit_status": "UNVERIFIED", "automated_check": audit,
              "remaining_findings": (audit or {}).get("issues", []) + (audit or {}).get("warnings", []),
              "audit_note": "実画像のAI監査は未実施です。制作状態チェックとユーザーによる画像確認を区別します。"}
    return review, png


def matching_acceptance(review: dict, records: list[dict]) -> dict | None:
    return next((r for r in records if r["target_id"] == review["target_id"]
                 and r["design_hash"] == review["design_hash"] and r["image_sha256"] == review["image_sha256"]), None)
