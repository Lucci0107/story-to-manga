"""外周を固定し、隣接する二領域から共有の斜め境界を作る。AI/APIを使わない。"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

DYNAMIC_COMPOSITION_VERSION = 4


def rectangle(box: Mapping[str, Any]) -> list[list[float]]:
    x, y, w, h = (float(box[k]) for k in ("x", "y", "width", "height"))
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def bounds(points: Sequence[Sequence[float]]) -> dict[str, float]:
    xs, ys = zip(*points)
    return dict(x=min(xs), y=min(ys), width=max(xs) - min(xs), height=max(ys) - min(ys))


def inside(
    point: Sequence[float], polygon: list[list[float]], tolerance: float = 1e-7
) -> bool:
    """凸polygonの内部／辺上か。時計・反時計回りの両方に対応。"""
    crosses = [
        (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
    ]
    return all(v >= -tolerance for v in crosses) or all(v <= tolerance for v in crosses)


def polygons_overlap(a: list[list[float]], b: list[list[float]]) -> bool:
    """分離軸判定。bboxが重なる共有斜辺を誤検出しない。"""
    for polygon in (a, b):
        for p, q in zip(polygon, polygon[1:] + polygon[:1]):
            axis = (p[1] - q[1], q[0] - p[0])
            left = [x * axis[0] + y * axis[1] for x, y in a]
            right = [x * axis[0] + y * axis[1] for x, y in b]
            if max(left) <= min(right) + 1e-8 or max(right) <= min(left) + 1e-8:
                return False
    return True


def lock_outer_edges(
    points: list[list[float]], box: Mapping[str, Any], safe: Mapping[str, float]
) -> list[list[float]]:
    """TL/TR/BR/BLの外周側座標だけ固定。内部の傾きは残す。"""
    points = deepcopy(points)
    for axis, side, vertices in (
        (0, "left", (0, 3)),
        (0, "right", (1, 2)),
        (1, "top", (0, 1)),
        (1, "bottom", (2, 3)),
    ):
        edge = (
            box["x"]
            if side == "left"
            else box["x"] + box["width"]
            if side == "right"
            else box["y"]
            if side == "top"
            else box["y"] + box["height"]
        )
        if abs(edge - safe[side]) < 1e-5:
            for vertex in vertices:
                points[vertex][axis] = safe[side]
    return points


def choose_family(
    page: Mapping[str, Any], family: str, dominant: int | None, count: int
) -> str:
    if str(page.get("layout")) in {"four_panel", "4koma"}:
        return "four-panel"
    if family == "psychological":
        return "psychological"
    if dominant is not None and dominant == count - 1:
        return "climax-bottom"
    if dominant == 0:
        return "dominant-top"
    requested = page.get("layout_family")
    if requested in {"conversation-asymmetric", "vertical-anchor", "diagonal-middle"}:
        return requested
    number = int(page.get("page_number", 1) or 1)
    choices = ("conversation-asymmetric", "vertical-anchor", "diagonal-middle")
    return choices[(number - 1) % len(choices)]


def text_length(panel: Mapping[str, Any]) -> int:
    return sum(
        len(str(t))
        for key in ("dialogue", "narration", "sfx")
        for t in panel.get(key, []) or []
    )


def shared_diagonal(first, second, axis, amount, gap, edge_id):
    """一つの中心線から平行な両岸を作る。gapはページ座標、実寸3:4で補正。"""
    a, b = rectangle(first), rectangle(second)
    if axis == "vertical":
        start, end = first["y"], first["y"] + first["height"]
        center = (first["x"] + first["width"] + second["x"]) / 2
        slope = 2 * amount / ((end - start) * 4 / 3)
        half = gap * math.sqrt(1 + slope * slope) / 2
        a[1][0], a[2][0] = center + amount - half, center - amount - half
        b[0][0], b[3][0] = center + amount + half, center - amount + half
        line = [[center + amount, start], [center - amount, end]]
    else:
        start, end = first["x"], first["x"] + first["width"]
        center = (first["y"] + first["height"] + second["y"]) / 2
        slope = 2 * amount * 4 / 3 / (end - start)
        half = gap * math.sqrt(1 + slope * slope) / 2
        a[3][1], a[2][1] = center + amount - half, center - amount - half
        b[0][1], b[1][1] = center + amount + half, center - amount + half
        line = [[start, center + amount], [end, center - amount]]
    return (
        a,
        b,
        dict(
            id=edge_id,
            axis=axis,
            center_line=line,
            width=gap,
            panel_ids=[first["panel_id"], second["panel_id"]],
        ),
    )


def apply_shared_geometry(boxes, panels, family, language, number, gap):
    """最大一つの共有境界（二コマ）を候補化。長文・心理・4コマは抑制。"""
    result = deepcopy(boxes)
    for box in result.values():
        box.update(polygon_points=rectangle(box), shape="rectangle", shape_reason="")
    if family in {"psychological", "four-panel"}:
        return result, []
    candidates = []
    keys = list(result)
    for i, k in enumerate(keys):
        for j in keys[i + 1 :]:
            a, b = result[k], result[j]
            if max(text_length(panels[k]), text_length(panels[j])) > 100:
                continue
            if abs(a["y"] - b["y"]) < 1e-5 and abs(a["height"] - b["height"]) < 1e-5:
                left, right = (k, j) if a["x"] < b["x"] else (j, k)
                if (
                    abs(
                        result[left]["x"]
                        + result[left]["width"]
                        + gap
                        - result[right]["x"]
                    )
                    < 1e-4
                ):
                    candidates.append((left, right, "vertical"))
            elif abs(a["x"] - b["x"]) < 1e-5 and abs(a["width"] - b["width"]) < 1e-5:
                top, bottom = (k, j) if a["y"] < b["y"] else (j, k)
                if (
                    abs(
                        result[top]["y"]
                        + result[top]["height"]
                        + gap
                        - result[bottom]["y"]
                    )
                    < 1e-4
                ):
                    candidates.append((top, bottom, "horizontal"))
    if not candidates:
        return result, []
    k, j, axis = candidates[(number - 1) % len(candidates)]
    a, b = result[k], result[j]
    dimension = "width" if axis == "vertical" else "height"
    amount = min(
        0.035 if axis == "vertical" else 0.025, min(a[dimension], b[dimension]) * 0.10
    )
    amount *= (1 if language == "ja" else -1) * (1 if number % 2 else -1)
    ap, bp, edge = shared_diagonal(a, b, axis, amount, gap, "shared-1")
    for box, points in ((a, ap), (b, bp)):
        box["base_box"] = {key: box[key] for key in ("x", "y", "width", "height")}
        box.update(bounds(points))
        box.update(
            polygon_points=points,
            shape="trapezoid" if axis == "vertical" else "polygon",
            shape_reason="読順を保つ共有境界で場面の切替を示す",
            shared_edge_ids=[edge["id"]],
        )
    return result, [edge]


def geometry_metrics(composition: Mapping[str, Any]) -> dict[str, Any]:
    panels = composition.get("panels", [])
    safe = composition.get("outer_bounds")
    result = dict(
        outer_edge_slant_count=0,
        boundary_alignment_error=0,
        outer_margin_variance=0.0,
        meaningful_dynamic_boundary_count=0,
        gutter_consistency_error=0,
    )
    if not safe:
        return result
    for panel in panels:
        points = panel.get("polygon_points", [])
        base = panel.get("base_box") or panel
        if len(points) != 4 or panel.get("full_bleed_effect"):
            continue
        locked = lock_outer_edges(points, base, safe)
        error = max(abs(a - b) for p, q in zip(points, locked) for a, b in zip(p, q))
        result["boundary_alignment_error"] = max(
            result["boundary_alignment_error"], round(error, 6)
        )
        result["outer_edge_slant_count"] += int(error > 1e-5)
        result["outer_margin_variance"] = max(
            result["outer_margin_variance"], round(error, 6)
        )
    by_id = {p["panel_id"]: p for p in panels}
    for edge in composition.get("shared_edges", []):
        a, b = [by_id.get(key) for key in edge["panel_ids"]]
        if not a or not b:
            result["gutter_consistency_error"] += 1
            continue
        ap, bp = a["polygon_points"], b["polygon_points"]
        axis = edge["axis"]
        ai, bi = ((1, 2), (0, 3)) if axis == "vertical" else ((3, 2), (0, 1))
        coordinate = 0 if axis == "vertical" else 1
        distances = [bp[y][coordinate] - ap[x][coordinate] for x, y in zip(ai, bi)]
        centers = [[(ap[x][d] + bp[y][d]) / 2 for d in (0, 1)] for x, y in zip(ai, bi)]
        error = max(
            abs(v - w)
            for p, q in zip(centers, edge["center_line"])
            for v, w in zip(p, q)
        )
        line = edge["center_line"]
        drift = abs(line[0][coordinate] - line[1][coordinate])
        result["meaningful_dynamic_boundary_count"] += int(drift > 0.008)
        span = abs(line[1][1 - coordinate] - line[0][1 - coordinate])
        slope = drift / max(span, 1e-8) * (3 / 4 if axis == "vertical" else 4 / 3)
        expected = edge["width"] * math.sqrt(1 + slope * slope)
        result["gutter_consistency_error"] += int(
            min(distances) <= 0
            or any(abs(d - expected) > 1e-5 for d in distances)
            or error > 1e-5
        )
    return result


def polygon_safety_metrics(page: Mapping[str, Any]) -> dict[str, int]:
    result = dict(polygon_protected_clip_count=0, polygon_text_clip_count=0)
    composition = page.get("composition") or {}
    if composition.get("composition_version", 0) < DYNAMIC_COMPOSITION_VERSION:
        return result
    sources = {p["id"]: p for p in page.get("panels", [])}
    for g in composition.get("panels", []):
        source = sources.get(g["panel_id"], {})
        for key, zones in (
            ("polygon_protected_clip_count", g.get("protected_zones", [])),
            (
                "polygon_text_clip_count",
                (source.get("text_layout") or {}).get("items", []),
            ),
        ):
            for zone in zones:
                if not all(k in zone for k in ("x", "y", "width", "height")):
                    continue
                points = [
                    [g["x"] + p[0] * g["width"], g["y"] + p[1] * g["height"]]
                    for p in rectangle(zone)
                ]
                result[key] += int(
                    not all(inside(p, g["polygon_points"], 1e-5) for p in points)
                )
    return result
