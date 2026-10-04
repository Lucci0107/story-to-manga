"""採用した漫画設計仕様。プラグインの会話命令と参照データを分離する。"""

from __future__ import annotations

CONTRACT_VERSION = "2.16.0"
SOURCE = {
    "name": "Manga Prompt Architect",
    "instructions_version": "6.33.0",
    "knowledge_version": CONTRACT_VERSION,
    "sections": ["0G", "0N", "0.3"],
}


def contract_metadata() -> dict:
    return {**SOURCE, "sections": list(SOURCE["sections"])}


def storyboard_contract_prompt() -> str:
    return (
        "長方形を基本に、出来事・動作・会話・反応・テンポからコマ数と面積を選ぶ。"
        "通常4〜6、重要場面2〜3、細かな会話・動作6〜8コマは目安でありノルマではない。"
        "専用形式・固定数の指定を優先する。均等グリッドも内容に合えば使用できる。"
        "変形コマの数・種類を義務にせず、最下段など位置だけで自動変形しない。"
        "変形する場合はpanel_shapeと、そのコマの動き・衝撃等に即した具体的なshape_reasonを記録する。"
        "panel_count_reasonとlayout_reasonには場面に即した短い理由を書く。"
        "場面導入・場所変更では場所・主要設備・人物と物の位置が分かる状況コマを置く。"
        "同じ場所の表情アップでは背景を省略できるが、必要な背景を画風名だけで消さない。"
        "location、spatial_relationship、reactionを記録し、会話には相手の必要な反応を対応付ける。"
        "dialogue_detailsはdialogueと同じ順序・件数とし、speaker、addressee、source、reactionを記録する。"
        "source=source_quoteは原文に実在する発言だけ。脚色はadaptation、出所不明はunspecifiedとする。"
        "実話・記録の未提供の発言・事件・心情を創作せず、間接説明はナレーションで保持する。"
        "絵・セリフで伝わる内容をナレーションで重複させず、沈黙も保持する。"
        "人物の顔立ち・年齢感・体格・衣装・左右特徴は設定どおり保持し、線・塗り・陰影を画風へ適応する。"
        "漫画原稿面は清潔な白を維持し、年代感のためにセピア・古紙・劣化を追加しない。"
    )


def image_contract_prompt() -> str:
    return (
        "長方形基本、変形ノルマなし、位置由来の自動変形禁止。承認された枠と演出理由を守る。"
        "指定された背景・設備・人物と物の位置・動作・表情・相手の反応を保持する。"
        "人物の顔立ち・年齢感・身体比率・衣装・固有特徴を無断変更せず、描画方式は線・塗り・陰影へ適応する。"
        "原稿面にセピア・黄ばみ・古紙・シミ・劣化を追加しない。場面内の原作にある古い小物は保持する。"
    )
