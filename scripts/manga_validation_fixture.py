"""本番作品を複製しない、画像生成前の4ページ構図検証用データ。API呼び出しなし。"""

from app.services.layout import reflow_page
from app.services.panel_direction import direction_is_ready
from app.services.artwork_geometry import artwork_aspect_ratio, artwork_generation_size


SETTINGS = {"language": "ja", "visual_style": "cinematic", "color_mode": "color", "composition_version": 3}


def validation_pages():
    """架空の医療チーム。原作品の本文・保存設定を復元したものではない。"""
    scenes = [
        ("establishing", [
            ("朝の病院の廊下。医師が窓のそばを歩く", "今日も、一人ずつ。", "thought", "テク テク", "footstep", "high"),
            ("診察室で資料を読む医師の横顔", "珍しい症例ですね。", "normal", "", "other", "medium"),
            ("患者の話を静かに聞く医師", "まず、お話を聞かせてください。", "normal", "", "other", "medium"),
            ("机の上の検査資料を指す手。文字は描かない", "小さな違いを見逃さない。", "thought", "カサ", "rustle", "low"),
        ]),
        ("dialogue", [
            ("医師が検査画像を慎重に確認する", "いつもと違う。", "thought", "", "other", "medium"),
            ("看護師が質問に答える", "もう一度、確認しましょう。", "normal", "", "other", "medium"),
            ("医師の落ち着いた顔の近景", "急がず、確実に。", "whisper", "", "other", "high"),
            ("同僚へうなずく医師", "準備はできています。", "normal", "", "other", "medium"),
        ]),
        ("action", [
            ("手術室で医師が手袋を着ける。血液や傷口を描かない", "始めます。", "normal", "パチン", "impact", "medium"),
            ("モニターに気づいた医師の緊張した目元", "一度、止めて！", "shout", "ピタッ", "stop", "medium"),
            ("医師が安全な器具を手に取り冷静に判断する。血液や傷口なし", "ここを確認しよう。", "normal", "", "other", "high"),
            ("看護師が静かにうなずく", "安定しています。", "whisper", "", "other", "medium"),
        ]),
        ("psychological", [
            ("夕暮れの静かな病院の窓。医師が遠くを見る", "", "normal", "シーン", "ambient", "medium"),
            ("手を洗った医師が安堵する横顔", "よかった。", "weak", "", "other", "medium"),
            ("朝日に向かう医師の穏やかな笑顔", "明日も、学び続けよう。", "thought", "", "other", "high"),
            ("机の上の閉じたノートと窓の光。文字は描かない", "", "normal", "", "other", "low"),
        ]),
    ]
    pages = []
    for number, (family, rows) in enumerate(scenes, 1):
        panels = []
        for index, (description, dialogue, semantic, sfx, sound, importance) in enumerate(rows, 1):
            panels.append({"id": f"validation-{number}-{index}", "order": index,
                           "description": description, "action": description,
                           "characters": [] if (number, index) in {(1, 4), (4, 4)} else ["架空の医師" if (number, index) not in {(2, 2), (3, 4)} else "架空の看護師"],
                           "character_position": "left", "shot_type": "close-up" if (number, index) in {(2, 3), (3, 2)} else "medium shot",
                           "importance": importance, "scene_type": family,
                           "dialogue": [dialogue] if dialogue else [], "dialogue_types": [semantic] if dialogue else [],
                           "narration": ["判断を重ね、次の一歩へ。"] if (number, index) == (4, 4) else [],
                           "sfx": [sfx] if sfx else [], "sfx_types": [sound] if sfx else [],
                           "generation_status": "not_started"})
        page = {"id": f"validation-page-{number}", "page_number": number, "page_role": family,
                "title": "", "panels": panels, "layout": "classic"}
        pages.append(reflow_page(page, SETTINGS))
    return pages


def preflight(pages):
    """本文・プロンプト・認証情報を含まない構図監査を返す。"""
    report = []
    for page in pages:
        for panel in page["panels"]:
            direction = panel["panel_direction"]
            geometry = panel["geometry"]
            ratio = artwork_aspect_ratio(panel)
            size = artwork_generation_size(panel)
            width, height = map(int, size.split("x"))
            assert direction_is_ready(panel, SETTINGS), panel["id"] + ": 構図未確定"
            assert abs(width / height / ratio - 1) < 0.02, panel["id"] + ": 生成比率不一致"
            assert not direction["breakout_policy"]["enabled"]
            report.append({"panel_id": panel["id"], "shape": geometry["shape"],
                           "area": round(geometry["width"] * geometry["height"], 4),
                           "dominant": geometry.get("dominant", False), "ratio": round(ratio, 4),
                           "generation_size": size, "status": direction["status"],
                           "character_zone": direction["character_zone"], "face_safe_zone": direction["face_safe_zone"],
                           "reserved_text_zones": direction["reserved_text_zones"], "crop_anchor": direction["crop_anchor"],
                           "breakout_policy": direction["breakout_policy"], "visual_style": direction["visual_style"],
                           "dialogue_types": panel["dialogue_types"], "sfx_types": panel["sfx_types"]})
    return report
