"""採用した漫画設計仕様。プラグインの会話命令と参照データを分離する。"""

from __future__ import annotations

CONTRACT_VERSION = "2.30.0"
SOURCE = {
    "name": "Manga Prompt Architect",
    "instructions_version": "6.47.0",
    "knowledge_version": CONTRACT_VERSION,
    "sections": ["0G", "0N", "0.3", "0R", "G4A", "13A"],
}

PANEL_LAYOUT_REFERENCE = {"name": "漫画コマ割りスタイルシート", "version": "1.0.0",
                          "status": "reviewed", "scope": "manga_pages"}
STYLE_TERMS = (
    "minimal linework", "simple iconic silhouette", "large simplified shapes",
    "strong form-defining shadows", "hard-edged cel shading", "sculptural volume",
    "bold tonal separation",
)


def rendering_modifiers(settings: dict) -> dict:
    return {"revision": "1.0.0", "source": "Manga Architect 6.45.0",
            "terms": list(STYLE_TERMS), "scope": "line_shape_shading",
            "implementation": "白黒は黒・白・整理したトーン面。カラーは大きな色面と明暗。墨・水彩は人物と主要物の影を明確にし、周囲のにじみを保持。",
            "preserve": "顔立ち・頭身・体格・衣装・固有模様・必要背景・採用画材。立体感を3D化や彫像化へ置換しない。",
            "user_adjustments": settings.get("style_adjustments", "")}


def creative_direction(settings: dict) -> dict:
    return {"revision": "1.0.0", "scope": "storyboard",
            "dialogue_mode": settings.get("dialogue_mode") or "conversation_led",
            "panel_direction": settings.get("panel_direction") or "dynamic",
            "reconstruction_policy": settings.get("reconstruction_policy") or "source_only",
            "sources": {key: "user" if settings.get(key) else "default" for key in
                        ("dialogue_mode", "panel_direction", "reconstruction_policy")}}


def contract_metadata() -> dict:
    return {**SOURCE, "sections": list(SOURCE["sections"])}


def storyboard_contract_prompt() -> str:
    return (
        "出来事・動作・会話・反応・テンポからコマ数と面積を選び、内容に沿う自由な配置を設計する。"
        "通常4〜6、重要場面2〜3、細かな会話・動作6〜8コマは目安でありノルマではない。"
        "専用形式・固定数の指定を優先する。均等グリッドは対比・一定テンポ等の具体的な意図がある場合に使う。"
        "複数の配置候補を比較し、layout_familyで段組・縦アンカー等を選ぶ。同じ骨格の意図なき反復を避ける。"
        "段優先を標準とし、明確な列分割は列内を読み切る。全コマと吹き出しの順序を一致させる。"
        "主感情・注目コマ・時間関係・余白の意図をlayout_reasonへ書き、インサートは同時の細部か後続イベントか区別する。"
        "変形コマの数・種類を義務にせず、最下段など位置だけで自動変形しない。"
        "変形する場合はpanel_shapeと、そのコマの動き・衝撃等に即した具体的なshape_reasonを記録する。"
        "panel_count_reasonとlayout_reasonには場面に即した短い理由を書く。"
        "場面導入・場所変更では場所・主要設備・人物と物の位置が分かる状況コマを置く。"
        "同じ場所の表情アップでは背景を省略できるが、必要な背景を画風名だけで消さない。"
        "location、spatial_relationship、reactionを記録し、会話には相手の必要な反応を対応付ける。"
        "dialogue_detailsはdialogueと同じ順序・件数とし、speaker、addressee、source、reactionを記録する。"
        "source=source_quoteは原文に実在する発言だけ。脚色はadaptation、出所不明はunspecifiedとする。"
        "creative_directionに従い、会話に適した場面は相手に向けた発言→受け答え→反応・行動を設計する。"
        "一人の長い説明を分割するだけにせず、必要な沈黙・独白・記録や専用形式を保持する。"
        "reconstruction_policy=source_onlyでは未提供の発言・事件・心情を創作せず、間接説明はナレーションで保持する。"
        "review_proposalsでは意味・事実・役割・時系列を保つ再構成案をadaptationとして記載できる。"
        "実際の発言と称さず、source_excerptへ元説明を正確に引用し、全編ネームの確認対象にする。"
        "新しい事件・成果・悪意・人物関係を会話不足のために加えない。"
        "絵・セリフで伝わる内容をナレーションで重複させず、沈黙も保持する。"
        "人物の顔立ち・年齢感・体格・衣装・左右特徴は設定どおり保持し、線・塗り・陰影を画風へ適応する。"
        "漫画原稿面は清潔な白を維持し、年代感のためにセピア・古紙・劣化を追加しない。"
    )


def image_contract_prompt() -> str:
    return (
        "内容に沿う自由配置、変形ノルマなし、位置由来の自動変形禁止。承認された枠と演出理由を守る。"
        "枠線は細い単線とし、人物や背景の描画線の太さと混同しない。"
        "指定された背景・設備・人物と物の位置・動作・表情・相手の反応を保持する。"
        "人物の顔立ち・年齢感・身体比率・衣装・固有特徴を無断変更せず、描画方式は線・塗り・陰影へ適応する。"
        "原稿面にセピア・黄ばみ・古紙・シミ・劣化を追加しない。場面内の原作にある古い小物は保持する。"
    )
