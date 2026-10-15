"""人物シートの固定配置。参照文書の座標を実装し、AIの判断で枠を動かさない。"""

from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from html import escape
from pathlib import Path
from .page_contract import A4_EXPORT_SIZE

LAYOUT_VERSION = "1.0.2"
RATIOS = ("70:99", "3:4", "4:3", "3:2")
CANVASES = {"70:99": A4_EXPORT_SIZE, "3:4": (1200, 1600), "4:3": (1600, 1200), "3:2": (1800, 1200)}
# x, y, 幅, 高さ。本体グリッド内の百分率であり、キャンバス全体ではない。
RECTANGLES = {
    "3:4": ((0, 0, 32, 27), (32, 0, 68, 27), (0, 27, 36, 16), (36, 27, 64, 16), (0, 43, 62, 18), (0, 61, 62, 26), (62, 61, 38, 26), (62, 43, 38, 18), (0, 87, 100, 13)),
    "4:3": ((0, 0, 26, 62), (26, 0, 44, 36), (70, 0, 30, 20), (26, 36, 44, 26), (70, 36, 30, 26), (0, 62, 66, 38), (66, 62, 34, 18), (70, 20, 30, 16), (66, 80, 34, 20)),
    "3:2": ((0, 0, 22, 68), (22, 0, 48, 40), (70, 0, 30, 23), (70, 23, 30, 45), (22, 40, 48, 28), (0, 68, 50, 32), (50, 68, 24, 32), (74, 68, 26, 15), (74, 83, 26, 17)),
}
ANGLES = ("正面", "斜め45度", "側面90度", "背面180度")
SECTIONS = (
    ("A", "主視覚・概要", ("主視覚",), "同じ人物の大型図。名前・身長（値と単位、または未設定）・外見の要約は2行以内。追加のプロフィール表を入れない。"),
    ("B", "全身4方向", ANGLES, "頭頂・接地線・体格・衣装の寸法を統一。左側面を基準にし、左右の特徴を反転させない。"),
    ("C", "顔・同一性", ANGLES[:3], "平常の顔を同じ縮尺で表示。顔のない対象には実在する識別部位を使う。"),
    ("D", "表情8種", ("平常", "喜び", "怒り", "悲しみ", "驚き", "不安", "自信", "決意"), "同じ顔・髪・装備・カメラを保持。表情を表せるか不明なら未設定の枠を残す。"),
    ("E", "ポーズ5種", ("立つ", "歩く", "座る", "脱力", "アクション"), "動物・非人間は元の身体構造に適応。全身を切らず、縮尺変更時は明示する。"),
    ("F", "造形細部10点", ("主要表面の前側", "主要表面の後側", "頭部の特徴・装備", "固有の識別要素", "肩・背・支持構造", "開口・接続・境界", "首・胸周辺の特徴", "手・前肢等の先端", "腰・下半身等の構造", "足・尾等の端部"), "採用済みの特徴だけを拡大。枠数を満たすために衣装・装備を創作しない。"),
    ("G", "基準色・素材", ("主色", "補助色", "明部色", "暗部色", "特徴色", "アクセント色") + tuple(f"素材{i}" for i in range(1, 9)), "上40%に基準色6枠、下60%に素材8枠。既知の部位と色だけを対応付け、未確認のHEXは未確定。推定色はApprox.と明記。素材は同じ画風で描く。"),
    ("H", "体格・シルエット", ANGLES, "同縮尺・同じ接地線の黒いシルエット。頭・肩・腰の補助線。未確認の身長を数値化しない。"),
    ("I", "世界観1カット", ("世界観",), "同じ人物1人。世界観が未設定なら簡潔な抽象背景。場所・職業・経歴を創作しない。"),
)


def source_metadata() -> list[dict]:
    return json.loads((Path(__file__).resolve().parents[1] / "catalogs/character_sheet_sources.json").read_text(encoding="utf-8"))


def _rect(x: float, y: float, width: float, height: float) -> dict:
    return dict(x=x, y=y, width=width, height=height)


def _cells(rect: dict, columns: int, rows: int, labels: tuple, prefix: str) -> list[dict]:
    return [
        {"id": f"{prefix}{i + 1:02d}", "label": label,
         "rect": _rect(rect["x"] + i % columns * rect["width"] / columns,
                       rect["y"] + i // columns * rect["height"] / rows,
                       rect["width"] / columns, rect["height"] / rows)}
        for i, label in enumerate(labels)
    ]


def fixed_sheet_layout(ratio: str) -> dict:
    if ratio not in RATIOS:
        raise ValueError("人物シートの比率はA4縦・3:4・4:3・3:2から選んでください。")
    width, height = CANVASES[ratio]
    padding, title_height = .005 * min(width, height), .018 * height
    sections = []
    for (key, name, labels, note), (x, y, w, h) in zip(SECTIONS, RECTANGLES["3:4" if ratio == "70:99" else ratio]):
        rect = _rect(.02 * width + .96 * width * x / 100 + .003 * width,
                     .07 * height + .91 * height * y / 100 + .003 * height,
                     .96 * width * w / 100 - .006 * width, .91 * height * h / 100 - .006 * height)
        inner = _rect(rect["x"] + padding, rect["y"] + padding + title_height,
                      rect["width"] - 2 * padding, rect["height"] - 2 * padding - title_height)
        grids = {"A": (1, 1), "B": (4, 1), "C": (3, 1), "D": (2, 4) if ratio == "3:2" else (4, 2),
                 "E": (5, 1), "F": (5, 2), "H": (4, 1), "I": (1, 1)}
        if key == "G":
            colors = (6, 1) if ratio == "4:3" else (3, 2)
            materials = (8, 1) if ratio == "4:3" else (4, 2)
            groups = [
                {"id": "color", "columns": colors[0], "rows": colors[1], "slots": _cells({**inner, "height": inner["height"] * .4}, *colors, labels[:6], "G-C")},
                {"id": "material", "columns": materials[0], "rows": materials[1], "slots": _cells({**inner, "y": inner["y"] + inner["height"] * .4, "height": inner["height"] * .6}, *materials, labels[6:], "G-M")},
            ]
        else:
            columns, rows = grids[key]
            groups = [{"id": key, "columns": columns, "rows": rows, "slots": _cells(inner, columns, rows, labels, key)}]
        sections.append({"id": key, "number": len(sections) + 1, "name": name, "count": len(labels),
                         "items": list(labels), "note": note, "nominal_rect": _rect(x, y, w, h), "rect": rect, "groups": groups})
    return {"id": "LAYOUT_A4_PORTRAIT" if ratio == "70:99" else "LAYOUT_" + ratio.replace(":", "_"), "version": LAYOUT_VERSION, "ratio": ratio,
            "canvas": {"width": width, "height": height}, "coordinate_basis": "canvas_pixels",
            "nominal_coordinate_basis": "body_grid_percent", "header": _rect(.02 * width, .02 * height, .96 * width, .04 * height),
            "body": _rect(.02 * width, .07 * height, .96 * width, .91 * height),
            "gutter": {"x": .006 * width, "y": .006 * height}, "padding": padding, "title_height": title_height,
            "sections": sections}


def render_sheet_svg(design: dict) -> str:
    """枠・ラベルは同一座標で確定描画する。人物画像を生成したように見せない。"""
    layout = design["fixed_layout"]
    width, height = layout["canvas"]["width"], layout["canvas"]["height"]
    font = min(width, height) * .01
    def box(rect: dict, attrs: str = "") -> str:
        return '<rect ' + ' '.join(f'{key}="{value:.4f}"' for key, value in rect.items()) + ' ' + attrs + '/>'
    def label(text: str, x: float, y: float, size: float = font, attrs: str = "") -> str:
        return f'<text x="{x:.4f}" y="{y:.4f}" font-size="{size:.4f}" {attrs}>{escape(text)}</text>'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">',
             '<title>人物シートの固定配置図（画像未生成）</title>',
             '<style>text{font-family:sans-serif;fill:#242622}rect{stroke:#85877f;stroke-width:1}</style>',
             f'<rect width="{width}" height="{height}" fill="white"/>',
             '<defs><clipPath id="header">' + box(layout["header"]) + '</clipPath></defs>',
             label("CHARACTER SHEET / " + design["character_name"], width * .025, height * .042, font * 1.6, 'clip-path="url(#header)"'),
             label(f'{layout["id"]} v{layout["version"]} / 固定配置図・画像未生成', width * .025, height * .058)]
    for section in layout["sections"]:
        rect = section["rect"]
        parts += [box(rect, f'fill="#f1f2ec" data-section="{section["id"]}"'),
                  label(section["id"] + " " + section["name"], rect["x"] + layout["padding"], rect["y"] + layout["padding"] + font * 1.2)]
        for group in section["groups"]:
            for slot in group["slots"]:
                cell, key = slot["rect"], slot["id"]
                cx = cell["x"] + cell["width"] / 2
                bottom = cell["y"] + cell["height"]
                short = slot["label"] if len(slot["label"]) <= 7 else slot["label"][:6] + "…"
                parts += [box(cell, f'fill="white" data-slot="{key}"'),
                          '<defs><clipPath id="' + key + '">' + box(cell) + '</clipPath></defs>',
                          f'<g clip-path="url(#{key})"><title>{escape(slot["label"])}</title>',
                          label(key, cx, cell["y"] + cell["height"] * .45, font, 'text-anchor="middle"'),
                          label(short, cx, bottom - font * 1.5, min(font, cell["width"] / (len(short) + 1)), 'text-anchor="middle"'),
                          label(design.get("height", {}).get("display_text", "身長：未設定") if key == "A01" else "未設定", cx, bottom - font * .35, min(font * .8, cell["width"] / 25), 'text-anchor="middle"'), '</g>']
    parts.append('</svg>')
    return "".join(parts)


def height_contract(character: dict, language: str = "ja") -> dict:
    """単位付きの設定値だけを使い、画像や単位のない数値から推定しない。"""
    raw = str(character.get("height") or "").strip()
    normalized = unicodedata.normalize("NFKC", raw)
    match = re.fullmatch(r"(?:(身長|体高|全長|Height|Body height|Body length)\s*[:：]?\s*)?(\d+(?:\.\d+)?)\s*(cm|mm|m|センチ|センチメートル|メートル|ミリメートル)", normalized, re.IGNORECASE)
    known = bool(match and float(match[2]) > 0)
    label = (match[1] if match and match[1] else ("Height" if language == "en" else "身長"))
    value, unit = (float(match[2]), match[3]) if known else (None, "")
    display = f"{value:g} {unit}" if known else ("Not set" if language == "en" else "未設定")
    return {"label": label, "value": value, "unit": unit, "status": "user_setting" if known else "unknown",
            "display_text": f"{label}：{display}", "placement": "A_profile",
            "source": "current_character_setting" if known else "unknown", "source_text": raw,
            "scope": {"character_id": character["id"], "age_range": character.get("age_range") or "未設定"},
            "revision": sha256(json.dumps([character["id"], character.get("age_range"), raw], ensure_ascii=False).encode()).hexdigest()}
