"""Map outlines: the reference set's regions projected, fitted and simplified into SVG paths.

Built once per map in the worker, so the browser and the HTML export draw the same shapes with
no mapping library and no tiles. Countries use the Equal Earth projection (equal-area, so large
countries do not dominate by distortion) and are cropped to the area the data covers; one
country's states use a plate carrée scaled by the cosine of the middle latitude, with Alaska and
Hawaii as insets for the United States and Russia's far east kept on one side of the
antimeridian.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

from insightfulcell.engine.geo.reference import Region, load

WIDTH: Final = 800.0
MAX_HEIGHT: Final = 640.0
PADDING: Final = 6.0
#: Simplification tolerance and the smallest island kept, in view-box units.
TOLERANCE: Final = 0.45
MIN_AREA: Final = 0.8
#: A country map is cropped to the countries with data when they cover less than this share
#: of the world's width or height.
CROP_BELOW: Final = 0.6
_LOWER_48_LAT: Final = 37.5
#: Inset: (left edge and width as shares of the main map's width, middle latitude, the
#: longitudes kept). Hawaii keeps its main islands, not the 2,000 km of reefs to the west.
_INSETS: Final = {
    "US-AK": (0.0, 0.26, 62.0, (-180.0, 180.0)),
    "US-HI": (0.28, 0.1, 20.5, (-161.0, -154.0)),
}


@dataclass(frozen=True, slots=True)
class Shape:
    code: str
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class Outlines:
    width: float
    height: float
    shapes: tuple[Shape, ...]


def outlines(level: str, country: str | None = None, focus: Collection[str] = ()) -> Outlines:
    """The regions of ``level`` (one ``country``'s states, or every country) as paths in a
    ``WIDTH``-wide view box. ``focus`` (codes with data) decides a country map's crop."""
    gazetteer = load()
    chosen: list[tuple[Region, BaseGeometry]] = [
        (r, g)
        for r, g in zip(gazetteer.regions, gazetteer.outlines, strict=True)
        if r.level == level
        and (level == "country" or r.country == country)
        and (r.code != "AQ" or r.code in focus)  # Antarctica only when it has data
    ]
    if not chosen:
        return Outlines(WIDTH, WIDTH / 2, ())
    projected = (
        [(r, _equal_earth(g)) for r, g in chosen]
        if level == "country"
        else _state_projection(chosen, country)
    )
    # A country's main body decides the crop, not its overseas territories.
    frame = _frame(
        [g for _, g in projected], [_largest(g) for r, g in projected if r.code in focus]
    )
    if level == "country" and frame is not None:
        projected = [(r, g) for r, g in projected if g.intersects(frame)]
    return _fit(projected, frame)


# --------------------------------------------------------------------------- projections


def _equal_earth(geometry: BaseGeometry) -> BaseGeometry:
    a1, a2, a3, a4 = 1.340264, -0.081106, 0.000893, 0.003796
    m = math.sqrt(3) / 2

    def project(coords: np.ndarray) -> np.ndarray:
        lon, lat = np.radians(coords[:, 0]), np.radians(coords[:, 1])
        theta = np.arcsin(m * np.sin(lat))
        t2 = theta * theta
        t6 = t2 * t2 * t2
        x = lon * np.cos(theta) / (m * (a1 + 3 * a2 * t2 + t6 * (7 * a3 + 9 * a4 * t2)))
        y = theta * (a1 + a2 * t2 + t6 * (a3 + a4 * t2))
        return np.column_stack([x, -y])  # SVG's y grows downwards

    return shapely.transform(geometry, project)


def _plate(geometry: BaseGeometry, middle_lat: float) -> BaseGeometry:
    scale = math.cos(math.radians(middle_lat))
    return shapely.transform(geometry, lambda c: np.column_stack([c[:, 0] * scale, -c[:, 1]]))


def _one_side(geometries: list[BaseGeometry]) -> list[BaseGeometry]:
    """Keep a country that crosses the antimeridian on one side of it: longitudes on the
    smaller side (by area) move by 360 degrees."""
    west = sum(shapely.area(shapely.clip_by_rect(g, -180, -90, 0, 90)) for g in geometries)
    east = sum(shapely.area(shapely.clip_by_rect(g, 0, -90, 180, 90)) for g in geometries)
    shift = 360.0 if east >= west else -360.0

    def move(coords: np.ndarray) -> np.ndarray:
        lon = coords[:, 0].copy()
        far = (lon < -90) if shift > 0 else (lon > 90)
        lon[far] += shift
        return np.column_stack([lon, coords[:, 1]])

    return [shapely.transform(g, move) for g in geometries]


def _state_projection(
    chosen: list[tuple[Region, BaseGeometry]], country: str | None
) -> list[tuple[Region, BaseGeometry]]:
    insets = {code: spec for code, spec in _INSETS.items() if country == "US"}
    main = [(r, g) for r, g in chosen if r.code not in insets]
    if not main:
        main, insets = chosen, {}
    geometries = [g for _, g in main]
    minx, _, maxx, _ = shapely.total_bounds(geometries)
    if maxx - minx > 180:
        geometries = _one_side(geometries)
    _, miny, _, maxy = shapely.total_bounds(geometries)
    middle = (miny + maxy) / 2 if country != "US" else _LOWER_48_LAT
    projected = [(r, _plate(g, middle)) for (r, _), g in zip(main, geometries, strict=True)]
    if not insets:
        return projected
    left, _, right, bottom = shapely.total_bounds([g for _, g in projected])
    span = right - left
    for region, geometry in chosen:
        spec = insets.get(region.code)
        if spec is None:
            continue
        offset, width_share, middle_lat, (west, east) = spec
        kept = shapely.clip_by_rect(geometry, west, -90, east, 90)
        (shifted,) = _one_side([kept]) if region.code == "US-AK" else (kept,)
        flat = _plate(shifted, middle_lat)
        x0, _, x1, y1 = flat.bounds
        scale = (span * width_share) / max(x1 - x0, 1e-9)

        def place(
            coords: np.ndarray, x0: float = x0, y1: float = y1, scale: float = scale,
            offset: float = offset,
        ) -> np.ndarray:  # fmt: skip
            x = left + span * offset + (coords[:, 0] - x0) * scale
            y = bottom + (coords[:, 1] - y1) * scale
            return np.column_stack([x, y])

        projected.append((region, shapely.transform(flat, place)))
    return projected


# --------------------------------------------------------------------------- fitting


def _frame(every: list[BaseGeometry], focus: list[BaseGeometry]) -> Polygon | None:
    """The area to draw: everything, or (for a world map) the countries with data plus a
    margin, when they cover only part of the world."""
    if not focus:
        return None
    ax0, ay0, ax1, ay1 = shapely.total_bounds(every)
    fx0, fy0, fx1, fy1 = shapely.total_bounds(focus)
    if (fx1 - fx0) >= CROP_BELOW * (ax1 - ax0) and (fy1 - fy0) >= CROP_BELOW * (ay1 - ay0):
        return None
    margin = 0.08 * max(fx1 - fx0, fy1 - fy0, 0.05 * (ax1 - ax0))
    return box(fx0 - margin, fy0 - margin, fx1 + margin, fy1 + margin)


def _fit(projected: list[tuple[Region, BaseGeometry]], frame: Polygon | None) -> Outlines:
    x0, y0, x1, y1 = (
        frame.bounds if frame is not None else shapely.total_bounds([g for _, g in projected])
    )
    span_x, span_y = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    inner = WIDTH - 2 * PADDING
    scale = inner / span_x
    if span_y * scale > MAX_HEIGHT - 2 * PADDING:
        scale = (MAX_HEIGHT - 2 * PADDING) / span_y
    width = span_x * scale + 2 * PADDING
    height = span_y * scale + 2 * PADDING

    def to_box(coords: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [(coords[:, 0] - x0) * scale + PADDING, (coords[:, 1] - y0) * scale + PADDING]
        )

    shapes = []
    for region, geometry in projected:
        fitted = shapely.transform(geometry, to_box).simplify(TOLERANCE, preserve_topology=True)
        path = _path(fitted)
        if path:
            shapes.append(Shape(code=region.code, name=region.name, path=path))
    return Outlines(round(width, 1), round(height, 1), tuple(shapes))


def _largest(geometry: BaseGeometry) -> BaseGeometry:
    polygons = _polygons(geometry)
    return max(polygons, key=lambda p: p.area) if polygons else geometry


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [g for g in getattr(geometry, "geoms", ()) if isinstance(g, Polygon)]


def _path(geometry: BaseGeometry) -> str:
    """SVG path data: one subpath per ring (draw with ``fill-rule: evenodd``). Islands too
    small to see are dropped, except a region's largest part."""
    polygons = sorted(_polygons(geometry), key=lambda p: -p.area)
    parts: list[str] = []
    for i, polygon in enumerate(polygons):
        if i > 0 and polygon.area < MIN_AREA:
            continue
        rings = [polygon.exterior, *(r for r in polygon.interiors if Polygon(r).area >= MIN_AREA)]
        for ring in rings:
            text = _ring(np.asarray(ring.coords))
            if text:
                parts.append(text)
    return "".join(parts)


def _ring(coords: np.ndarray) -> str:
    points: list[str] = []
    last = ""
    for x, y in coords[:-1] if len(coords) > 1 else coords:
        point = f"{x:.1f} {y:.1f}"
        if point != last:
            points.append(point)
            last = point
    if len(points) < 3:
        return ""
    return "M" + "L".join(points) + "Z"
