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


def detect_shared_gutters(
    boxes: Mapping[Any, Mapping[str, Any]], gap: float
) -> list[dict[str, Any]]:
    """完全一致する辺とT字接続の部分辺を、同じ隣接判定で列挙する。"""
    edges = []
    values = list(boxes.values())
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            for axis, coordinate, length, other, extent in (
                ("vertical", "x", "width", "y", "height"),
                ("horizontal", "y", "height", "x", "width"),
            ):
                first, second = (a, b) if a[coordinate] < b[coordinate] else (b, a)
                distance = second[coordinate] - first[coordinate] - first[length]
                lo = max(first[other], second[other])
                hi = min(first[other] + first[extent], second[other] + second[extent])
                if hi - lo < 1e-5 or abs(distance - gap) > 1e-4:
                    continue
                center = (first[coordinate] + first[length] + second[coordinate]) / 2
                line = (
                    [[center, lo], [center, hi]]
                    if axis == "vertical"
                    else [[lo, center], [hi, center]]
                )
                full = (
                    abs(first[other] - second[other]) < 1e-5
                    and abs(first[extent] - second[extent]) < 1e-5
                )
                band = f"{axis}:{lo:.5f}:{hi:.5f}"
                edges.append(
                    dict(
                        id=f"gutter-{len(edges) + 1}",
                        axis=axis,
                        center_line=line,
                        width=distance,
                        panel_ids=[first["panel_id"], second["panel_id"]],
                        full_span=full,
                        band_id=band,
                        angle_family="vertical",
                        angle=0.0,
                    )
                )
    return edges


def gutter_angle(edge: Mapping[str, Any]) -> float:
    line = edge["center_line"]
    axis = 0 if edge["axis"] == "vertical" else 1
    drift = line[0][axis] - line[1][axis]
    span = line[1][1 - axis] - line[0][1 - axis]
    return math.degrees(math.atan2(drift * (3 / 4 if axis == 0 else 4 / 3), span))


def materialize_gutters(
    boxes: Mapping[Any, Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> dict:
    """全頂点を一度だけ初期化し、共有する各辺の座標だけ更新する。"""
    result = deepcopy(boxes)
    by_id = {g["panel_id"]: g for g in result.values()}
    for g in result.values():
        base = g.get("base_box") or g
        g.update({k: base[k] for k in ("x", "y", "width", "height")})
        g.update(
            polygon_points=rectangle(g),
            shared_edge_ids=[],
            shape="rectangle",
            shape_reason="",
        )
    for edge in edges:
        a, b = [by_id[key] for key in edge["panel_ids"]]
        for g in (a, b):
            g["shared_edge_ids"].append(edge["id"])
        if not edge.get("full_span", True):
            continue
        coordinate = 0 if edge["axis"] == "vertical" else 1
        ai, bi = ((1, 2), (0, 3)) if coordinate == 0 else ((3, 2), (0, 1))
        angle = gutter_angle(edge)
        offset = edge["width"] / math.cos(math.radians(angle)) / 2
        for endpoint, x, y in zip(edge["center_line"], ai, bi):
            a["polygon_points"][x][coordinate] = endpoint[coordinate] - offset
            b["polygon_points"][y][coordinate] = endpoint[coordinate] + offset
    for g in result.values():
        points = g["polygon_points"]
        angled = any(
            abs(p[0] - q[0]) > 1e-6 and abs(p[1] - q[1]) > 1e-6
            for p, q in zip(points, points[1:] + points[:1])
        )
        g.setdefault("base_box", {k: g[k] for k in ("x", "y", "width", "height")})
        g.update(bounds(points))
        g["area"] = (
            abs(
                sum(
                    p[0] * q[1] - q[0] * p[1]
                    for p, q in zip(points, points[1:] + points[:1])
                )
            )
            / 2
        )
        if angled:
            g.update(
                shape="trapezoid",
                shape_reason="同じ段の共有ガターを一定幅・共通角度で配置",
            )
        g["gutter"] = {
            "type": "diagonal" if angled else "normal",
            "width": next(
                (e["width"] for e in edges if g["panel_id"] in e["panel_ids"]), 0.014
            ),
        }
    return result


def align_partial_segments(geometries, edges):
    """T字接続の共有区間を、斜線適用後に実際に向かい合う範囲へ合わせる。"""
    result = deepcopy(edges)
    by_id = {g["panel_id"]: g for g in geometries.values()}
    for edge in result:
        if edge.get("full_span", True):
            continue
        coordinate = 0 if edge["axis"] == "vertical" else 1
        ai, bi = ((1, 2), (0, 3)) if coordinate == 0 else ((3, 2), (0, 1))
        a, b = [by_id[key]["polygon_points"] for key in edge["panel_ids"]]
        lo = max(a[ai[0]][1 - coordinate], b[bi[0]][1 - coordinate])
        hi = min(a[ai[1]][1 - coordinate], b[bi[1]][1 - coordinate])
        edge["center_line"][0][1 - coordinate] = lo
        edge["center_line"][1][1 - coordinate] = hi
    return result


def scale_gutter_band(
    edges: Sequence[Mapping[str, Any]], band: str, factor: float
) -> list[dict]:
    result = deepcopy(edges)
    for edge in result:
        if edge.get("band_id") != band:
            continue
        axis = 0 if edge["axis"] == "vertical" else 1
        line = edge["center_line"]
        center = (line[0][axis] + line[1][axis]) / 2
        for point in line:
            point[axis] = center + (point[axis] - center) * factor
        edge["angle"] = gutter_angle(edge)
        edge["angle_family"] = (
            ("slight-left" if edge["angle"] > 0 else "slight-right")
            if abs(edge["angle"]) > 1e-6
            else "vertical"
        )
    return result


def apply_shared_geometry(boxes, panels, family, language, number, gap):
    edges = detect_shared_gutters(boxes, gap)
    groups = {}
    source_by_id = {box["panel_id"]: panels[key] for key, box in boxes.items()}
    for edge in edges:
        if edge["full_span"]:
            groups.setdefault(edge["band_id"], []).append(edge)
    candidates = []
    for band, group in groups.items():
        ids = {key for e in group for key in e["panel_ids"]}
        conflicts = any(
            not e["full_span"]
            and e["axis"] == group[0]["axis"]
            and ids.intersection(e["panel_ids"])
            for e in edges
        )
        if not conflicts and all(text_length(source_by_id[key]) <= 100 for key in ids):
            candidates.append(band)
    if candidates and family not in {"psychological", "four-panel"}:
        band = candidates[(number - 1) % len(candidates)]
        group = groups[band]
        dimension = "width" if group[0]["axis"] == "vertical" else "height"
        ids = {key for e in group for key in e["panel_ids"]}
        minimum = min(g[dimension] for g in boxes.values() if g["panel_id"] in ids)
        span = (
            group[0]["center_line"][1][1 if dimension == "width" else 0]
            - group[0]["center_line"][0][1 if dimension == "width" else 0]
        )
        angle_limit = (
            span
            * math.tan(math.radians(12))
            / (3 / 4 if dimension == "width" else 4 / 3)
            / 2
        )
        amount = min(
            0.035 if dimension == "width" else 0.025, minimum * 0.10, angle_limit
        )
        amount *= (1 if language == "ja" else -1) * (1 if number % 2 else -1)
        for edge in group:
            axis = 0 if edge["axis"] == "vertical" else 1
            edge["center_line"][0][axis] += amount
            edge["center_line"][1][axis] -= amount
        edges = scale_gutter_band(edges, band, 1.0)
    geometries = materialize_gutters(boxes, edges)
    return geometries, align_partial_segments(geometries, edges)


def normalize_legacy_gutters(composition: Mapping[str, Any]) -> dict:
    """保存データを変更せず、旧版の独立した斜辺を段全体の共有線へ統合。"""
    result = deepcopy(composition)
    panels = result.get("panels", [])
    if (
        not panels
        or result.get("shared_gutter_version") == 2
        or any(
            len(g.get("polygon_points", [])) != 4 or g.get("full_bleed_effect")
            for g in panels
        )
    ):
        return result
    boxes = {i: dict(g, **(g.get("base_box") or {})) for i, g in enumerate(panels)}
    # 既存の通常ガター幅を使う。近似配置へ勝手に密着させない。
    gaps = [float((g.get("gutter") or {}).get("width", 0.014)) for g in panels]
    gap = sorted(gaps)[len(gaps) // 2]
    edges = detect_shared_gutters(boxes, gap)
    groups = {}
    by_id = {g["panel_id"]: g for g in panels}
    for e in edges:
        if e["full_span"] and e["axis"] == "vertical":
            groups.setdefault(e["band_id"], []).append(e)
    for band, group in groups.items():
        axis = 0 if group[0]["axis"] == "vertical" else 1
        indices = (1, 2) if axis == 0 else (3, 2)
        drifts = []
        for edge in group:
            for side, key in enumerate(edge["panel_ids"]):
                g = by_id[key]
                pts = g.get("polygon_points", [])
                if len(pts) == 4 and g.get("shape") not in (
                    "rectangle",
                    "wide",
                    "tall",
                ):
                    indices = (
                        ((1, 2) if side == 0 else (0, 3))
                        if axis == 0
                        else ((3, 2) if side == 0 else (0, 1))
                    )
                    drifts.append((pts[indices[0]][axis] - pts[indices[1]][axis]) / 2)
        ids = {key for edge in group for key in edge["panel_ids"]}
        conflicts = any(
            not e["full_span"]
            and e["axis"] == group[0]["axis"]
            and ids.intersection(e["panel_ids"])
            for e in edges
        )
        if drifts and not conflicts:
            amount = max(drifts, key=abs)
            dim = "width" if axis == 0 else "height"
            span = (
                group[0]["center_line"][1][1 - axis]
                - group[0]["center_line"][0][1 - axis]
            )
            limit = min(
                min(box[dim] for box in boxes.values()) * 0.10,
                span * math.tan(math.radians(12)) / (3 / 4 if axis == 0 else 4 / 3) / 2,
            )
            amount = max(-limit, min(limit, amount))
            for e in group:
                e["center_line"][0][axis] += amount
                e["center_line"][1][axis] -= amount
    for band, group in groups.items():
        ids = {key for edge in group for key in edge["panel_ids"]}
        factors = (
            (0.0,)
            if any(by_id[key].get("artwork_viewport") for key in ids)
            else (1.0, 0.65, 0.35, 0.18, 0.0)
        )
        for factor in factors:
            candidate = scale_gutter_band(edges, band, factor)
            generated = materialize_gutters(boxes, candidate)
            safe = True
            for g in generated.values():
                if g["panel_id"] not in ids:
                    continue
                zones = list(g.get("protected_zones") or []) + list(
                    (g.get("text_safe_zones") or {}).values()
                )
                for zone in zones:
                    if not all(k in zone for k in ("x", "y", "width", "height")):
                        continue
                    points = [
                        [g["x"] + p[0] * g["width"], g["y"] + p[1] * g["height"]]
                        for p in rectangle(zone)
                    ]
                    safe = safe and all(inside(p, g["polygon_points"]) for p in points)
            if safe or factor == 0:
                edges = candidate
                break
    result["panels"] = list(materialize_gutters(boxes, edges).values())
    for edge in edges:
        edge["angle"] = gutter_angle(edge)
        edge["angle_family"] = (
            ("slight-left" if edge["angle"] > 0 else "slight-right")
            if abs(edge["angle"]) > 1e-6
            else "vertical"
        )
    edges = align_partial_segments(
        {i: g for i, g in enumerate(result["panels"])}, edges
    )
    result.update(shared_edges=edges, shared_gutter_version=2)
    return result


def geometry_metrics(composition: Mapping[str, Any]) -> dict[str, Any]:
    panels = composition.get("panels", [])
    safe = composition.get("outer_bounds")
    result = dict(
        outer_edge_slant_count=0,
        boundary_alignment_error=0,
        outer_margin_variance=0.0,
        meaningful_dynamic_boundary_count=0,
        gutter_consistency_error=0,
        shared_gutter_mismatch_count=0,
        adjacent_edge_angle_delta=0.0,
        gutter_width_variance=0.0,
        gutter_gap_count=0,
        gutter_overlap_count=0,
    )
    for panel in panels:
        points = panel.get("polygon_points", [])
        base = panel.get("base_box") or panel
        if len(points) != 4 or panel.get("full_bleed_effect"):
            continue
        locked = lock_outer_edges(points, base, safe) if safe else points
        error = max(abs(a - b) for p, q in zip(points, locked) for a, b in zip(p, q))
        result["boundary_alignment_error"] = max(
            result["boundary_alignment_error"], round(error, 6)
        )
        result["outer_edge_slant_count"] += int(error > 1e-5)
        result["outer_margin_variance"] = max(
            result["outer_margin_variance"], round(error, 6)
        )
    by_id = {p["panel_id"]: p for p in panels}
    band_angles = {}
    for edge in composition.get("shared_edges", []):
        a, b = [by_id.get(key) for key in edge["panel_ids"]]
        if not a or not b:
            result["gutter_consistency_error"] += 1
            result["shared_gutter_mismatch_count"] += 1
            continue
        ap, bp = a["polygon_points"], b["polygon_points"]
        axis = edge["axis"]
        ai, bi = ((1, 2), (0, 3)) if axis == "vertical" else ((3, 2), (0, 1))
        coordinate = 0 if axis == "vertical" else 1

        def sample(points, indices, position):
            p, q = [points[i] for i in indices]
            t = (position - p[1 - coordinate]) / max(
                q[1 - coordinate] - p[1 - coordinate], 1e-9
            )
            return p[coordinate] + t * (q[coordinate] - p[coordinate])

        line = edge["center_line"]
        av = [sample(ap, ai, p[1 - coordinate]) for p in line]
        bv = [sample(bp, bi, p[1 - coordinate]) for p in line]
        distances = [b - a for a, b in zip(av, bv)]
        error = max(abs((a + b) / 2 - p[coordinate]) for a, b, p in zip(av, bv, line))
        drift = abs(line[0][coordinate] - line[1][coordinate])
        result["meaningful_dynamic_boundary_count"] += int(drift > 0.008)
        angle = gutter_angle(edge)
        if edge.get("band_id"):
            band_angles.setdefault(edge["band_id"], []).append(angle)
        expected = edge["width"] / math.cos(math.radians(angle))
        bad = (
            min(distances) <= 0
            or any(abs(d - expected) > 1e-5 for d in distances)
            or error > 1e-5
        )
        if composition.get("shared_gutter_version") == 2:
            bad = bad or any(
                edge["id"] not in g.get("shared_edge_ids", []) for g in (a, b)
            )
        result["gutter_consistency_error"] += int(bad)
        result["shared_gutter_mismatch_count"] += int(bad)
        result["gutter_gap_count"] += int(max(distances) > expected + 1e-5)
        result["gutter_overlap_count"] += int(min(distances) < -1e-5)
        result["gutter_width_variance"] = max(
            result["gutter_width_variance"], round(abs(distances[0] - distances[1]), 6)
        )
        span = max(line[1][1 - coordinate] - line[0][1 - coordinate], 1e-9)
        ratio = 3 / 4 if coordinate == 0 else 4 / 3
        angles = [
            math.degrees(math.atan((v[0] - v[1]) * ratio / span)) for v in (av, bv)
        ]
        result["adjacent_edge_angle_delta"] = max(
            result["adjacent_edge_angle_delta"], round(abs(angles[0] - angles[1]), 6)
        )
    for angles in band_angles.values():
        delta = round(max(angles) - min(angles), 6)
        result["adjacent_edge_angle_delta"] = max(
            result["adjacent_edge_angle_delta"], delta
        )
        if delta > 0.1:
            result["shared_gutter_mismatch_count"] += 1
            result["gutter_consistency_error"] += 1
    if composition.get("shared_gutter_version") == 2 and panels:
        gap = float((panels[0].get("gutter") or {}).get("width", 0.014))
        expected_edges = detect_shared_gutters(
            {i: dict(p, **(p.get("base_box") or {})) for i, p in enumerate(panels)}, gap
        )
        actual = {tuple(e["panel_ids"]) for e in composition.get("shared_edges", [])}
        missing = sum(tuple(e["panel_ids"]) not in actual for e in expected_edges)
        result["shared_gutter_mismatch_count"] += missing
        result["gutter_consistency_error"] += missing
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
