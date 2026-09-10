"""保存した演出トークンから、文字領域を越えずに枠を描く。"""

import math

from .text_composition import body_font, wrap_text


def draw_directed_text(draw, item, left, top, width, height):
    """Preview/PDF/ZIP共通の意味別文字描画。未対応の旧形式は呼び元へ戻す。"""
    token = item.get("direction")
    if not isinstance(token, dict):
        return False
    kind = item.get("type", "bubble")
    shape = token.get("shape", "round")
    tail = token.get("tail", "none")
    stroke = int(token.get("stroke_width", 2))
    background = token.get("background", "#ffffff")
    fill = token.get("fill", "#222222")
    bottom = top + height
    body_bottom = bottom - (8 if tail != "none" else 0)
    right = left + width
    tail_center = 0.28 if item.get("side") == "right" else 0.72
    tail_direction = -1 if item.get("side") == "right" else 1
    if shape == "burst":
        # 尖端も文字の予約領域に収め、隣の顔や文字へ侵入させない。
        cx, cy = (left + right) / 2, (top + body_bottom) / 2
        points = []
        for index in range(40):
            angle = math.pi * 2 * index / 40
            radius = 1 if index % 2 == 0 else 0.88
            points.append((cx + math.cos(angle) * width / 2 * radius,
                           cy + math.sin(angle) * (body_bottom - top) / 2 * radius))
        draw.polygon(points, fill=background)
        draw.line(points + points[:1], fill=fill, width=stroke)
    elif shape == "box":
        draw.rectangle((left, top, right, body_bottom), fill=background, outline=fill, width=stroke)
    elif shape == "round":
        draw.rounded_rectangle((left, top, right, body_bottom), radius=min(width, body_bottom - top) * 0.45,
                               fill=background, outline=fill, width=stroke)
    if tail == "thought":
        for dx, dy, radius in ((0, 0, 3), (5, 4, 1)):
            cx, cy = left + width * tail_center + dx * tail_direction, body_bottom + 2 + dy
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=background, outline=fill)
    elif tail == "spoken":
        points = [(left + width * (tail_center - 0.07), body_bottom - 2), (left + width * (tail_center + tail_direction * 0.03), bottom - 1),
                  (left + width * (tail_center + 0.07), body_bottom - 2)]
        draw.polygon(points, fill=background)
        draw.line(points, fill=fill, width=stroke)
    padding = 12 if shape not in {"none", "burst"} else 0 if shape == "none" else max(12, width * 0.17)
    available_width = max(1, width - 2 * padding)
    available_height = max(1, body_bottom - top - (12 if shape != "none" else 0))
    base_size = float(item.get("font_size") or 17)
    measured = isinstance(item.get("lines"), list) and bool(item.get("font_size"))
    size = max(12, round(base_size if measured else base_size * float(token.get("size_scale", 1))))
    # 実測して予約領域内へ収める。文字本文を省略・重複しない。
    while True:
        font = body_font(size)
        lines = item["lines"] if measured else wrap_text(str(item.get("text", "")), available_width, size)
        wrapped = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2, stroke_width=int(token.get("text_stroke", 0)))
        if measured or size <= 12 or (bounds[2] - bounds[0] <= available_width and bounds[3] - bounds[1] <= available_height):
            break
        size -= 1
    tx = left + (width - (bounds[2] - bounds[0])) / 2 - bounds[0]
    ty = top + (body_bottom - top - (bounds[3] - bounds[1])) / 2 - bounds[1]
    draw.multiline_text((tx, ty), wrapped, font=font, fill=fill, spacing=2,
                        align="left" if kind == "narration" else "center",
                        stroke_width=int(token.get("text_stroke", 0)),
                        stroke_fill=token.get("text_stroke_fill", fill))
    return True
