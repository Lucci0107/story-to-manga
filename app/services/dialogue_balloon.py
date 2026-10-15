"""セリフの内心・声・吹き出しを同じ意味で設計と組版へ渡す。"""

BALLOONS = {
    "normal": ("spoken", "normal", "oval", "pointed", "通常の会話・楕円"),
    "thought": ("inner", "normal", "thought_cloud", "round_dots", "心の声・雲型と丸い点"),
    "shout": ("spoken", "shout", "jagged", "pointed", "声に出す叫び・ギザギザ"),
    "inner_scream": ("inner", "strong_scream", "uni_flash", "none", "心の強い叫び・ウニフラッシュ"),
    "trembling": ("spoken", "trembling", "wavy", "pointed", "震える声・波線"),
}


def balloon_family(detail: dict, fallback: str = "normal") -> str:
    if detail.get("voice_scope") == "inner":
        return "inner_scream" if detail.get("delivery") in {"strong_scream", "shout"} else "thought"
    if detail.get("voice_scope") == "spoken":
        return {"shout": "shout", "strong_scream": "shout", "trembling": "trembling"}.get(detail.get("delivery"), "normal")
    return fallback


def balloon_spec(detail: dict, family: str, panel_id: str = "", index: int = 0) -> dict:
    """形の不整合を声の意味から解決する。句読点から意味を補わない。"""
    scope, delivery, shape, tail, label = BALLOONS.get(balloon_family(detail, family), BALLOONS["normal"])
    raw = detail.get("balloon") if isinstance(detail.get("balloon"), dict) else {}
    return {
        "dialogue_id": f"{panel_id}:dialogue-{index + 1}", "voice_scope": scope, "delivery": delivery,
        "balloon": {"type": shape, "tail_type": tail,
                    "owner_character_id": str(raw.get("owner_character_id") or detail.get("speaker") or "")[:80],
                    "placement": str(raw.get("placement") or "")[:240],
                    "reason": str(raw.get("reason") or label)[:240]},
    }


def panel_dialogue_detail(panel: dict, index: int) -> dict:
    details = panel.get("dialogue_details") or []
    return details[index] if index < len(details) and isinstance(details[index], dict) else {}


def balloon_signature(panel: dict) -> list:
    """新しい明示設定だけ署名する。旧作品の署名を一括変更しない。"""
    return [{k: d[k] for k in ("voice_scope", "delivery", "balloon") if k in d}
            for d in panel.get("dialogue_details") or [] if isinstance(d, dict)]
