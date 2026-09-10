"""背景内の文字と、後工程のセリフを区別する生成方針。"""

POLICIES = {"none", "abstract_only", "intentional_exact_text"}


def resolve_in_world_text(panel):
    """正確な本文のない例外は許可しない。画像内文字の自動検出は行わない。"""
    policy = panel.get("in_world_text_policy", "abstract_only")
    if policy not in POLICIES:
        policy = "abstract_only"
    text = str(panel.get("in_world_exact_text") or "").strip()[:200]
    if policy == "intentional_exact_text" and not text:
        policy = "abstract_only"
    return {"policy": policy, "exact_text": text if policy == "intentional_exact_text" else "",
            "placement": "controlled_overlay_required" if policy == "intentional_exact_text" else "no_readable_text"}


def in_world_text_prompt(panel):
    """背景文字を原画へ描き込まず、意図した文言は編集可能な合成工程へ残す。"""
    resolved = resolve_in_world_text(panel)
    base = "Do not render readable words, letters, signage, labels, captions, speech text, or pseudo-text in the artwork. "
    if resolved["policy"] == "none":
        return base + "Omit signage and text-bearing surfaces entirely."
    if resolved["policy"] == "intentional_exact_text":
        return base + "Story-critical exact text is reserved for a controlled overlay. Leave the intended sign surface blank and unobstructed; do not invent or paint its wording."
    return base + "Background signs should be blank, abstract, symbolic, or unreadable. No random letters, pseudo-Japanese, or garbled labels."
