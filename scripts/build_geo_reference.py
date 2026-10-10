"""Build the packaged geographic reference set from Natural Earth (public domain).

Run offline, once per edition; the output is committed and its SHA-256 is recorded in
``engine/geo/reference.py`` and in the ``ref.geo_reference_sets`` catalogue (migration 0007).

    curl -sSfLO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_0_countries.geojson
    curl -sSfLO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_1_states_provinces.geojson
    cd backend && uv run python ../scripts/build_geo_reference.py \\
        ne_50m_admin_0_countries.geojson ne_50m_admin_1_states_provinces.geojson \\
        src/insightfulcell/engine/geo/data/ne-5.1.2.json.gz

Each region keeps its code (ISO 3166-1 alpha-2 for countries, ISO 3166-2 for states), names
and abbreviations to match place names against, and a simplified outline to
assign coordinates to (WKT, rounded to about 100 m). Nothing else from the source is kept.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from shapely import make_valid, to_wkt, union_all
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

EDITION = "ne-5.1.2"
TOLERANCE_DEGREES = 0.02  # about 2 km: enough to place a point in a country or state
DECIMALS = 3  # about 100 m
# ISO 3166-2 codes Natural Earth marks as provisional ("~"): not standard codes, so left out.
_PROVISIONAL = "~"


def _names(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        for part in value.split("|"):
            text = part.strip()
            if text and text != "-99" and text not in out:
                out.append(text)
    return out


def _shape(geometry: dict[str, Any]) -> BaseGeometry:
    return make_valid(shape(geometry))


def _outline(shp: BaseGeometry) -> str:
    simple = shp.simplify(TOLERANCE_DEGREES, preserve_topology=True)
    return to_wkt(simple, rounding_precision=DECIMALS, output_dimension=2)


def countries(path: Path) -> list[dict[str, Any]]:
    """One region per ISO code. Natural Earth splits some countries into several features
    (Australia and its external territories all carry ``AU``): their outlines are merged and
    the main feature (the one whose own code is the ISO code) names the country."""
    data = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = {}
    for feature in data["features"]:
        code = feature["properties"].get("ISO_A2_EH")
        if not code or code == "-99":
            continue  # disputed areas without an ISO code
        groups.setdefault(code, []).append(feature)
    out = []
    for code, features in groups.items():
        main = next(
            (f for f in features if f["properties"]["ADM0_A3"] == f["properties"]["ISO_A3_EH"]),
            features[0],
        )
        p = main["properties"]
        out.append(
            {
                "level": "country",
                "code": code,
                "country": code,
                "name": p["NAME_EN"] or p["NAME"],
                "names": _names(
                    p.get("NAME_EN"),
                    p.get("NAME"),
                    p.get("NAME_LONG"),
                    p.get("ADMIN"),
                    p.get("FORMAL_EN"),
                    p.get("NAME_ALT"),
                    p.get("NAME_CIAWF"),
                    p.get("BRK_NAME"),
                ),
                "codes": _names(code, p.get("ISO_A3_EH"), p.get("ABBREV")),
                "geometry": _outline(union_all([_shape(f["geometry"]) for f in features])),
            }
        )
    return out


def states(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feature in data["features"]:
        p = feature["properties"]
        code = p.get("iso_3166_2") or ""
        if _PROVISIONAL in code or "-" not in code:
            continue
        country, local = code.split("-", 1)
        out.append(
            {
                "level": "state",
                "code": code,
                "country": country,
                # ``name``, not ``name_en``: the English name of DC is "Washington".
                "name": p["name"],
                "names": _names(p.get("name"), p.get("name_en"), p.get("name_alt"), p.get("gn_name")),
                "codes": _names(local, p.get("postal"), p.get("abbrev")),
                "geometry": _outline(_shape(feature["geometry"])),
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("countries", type=Path)
    parser.add_argument("states", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    regions = countries(args.countries) + states(args.states)
    regions.sort(key=lambda r: (r["level"], r["code"]))
    document = {
        "edition": EDITION,
        "source": "Natural Earth 5.1.2 (public domain): 1:50m countries and states/provinces",
        "regions": regions,
    }
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # mtime=0: the same input always produces the same bytes (and the same digest).
    payload = gzip.compress(raw.encode("utf-8"), compresslevel=9, mtime=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"{len(regions)} regions, {len(payload):,} bytes, sha256 {digest}")


if __name__ == "__main__":
    main()
